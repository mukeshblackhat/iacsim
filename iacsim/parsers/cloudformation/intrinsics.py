"""Resolve CloudFormation intrinsic functions into the placeholder convention
shared with the Terraform parser (core/refs.py), so inference rules need no
format-specific code.

    {"Ref": "Orders"}                         → "${aws_dynamodb_table.Orders.id}"
    {"Fn::GetAtt": ["Orders", "Arn"]}         → "${aws_dynamodb_table.Orders.arn}"
    {"Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"}, ":x"]]}  → "arn:aws:x"
    {"Fn::Sub": "${Orders.Arn}/index/*"}      → "${aws_dynamodb_table.Orders.arn}/index/*"
    {"Ref": "AWS::Region"}                    → the template's region

Addresses are `<terraform type>.<LogicalId>` — Terraform-shaped on purpose,
so `refs.split_address` and every inference rule treat both formats alike.

Best-effort with a warning: Fn::Select on an unresolved list (first item),
Fn::If (Conditions are not evaluated; true branch), Fn::FindInMap,
Fn::ImportValue, Fn::Cidr (kept as "<name>" markers).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from iacsim.core.refs import placeholder

_SUB_VAR = re.compile(r"\$\{([^}]+)\}")


@dataclass
class Context:
    types: dict[str, str]              # logical id → canonical terraform type
    parameters: dict[str, Any]         # parameter name → default (None when absent)
    region: str
    stack_name: str
    warnings: list[str]
    account_id: str = "123456789012"
    _warned: set[str] = field(default_factory=set)

    def address(self, logical_id: str) -> str:
        return f"{self.types[logical_id]}.{logical_id}"

    def warn(self, message: str) -> None:
        if message not in self._warned:              # one line per distinct problem
            self._warned.add(message)
            self.warnings.append(f"{self.stack_name}: {message}")

    def pseudo(self, name: str) -> Any:
        return {
            "AWS::Region": self.region,
            "AWS::AccountId": self.account_id,
            "AWS::Partition": "aws",
            "AWS::StackName": self.stack_name,
            "AWS::StackId": f"arn:aws:cloudformation:{self.region}:{self.account_id}:stack/{self.stack_name}/0",
            "AWS::URLSuffix": "amazonaws.com",
            "AWS::NotificationARNs": [],
            "AWS::NoValue": None,
        }[name]


def resolve(value: Any, ctx: Context) -> Any:
    """Recursively replace intrinsics; everything else is returned as-is."""
    if isinstance(value, dict):
        if len(value) == 1:
            (key, arg), = value.items()
            if key == "Ref":
                return _ref(arg, ctx)
            if key.startswith("Fn::"):
                return _fn(key[4:], arg, ctx)
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    return value


# ------------------------------------------------------------------ Ref / GetAtt

def _ref(name: Any, ctx: Context) -> Any:
    if not isinstance(name, str):
        return resolve(name, ctx)
    if name in ctx.types:
        return placeholder(ctx.address(name), "id")
    if name.startswith("AWS::"):
        try:
            return ctx.pseudo(name)
        except KeyError:
            ctx.warn(f"unknown pseudo parameter {name}")
            return f"<{name}>"
    if name in ctx.parameters:
        if ctx.parameters[name] is None:
            ctx.warn(f"parameter {name} has no Default; kept as <{name}>")
            return f"<{name}>"
        return ctx.parameters[name]
    ctx.warn(f"Ref to unknown logical id '{name}'")
    return f"<{name}>"


def _get_att(arg: Any, ctx: Context) -> Any:
    if isinstance(arg, str):
        logical_id, _, attr = arg.partition(".")
    else:
        logical_id = str(resolve(arg[0], ctx))
        attr = ".".join(str(resolve(a, ctx)) for a in arg[1:])
    attr = attr.lower()                                   # "Arn" → "arn", like Terraform's `.arn`
    if logical_id in ctx.types:
        return placeholder(ctx.address(logical_id), attr)
    ctx.warn(f"Fn::GetAtt on unknown logical id '{logical_id}'")
    return f"<{logical_id}.{attr}>"


# ------------------------------------------------------------------ Fn::*

def _fn(name: str, arg: Any, ctx: Context) -> Any:
    if name == "GetAtt":
        return _get_att(arg, ctx)
    if name == "Join":
        delimiter, items = arg
        return str(delimiter).join(_text(i) for i in resolve(items, ctx))
    if name == "Sub":
        return _sub(arg, ctx)
    if name == "Select":
        return _select(arg, ctx)
    if name == "If":
        condition, if_true, _if_false = arg
        ctx.warn(f"Fn::If on condition '{condition}': Conditions are not evaluated; taking the true branch")
        return resolve(if_true, ctx)
    if name == "Split":
        delimiter, text = arg
        text = resolve(text, ctx)
        return text.split(delimiter) if isinstance(text, str) else text
    if name == "Base64":
        return resolve(arg, ctx)
    if name == "GetAZs":
        region = resolve(arg, ctx) or ctx.region
        return [f"{region}{suffix}" for suffix in "abc"]
    if name == "ImportValue":
        ctx.warn(f"Fn::ImportValue {resolve(arg, ctx)!r}: cross-stack export not resolved")
        return f"<import:{resolve(arg, ctx)}>"
    ctx.warn(f"Fn::{name} is not evaluated; kept as <{name}>")
    return f"<{name}>"


def _sub(arg: Any, ctx: Context) -> str:
    if isinstance(arg, str):
        template, variables = arg, {}
    else:
        template, variables = arg[0], resolve(arg[1], ctx)

    def replace(m: re.Match) -> str:
        name = m.group(1)
        if name.startswith("!"):                          # ${!Literal} → ${Literal}
            return "${" + name[1:] + "}"
        if name in variables:
            return _text(variables[name])
        if "." in name:
            return _text(_get_att(name, ctx))
        return _text(_ref(name, ctx))

    return _SUB_VAR.sub(replace, template)


def _select(arg: Any, ctx: Context) -> Any:
    index, items = arg
    items = resolve(items, ctx)
    if isinstance(items, list) and items:
        try:
            return items[int(resolve(index, ctx))]
        except (ValueError, IndexError):
            pass
        ctx.warn(f"Fn::Select index {index!r} out of range; taking the first item")
        return items[0]
    ctx.warn("Fn::Select on an unresolved list; kept as <Select>")
    return "<Select>"


def _text(value: Any) -> str:
    """How a resolved value reads when spliced into a string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value)
