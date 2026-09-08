# griffe-bagof-magic

A [Griffe](https://mkdocstrings.github.io/griffe/) extension that teaches
documentation tools about
[`bagof.magic.Magic`](https://github.com/bagofseeds/bagof-magic) classes --
the way griffe's built-in extension handles `dataclasses`, and the community
ones handle `attrs` / `pydantic`.

`Magic` writes its `__init__` (and its other methods) while the class is being
created, so a static tool like griffe sees an empty class body: the API
reference for a `Magic` subclass would show no constructor, no parameters and
no fields. This extension fills that in, for classes defined **by
inheritance** *and* **by decorator**:

```python
from bagof.magic import Magic, magic

class Point(Magic, frozen=True):   # detected via the base class
    x: float
    y: float

@magic(kw_only=True)               # detected via the decorator
class Named:
    name: str
```

## What it adds

For each magic class it reconstructs the generated constructor and, when the
documented package can be imported, reads the live class so the documentation
matches what the class actually does:

- **Parameters typed by what they accept, attributes by what they hold.** A
  converting field accepts more than it stores -- a `datetime` field built
  with `convert=True` accepts a `str`, an `int` or a `float` as well. The
  constructor parameter shows that accepted type (the converter's `like`
  hint, unioned with the declared type); the attribute shows the declared
  type it ends up holding.
- **Defaults**, including `<factory>` for a field whose default is built.
- **A field table** -- name, type, default, and whether each field is
  keyword-only, frozen, converted or validated.
- **The generated methods** (`__eq__`, `__repr__`, the ordering dunders, the
  dict-like interface, ...) each labelled `generated`, so a page can show or
  hide them as a group.

A hand-written `__init__` (or any hand-written method) always wins and is left
exactly as the source has it.

### Static fallback

When the documented package **cannot** be imported (a minimal docs-build
environment), the extension falls back to analysing the source alone. It still
reconstructs the constructor from the annotations -- detecting the per-field
markers (`KwOnly`, `ClassVar`, `Var`, `Default`, `Factory`, ...) -- but it
cannot compute a `like` hint (that needs the live converter) and it
approximates a few things the live path gets exact, such as aliases and
positional-only fields. Install the package you are documenting (see
[Usage](#usage)) to get the full output.

## Usage

Install the extension, ideally alongside the package you are documenting so
the live path is available:

```sh
pip install griffe-bagof-magic
# or, to guarantee the documented package is importable during the build:
pip install "griffe-bagof-magic[dynamic]"
```

Reference it from your mkdocstrings configuration:

```yaml
plugins:
  - mkdocstrings:
      handlers:
        python:
          options:
            extensions:
              - griffe_bagof_magic:MagicExtension
```

Or use it standalone with griffe:

```python
import griffe
from griffe_bagof_magic import MagicExtension

data = griffe.load(
    "your_package",
    extensions=griffe.load_extensions(MagicExtension()),
)
```
