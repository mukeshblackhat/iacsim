"""Name → implementation registries.

Every extension point in the spec (parser, walker, cost rule, ...) has one
Registry. Built-in implementations register themselves on import; third-party
plugins register through the `iacsim.plugins` entry-point group or by dropping
a module into ./plugins. Config files then pick implementations *by name*, so
swapping one never touches the engine.

Usage:
    WALKERS = Registry[Walker]("walker")

    @WALKERS.register("expected_value")
    class ExpectedValueWalker(Walker): ...

    walker = WALKERS.get("expected_value")()
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
import sys
from pathlib import Path
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class UnknownImplementation(KeyError):
    """Raised when a config asks for a name nobody registered."""


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind            # human label used in error messages, e.g. "walker"
        self._items: dict[str, type[T]] = {}

    def register(self, name: str) -> Callable[[type[T]], type[T]]:
        """Decorator: `@REGISTRY.register("name")` above a class."""
        def decorator(cls: type[T]) -> type[T]:
            if name in self._items and self._items[name] is not cls:
                raise ValueError(f"{self.kind} '{name}' is already registered by {self._items[name]}")
            self._items[name] = cls
            cls.registry_name = name  # type: ignore[attr-defined]  # handy for reports
            return cls
        return decorator

    def get(self, name: str) -> type[T]:
        try:
            return self._items[name]
        except KeyError:
            available = ", ".join(sorted(self._items)) or "<none>"
            raise UnknownImplementation(
                f"unknown {self.kind} '{name}'. Available: {available}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: str) -> bool:
        return name in self._items


# --------------------------------------------------------------- discovery

def load_builtin_plugins() -> None:
    """Import every built-in package so their @register decorators run."""
    import iacsim
    for module_info in pkgutil.walk_packages(iacsim.__path__, prefix="iacsim."):
        importlib.import_module(module_info.name)


def load_external_plugins(plugins_dir: Path | None = None) -> None:
    """Import plugins from the entry-point group and from a local ./plugins directory."""
    for entry_point in importlib.metadata.entry_points(group="iacsim.plugins"):
        entry_point.load()

    if plugins_dir and plugins_dir.is_dir():
        sys.path.insert(0, str(plugins_dir))
        for module_info in pkgutil.iter_modules([str(plugins_dir)]):
            importlib.import_module(module_info.name)
