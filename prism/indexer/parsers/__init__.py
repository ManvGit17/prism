"""Language parsers, looked up by file extension.

A Java or TypeScript parser implements `LanguageParser` and is added to
`_PARSERS`; nothing else needs to change for files to be picked up.
"""

from prism.indexer.parsers.base import CallSite, LanguageParser, ParsedFile, ReceiverKind
from prism.indexer.parsers.python import PythonParser

_PARSERS: tuple[LanguageParser, ...] = (PythonParser(),)
_BY_EXTENSION: dict[str, LanguageParser] = {
    ext: parser for parser in _PARSERS for ext in parser.extensions
}


def parser_for(path: str) -> LanguageParser | None:
    """The parser for a repo-relative path, or None if the language is not supported."""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    return _BY_EXTENSION.get("." + name.rsplit(".", 1)[-1].lower())


def supported_extensions() -> frozenset[str]:
    return frozenset(_BY_EXTENSION)


__all__ = [
    "CallSite",
    "LanguageParser",
    "ParsedFile",
    "PythonParser",
    "ReceiverKind",
    "parser_for",
    "supported_extensions",
]
