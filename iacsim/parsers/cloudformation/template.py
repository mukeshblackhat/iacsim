"""Locate and load CloudFormation templates (JSON, or YAML with short-form tags).

YAML templates use `!Ref X`, `!GetAtt A.B`, `!Sub "..."`, `!Join [...]` and
friends. They are turned into the long form (`{"Ref": ...}`, `{"Fn::GetAtt":
[...]}`) so the intrinsics resolver sees one shape regardless of format.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

TEMPLATE_MARKERS = ("AWSTemplateFormatVersion", "Resources")
TEMPLATE_GLOBS = ("*.template.json", "template.json", "template.yaml", "template.yml", "*.template.yaml")
DEFAULT_REGION = "us-east-1"

_REGION = re.compile(r"\b(?:us|eu|ap|sa|ca|me|af|il)-[a-z]+-\d\b")


def template_files(path: Path) -> list[Path]:
    """The template(s) at `path`: the file itself, or every template in a directory
    (cdk.out holds one per stack)."""
    if path.is_file():
        return [path]
    found: dict[Path, None] = {}
    for pattern in TEMPLATE_GLOBS:
        for file in sorted(path.glob(pattern)):
            found.setdefault(file)
    return [f for f in found if looks_like_template(f)]


def looks_like_template(file: Path) -> bool:
    try:
        head = file.read_text()[:4000]
    except OSError:
        return False
    return any(marker in head for marker in TEMPLATE_MARKERS)


def load_template(file: Path) -> dict[str, Any]:
    text = file.read_text()
    if file.suffix == ".json" or text.lstrip().startswith("{"):
        return json.loads(text)
    return yaml.load(text, Loader=_CfnLoader) or {}


def guess_region(text: str) -> str | None:
    """CDK bakes the region into ARNs (`arn:aws:apigateway:us-east-1:...`);
    the first region-looking literal wins."""
    m = _REGION.search(text)
    return m.group(0) if m else None


def stack_name(file: Path) -> str:
    return file.name.split(".", 1)[0]


# ------------------------------------------------------------------ YAML short tags

class _CfnLoader(yaml.SafeLoader):
    pass


def _tag_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> dict[str, Any]:
    """`!Ref X` → {"Ref": "X"}; `!GetAtt A.B` → {"Fn::GetAtt": ["A", "B"]}; `!Foo v` → {"Fn::Foo": v}."""
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    if tag_suffix == "Ref":
        return {"Ref": value}
    if tag_suffix == "GetAtt" and isinstance(value, str):
        return {"Fn::GetAtt": value.split(".", 1)}
    return {f"Fn::{tag_suffix}": value}


_CfnLoader.add_multi_constructor("!", _tag_constructor)
