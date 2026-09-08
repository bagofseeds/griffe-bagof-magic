"""The single point of contact with ``bagof-magic`` / ``bagof-converters``.

The dynamic path needs to read a class the way ``bagof.magic`` built it,
including a little of the machinery that has no public surface yet. Every
such import is gathered here, behind one guard, so the coupling is in one
place a reader can audit -- and so a version that moved a private name
degrades the extension to its static fallback instead of crashing a docs
build.

``AVAILABLE`` says whether the live path can run at all. When it is
``False`` (the packages are not installed, or a private name moved), the
extension analyses the source statically instead.
"""

from __future__ import annotations

import ast
from typing import Any

AVAILABLE = True

try:
    # Public API.
    from bagof.converters import Converter
    from bagof.magic import is_magic

    # No public surface yet -- imported under the guard, promoted upstream
    # later (see the tracking issue).
    from bagof.magic._constants import (  # noqa: F401
        _FIELDS,
        _GENERATED,
        _OPTIONS,
        MISSING,
        _HasFactory,
    )
    from bagof.magic._magic import _make_mapping
    from bagof.magic._resolve import _Deferred
except Exception:  # pragma: no cover -- exercised only where uninstalled
    AVAILABLE = False
    is_magic = None  # type: ignore[assignment]
    Converter = None  # type: ignore[assignment]
    _Deferred = ()  # type: ignore[assignment]
    _FIELDS = _GENERATED = _OPTIONS = "\0"  # unfindable attribute names
    MISSING = object()
    _HasFactory = None  # type: ignore[assignment]

    def _make_mapping(*_: Any, **__: Any) -> dict[str, Any]:
        return {}


# A one-line description of each method a class can generate, keyed by the
# option that asks for it. A class records which option each generated
# method came from, so nothing here is keyed by method name: an option
# that takes a name (``eq="same_as"``) still binds the method correctly.
GENERATED_DOC = {
    "init": "Build an instance from its fields.",
    "repr": "Show the instance as the call that would build it again.",
    "eq": "Compare field by field with another instance of the same class.",
    "lt": "Order instances by comparing their fields in turn.",
    "le": "Order instances by comparing their fields in turn.",
    "gt": "Order instances by comparing their fields in turn.",
    "ge": "Order instances by comparing their fields in turn.",
    "hash": "Hash the fields that take part in the comparison.",
    "state": "Save and restore the fields, for pickling and copying.",
    "match_args": "The fields a positional pattern matches, in order.",
    "mapping": "Read the fields the way a mapping is read.",
    "setattr": "Set a field, running its conversion and validation.",
    "delattr": "Delete a field.",
    "replace": "Build a copy with some fields replaced.",
}


def magic_fields(live: type) -> dict[str, Any]:
    """Every field of a live magic class, keyed by name.

    Unlike the public ``fields()``, this keeps the pseudo-fields
    (``ClassVar`` and init-only ``Var``) -- the field table documents
    them, and an init-only ``Var`` is a genuine constructor parameter.
    """
    return dict(getattr(live, _FIELDS, None) or {})


def generated_names(live: type) -> dict[str, str]:
    """Every method the class generated, and the option that asked for it.

    The class records this for every option but the dict-like interface,
    whose method names are asked of the code that writes them.
    """
    names = dict(getattr(live, _GENERATED, None) or {})
    options = getattr(live, _OPTIONS, None)
    if getattr(options, "mapping", False):
        for name in _make_mapping("", {}):
            names.setdefault(name, "mapping")
    return names


def has_factory(field: Any) -> Any:
    """A repr-able stand-in for a field's factory (prints ``<factory>``)."""
    return _HasFactory(field.factory) if _HasFactory is not None else None


def _resolve(text: str, globalns: dict, localns: dict) -> Any:
    """Evaluate a forward-referenced type, the safe way ``Magic`` does.

    Text with a call in it is refused rather than run, so an annotation
    is never executed -- the same rule the builder applies. ``None`` when
    it cannot be resolved.
    """
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return None
    if any(isinstance(node, ast.Call) for node in ast.walk(tree)):
        return None
    try:
        return eval(  # noqa: S307 -- guarded above; no call can run
            compile(tree, "<hint>", "eval"), globalns, localns
        )
    except Exception:
        return None


def like_of(
    field: Any, globalns: dict, localns: dict
) -> Any | None:
    """The input hint a field's converter accepts, or ``None``.

    ``None`` when the field does not convert, when a hand-supplied
    callable converter gives no ``like``, or when a forward-referenced
    type cannot be resolved -- in every such case the caller falls back
    to the declared type.
    """
    if not AVAILABLE:
        return None
    converter = field.converter
    if isinstance(converter, Converter):
        try:
            return converter.like()
        except Exception:  # pragma: no cover -- registry is user code
            return None
    if _Deferred and isinstance(converter, _Deferred):
        resolved = _resolve(str(field.type), globalns, localns)
        if resolved is not None:
            try:
                return Converter.get(resolved).like()
            except Exception:  # pragma: no cover
                return None
    return None
