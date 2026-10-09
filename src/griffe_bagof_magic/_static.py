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
    Docstring,
    ExprCall,
    ExprSubscript,
    ExprTuple,
    Function,
    Parameter,
    ParameterKind,
    Parameters,
    Parser,
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
_CLASS_VAR = {
    "bagof.magic.ClassVar", "typing.ClassVar", "typing_extensions.ClassVar",
}
_VAR = {"bagof.magic.Var", "bagof.magic.InitVar"}
_DEFAULT = {"bagof.magic.Default"}
_FACTORY = {"bagof.magic.Factory"}

_ALL_MARKERS = (
    _NO_INIT | _INIT | _KW_ONLY | _NOT_KW_ONLY | _CLASS_VAR
    | _VAR | _DEFAULT | _FACTORY
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
    __slots__ = ("inner", "include", "kind", "default", "var")

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.include = True
        self.var = False
        self.kind: ParameterKind | None = None
        self.default: Any = None

    def apply(self, marker: str, extras: list) -> None:
        if marker in _CLASS_VAR or marker in _VAR:
            self.var = True
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

    def analyse(hint: Any) -> None:
        if not isinstance(hint, ExprSubscript):
            return
        path = getattr(hint.left, "canonical_path", None)
        slice_ = hint.slice
        elements = (
            list(slice_.elements)
            if isinstance(slice_, ExprTuple)
            else [slice_]
        )
        if path in _ALL_MARKERS:
            field.inner = elements[0] if elements else hint
            analyse(field.inner)
            field.apply(path, elements[1:])
        elif path in _ANNOTATED and elements:
            field.inner = elements[0]
            analyse(field.inner)
            for meta in elements[1:]:
                if isinstance(meta, ExprCall):
                    meta_path = getattr(meta.function, "canonical_path", None)
                    if meta_path in _ALL_MARKERS:
                        args = [
                            a for a in meta.arguments if not hasattr(a, "name")
                        ]
                        field.apply(meta_path, args)
                    elif meta_path == "bagof.magic.Field":
                        for argument in meta.arguments:
                            name = getattr(argument, "name", None)
                            value = _literal(getattr(argument, "value", None))
                            if name == "var" and isinstance(value, bool):
                                field.var = value
                            if name == "init" and isinstance(value, bool):
                                field.include = value
                            elif name == "kw_only" and isinstance(value, bool):
                                field.kind = (
                                    ParameterKind.keyword_only if value
                                    else ParameterKind.positional_or_keyword
                                )

    analyse(annotation)

    if field.include and _is_class_variable(attribute):
        field.include = False
    if field.var and not field.include:
        attribute.labels.discard("instance-attribute")
        attribute.labels.add("class-attribute")
    return field


def _parameters(
    class_: Class, kw_only_default: bool,
    members: dict[str, tuple[Attribute, bool, bool]] | None = None,
) -> list[Parameter]:
    parameters = []
    if members is None:
        members = {
            member.name: (member, kw_only_default,
                          _options(class_).get("alias") is False)
            for member in class_.members.values()
            if isinstance(member, Attribute)
        }
    for member, kw_only_default, private in members.values():
        if not isinstance(member, Attribute):
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
                (member.name if private
                 else member.name.lstrip("_")),
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
    # Merge by stored field name before filtering. A subclass can replace
    # an inherited constructor field with a ClassVar or NoInit field.
    members: dict[str, tuple[Attribute, bool, bool]] = {}
    for owner in [*reversed(mro), class_]:
        if owner is not class_ and not looks_magic(owner):
            continue
        owner_options = _options(owner)
        for member in owner.members.values():
            if isinstance(member, Attribute) and member.annotation is not None:
                members[member.name] = (
                    member, owner_options.get("kw_only", False),
                    owner_options.get("alias") is False,
                )
    parameters = _parameters(class_, False, members)

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
    lines = []
    for parameter in init.parameters:
        lines.append(parameter.name)
        if parameter.docstring is not None:
            text = " ".join(parameter.docstring.value.split())
            if text:
                lines.append("    " + text)
    init.docstring = Docstring(
        "Parameters\n----------\n" + "\n".join(lines),
        parent=init,
        parser=Parser.numpy,
    )
    init.labels.add("generated")
    class_.set_member("__init__", init)
    return True
