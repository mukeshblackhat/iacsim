"""Shared fixtures: each example parsed once per session through the real pipeline."""

from pathlib import Path

import pytest

from iacsim.core.config import load_config
from iacsim.core.pipeline import build_graph
from iacsim.core.registry import load_builtin_plugins

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(scope="session", autouse=True)
def _plugins():
    load_builtin_plugins()


def _build(name: str):
    target = EXAMPLES / name
    graph, raw = build_graph(target, load_config(target))
    return graph, raw


@pytest.fixture(scope="session")
def classic_web():
    return _build("classic-web")


@pytest.fixture(scope="session")
def classic_web_bad():
    return _build("classic-web-bad")


@pytest.fixture(scope="session")
def foosh():
    return _build("foosh-serverless")


def edge(graph, src: str, dst: str):
    e = graph.find_edge(src, dst)
    assert e is not None, f"missing edge {src} → {dst}; have: " + "\n".join(f"{x.src} → {x.dst}" for x in graph.edges)
    return e
