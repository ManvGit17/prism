"""The parser interface every language implements.

A parser turns one file into nodes, DEFINES edges and unresolved call sites. It
sees one file at a time and knows nothing about the rest of the repo; the
resolver links call sites to definitions afterwards. This is what lets a PR
overlay parse only the changed files.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from prism.indexer.model import ClassNode, Edge, FileNode, FunctionNode, Node


class ReceiverKind(StrEnum):
    NONE = "none"  # foo()
    SELF = "self"  # self.foo(), cls.foo(), this.foo()
    SUPER = "super"  # super().foo()
    NAME = "name"  # a.b.foo(): receiver is a dotted name
    EXPR = "expr"  # f().foo(), x[0].foo(): receiver is any other expression


@dataclass(frozen=True, slots=True)
class CallSite:
    caller: str  # qualified name of the enclosing function, class or file
    name: str  # the called name, last segment only
    receiver_kind: ReceiverKind
    receiver: str | None  # dotted receiver text for NAME, else None
    line: int
    enclosing_class: str | None  # class whose `self` is in scope, for SELF and SUPER


@dataclass(frozen=True, slots=True)
class ParsedFile:
    repo_id: str
    path: str
    language: str
    source: bytes
    file: FileNode
    classes: tuple[ClassNode, ...]
    functions: tuple[FunctionNode, ...]
    defines: tuple[Edge, ...]
    calls: tuple[CallSite, ...]
    has_errors: bool = False

    @property
    def nodes(self) -> tuple[Node, ...]:
        return (self.file, *self.classes, *self.functions)


class LanguageParser(Protocol):
    language: str
    extensions: tuple[str, ...]

    def parse(self, repo_id: str, path: str, source: bytes) -> ParsedFile:
        """Parse one file. `path` is repo-relative with forward slashes."""
        ...
