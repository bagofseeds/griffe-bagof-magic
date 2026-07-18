# griffe-bagof-magic

A [Griffe](https://mkdocstrings.github.io/griffe/) extension that teaches
documentation tools about
[`bagof.magic.Magic`](https://github.com/bagofseeds/bagof-magic) classes --
the way griffe's built-in extension handles `dataclasses`, and the community
ones handle `attrs` / `pydantic`.

`Magic` generates its `__init__` (and other methods) at runtime, so a purely
static tool like griffe sees an empty class body. This extension
reconstructs the generated constructor during static analysis, for classes
defined **by inheritance** *and* **by decorator**:

```python
from bagof.magic import Magic, magic

class Point(Magic, frozen=True):   # detected via the base class
    x: float
    y: float

@magic(kw_only=True)               # detected via the decorator
class Named:
    name: str
```

It understands the per-field annotation markers (both the `KwOnly[int]`
shorthand and the `Annotated[int, KwOnly()]` form):

- `Init` / `NoInit`, `KwOnly` / `NotKwOnly`
- `Var` / `ClassVar` (excluded from the constructor)
- `Default[T, value]` / `Factory[T, fn]` (defaults)

and the class-level `kw_only` / `init` options, and inherited fields.

## Usage

Install it, then reference it from your mkdocstrings configuration:

```yaml
plugins:
  - mkdocstrings:
      handlers:
        python:
          options:
            extensions:
              - griffe_bagof_magic:MagicExtension
```

Or, standalone with griffe:

```python
import griffe
from griffe_bagof_magic import MagicExtension

data = griffe.load(
    "your_package",
    extensions=griffe.load_extensions(MagicExtension()),
)
```
