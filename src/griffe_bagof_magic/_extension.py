"""The extension: decide a class's path and document it once."""

from __future__ import annotations

import importlib
from typing import Any

import griffe

from . import _bagof, _static


class MagicExtension(griffe.Extension):
    """Document what a [`Magic`][bagof.magic.Magic] class writes for itself.

    For each magic class -- defined by inheritance (``class C(Magic)``) or
    by the ``@magic`` decorator -- it reconstructs the generated
    constructor, types the parameters by what they accept and the
    attributes by what they hold, lists the fields, and labels the other
    generated methods. When the documented package can be imported it
    reads the live class (exact); otherwise it falls back to the source.
    """

    def on_class(
        self,
        *,
        cls: griffe.Class,
        loader: griffe.GriffeLoader,
        **kwargs: Any,
    ) -> None:
        if "magic" in cls.labels:
            # Already documented: on_class can fire more than once, and a
            # second field table (or a re-read constructor) is wrong.
            return

        live = _live_object(cls, loader) if _bagof.AVAILABLE else None
        if live is not None:
            if not _bagof.is_magic(live):
                return
            from . import _dynamic

            if _dynamic.document(cls, live, loader):
                cls.labels.add("magic")
            return

        # No live object -- analyse the source instead, but only for a
        # class that actually looks magic there.
        if _static.looks_magic(cls) and "__init__" not in cls.members:
            if _static.document(cls):
                cls.labels.add("magic")


def _live_object(
    cls: griffe.Class, loader: griffe.GriffeLoader
) -> object | None:
    """The real object griffe read ``cls`` from, or ``None``.

    ``None`` when the module cannot be imported here -- a docs build
    where the package is not installed -- in which case the caller falls
    back to static analysis. The loader's search paths are made
    importable first, so a package griffe can find is one Python can too.
    """
    path = []
    obj: griffe.Object = cls
    while not obj.is_module and obj.parent is not None:
        if "[" in obj.name:
            # A generated parameterisation (`Box[int]`) is bound on the
            # module under a name that is not an importable attribute.
            return None
        path.append(obj.name)
        obj = obj.parent

    with _importable(loader):
        try:
            live: Any = importlib.import_module(obj.path)
        except Exception:
            # Any failure at import -- not only ImportError -- means the
            # live path is unavailable; degrade to static.
            return None
    for name in reversed(path):
        live = getattr(live, name, None)
        if live is None:
            return None
    return live


class _importable:
    """Temporarily put the loader's search paths on ``sys.path``.

    griffe finds a package by its own search paths, which are not
    necessarily on ``sys.path``; importing it needs them there.
    """

    def __init__(self, loader: griffe.GriffeLoader) -> None:
        finder = getattr(loader, "finder", None)
        self._paths = [str(p) for p in getattr(finder, "search_paths", [])]

    def __enter__(self) -> None:
        import sys

        self._added = [p for p in self._paths if p not in sys.path]
        sys.path[:0] = self._added

    def __exit__(self, *exc: object) -> None:
        import sys

        for path in self._added:
            try:
                sys.path.remove(path)
            except ValueError:  # pragma: no cover
                pass
