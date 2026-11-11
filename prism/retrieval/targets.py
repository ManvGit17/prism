"""Which symbols each hunk changes.

Head symbols come from the PR overlay: the innermost function or class around
each added line (around the hunk's position for a pure deletion). Removed lines
are looked up in the base graph at the old path; a base symbol the overlay no
longer has was deleted or renamed, and is kept so its callers can still be
found. A hunk is classed as:

* `symbol`: it touches one or more functions or classes at the head;
* `deleted`: it only removes or renames symbols;
* `module`: it changes code outside any function or class (imports, constants);
* `non_code`: the file has no parser (docs, config).
"""

from dataclasses import dataclass, field
from typing import Literal

from prism.indexer.model import ClassNode, FileNode, FunctionNode, Node
from prism.indexer.parsers import parser_for
from prism.retrieval.diff import FileDiff, Hunk
from prism.store.base import GraphReader

TargetKind = Literal["symbol", "module", "deleted", "non_code"]


@dataclass
class HunkTarget:
    file: FileDiff
    hunk: Hunk
    kind: TargetKind
    symbols: list[Node] = field(default_factory=list)  # at the head (overlay)
    deleted: list[Node] = field(default_factory=list)  # in the base, gone at the head


def innermost(nodes: list[Node], line: int) -> Node | None:
    """The innermost function or class around `line`, else the file node, else None."""
    file_node = next((n for n in nodes if isinstance(n, FileNode)), None)
    if file_node is None or not file_node.contains_line(line):
        return None
    around = [n for n in nodes if isinstance(n, ClassNode | FunctionNode) and n.contains_line(line)]
    if not around:
        return file_node
    return min(around, key=lambda n: (n.end_line - n.start_line, -n.start_line))


def _dedupe(nodes: list[Node]) -> list[Node]:
    seen: set[str] = set()
    out: list[Node] = []
    for n in nodes:
        if n.qualified_name not in seen:
            seen.add(n.qualified_name)
            out.append(n)
    return out


def _deletion_points(hunk: Hunk, head_nodes: list[Node]) -> list[Node | None]:
    """For a hunk that only removes lines: the symbol the lines were removed from, when the
    context lines on both sides of a removed block sit in the same symbol."""
    out: list[Node | None] = []
    lines = hunk.lines
    for i, ln in enumerate(lines):
        if ln.kind != "-" or (i > 0 and lines[i - 1].kind == "-"):
            continue  # only the start of each removed block
        before = next((x.new_line for x in reversed(lines[:i]) if x.kind == " "), None)
        after = next((x.new_line for x in lines[i:] if x.kind == " "), None)
        if before is None or after is None:
            continue
        a, b = innermost(head_nodes, before), innermost(head_nodes, after)
        if a is not None and b is not None and a.qualified_name == b.qualified_name:
            out.append(a)
    return out


def locate(
    files: list[FileDiff], repo_id: str, base: GraphReader, head: GraphReader
) -> list[HunkTarget]:
    """One target per hunk of every non-binary file, in diff order."""
    out: list[HunkTarget] = []
    for f in files:
        if f.is_binary:
            continue
        is_code = parser_for(f.path) is not None
        head_nodes = head.nodes_in_file(repo_id, f.new_path) if is_code and f.new_path else []
        base_nodes = base.nodes_in_file(repo_id, f.old_path) if is_code and f.old_path else []
        for hunk in f.hunks:
            if not is_code:
                out.append(HunkTarget(f, hunk, "non_code"))
                continue
            found = [innermost(head_nodes, ln) for ln in hunk.added_lines]
            if not hunk.added_lines:
                found = _deletion_points(hunk, head_nodes)
            symbols = _dedupe([n for n in found if n is not None and not isinstance(n, FileNode)])
            module_level = any(isinstance(n, FileNode) for n in found)
            removed = [innermost(base_nodes, ln) for ln in hunk.removed_lines]
            deleted = _dedupe(
                [
                    n
                    for n in removed
                    if n is not None
                    and not isinstance(n, FileNode)
                    and head.get_node(repo_id, n.qualified_name) is None
                ]
            )
            if symbols:
                kind: TargetKind = "symbol"
            elif deleted:
                kind = "deleted"
            else:
                kind = "module" if module_level or f.new_path else "deleted"
            out.append(HunkTarget(f, hunk, kind, symbols, deleted))
    return out
