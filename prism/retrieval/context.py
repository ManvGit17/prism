"""Build the context bundle for a diff: locate, collect, rerank, pack.

Every context mode goes through `build_context`, so `none`, `vector`, `graph`
and `graph+vector` share one packer and one budget; the mode only decides
which collectors run.
"""

from dataclasses import dataclass
from pathlib import Path

from prism.config import get_settings
from prism.indexer.embedder import Embedder
from prism.indexer.parsers import parser_for
from prism.indexer.pipeline import build_overlay
from prism.retrieval.collect import Collector, SnippetReader, dedupe
from prism.retrieval.diff import FileDiff, FileStatus, parse_diff
from prism.retrieval.pack import ordered, pack
from prism.retrieval.render import wrapper
from prism.retrieval.rerank import NoReranker, Reranker
from prism.retrieval.targets import HunkTarget, locate
from prism.retrieval.tokens import CharTokenCounter, TokenCounter
from prism.schemas import (
    ContextBundle,
    ContextItem,
    ContextKind,
    ContextMode,
    DroppedItem,
    HunkContext,
)
from prism.store.base import GraphReader, VectorReader


@dataclass
class ContextOptions:
    mode: ContextMode
    budget: int
    hops: int = 1
    vector_k: int = 8
    snippet_max_lines: int = 40
    min_item_tokens: int = 40

    @classmethod
    def from_settings(cls, **overrides: object) -> "ContextOptions":
        s = get_settings()
        values: dict[str, object] = {
            "mode": ContextMode(s.context_mode),
            "budget": s.context_budget_tokens,
            "hops": s.context_hops,
            "vector_k": s.context_vector_k,
            "snippet_max_lines": s.context_snippet_max_lines,
            "min_item_tokens": s.context_min_item_tokens,
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)  # type: ignore[arg-type]


# Kinds that can be numerous and weak keep only their best-scored items.
KIND_CAPS = {ContextKind.CLASS_TEST: 3}


def cap_per_kind(items: list[ContextItem]) -> tuple[list[ContextItem], list[DroppedItem]]:
    kept: list[ContextItem] = []
    dropped: list[DroppedItem] = []
    seen: dict[ContextKind, int] = {}
    for item in ordered(items):
        cap = KIND_CAPS.get(item.kind)
        n = seen.get(item.kind, 0)
        if cap is not None and n >= cap:
            dropped.append(
                DroppedItem(
                    kind=item.kind,
                    qualified_name=item.qualified_name,
                    tokens=0,
                    reason=f"beyond the {cap} best {item.kind.value} items",
                )
            )
            continue
        seen[item.kind] = n + 1
        kept.append(item)
    return kept, dropped


def head_sources(root: Path, files: list[FileDiff]) -> dict[str, bytes | None]:
    """Changed code files at the head, read from the working tree; None marks a deletion."""
    changed: dict[str, bytes | None] = {}
    for f in files:
        if f.is_binary:
            continue
        if f.old_path and f.old_path != f.new_path and parser_for(f.old_path):
            changed[f.old_path] = None
        if f.new_path and parser_for(f.new_path):
            full = root / f.new_path
            changed[f.new_path] = full.read_bytes() if full.is_file() else None
        elif f.status is FileStatus.DELETED and f.old_path and parser_for(f.old_path):
            changed[f.old_path] = None
    return changed


def query_text(target: HunkTarget) -> str:
    """What the reranker and the vector search compare against: the hunk at the head
    plus the changed symbols' signatures."""
    sigs = [getattr(n, "signature", n.qualified_name) for n in [*target.symbols, *target.deleted]]
    removed = [ln.text for ln in target.hunk.lines if ln.kind == "-"]
    parts = [*sigs, target.hunk.new_text()]
    if not target.hunk.added_lines and removed:
        parts.append("\n".join(removed))
    return "\n".join(p for p in parts if p)


def build_context(
    root: Path,
    repo_id: str,
    diff_text: str,
    base: GraphReader,
    options: ContextOptions,
    *,
    vectors: VectorReader | None = None,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
    counter: TokenCounter | None = None,
) -> ContextBundle:
    """Context for every hunk of a diff whose head is checked out at `root`."""
    reranker = reranker or NoReranker()
    counter = counter or CharTokenCounter()
    files = parse_diff(diff_text)
    changed = head_sources(root, files)
    head = build_overlay(base, repo_id, changed)
    targets = locate(files, repo_id, base, head)
    collector = Collector(
        repo_id=repo_id,
        head=head,
        base=base,
        snippets=SnippetReader(root, options.snippet_max_lines),
        hops=options.hops,
    )
    mode = options.mode
    hunks: list[HunkContext] = []
    for target in targets:
        items: list[ContextItem] = []
        names = {n.qualified_name for n in [*target.symbols, *target.deleted]}
        if mode.uses_graph:
            focus = target.hunk.new_range
            items = dedupe(collector.graph_items(target.symbols, target.deleted, focus), names)
        query = query_text(target)
        if mode.uses_vector and vectors is not None and embedder is not None:
            vector = embedder.embed([query])[0]
            items.extend(
                collector.vector_items(
                    vector,
                    vectors,
                    options.vector_k,
                    exclude_paths=set(changed),
                    exclude_names=names | {i.qualified_name for i in items},
                )
            )
        if items:
            scores = reranker.score(query, [f"{i.qualified_name}\n{i.snippet}" for i in items])
            items = [i.model_copy(update={"score": s}) for i, s in zip(items, scores, strict=True)]
        items, capped = cap_per_kind(items)
        start, end = target.hunk.new_range
        # The budget covers the whole rendered block, wrapper included.
        overhead = counter.count("\n".join(wrapper(target.file.path, start, end))) + 1
        budget = max(0, options.budget - overhead)
        kept, dropped, used = pack(ordered(items), budget, counter, options.min_item_tokens)
        dropped = [*capped, *dropped]
        used = used + overhead if kept else 0
        hunks.append(
            HunkContext(
                path=target.file.path,
                hunk_header=target.hunk.header,
                new_start=start,
                new_end=end,
                symbols=tuple(n.qualified_name for n in target.symbols),
                deleted_symbols=tuple(n.qualified_name for n in target.deleted),
                target=target.kind,
                items=tuple(kept),
                dropped=tuple(dropped),
                tokens_used=used,
                budget=options.budget,
            )
        )
    return ContextBundle(
        repo_id=repo_id,
        mode=mode,
        token_counter=counter.name,
        reranker=reranker.name,
        hunks=tuple(hunks),
        skipped_files=tuple(f.path for f in files if f.is_binary),
    )
