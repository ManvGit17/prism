"""Traces of a review: one trace per PR, a span per graph step, file and hunk (ADR 0014).

    review (trace: tags repo:<id>, pr:<n>, model:<reviewer>)
    ├─ planner
    ├─ file <path>                       one per Send() of the fan-out
    │  └─ hunk <header>  (generation)    the local model's call, or "escalated" / "cached"
    ├─ sast
    ├─ context
    ├─ verifier
    └─ aggregator

The pipeline talks to a small `Span` interface. `NoopTracer` is the default and costs nothing;
`LangfuseTracer` sends to a Langfuse server (self-hosted in the `langfuse` Compose profile).
Everything sent is masked with `prism.review.redact`: the self-hosted server stays on this
machine, but the same code can point at a hosted one.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from prism.config import Settings

Kind = Literal["span", "generation", "tool", "chain", "evaluator", "retriever"]
Level = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]


class Span(Protocol):
    def child(
        self,
        name: str,
        *,
        kind: Kind = "span",
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
        prompt: Any = None,
    ) -> Span: ...

    def end(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        level: Level | None = None,
        usage: Mapping[str, int] | None = None,
    ) -> None: ...


class Tracer(Protocol):
    name: str

    def review(
        self, *, name: str, tags: Sequence[str], metadata: Mapping[str, Any], input: Any
    ) -> Any:  # a context manager yielding the root Span
        ...

    def flush(self) -> None: ...

    def trace_url(self) -> str | None: ...

    def trace_id(self) -> str | None: ...


class NoopSpan:
    def child(self, name: str, **_: Any) -> NoopSpan:
        return self

    def end(self, **_: Any) -> None:
        return None


class NoopTracer:
    name = "off"

    @contextmanager
    def review(self, **_: Any) -> Iterator[NoopSpan]:
        yield NoopSpan()

    def flush(self) -> None:
        return None

    def trace_url(self) -> str | None:
        return None

    def trace_id(self) -> str | None:
        return None


def mask(*, data: Any, **_: Any) -> Any:
    """Redact secrets in anything sent to Langfuse, recursively."""
    from prism.review.redact import redact

    if isinstance(data, str):
        return redact(data).text
    if isinstance(data, Mapping):
        return {k: mask(data=v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [mask(data=v) for v in data]
    return data


class LangfuseSpan:
    def __init__(self, obs: Any, *, managed: bool = False) -> None:
        self._obs = obs
        # The root is ended by the SDK's context manager; ending it here too only warns.
        self._managed = managed

    def child(
        self,
        name: str,
        *,
        kind: Kind = "span",
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
        prompt: Any = None,
    ) -> LangfuseSpan:
        kwargs: dict[str, Any] = {"name": name, "as_type": kind, "input": input}
        if metadata:
            kwargs["metadata"] = dict(metadata)
        if kind == "generation":
            kwargs["model"] = model
            if prompt is not None:
                kwargs["prompt"] = prompt
        return LangfuseSpan(self._obs.start_observation(**kwargs))

    def end(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        level: Level | None = None,
        usage: Mapping[str, int] | None = None,
    ) -> None:
        update: dict[str, Any] = {}
        if output is not None:
            update["output"] = output
        if metadata:
            update["metadata"] = dict(metadata)
        if level:
            update["level"] = level
        if usage:
            update["usage_details"] = dict(usage)
        if update:
            self._obs.update(**update)
        if not self._managed:
            self._obs.end()


class LangfuseTracer:
    name = "langfuse"

    def __init__(self, client: Any, host: str, project_id: str = "prism") -> None:
        self.client = client
        self.host = host.rstrip("/")
        self.project_id = project_id
        self._trace_id: str | None = None

    @classmethod
    def from_settings(cls, s: Settings) -> LangfuseTracer:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key.get_secret_value(),
            base_url=s.langfuse_host,
            environment=s.langfuse_environment,
            mask=mask,
        )
        return cls(client, s.langfuse_host, s.langfuse_project_id)

    @contextmanager
    def review(
        self, *, name: str, tags: Sequence[str], metadata: Mapping[str, Any], input: Any
    ) -> Iterator[LangfuseSpan]:
        from langfuse import propagate_attributes

        meta = {k: str(v)[:200] for k, v in metadata.items()}
        with (
            propagate_attributes(trace_name=name, tags=list(tags), metadata=meta),
            self.client.start_as_current_observation(
                name=name, as_type="chain", input=input
            ) as root,
        ):
            self._trace_id = root.trace_id
            yield LangfuseSpan(root, managed=True)

    def flush(self) -> None:
        self.client.flush()

    def trace_id(self) -> str | None:
        return self._trace_id

    def trace_url(self) -> str | None:
        if self._trace_id is None:
            return None
        return f"{self.host}/project/{self.project_id}/traces/{self._trace_id}"


def make_tracer(s: Settings) -> Tracer:
    if s.tracing == "langfuse":
        return LangfuseTracer.from_settings(s)
    return NoopTracer()
