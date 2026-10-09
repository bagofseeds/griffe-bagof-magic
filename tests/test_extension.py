"""Tests for the bagof-magic griffe extension.

The dynamic path (the live class is importable) is what runs here and in
CI, since the ``test`` extra installs ``bagof-magic``. The static
fallback is exercised by forcing ``_bagof.AVAILABLE`` off.
"""

from __future__ import annotations

import itertools
import sys
import tempfile
from pathlib import Path

import griffe
import pytest

from griffe_bagof_magic import MagicExtension, _bagof

# The live path needs bagof-magic importable; skip everything otherwise.
pytest.importorskip("bagof.magic")

_counter = itertools.count()


def load(
    source: str, parser: griffe.Parser | None = None,
) -> griffe.Module:
    """Load a one-module package built from ``source``.

    Each call gets a fresh module name, so the live path imports the
    module under test and not one a previous test left in ``sys.modules``.
    """
    name = f"sample_{next(_counter)}"
    tmp = Path(tempfile.mkdtemp())
    pkg = tmp / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(source)
    search = [str(tmp)] + [p for p in sys.path if isinstance(p, str) and p]
    return griffe.load(
        name,
        docstring_parser=parser,
        search_paths=search,
        extensions=griffe.load_extensions(MagicExtension()),
    )


def params(cls: griffe.Class) -> list:
    """``(name, str(annotation), str(default), kind)`` per init parameter."""
    init = cls.members.get("__init__")
    if init is None:
        return None
    return [
        (
            p.name,
            None if p.annotation is None else str(p.annotation),
            None if p.default is None else str(p.default),
            p.kind.value,
        )
        for p in init.parameters
    ]


def names(cls: griffe.Class) -> list:
    got = params(cls)
    return None if got is None else [name for name, *_ in got]


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------


def test_inheritance_is_detected() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class Point(Magic):\n"
        "    x: float\n"
        "    y: float\n"
    )
    assert "magic" in mod["Point"].labels
    assert names(mod["Point"]) == ["x", "y"]


def test_decorator_is_detected() -> None:
    mod = load(
        "from bagof.magic import magic\n"
        "@magic\n"
        "class Point:\n"
        "    x: float\n"
    )
    assert "magic" in mod["Point"].labels
    assert names(mod["Point"]) == ["x"]


def test_subclass_inherits_fields() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class Base(Magic):\n"
        "    x: int\n"
        "class Kid(Base):\n"
        "    y: int\n"
    )
    assert names(mod["Kid"]) == ["x", "y"]


def test_non_magic_class_is_untouched() -> None:
    mod = load("class Plain:\n    x: int\n")
    assert "magic" not in mod["Plain"].labels
    assert names(mod["Plain"]) is None


def test_hand_written_init_is_left_alone() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class Custom(Magic):\n"
        "    x: int\n"
        "    def __init__(self, x, extra=1):\n"
        "        ...\n"
    )
    # Read verbatim from the source, receiver and all -- left untouched.
    assert names(mod["Custom"]) == ["self", "x", "extra"]
    assert "generated" not in mod["Custom"]["__init__"].labels


def test_fieldless_magic_is_untouched() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        'class Empty(Magic):\n    """Nothing."""\n'
    )
    assert names(mod["Empty"]) is None
    assert "**Fields**" not in mod["Empty"].docstring.value


# ----------------------------------------------------------------------
# The like / type split
# ----------------------------------------------------------------------


def test_parameter_uses_like_attribute_uses_declared() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic, convert=True):\n"
        "    s: str\n"
    )
    # A converting str field accepts more than it stores.
    (name, annotation, _, _), = params(mod["C"])
    assert name == "s"
    assert annotation == "str | bytes"
    assert str(mod["C"].members["s"].annotation) == "str"


def test_like_for_datetime() -> None:
    mod = load(
        "import datetime\n"
        "from bagof.magic import Magic\n"
        "class C(Magic, convert=True):\n"
        "    when: datetime.datetime\n"
    )
    annotation = params(mod["C"])[0][1]
    assert annotation.startswith("datetime")
    for part in ("str", "int", "float"):
        assert part in annotation
    assert str(mod["C"].members["when"].annotation) == "datetime.datetime"


