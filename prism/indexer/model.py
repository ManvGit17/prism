"""Graph model for the code index.

Nodes are files, classes and functions (methods included). Every node carries
`repo_id`, and a node's identity is `(repo_id, qualified_name)`.

Edges are DEFINES, IMPORTS, CALLS and TESTS. An edge belongs to the file of its
source node (`Edge.path`), so replacing a file replaces the edges that start in
it. CALLS and TESTS edges carry a confidence and how they were resolved
(ADR 0004).

Qualified names:

* a file is named by its repo-relative POSIX path, e.g. `shop/cart.py`;
* a module is the path with dots, e.g. `shop.cart` (`shop/__init__.py` is `shop`);
* a class or function is the module plus its nesting, e.g. `shop.cart.Cart.add`;
* a second definition with the same name in the same scope (a property setter,
  a conditional redefinition) gets a suffix: `shop.models.Item.label#2`.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeKind(StrEnum):
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"


class EdgeKind(StrEnum):
    DEFINES = "DEFINES"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    TESTS = "TESTS"


class Resolution(StrEnum):
    """How a call site was matched to a definition. Bands are in ADR 0004."""

    SAME_FILE = "same_file"
    SELF_METHOD = "self_method"
    INHERITED = "inherited"
    IMPORT = "import"
    STAR_IMPORT = "star_import"
    NAME_UNIQUE = "name_unique"
    NAME_AMBIGUOUS = "name_ambiguous"
    NAME_COMMON = "name_common"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NodeKey(_Frozen):
    repo_id: str
    qualified_name: str


class ImportRef(_Frozen):
    """One name bound by an import statement.

    `target` is the dotted name the local name stands for. `import a.b` binds
    `a` to `a`; `import a.b as x` binds `x` to `a.b`; `from a import b as c`
    binds `c` to `a.b`. A star import has `local_name == "*"` and `target` set
    to the module.
    """

    local_name: str
    target: str
    module: str
    line: int = Field(ge=1)

    @property
    def is_star(self) -> bool:
        return self.local_name == "*"


class _NodeBase(_Frozen):
    repo_id: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    name: str
    path: str = Field(description="Repo-relative POSIX path of the file.")
    language: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @property
    def key(self) -> NodeKey:
        return NodeKey(repo_id=self.repo_id, qualified_name=self.qualified_name)

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


class FileNode(_NodeBase):
    kind: Literal[NodeKind.FILE] = NodeKind.FILE
    module: str = Field(description="Dotted module name derived from the path.")
    is_test: bool = False
    imports: tuple[ImportRef, ...] = ()


class ClassNode(_NodeBase):
    kind: Literal[NodeKind.CLASS] = NodeKind.CLASS
    owner: str = Field(description="Qualified name of the defining file, class or function.")
    docstring: str | None = None
    bases: tuple[str, ...] = Field(
        default=(), description="Base class expressions, expanded through the file's imports."
    )
    decorators: tuple[str, ...] = ()
    span: tuple[int, int] = Field(description="Byte offsets of the whole definition.")
    is_test: bool = False
    is_fallback: bool = Field(default=False, description="Defined inside an `except` clause.")


class FunctionNode(_NodeBase):
    kind: Literal[NodeKind.FUNCTION] = NodeKind.FUNCTION
    owner: str = Field(description="Qualified name of the defining file, class or function.")
    signature: str
    docstring: str | None = None
    decorators: tuple[str, ...] = ()
    span: tuple[int, int] = Field(description="Byte offsets of the whole definition.")
    body_span: tuple[int, int] = Field(description="Byte offsets of the body.")
    is_method: bool = False
    is_async: bool = False
    is_test: bool = False
    is_fallback: bool = Field(default=False, description="Defined inside an `except` clause.")


Node = FileNode | ClassNode | FunctionNode
AnyNode = Annotated[Node, Field(discriminator="kind")]


class Edge(_Frozen):
    kind: EdgeKind
    repo_id: str = Field(min_length=1)
    src: str
    dst: str
    path: str = Field(description="File the edge starts in; the file that owns it.")
    line: int | None = Field(default=None, description="First call or import line.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    resolution: Resolution | None = None

    @property
    def ident(self) -> tuple[EdgeKind, str, str]:
        return (self.kind, self.src, self.dst)


class FileGraph(_Frozen):
    """Everything one file contributes to the graph: its nodes and the edges that start in it."""

    repo_id: str
    path: str
    nodes: tuple[AnyNode, ...]
    edges: tuple[Edge, ...] = ()


def module_name_for_path(path: str) -> str:
    """`shop/cart.py` -> `shop.cart`; `shop/__init__.py` -> `shop`."""
    stem = path.rsplit(".", 1)[0] if "." in path.rsplit("/", 1)[-1] else path
    parts = [p for p in stem.split("/") if p]
    if parts and parts[-1] == "__init__" and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(parts)
