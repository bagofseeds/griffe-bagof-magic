"""Documenting a magic class from the live object griffe read it from.

This is the preferred path: importing the class gives the real compiled
``__init__`` (a genuine ``inspect.signature``), the resolved converters
(for ``like`` hints) and the full field table, none of which is in the
source for a static tool to find.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import griffe

from . import _render
from ._bagof import (
    MISSING,
    generated_names,
    has_factory,
    like_of,
    magic_fields,
)


def document(cls: griffe.Class, live: type, loader: Any) -> bool:
    """Fill in what a magic class writes for itself. ``True`` if it did.

    A magic class with no fields has nothing to add -- its constructor
    takes nothing and its table would be empty -- so it is left alone and
    this returns ``False``.
    """
    fields = magic_fields(live)
    if not fields:
        return False
    globalns, localns = _scopes(live)
    _rewrite_attributes(cls, fields)
    _add_methods(cls, live, fields, globalns, localns)
    _add_field_table(cls, fields)
    return True


def _scopes(live: type) -> tuple[dict, dict]:
    """The names a forward-referenced field type resolves against."""
    try:
        module = importlib.import_module(live.__module__)
        globalns = dict(getattr(module, "__dict__", {}))
    except Exception:  # pragma: no cover
        globalns = {}
    return globalns, {live.__name__: live}


# ----------------------------------------------------------------------
# Attributes: the declared type the instance holds
# ----------------------------------------------------------------------


def _rewrite_attributes(cls: griffe.Class, fields: dict[str, Any]) -> None:
    for field in fields.values():
        member = cls.members.get(field.name)
        if member is None or not member.is_attribute:
            continue
        if "property" in member.labels:
            continue
        if field.var and field.init:
            # An init-only field is a constructor parameter, never stored
            # as an attribute -- so it has no attribute to document.
            cls.del_member(field.name)
            continue
        member.annotation = _render.expression(
            _render.declared_type(field), cls
        )


# ----------------------------------------------------------------------
# Methods: the generated constructor and the rest
# ----------------------------------------------------------------------


def _add_methods(
    cls: griffe.Class,
    live: type,
    fields: dict[str, Any],
    globalns: dict,
    localns: dict,
) -> None:
    by_param = {field.public_name: field for field in fields.values()}
    for name, option in generated_names(live).items():
        method = getattr(live, name, None)
        if not inspect.isroutine(method):
            # Not everything an option writes is a method: a class that
            # compares by identity gets `__hash__ = None`, and
            # `match_args` binds a tuple.
            continue
        if name in cls.members:
            # Read from the source, so it is hand-written, and a
            # hand-written method always wins over the generated one.
            continue
        if name == "__init__":
            if option == "init" and _is_neutralised(method):
                # `init=False` leaves a `(*args, **kwargs)` passthrough,
                # which is not a constructor worth showing.
                continue
            function = _make_init(cls, method, by_param, globalns, localns)
        else:
            function = _make_stub(cls, name, method, option)
        cls.set_member(name, function)


def _is_neutralised(method: object) -> bool:
    try:
        parameters = list(inspect.signature(method).parameters.values())
    except (TypeError, ValueError):  # pragma: no cover
        return False
    rest = parameters[1:]
    return (
        len(rest) == 2
        and rest[0].kind is inspect.Parameter.VAR_POSITIONAL
        and rest[1].kind is inspect.Parameter.VAR_KEYWORD
    )


def _make_init(
    cls: griffe.Class,
    method: object,
    by_param: dict[str, Any],
    globalns: dict,
    localns: dict,
) -> griffe.Function:
    parameters = []
    signature = inspect.signature(method)
    for index, parameter in enumerate(signature.parameters.values()):
        if index == 0:
            # The receiver, skipped by position: a field named `self`
            # would collide with it by name.
            continue
        field = by_param.get(parameter.name)
        annotation = None
        if field is not None:
            like = like_of(field, globalns, localns)
            annotation = _render.expression(
                _render.accepted_type(field.type, like), cls
            )
        parameters.append(
            griffe.Parameter(
                parameter.name,
                annotation=annotation,
                kind=griffe.ParameterKind(parameter.kind.description),
                default=_default_of(parameter),
            )
        )
    function = griffe.Function(
        "__init__",
        parameters=griffe.Parameters(*parameters),
        returns="None",
        docstring=_init_docstring(cls, signature, by_param),
        parent=cls,
    )
    function.labels.add("generated")
    return function


def _default_of(parameter: inspect.Parameter) -> str | None:
    if parameter.default is inspect.Parameter.empty:
        return None
    return _render.default_repr(parameter.default)


def _init_docstring(
    cls: griffe.Class,
    signature: inspect.Signature,
    by_param: dict[str, Any],
) -> griffe.Docstring | None:
    """A `Parameters` section carrying each field's own documentation.

    The types are deliberately left off: the signature already annotates
    each parameter with its `like` type, and a type here would override
    it in the rendered parameter table.
    """
    lines = []
    for index, parameter in enumerate(signature.parameters.values()):
        if index == 0:
            continue
        field = by_param.get(parameter.name)
        text = " ".join((getattr(field, "doc", "") or "").split())
        if text:
            lines.append(parameter.name)
            lines.append("    " + text)
    if not lines:
        return None
    body = "Parameters\n----------\n" + "\n".join(lines)
    return _docstring(body, cls)


def _make_stub(
    cls: griffe.Class, name: str, method: object, option: str
) -> griffe.Function:
    from ._bagof import GENERATED_DOC

    own = inspect.cleandoc(getattr(method, "__doc__", "") or "")
    doc = own or GENERATED_DOC.get(option, "")
    function = griffe.Function(
        name,
        parameters=_plain_parameters(method),
        docstring=_docstring(doc, cls) if doc else None,
        parent=cls,
    )
    function.labels.add("generated")
    return function


def _plain_parameters(method: object) -> griffe.Parameters:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):  # pragma: no cover
        return griffe.Parameters()
    parameters = []
    for index, parameter in enumerate(signature.parameters.values()):
        if index == 0:
            continue
        parameters.append(
            griffe.Parameter(
                parameter.name,
                kind=griffe.ParameterKind(parameter.kind.description),
                default=_default_of(parameter),
            )
        )
    return griffe.Parameters(*parameters)


# ----------------------------------------------------------------------
# The field table
# ----------------------------------------------------------------------


def _default_cell(field: Any) -> str:
    if field.build:
        return f"`{has_factory(field)!r}`"
    if field.default is MISSING:
        return "*required*"
    return f"`{_render.default_repr(field.default)}`"


def _description_cell(field: Any) -> str:
    # A field that is a constructor parameter is documented in the
    # parameter table instead, so only a non-parameter counts here.
    if field.init:
        return ""
    return " ".join((getattr(field, "doc", "") or "").split())


def _escape(text: str) -> str:
    # A `|` (from an optional/union type) would split the markdown cell.
    return text.replace("|", "\\|")


def _field_table(fields: dict[str, Any]) -> str:
    columns = [
        ("Field", lambda f: f"`{f.public_name}`"),
        ("Type", lambda f: _escape(f"`{_render.declared_type(f)}`")),
        ("Default", lambda f: _escape(_default_cell(f))),
        ("Notes", _render.notes),
        ("Description", _description_cell),
    ]
    # The last two columns are drawn only when some field has something
    # to put in them.
    columns = [
        (title, cell)
        for title, cell in columns
        if title in ("Field", "Type", "Default")
        or any(cell(field) for field in fields.values())
    ]
    rows = [[title for title, _ in columns], ["---"] * len(columns)]
    rows += [
        [cell(field) for _, cell in columns] for field in fields.values()
    ]
    lines = ["| " + " | ".join(row) + " |" for row in rows]
    return "**Fields**\n\n" + "\n".join(lines)


def _add_field_table(cls: griffe.Class, fields: dict[str, Any]) -> None:
    table = _field_table(fields)
    if cls.docstring is None:
        cls.docstring = _docstring(table, cls)
    elif "**Fields**" not in cls.docstring.value:
        cls.docstring.value = cls.docstring.value.rstrip() + "\n\n" + table


# ----------------------------------------------------------------------
# Shared
# ----------------------------------------------------------------------


def _docstring(text: str, parent: griffe.Object) -> griffe.Docstring:
    """A docstring read the same way as the ones griffe read from source.

    The parser is a setting of the site, recorded on everything griffe
    reads; a docstring made here takes it from the nearest object that
    has one.
    """
    obj: griffe.Object | None = parent
    while obj is not None:
        if obj.docstring is not None:
            return griffe.Docstring(
                text,
                parent=parent,
                parser=obj.docstring.parser,
                parser_options=obj.docstring.parser_options,
            )
        obj = obj.parent
    return griffe.Docstring(text, parent=parent)
