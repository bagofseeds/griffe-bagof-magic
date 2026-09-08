"""
Griffe extension adding support for [`bagof.magic.Magic`][] classes.

Like griffe's built-in dataclasses extension (and the community attrs /
pydantic ones), this reconstructs the ``__init__`` method that
[`Magic`][bagof.magic.Magic] generates at runtime, so documentation tools
render the real constructor signature and per-field docs -- for classes
defined by inheritance (``class C(Magic): ...``) *and* by the ``@magic``
decorator.

Beyond the signature, when the documented package can be imported it types
each constructor parameter by what the field *accepts* (a converter's
``like`` hint, unioned with the declared type) while typing the resulting
attribute by what it *holds* (the declared type), fills in defaults, lists
the fields, and labels the other generated methods.
"""

from __future__ import annotations

__all__ = ["MagicExtension", "__version__"]

from ._extension import MagicExtension

try:
    from ._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "0+unknown"
