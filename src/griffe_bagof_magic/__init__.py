"""
Griffe extension adding support for [`bagof.magic.Magic`][] classes.

Like griffe's built-in dataclasses extension (and the community attrs /
pydantic ones), this reconstructs the ``__init__`` method that
[`Magic`][bagof.magic.Magic] generates at runtime, so that documentation
tools render the real constructor signature and per-field docs -- for
classes defined by inheritance (``class C(Magic): ...``) *and* by the
``@magic`` decorator.
"""

from __future__ import annotations

__all__ = ["MagicExtension"]

import ast
from typing import Any

from griffe import (
    Attribute,
    Class,
    ExprCall,
    ExprSubscript,
    ExprTuple,
    Extension,
    Function,
    Module,
    Parameter,
    ParameterKind,
    Parameters,
)

try:
    from griffe_bagof_magic._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "0+unknown"

__all__ += ["__version__"]

# The base class and decorator that mark a class as "magic".
_MAGIC_BASE = "bagof.magic.Magic"
_MAGIC_DECORATOR = "bagof.magic.magic"

# Per-field annotation markers, by the canonical path of the shorthand
# generic (``x: KwOnly[int]``) -- their ``Annotated`` equivalents share the
# same names, handled via ``_marker_of``.
_NO_INIT = {"bagof.magic.NoInit"}
_INIT = {"bagof.magic.Init"}
_KW_ONLY = {"bagof.magic.KwOnly"}
_NOT_KW_ONLY = {"bagof.magic.NotKwOnly", "bagof.magic.Positional"}
# Pseudo-fields that are not constructor parameters.
_CLASS_VAR = {"bagof.magic.ClassVar"}
_NON_INIT_VAR = {"bagof.magic.Var"}
# Default-carrying markers: ``Default[T, value]`` / ``Factory[T, fn]``.
_DEFAULT = {"bagof.magic.Default"}
_FACTORY = {"bagof.magic.Factory"}

_ALL_MARKERS = (
    _NO_INIT | _INIT | _KW_ONLY | _NOT_KW_ONLY | _CLASS_VAR | _NON_INIT_VAR
    | _DEFAULT | _FACTORY
)


def _literal(value: Any) -> Any:
    """Best-effort evaluate a griffe expression / string to a Python value."""
    try:
        return ast.literal_eval(str(value))
    except (ValueError, SyntaxError):
        return None


def _decorator_options(class_: Class) -> dict:
    """Options passed to the ``@magic(...)`` decorator, if any."""
    for decorator in class_.decorators:
        value = decorator.value
        path = getattr(value, "canonical_path", None)
        if path == _MAGIC_DECORATOR and isinstance(value, ExprCall):
            options = {}
            for argument in value.arguments:
                name = getattr(argument, "name", None)
                if name is not None:
                    options[name] = _literal(argument.value)
            return options
    return {}


def _magic_options(class_: Class) -> dict:
    """
    Class-level options, from the base-class keywords (``class C(Magic,
    kw_only=True)``) and from the ``@magic(...)`` decorator.
    """
    options = {k: _literal(v) for k, v in (class_.keywords or {}).items()}
    options.update(_decorator_options(class_))
    return options


def _directly_magic(class_: Class) -> bool:
    """Whether ``class_`` itself derives from ``Magic`` or is ``@magic``-ed."""
    for base in class_.bases:
        if getattr(base, "canonical_path", None) == _MAGIC_BASE:
            return True
    for decorator in class_.decorators:
        path = getattr(decorator.value, "canonical_path", None)
        if path == _MAGIC_DECORATOR:
            return True
    return False


def _is_magic(class_: Class) -> bool:
    """Whether ``class_`` is a magic class -- directly or via an ancestor."""
    if _directly_magic(class_):
        return True
    try:
        return any(_directly_magic(parent) for parent in class_.mro())
    except ValueError:
        return False


_ANNOTATED = {"typing.Annotated", "typing_extensions.Annotated"}


class _Field:
    """The result of analysing a field annotation."""

    __slots__ = ("inner", "include", "kind", "default")

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.include = True   # a constructor parameter?
        self.kind = None      # ParameterKind override, or None
        self.default = None   # default value expression, or None

    def apply(self, marker: str, extras: list) -> None:
        """Apply one marker's effect (with any subscript/call ``extras``)."""
        if marker in _CLASS_VAR or marker in _NON_INIT_VAR \
                or marker in _NO_INIT:
            self.include = False
        elif marker in _INIT:
            self.include = True
        elif marker in _KW_ONLY:
            self.kind = ParameterKind.keyword_only
        elif marker in _NOT_KW_ONLY:
            self.kind = ParameterKind.positional_or_keyword
        elif marker in _DEFAULT and extras:
            self.default = extras[0]
        elif marker in _FACTORY and extras:
            self.default = ExprCall(function=extras[0], arguments=[])


