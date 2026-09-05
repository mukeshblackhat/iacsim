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
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


class UnknownImplementation(KeyError):
    """Raised when a config asks for a name nobody registered."""


class Registry[T]:
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

# Every package whose import registers built-ins. Listed explicitly (instead of
# walking the whole `iacsim` tree) so start-up imports only what registers
# something: `iacsim.viewer` (http, ssl, webbrowser) is imported by `iacsim view`
# alone. tests/test_registry_smoke.py proves the list is complete by comparing
# the registries against a full walk.
BUILTIN_MODULES = (
    "iacsim.parsers.terraform",
    "iacsim.parsers.cloudformation",
    "iacsim.graph.normalisers",
    "iacsim.graph.inference",
    "iacsim.scenarios",
    "iacsim.latency.profile",
    "iacsim.latency.rules",
    "iacsim.latency.calibrate",
    "iacsim.simulator",
    "iacsim.analyzer",
    "iacsim.reporter",
)

_entry_points_loaded = False


def load_builtin_plugins() -> None:
    """Import the built-in packages so their @register decorators run."""
    for name in BUILTIN_MODULES:
        importlib.import_module(name)


def walk_all_modules() -> list[str]:
    """Every module under `iacsim`, imported — the slow, exhaustive form used by
    the smoke test to prove BUILTIN_MODULES misses nothing."""
    import iacsim
    names = [m.name for m in pkgutil.walk_packages(iacsim.__path__, prefix="iacsim.")]
    for name in names:
        importlib.import_module(name)
    return names


def load_entry_points() -> None:
    """Load plugins published through the `iacsim.plugins` entry-point group — once per process."""
    global _entry_points_loaded
    if _entry_points_loaded:
        return
    for entry_point in importlib.metadata.entry_points(group="iacsim.plugins"):
        entry_point.load()
    _entry_points_loaded = True


def load_plugins_dir(plugins_dir: Path | None) -> None:
    """Import every module in a local ./plugins directory (a drop-in extension point)."""
    if plugins_dir and plugins_dir.is_dir():
        path = str(plugins_dir)
        if path not in sys.path:
            sys.path.insert(0, path)
        for module_info in pkgutil.iter_modules([path]):
            importlib.import_module(module_info.name)


def load_external_plugins(plugins_dir: Path | None = None) -> None:
    """Entry points + a plugins directory (kept for callers of the old single function)."""
    load_entry_points()
    load_plugins_dir(plugins_dir)