def test_optional_is_preserved_on_the_attribute() -> None:
    mod = load(
        "import typing_extensions as tx\n"
        "from bagof.magic import Magic\n"
        "class C(Magic):\n"
        "    note: tx.Optional[str] = None\n"
    )
    assert str(mod["C"].members["note"].annotation) == "str | None"


def test_non_converting_field_keeps_declared_on_parameter() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic):\n"
        "    n: int\n"
    )
    assert params(mod["C"])[0][1] == "int"


# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------


def test_defaults_value_factory_and_required() -> None:
    mod = load(
        "from bagof.magic import Magic, Factory\n"
        "class C(Magic):\n"
        "    required: int\n"
        "    value: int = 5\n"
        "    made: Factory[list]\n"
    )
    by_name = {name: default for name, _, default, _ in params(mod["C"])}
    assert by_name["required"] is None          # no default
    assert by_name["value"] == "5"
    assert by_name["made"] == "<factory>"


# ----------------------------------------------------------------------
# Field kinds and pseudo-fields
# ----------------------------------------------------------------------


def test_keyword_only_is_ordered_last() -> None:
    mod = load(
        "from bagof.magic import Magic, KwOnly\n"
        "class C(Magic):\n"
        "    a: int\n"
        "    b: KwOnly[int] = 0\n"
    )
    got = params(mod["C"])
    assert got[0][0] == "a" and got[0][3] == "positional or keyword"
    assert got[1][0] == "b" and got[1][3] == "keyword-only"


def test_class_var_excluded_but_kept_as_attribute() -> None:
    mod = load(
        "from bagof.magic import Magic, ClassVar\n"
        "class C(Magic):\n"
        "    x: int\n"
        "    kind: ClassVar[str] = 'k'\n"
    )
    assert names(mod["C"]) == ["x"]
    assert str(mod["C"].members["kind"].annotation) == "str"


def test_init_var_is_a_parameter_and_not_an_attribute() -> None:
    mod = load(
        "from bagof.magic import Magic, Var\n"
        "class C(Magic):\n"
        "    x: int\n"
        "    scratch: Var[int] = 0\n"
    )
    assert names(mod["C"]) == ["x", "scratch"]
    # An init-only field is never stored, so it has no attribute.
    assert "scratch" not in mod["C"].members


def test_init_false_suppresses_the_constructor() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic, init=False):\n"
        "    x: int\n"
    )
    assert "__init__" not in mod["C"].members


def test_a_field_named_self_does_not_leak_the_receiver() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic):\n"
        "    self: int\n"
        "    other: int = 0\n"
    )
    assert names(mod["C"]) == ["self", "other"]
    got = {p.name for p in mod["C"]["__init__"].parameters}
    assert "__magic_self__" not in got


def test_alias_names_the_parameter_and_the_attribute() -> None:
    mod = load(
        "import typing_extensions as tx\n"
        "from bagof.magic import Magic, Field\n"
        "class C(Magic):\n"
        "    name: tx.Annotated[str, Field(alias='title')]\n"
    )
    assert names(mod["C"]) == ["title"]        # the parameter is the alias
    assert "name" in mod["C"].members          # the attribute is the field


# ----------------------------------------------------------------------
# The field table and generated methods
# ----------------------------------------------------------------------


def test_field_table_is_appended() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        'class C(Magic):\n    """A thing."""\n    x: int\n    y: int = 0\n'
    )
    doc = mod["C"].docstring.value
    assert "A thing." in doc
    assert "| Field | Type | Default |" in doc
    assert "| `x` | `int` | *required* |" in doc
    assert "| `y` | `int` | `0` |" in doc


def test_field_table_escapes_a_union_pipe() -> None:
    mod = load(
        "import typing_extensions as tx\n"
        "from bagof.magic import Magic\n"
        'class C(Magic):\n    """T."""\n    note: tx.Optional[str] = None\n'
    )
    assert r"`str \| None`" in mod["C"].docstring.value


def test_generated_methods_are_labelled() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic):\n    x: int\n"
    )
    for name in ("__eq__", "__repr__"):
        assert "generated" in mod["C"].members[name].labels


