"""Terraform JSON syntax (`*.tf.json`, `*.tfvars.json`) → the python-hcl2 document shape.

python-hcl2 gives us blocks as lists of one-key dicts and *quoted* string
literals ('"text"'), with expressions as '${...}'. Terraform JSON gives plain
objects and plain strings where `${...}` inside a string is an interpolation.
This module rewrites the JSON into the hcl2 shape so the loader and the
evaluator never know which syntax the file used.

    {"resource": {"aws_x": {"a": {"name": "n", "arn": "${aws_y.b.arn}"}}}}
      → {"resource": [{"aws_x": {"a": {"name": '"n"', "arn": "${aws_y.b.arn}"}}}]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# top-level keys that are blocks in HCL (become a list of dicts)
BLOCK_KEYS = ("resource", "data", "variable", "output", "module", "provider", "locals", "terraform",
              "moved", "import", "check", "removed")


def load_tf_json(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text())
    out: dict[str, Any] = {}
    for key, value in doc.items():
        blocks = value if isinstance(value, list) else [value]
        out[key] = [_quote_leaves(b) for b in blocks] if key in BLOCK_KEYS else _quote_leaves(value)
    return out


def load_tfvars_json(path: Path) -> dict[str, Any]:
    """{"name": value} → {"name": quoted value}, the shape `terraform.tfvars` parses to."""
    return {k: _quote_leaves(v) for k, v in json.loads(path.read_text()).items()}


def _quote_leaves(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _quote_leaves(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_quote_leaves(v) for v in value]
    if isinstance(value, str):
        return value if _is_bare_expression(value) else '"' + value.replace('"', '\\"') + '"'
    return value


def _is_bare_expression(text: str) -> bool:
    """'${aws_y.b.arn}' alone is an expression; 'a${b}c' is a template (quote it)."""
    if not (text.startswith("${") and text.endswith("}")):
        return False
    depth = 0
    for i, ch in enumerate(text):
        if text.startswith("${", i):
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and i != len(text) - 1:
                return False
    return depth == 0
