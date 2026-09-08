"""Turning type hints and fields into what the documentation shows.

Everything here is pure: a hint (or a field) in, a string or a griffe
expression out. It is shared by the dynamic and static paths so the two
spell a type the same way.
"""

from __future__ import annotations

import ast
import reprlib
from typing import Any

import griffe

try:
    import typing_extensions as _tx
except ImportError:  # pragma: no cover
    import typing as _tx  # type: ignore[no-redef]

_NONE = type(None)

_shorten = reprlib.Repr()
_shorten.maxstring = 60
_shorten.maxother = 60


def _origin(hint: Any) -> Any:
    try:
        return _tx.get_origin(hint)
    except Exception:  # pragma: no cover -- exotic objects
        return None


def doc_type(hint: Any) -> str:
    """Spell a single type hint the way the documentation should show it."""
    if hint is None or hint is _NONE:
        return "None"
    if isinstance(hint, str):
        return hint
    forward = getattr(hint, "__forward_arg__", None)
    if forward is not None:
        # A type carried by name. The name is what a reader recognises.
        return forward
    if hint is _tx.Any:
        return "Any"
    origin = _origin(hint)
    if origin is _tx.Union:
        return " | ".join(doc_type(arg) for arg in _tx.get_args(hint))
    if origin is not None:
        # A parameterised generic (`list[str]`) must be spelled in full.
        text = repr(hint)
        return text[7:] if text.startswith("typing.") else text
    if isinstance(hint, type):
        module = getattr(hint, "__module__", None)
        if module in (None, "builtins"):
            return hint.__qualname__
        # Module-qualified so the name is unambiguous and cross-references
        # can resolve it (`datetime.datetime`, not a bare `datetime`).
        return f"{module}.{hint.__qualname__}"
    return repr(hint)


def _flatten_union(hint: Any) -> list[Any]:
    if _origin(hint) is _tx.Union:
        out: list[Any] = []
        for arg in _tx.get_args(hint):
            out.extend(_flatten_union(arg))
        return out
    return [hint]


def accepted_type(declared: Any, like: Any | None) -> str:
    """The honest input type: the declared type unioned with the converter's.

    A converter always accepts a value already of the declared type, and
    its ``like`` may be either broader (``str`` also accepts ``bytes``) or,
    where a branch has no useful ``like``, narrower -- so the two are
    unioned. ``Any`` drops out (it would swallow everything), an optional
    stays optional, and a single remaining member is spelled on its own.
    """
    members = _flatten_union(declared)
    if like is not None:
        members += _flatten_union(like)

    parts: list[str] = []
    has_none = False
    for member in members:
        if member is _tx.Any:
            continue
        if member is None or member is _NONE:
            has_none = True
            continue
        spelled = doc_type(member)
        if spelled not in parts:
            parts.append(spelled)
    if has_none and "None" not in parts:
        parts.append("None")
    if not parts:
        # Everything was `Any`; the declared type is still worth showing.
        return doc_type(declared)
    return " | ".join(parts)


def declared_type(field: Any) -> str:
    """A field's own type, as the attribute holds it (optional preserved)."""
    return accepted_type(field.type, None)


def expression(text: str, parent: griffe.Object) -> Any:
    """A griffe expression for ``text``, so cross-references still resolve.

    Falls back to the plain string if the text is not a parseable type
    expression, which still renders -- just without the links.
    """
    try:
        node = ast.parse(text, mode="eval").body
        return griffe.get_expression(node, parent=parent)
    except Exception:  # pragma: no cover -- text is always a type here
        return text


def default_repr(value: Any) -> str:
    """A default value as the field table shows it, kept short."""
    return _shorten.repr(value)


def notes(field: Any) -> str:
    """What is worth saying about a field beyond its type and default."""
    out: list[str] = []
    if field.var:
        out.append("init-only" if field.init else "class attribute")
    elif not field.init:
        out.append("not a parameter")
    elif field.kw and not field.positional:
        out.append("keyword-only")
    elif field.positional and not field.kw:
        out.append("positional-only")
    if field.frozen:
        out.append("frozen")
    if field.convert:
        out.append("converted")
    if field.validate:
        out.append("validated")
    return ", ".join(out)