def test_hand_written_method_is_not_labelled() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic):\n"
        "    x: int\n"
        "    def __repr__(self):\n"
        '        """Mine."""\n'
        "        return 'c'\n"
    )
    written = mod["C"].members["__repr__"]
    assert "generated" not in written.labels
    assert written.docstring.value == "Mine."


def test_mapping_methods_are_labelled() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic, mapping=True):\n    x: int\n"
    )
    for name in ("__getitem__", "__iter__", "__len__"):
        assert "generated" in mod["C"].members[name].labels


def test_the_real_magic_base_is_left_alone() -> None:
    mod = griffe.load(
        "bagof.magic",
        search_paths=[p for p in sys.path if isinstance(p, str) and p],
        extensions=griffe.load_extensions(MagicExtension()),
    )
    assert "__init__" not in mod["Magic"].members
    assert "magic" not in mod["Field"].labels


# ----------------------------------------------------------------------
# The static fallback (forced by turning the live path off)
# ----------------------------------------------------------------------


@pytest.fixture
def static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_bagof, "AVAILABLE", False)


def test_static_reconstructs_from_the_source(static: None) -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class Point(Magic):\n    x: float\n    y: float\n"
    )
    assert "magic" in mod["Point"].labels
    assert names(mod["Point"]) == ["x", "y"]


def test_static_keyword_only(static: None) -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class P(Magic, kw_only=True):\n    x: int\n"
    )
    assert params(mod["P"])[0][3] == "keyword-only"


def test_static_excludes_class_var_keeps_init_var(static: None) -> None:
    mod = load(
        "from bagof.magic import Magic, ClassVar, Var\n"
        "class P(Magic):\n"
        "    x: int\n"
        "    kind: ClassVar[str] = 'k'\n"
        "    scratch: Var[int] = 0\n"
    )
    # ClassVar is not a parameter; an init-only Var is.
    assert names(mod["P"]) == ["x", "scratch"]


def test_static_default_marker(static: None) -> None:
    mod = load(
        "from bagof.magic import Magic, Default\n"
        "class P(Magic):\n    a: Default[int, 5]\n"
    )
    a = next(p for p in mod["P"]["__init__"].parameters if p.name == "a")
    assert str(a.annotation) == "int"
    assert str(a.default) == "5"


def test_static_annotated_marker(static: None) -> None:
    mod = load(
        "import typing_extensions as tx\n"
        "from bagof.magic import Magic, KwOnly\n"
        "class P(Magic):\n"
        "    x: int\n"
        "    y: tx.Annotated[int, KwOnly()]\n"
    )
    y = next(p for p in mod["P"]["__init__"].parameters if p.name == "y")
    assert str(y.annotation) == "int"
    assert y.kind.value == "keyword-only"


@pytest.mark.parametrize("parser", [None, *griffe.Parser])
@pytest.mark.parametrize("fallback", [False, True])
def test_public_parameter_names_and_parsed_table(
    monkeypatch: pytest.MonkeyPatch, fallback: bool,
    parser: griffe.Parser | None,
) -> None:
    if fallback:
        monkeypatch.setattr(_bagof, "AVAILABLE", False)
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic):\n"
        "    _header: str = 'hello'\n"
        '    """Header text."""\n'
        "    count: int = 2\n",
        parser=parser,
    )
    cls = mod["C"]
    assert names(cls) == ["header", "count"]
    init = cls["__init__"]
    assert init.docstring.parent is init
    sections = init.docstring.parsed
    section = next(s for s in sections if s.kind.value == "parameters")
    assert [p.name for p in section.value] == ["header", "count"]
    assert section.value[0].description == "Header text."
    assert [str(p.annotation) for p in section.value] == ["str", "int"]
    assert [str(p.value) for p in section.value] == ["'hello'", "2"]


def test_preferred_alias_has_type_and_documentation() -> None:
    mod = load(
        "from typing import Annotated\n"
        "from bagof.magic import Magic, Field\n"
        "class C(Magic):\n"
        "    _header: Annotated[str, Field(\n"
        "        alias=('header', 'heading'), doc='Header text.')]\n"
    )
    init = mod["C"]["__init__"]
    assert names(mod["C"]) == ["header"]
    assert all(str(p.annotation) == "str" for p in init.parameters)
    section = next(s for s in init.docstring.parsed
                   if s.kind.value == "parameters")
    assert all(p.description == "Header text." for p in section.value)


