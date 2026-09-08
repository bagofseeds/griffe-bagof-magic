"""Documenting a magic class from griffe's source tree alone.

The fallback for when the documented package cannot be imported (a
minimal docs-build environment). It reconstructs the constructor from the
annotations griffe read, detecting the per-field markers by their
canonical path. It cannot compute a ``like`` hint (that needs the live
converter), and it approximates a few things the dynamic path gets exact
-- aliases, positional-only fields, and ``Field(...)`` keywords buried in
``Annotated`` metadata -- so it is a graceful degradation, not a
replacement.
"""

from __future__ import annotations

from typing import Any

from griffe import (
    Attribute,
    Class,
    ExprCall,
    ExprSubscript,
    ExprTuple,
    Function,
    Parameter,
    ParameterKind,
    Parameters,
)

_MAGIC_BASE = "bagof.magic.Magic"
_MAGIC_DECORATOR = "bagof.magic.magic"

# Per-field markers, by the canonical path of the shorthand generic
# (``x: KwOnly[int]``); the ``Annotated[int, KwOnly()]`` form shares the
# same names, applied through `_analyse`.
_NO_INIT = {"bagof.magic.NoInit"}
_INIT = {"bagof.magic.Init"}
_KW_ONLY = {"bagof.magic.KwOnly"}
_NOT_KW_ONLY = {"bagof.magic.NotKwOnly", "bagof.magic.Positional"}
# Only a class variable is not a constructor parameter. An init-only
# ``Var`` (the analogue of `dataclasses.InitVar`) *is* one.
_CLASS_VAR = {"bagof.magic.ClassVar"}
_DEFAULT = {"bagof.magic.Default"}
_FACTORY = {"bagof.magic.Factory"}

_ALL_MARKERS = (
    _NO_INIT | _INIT | _KW_ONLY | _NOT_KW_ONLY | _CLASS_VAR
    | _DEFAULT | _FACTORY
)
_ANNOTATED = {"typing.Annotated", "typing_extensions.Annotated"}


def looks_magic(class_: Class) -> bool:
    """Whether the source makes ``class_`` look like a magic class."""
    if _directly_magic(class_):
        return True
    try:
        return any(_directly_magic(parent) for parent in class_.mro())
    except ValueError:
        return False


def _directly_magic(class_: Class) -> bool:
    for base in class_.bases:
        if getattr(base, "canonical_path", None) == _MAGIC_BASE:
            return True
    for decorator in class_.decorators:
        path = getattr(decorator.value, "canonical_path", None)
        if path == _MAGIC_DECORATOR:
            return True
    return False


def _literal(value: Any) -> Any:
    import ast

    try:
        return ast.literal_eval(str(value))
    except (ValueError, SyntaxError):
        return None


def _decorator_options(class_: Class) -> dict:
    for decorator in class_.decorators:
        value = decorator.value
        if getattr(value, "canonical_path", None) == _MAGIC_DECORATOR \
                and isinstance(value, ExprCall):
            options = {}
            for argument in value.arguments:
                name = getattr(argument, "name", None)
                if name is not None:
                    options[name] = _literal(argument.value)
            return options
    return {}


def _options(class_: Class) -> dict:
    options = {k: _literal(v) for k, v in (class_.keywords or {}).items()}
    options.update(_decorator_options(class_))
    return options


class _Field:
    __slots__ = ("inner", "include", "kind", "default")

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.include = True
        self.kind: ParameterKind | None = None
        self.default: Any = None

    def apply(self, marker: str, extras: list) -> None:
        if marker in _CLASS_VAR or marker in _NO_INIT:
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
    labels = attribute.labels
    return "class-attribute" in labels and "instance-attribute" not in labels


def _analyse(attribute: Attribute) -> _Field:
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
            field.inner = elements[0] if elements else annotation
            field.apply(path, elements[1:])
        elif path in _ANNOTATED and elements:
            field.inner = elements[0]
            for meta in elements[1:]:
                if isinstance(meta, ExprCall):
                    meta_path = getattr(meta.function, "canonical_path", None)
                    if meta_path in _ALL_MARKERS:
                        args = [
                            a for a in meta.arguments if not hasattr(a, "name")
                        ]
                        field.apply(meta_path, args)

    if field.include and _is_class_variable(attribute):
        field.include = False
    return field


def _parameters(class_: Class, kw_only_default: bool) -> list[Parameter]:
    parameters = []
    for member in class_.members.values():
        if not member.is_attribute:
            continue
        if member.annotation is None or "property" in member.labels:
            continue
        field = _analyse(member)
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
    # Subclass fields overwrite inherited ones; keyword-only parameters
    # come last so the signature is valid.
    unique = {p.name: p for p in parameters}
    kw_only = ParameterKind.keyword_only
    positional = [p for p in unique.values() if p.kind is not kw_only]
    keyword = [p for p in unique.values() if p.kind is kw_only]
    return positional + keyword


def document(class_: Class) -> bool:
    """Reconstruct the constructor from the source. ``True`` if it did."""
    options = _options(class_)
    if options.get("init") is False:
        return False

    parameters: list[Parameter] = []
    try:
        mro = list(class_.mro())
    except ValueError:
        mro = []
    for parent in reversed(mro):
        if _directly_magic(parent):
            parent_kw = _options(parent).get("kw_only", False)
            parameters.extend(_parameters(parent, parent_kw))
    parameters.extend(_parameters(class_, options.get("kw_only", False)))

    if not parameters:
        return False

    # No receiver parameter, matching the dynamic path: the fields are
    # what a reader cares about, and a field named `self` (which the real
    # constructor takes under an internal name) is then shown as itself.
    init = Function(
        "__init__",
        lineno=0,
        endlineno=0,
        parent=class_,
        parameters=Parameters(*_reorder(parameters)),
        returns="None",
    )
    init.labels.add("generated")
    class_.set_member("__init__", init)
    return True
