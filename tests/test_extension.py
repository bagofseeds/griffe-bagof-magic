"""Tests for the bagof-magic griffe extension."""

import tempfile
from pathlib import Path

import griffe
import pytest

from griffe_bagof_magic import MagicExtension

# bagof-magic must be importable for its canonical paths to resolve.
pytest.importorskip("bagof.magic")


def load(source: str) -> griffe.Module:
    """Load a one-module package containing ``source`` with the extension."""
    tmp = Path(tempfile.mkdtemp())
    pkg = tmp / "sample"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(source)
    search = [str(tmp), "/workspaces/bagof-magic/src"]
    return griffe.load(
        "sample",
        search_paths=search,
        extensions=griffe.load_extensions(MagicExtension()),
    )


def init_params(cls: griffe.Class) -> list:
    """The parameter names of a synthesized ``__init__`` (excluding self)."""
    init = cls.members.get("__init__")
    if init is None:
        return None
    return [p.name for p in init.parameters if p.name != "self"]


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
    assert init_params(mod["Point"]) == ["x", "y"]


def test_decorator_is_detected() -> None:
    mod = load(
        "from bagof.magic import magic\n"
        "@magic\n"
        "class Point:\n"
        "    x: float\n"
    )
    assert "magic" in mod["Point"].labels
    assert init_params(mod["Point"]) == ["x"]


def test_subclass_inherits_fields() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class Base(Magic):\n"
        "    x: int\n"
        "class Kid(Base):\n"
        "    y: int\n"
    )
    assert "magic" in mod["Kid"].labels
    assert init_params(mod["Kid"]) == ["x", "y"]


def test_non_magic_class_is_untouched() -> None:
    mod = load(
        "class Plain:\n"
        "    x: int\n"
    )
    assert "magic" not in mod["Plain"].labels
    assert init_params(mod["Plain"]) is None


def test_existing_init_is_not_overwritten() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class Custom(Magic):\n"
        "    x: int\n"
        "    def __init__(self, x, extra=1):\n"
        "        ...\n"
    )
    assert init_params(mod["Custom"]) == ["x", "extra"]


# ----------------------------------------------------------------------
# Class-level options
# ----------------------------------------------------------------------


def test_kw_only_inheritance_keyword() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class P(Magic, kw_only=True):\n"
        "    x: int\n"
    )
    kinds = [p.kind.value for p in mod["P"]["__init__"].parameters
             if p.name != "self"]
    assert kinds == ["keyword-only"]


def test_kw_only_decorator_argument() -> None:
    mod = load(
        "from bagof.magic import magic\n"
        "@magic(kw_only=True)\n"
        "class P:\n"
        "    x: int\n"
    )
    kinds = [p.kind.value for p in mod["P"]["__init__"].parameters
             if p.name != "self"]
    assert kinds == ["keyword-only"]


def test_init_false_suppresses_init() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class P(Magic, init=False):\n"
        "    x: int\n"
    )
    assert init_params(mod["P"]) is None


# ----------------------------------------------------------------------
# Per-field markers
# ----------------------------------------------------------------------


def test_class_var_is_excluded() -> None:
    mod = load(
        "from bagof.magic import Magic, ClassVar\n"
        "class P(Magic):\n"
        "    x: int\n"
        "    kind: ClassVar[str] = 'k'\n"
    )
    assert init_params(mod["P"]) == ["x"]


def test_no_init_marker_is_excluded() -> None:
    mod = load(
        "from bagof.magic import Magic, NoInit\n"
        "class P(Magic):\n"
        "    x: int\n"
        "    y: NoInit[int] = 3\n"
    )
    assert init_params(mod["P"]) == ["x"]


def test_kw_only_marker_on_field() -> None:
    mod = load(
        "from bagof.magic import Magic, KwOnly\n"
        "class P(Magic):\n"
        "    x: int\n"
        "    y: KwOnly[int]\n"
    )
    params = {p.name: p.kind.value for p in mod["P"]["__init__"].parameters}
    assert params["x"] == "positional or keyword"
    assert params["y"] == "keyword-only"


def test_kw_only_field_unwraps_to_inner_type() -> None:
    mod = load(
        "from bagof.magic import Magic, KwOnly\n"
        "class P(Magic):\n"
        "    y: KwOnly[int]\n"
    )
    y = next(p for p in mod["P"]["__init__"].parameters if p.name == "y")
    assert str(y.annotation) == "int"


def test_default_value_is_preserved() -> None:
    mod = load(
        "from bagof.magic import Magic\n"
        "class P(Magic):\n"
        "    a: int\n"
        "    b: int = 5\n"
    )
    b = next(p for p in mod["P"]["__init__"].parameters if p.name == "b")
    assert str(b.default) == "5"


# ----------------------------------------------------------------------
# Robustness
# ----------------------------------------------------------------------


def test_loads_the_real_bagof_magic_package() -> None:
    mod = griffe.load(
        "bagof.magic",
        search_paths=["/workspaces/bagof-magic/src"],
        extensions=griffe.load_extensions(MagicExtension()),
    )
    # The base class has no fields, so it must not gain a spurious __init__.
    assert "__init__" not in mod["Magic"].members
    # Field is a slots class, not a Magic, and must be left alone.
    assert "magic" not in mod["Field"].labels


def test_default_marker_extracts_type_and_value() -> None:
    mod = load(
        "from bagof.magic import Magic, Default\n"
        "class P(Magic):\n"
        "    a: Default[int, 5]\n"
    )
    a = next(p for p in mod["P"]["__init__"].parameters if p.name == "a")
    assert str(a.annotation) == "int"
    assert str(a.default) == "5"


def test_annotated_marker_form() -> None:
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


def test_annotated_no_init_is_excluded() -> None:
    mod = load(
        "import typing_extensions as tx\n"
        "from bagof.magic import Magic, NoInit\n"
        "class P(Magic):\n"
        "    x: int\n"
        "    y: tx.Annotated[int, NoInit()]\n"
    )
    assert init_params(mod["P"]) == ["x"]


def test_var_marker_is_excluded() -> None:
    mod = load(
        "from bagof.magic import Magic, Var\n"
        "class P(Magic):\n"
        "    x: int\n"
        "    y: Var[int]\n"
    )
    assert init_params(mod["P"]) == ["x"]