def _is_class_variable(attribute: Attribute) -> bool:
    """Whether an attribute is a class variable (not a constructor param)."""
    labels = attribute.labels
    return "class-attribute" in labels and "instance-attribute" not in labels


def _analyze_field(attribute: Attribute) -> _Field:
    """
    Resolve a field's real type and constructor semantics, from either a
    shorthand marker (``KwOnly[int]``) or an ``Annotated[int, KwOnly()]``.
    """
    annotation = attribute.annotation
    field = _Field(annotation)
    field.default = attribute.value

    if isinstance(annotation, ExprSubscript):
        path = getattr(annotation.left, "canonical_path", None)
        slice_ = annotation.slice
        elements = (
            list(slice_.elements)
            if isinstance(slice_, ExprTuple)
            else [slice_]
        )
        if path in _ALL_MARKERS:
            # ``Marker[T]`` / ``Marker[T, extra]``.
            field.inner = elements[0] if elements else annotation
            field.apply(path, elements[1:])
        elif path in _ANNOTATED and elements:
            # ``Annotated[T, Marker(), ...]``.
            field.inner = elements[0]
            for meta in elements[1:]:
                if isinstance(meta, ExprCall):
                    meta_path = getattr(meta.function, "canonical_path", None)
                    if meta_path in _ALL_MARKERS:
                        args = [a for a in meta.arguments
                                if not hasattr(a, "name")]
                        field.apply(meta_path, args)

    if field.include and _is_class_variable(attribute):
        field.include = False
    return field


def _magic_parameters(class_: Class, kw_only_default: bool) -> list[Parameter]:
    parameters = []
    for member in class_.members.values():
        if not member.is_attribute:
            continue
        if member.annotation is None or "property" in member.labels:
            continue

        field = _analyze_field(member)
        if not field.include:
            continue

        if field.kind is not None:
            kind = field.kind
        elif kw_only_default:
            kind = ParameterKind.keyword_only
        else:
            kind = ParameterKind.positional_or_keyword

        parameters.append(
            Parameter(
                member.name,
                annotation=field.inner,
                kind=kind,
                default=field.default,
                docstring=member.docstring,
            )
        )
    return parameters


def _reorder(parameters: list[Parameter]) -> list[Parameter]:
    # De-duplicate (subclass fields overwrite inherited ones), then put
    # keyword-only parameters last so the signature is valid.
    unique = {p.name: p for p in parameters}
    kw_only = ParameterKind.keyword_only
    pos = [p for p in unique.values() if p.kind is not kw_only]
    kw = [p for p in unique.values() if p.kind is kw_only]
    return pos + kw


def _set_magic_init(class_: Class) -> None:
    options = _magic_options(class_)
    if options.get("init") is False:
        return

    # Collect parameters from every magic class in the MRO (parents first).
    parameters: list[Parameter] = []
    try:
        mro = list(class_.mro())
    except ValueError:
        mro = []
    for parent in reversed(mro):
        if _directly_magic(parent):
            parent_kw_only = _magic_options(parent).get("kw_only", False)
            parameters.extend(_magic_parameters(parent, parent_kw_only))
    parameters.extend(
        _magic_parameters(class_, options.get("kw_only", False))
    )

    self_param = Parameter(
        "self", annotation=None,
        kind=ParameterKind.positional_or_keyword, default=None,
    )
    init = Function(
        "__init__",
        lineno=0,
        endlineno=0,
        parent=class_,
        parameters=Parameters(self_param, *_reorder(parameters)),
        returns="None",
    )
    class_.set_member("__init__", init)
    class_.labels.add("magic")


def _apply(obj: Module | Class, seen: set[str]) -> None:
    if obj.canonical_path in seen:
        return
    seen.add(obj.canonical_path)
    if isinstance(obj, Class):
        if _is_magic(obj) and "__init__" not in obj.members:
            _set_magic_init(obj)
    for member in obj.members.values():
        if not member.is_alias and (member.is_module or member.is_class):
            _apply(member, seen)  # type: ignore[arg-type]


class MagicExtension(Extension):
    """Griffe extension that documents [`Magic`][bagof.magic.Magic] classes."""

    def on_package(self, *, pkg: Module, **kwargs: Any) -> None:  # noqa: ARG002
        """Reconstruct magic ``__init__`` methods across a loaded package."""
        _apply(pkg, set())
