"""Every extension point has at least one built-in registered, and config
defaults only reference names that exist. Runs without parsing anything."""

from iacsim.core.config import DEFAULTS
from iacsim.core.interfaces import (
    ANALYZERS,
    COST_RULES,
    INFERENCE_RULES,
    NORMALISERS,
    PARSERS,
    PROFILE_SOURCES,
    REPORTERS,
    SCENARIO_SOURCES,
    WALKERS,
)
from iacsim.core.registry import load_builtin_plugins


def setup_module():
    load_builtin_plugins()


def test_defaults_reference_registered_names():
    assert DEFAULTS["provider"] in NORMALISERS
    for name in DEFAULTS["inference"]["rules"]:
        assert name in INFERENCE_RULES, name
    for name in DEFAULTS["scenarios"]["sources"]:
        assert name in SCENARIO_SOURCES, name
    for name in DEFAULTS["latency"]["rules"]:
        assert name in COST_RULES, name
    assert DEFAULTS["simulation"]["walker"] in WALKERS
    for name in DEFAULTS["analysis"]["analyzers"]:
        assert name in ANALYZERS, name
    for name in DEFAULTS["report"]["outputs"]:
        assert name in REPORTERS, name


def test_every_registry_has_a_builtin():
    for reg in (PARSERS, NORMALISERS, INFERENCE_RULES, SCENARIO_SOURCES,
                PROFILE_SOURCES, COST_RULES, WALKERS, ANALYZERS, REPORTERS):
        assert reg.names(), reg.kind