def test_static_alias_false_preserves_underscores(static: None) -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class C(Magic, alias=False):\n    _header: str\n"
    )
    assert names(mod["C"]) == ["_header"]


@pytest.mark.parametrize("hint", [
    "ClassVar[str]",
    "Annotated[ClassVar[str], 'metadata']",
    "Annotated[str, MagicClassVar()]",
    "Annotated[str, Field(init=False)]",
    "Annotated[str, Field(var=True, init=False)]",
])
@pytest.mark.parametrize("fallback", [False, True])
def test_excluded_fields_match_runtime(
    monkeypatch: pytest.MonkeyPatch, hint: str, fallback: bool,
) -> None:
    if fallback:
        monkeypatch.setattr(_bagof, "AVAILABLE", False)
    mod = load(
        "from typing import Annotated, ClassVar\n"
        "from bagof.magic import Magic, Field, ClassVar as MagicClassVar\n"
        "class C(Magic):\n"
        f"    kind: {hint} = 'thing'\n"
        "    x: int\n"
    )
    assert names(mod["C"]) == ["x"]
    if "ClassVar" in hint or "var=True" in hint:
        assert "instance-attribute" not in mod["C"]["kind"].labels
        assert "class-attribute" in mod["C"]["kind"].labels


@pytest.mark.parametrize("fallback", [False, True])
def test_subclass_classvar_removes_inherited_parameter(
    monkeypatch: pytest.MonkeyPatch, fallback: bool,
) -> None:
    if fallback:
        monkeypatch.setattr(_bagof, "AVAILABLE", False)
    mod = load(
        "from typing import ClassVar\n"
        "from bagof.magic import Magic\n"
        "class Base(Magic):\n    kind: str\n    x: int\n"
        "class Middle(Base):\n    y: int\n"
        "class Child(Middle):\n    kind: ClassVar[str] = 'thing'\n"
    )
    assert names(mod["Child"]) == ["x", "y"]


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("declaration", [
    "Alias = ClassVar[str]",
    "Alias: TypeAlias = ClassVar[str]",
    "Hidden = ClassVar[str]\nAlias = Hidden",
    "Alias = Annotated[str, MagicClassVar()]",
])
def test_classvar_inside_type_alias(
    monkeypatch: pytest.MonkeyPatch, fallback: bool, declaration: str,
) -> None:
    if fallback:
        monkeypatch.setattr(_bagof, "AVAILABLE", False)
    mod = load(
        "from typing import Annotated, ClassVar, TypeAlias\n"
        "from bagof.magic import Magic, ClassVar as MagicClassVar\n"
        f"{declaration}\n"
        "class C(Magic):\n    kind: Alias = 'thing'\n    x: int\n"
    )
    assert names(mod["C"]) == ["x"]
    assert "instance-attribute" not in mod["C"]["kind"].labels


def test_static_recursive_alias_terminates(static: None) -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "A = B\nB = A\n"
        "class C(Magic):\n    x: A\n"
    )
    assert names(mod["C"]) == ["x"]


def test_static_imported_classvar_alias(
    static: None, tmp_path: Path,
) -> None:
    package = tmp_path / f"alias_sample_{next(_counter)}"
    package.mkdir()
    (package / "hints.py").write_text(
        "from typing_extensions import ClassVar, Tuple\n"
        "FieldNames = ClassVar[Tuple[str, ...]]\n"
    )
    (package / "__init__.py").write_text(
        "from bagof.magic import Magic\n"
        "from .hints import FieldNames\n"
        "class C(Magic):\n"
        "    data_fields: FieldNames = ()\n"
        "    x: int\n"
    )
    mod = griffe.load(
        package.name, search_paths=[tmp_path],
        extensions=griffe.load_extensions(MagicExtension()),
    )
    assert names(mod["C"]) == ["x"]
    assert "instance-attribute" not in mod["C"]["data_fields"].labels
