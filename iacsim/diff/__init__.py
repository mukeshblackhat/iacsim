"""`iacsim diff` — the data model and the differ live here, out of the IR (WP8).

Nothing in parse → graph → simulate imports this package; only `cli.py` and the
reporters do, so a change to how diffs are aligned never touches the engine.
"""
