"""DECISIONS.md and CODE_FLOW.md cite `path:line` — every citation must point at a real
file and a line that exists. A `~` before the line means "approximate" (the file is under
active edit); it still has to exist and be within the file's length.

Examples matched: `iacsim/core/pipeline.py:53-58`, `iacsim/cli.py:74`, `traversal.py:~210 _edge_for`.
Bare-name citations (`loader.py:104`) resolve to the unique file with that name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["DECISIONS.md", "CODE_FLOW.md"]

_REF = re.compile(r"`([\w./-]+\.(?:py|yaml|md|tf|html|toml)|Makefile):(~?)(\d+)(?:-(\d+))?\b")
_FENCE = re.compile(r"^```", re.MULTILINE)


def _prose(text: str) -> str:
    """Drop fenced code blocks (diagrams use pseudo-paths)."""
    out, keep = [], True
    for chunk in _FENCE.split(text):
        if keep:
            out.append(chunk)
        keep = not keep
    return "".join(out)


def _resolve(name: str) -> Path | None:
    direct = ROOT / name
    if direct.is_file():
        return direct
    matches = [p for p in ROOT.rglob(Path(name).name) if ".venv" not in p.parts]
    return matches[0] if len(matches) == 1 else None


@pytest.mark.parametrize("doc", DOCS)
def test_every_file_line_reference_exists(doc: str) -> None:
    text = _prose((ROOT / doc).read_text(encoding="utf-8"))
    refs = _REF.findall(text)
    assert refs, f"{doc}: no path:line references found — regex or doc broken"

    counts: dict[Path, int] = {}
    bad: list[str] = []
    for name, _approx, start, end in refs:
        path = _resolve(name)
        if path is None:
            bad.append(f"{name}: file not found (or ambiguous bare name)")
            continue
        n = counts.setdefault(path, sum(1 for _ in path.open(encoding="utf-8")))
        last = int(end or start)
        if int(start) < 1 or last > n:
            bad.append(f"{name}:{start}{'-' + end if end else ''}: file has {n} lines")
    assert not bad, f"{doc}: {len(bad)} bad reference(s):\n  " + "\n  ".join(bad)
    print(f"{doc}: {len(refs)} file:line references verified")
