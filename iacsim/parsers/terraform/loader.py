"""Loads a Terraform module directory (and its local child modules) and expands
every `resource` block into concrete instances.

One ModuleInstance per module *instance* — a `module "x"` block with
`for_each` yields one ModuleInstance per key. Each instance owns:

    variables   name → default (raw)          inputs from the parent win
    locals      name → raw expression         evaluated lazily, memoised
    outputs     name → raw expression         evaluated lazily, memoised
    modules     name → block                  children, created lazily
    resources   [(type, name, body)]          expanded lazily, memoised
    regions     "aws" / "aws.<alias>" → region string   (provider blocks + `providers` map)

Everything is lazy so that cross-module references (compute needs the
database address, the database needs compute's security group) resolve
without ordering — a reference is just a Ref, it never forces the target to
be evaluated. Cycles that *do* need a value (a for_each that depends on
itself) are caught and reported as a warning.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import hcl2

from iacsim.core.models import RawResource
from iacsim.core.refs import addresses_in
from iacsim.parsers.terraform.evaluator import (
    EvalError,
    ModuleGroup,
    ModuleRef,
    Ref,
    ResourceInstances,
    Scope,
    Unresolved,
    evaluate_raw,
    iterate,
    stringify,
    to_plain,
    unquote_key,
)
from iacsim.parsers.terraform.hcl_expr import ParseError

_META_KEYS = ("for_each", "count", "provider", "providers", "dynamic", "lifecycle", "source", "__is_block__", "__comments__")
_PROVIDER_REF = re.compile(r"^\$\{(\w+)\.(\w+)\}$")


class Warnings:
    """Deduplicated warning list shared by every module instance in one parse."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self._seen: set[str] = set()

    def add(self, message: str) -> None:
        if message not in self._seen:
            self._seen.add(message)
            self.items.append(message)


class ModuleInstance:
    def __init__(
        self,
        dir: Path,
        root_dir: Path,
        warnings: Warnings,
        address_prefix: str = "",
        parent: ModuleInstance | None = None,
        input_exprs: dict[str, Any] | None = None,
        parent_scope: Scope | None = None,
        provider_map: dict[str, str] | None = None,
    ) -> None:
        self.dir = dir
        self.root_dir = root_dir
        self.warnings = warnings
        self.address_prefix = address_prefix           # "" for root, 'module.x["k"].' for children
        self.parent = parent
        self.input_exprs = input_exprs or {}
        self.parent_scope = parent_scope
        self.scope = Scope(self)

        self.variables: dict[str, Any] = {}
        self.locals: dict[str, Any] = {}
        self.outputs: dict[str, Any] = {}
        self.modules: dict[str, dict] = {}
        self.resources: list[tuple[str, str, dict, str]] = []     # type, name, body, file
        self.provider_blocks: list[dict] = []
        self._load_files()

        self._inputs: dict[str, Any] = {}
        self._locals: dict[str, Any] = {}
        self._outputs: dict[str, Any] = {}
        self._children: dict[str, ModuleRef | ModuleGroup] = {}
        self._instances: dict[str, ResourceInstances] = {}
        self._in_progress: set[str] = set()

        self.regions = self._resolve_regions(provider_map or {})

    # ------------------------------------------------------------ loading

    def _load_files(self) -> None:
        for file in sorted(self.dir.glob("*.tf")):
            try:
                with open(file) as fh:
                    doc = hcl2.load(fh)
            except Exception as e:  # noqa: BLE001 — hcl2 surfaces lark errors of many types
                self.warnings.add(f"{file}: could not parse ({str(e).splitlines()[0]})")
                continue
            for block in doc.get("variable", []):
                for name, body in _labelled(block):
                    self.variables[name] = body.get("default")
            for block in doc.get("locals", []):
                self.locals.update({k: v for k, v in block.items() if k not in ("__is_block__", "__comments__")})
            for block in doc.get("output", []):
                for name, body in _labelled(block):
                    self.outputs[name] = body.get("value")
            for block in doc.get("module", []):
                for name, body in _labelled(block):
                    self.modules[name] = body
            for block in doc.get("provider", []):
                for name, body in _labelled(block):
                    self.provider_blocks.append({"name": name, **body})
            for block in doc.get("resource", []):
                for rtype, inner in _labelled(block):
                    for rname, body in _labelled(inner):
                        self.resources.append((rtype, rname, body, str(file)))

    def _resolve_regions(self, provider_map: dict[str, str]) -> dict[str, str]:
        regions: dict[str, str] = {}
        if self.parent:
            regions["aws"] = self.parent.regions.get("aws")           # default provider is inherited
            for child_alias, parent_alias in provider_map.items():
                regions[child_alias] = self.parent.regions.get(parent_alias)
        for block in self.provider_blocks:
            if block["name"] != "aws":
                continue
            alias = _literal(block.get("alias"))
            key = f"aws.{alias}" if alias else "aws"
            try:
                region = evaluate_raw(block.get("region"), self.scope)
            except (EvalError, ParseError) as e:
                self.warnings.add(f"{self.address_prefix or 'root'} provider {key}: region not resolved ({e})")
                region = None
            regions[key] = region if isinstance(region, str) else None
        return regions

    # ------------------------------------------------------------ lazy lookups

    def input(self, name: str) -> Any:
        if name in self._inputs:
            return self._inputs[name]
        if name in self.input_exprs and self.parent_scope is not None:
            value = evaluate_raw(self.input_exprs[name], self.parent_scope)
        elif name in self.variables and self.variables[name] is not None:
            value = evaluate_raw(self.variables[name], self.scope)
        elif name in self.variables:
            value = None
        else:
            self.warnings.add(f"{self.address_prefix or 'root'}: variable '{name}' has no value")
            value = Unresolved(f"var.{name}")
        self._inputs[name] = value
        return value

    def local(self, name: str) -> Any:
        if name not in self._locals:
            if name not in self.locals:
                raise EvalError(f"unknown local '{name}'")
            self._locals[name] = self._guarded(f"local.{name}", lambda: evaluate_raw(self.locals[name], self.scope))
        return self._locals[name]

    def output(self, name: str) -> Any:
        if name not in self._outputs:
            if name not in self.outputs:
                raise EvalError(f"module {self.address_prefix or 'root'} has no output '{name}'")
            self._outputs[name] = self._guarded(f"output.{name}", lambda: evaluate_raw(self.outputs[name], self.scope))
        return self._outputs[name]

    def child(self, name: str) -> ModuleRef | ModuleGroup:
        if name not in self._children:
            if name not in self.modules:
                raise EvalError(f"unknown module '{name}'")
            self._children[name] = self._build_child(name, self.modules[name])
        return self._children[name]

    def resource(self, rtype: str, name: str) -> ResourceInstances:
        key = f"{rtype}.{name}"
        if key not in self._instances:
            for t, n, body, _file in self.resources:
                if t == rtype and n == name:
                    self._instances[key] = self._guarded(key, lambda b=body: self._expand(rtype, name, b))
                    break
            else:
                raise EvalError(f"unknown resource {key}")
        return self._instances[key]

    def _guarded(self, key: str, compute):
        if key in self._in_progress:
            raise EvalError(f"circular reference through {key}")
        self._in_progress.add(key)
        try:
            return compute()
        finally:
            self._in_progress.discard(key)

    # ------------------------------------------------------------ children

    def _build_child(self, name: str, body: dict) -> ModuleRef | ModuleGroup:
        source = _literal(body.get("source")) or ""
        if not source.startswith((".", "/")):
            self.warnings.add(f"module '{name}': remote source '{source}' is not supported; skipped")
            return ModuleGroup({})
        child_dir = (self.dir / source).resolve()
        if not child_dir.is_dir():
            self.warnings.add(f"module '{name}': source directory {child_dir} not found; skipped")
            return ModuleGroup({})

        provider_map = _provider_map(body.get("providers"))
        inputs = {k: v for k, v in body.items() if k not in _META_KEYS}

        def make(prefix: str, bindings: dict[str, Any]) -> ModuleRef:
            inst = ModuleInstance(child_dir, self.root_dir, self.warnings, prefix, self, inputs,
                                  self.scope.child(bindings), provider_map)
            return ModuleRef(inst)

        if "for_each" in body:
            keys = self._for_each_keys(f"module.{name}", body["for_each"])
            return ModuleGroup({k: make(f'{self.address_prefix}module.{name}["{k}"].', {"each": {"key": k, "value": v}})
                                for k, v in keys})
        if "count" in body:
            n = self._count(f"module.{name}", body["count"])
            return ModuleGroup({i: make(f"{self.address_prefix}module.{name}[{i}].", {"count": {"index": i}})
                                for i in range(n)})
        return ModuleRef(make(f"{self.address_prefix}module.{name}.", {}).instance)

    # ------------------------------------------------------------ resource expansion

    def _expand(self, rtype: str, name: str, body: dict) -> ResourceInstances:
        base = f"{self.address_prefix}{rtype}.{name}"
        if "for_each" in body:
            keys = self._for_each_keys(base, body["for_each"])
            return ResourceInstances(base, "for_each", {k: Ref(f'{base}["{k}"]') for k, _ in keys})
        if "count" in body:
            n = self._count(base, body["count"])
            return ResourceInstances(base, "count", {i: Ref(f"{base}[{i}]") for i in range(n)})
        return ResourceInstances(base, "single", {None: Ref(base)})

    def _for_each_keys(self, address: str, raw: Any) -> list[tuple[Any, Any]]:
        try:
            value = evaluate_raw(raw, self.scope)
            pairs = iterate(value)
            if isinstance(value, list):                    # toset(list): the value is the key
                pairs = [(stringify(v), v) for _, v in pairs]
            return pairs
        except (EvalError, ParseError) as e:
            self.warnings.add(f"{address}: for_each not resolved ({e}); treated as a single instance")
            return [("*", Unresolved("each.value"))]

    def _count(self, address: str, raw: Any) -> int:
        try:
            return int(evaluate_raw(raw, self.scope))
        except (EvalError, ParseError, TypeError, ValueError) as e:
            self.warnings.add(f"{address}: count not resolved ({e}); assuming 1")
            return 1

    def _bindings_for(self, instances: ResourceInstances, body: dict) -> list[tuple[Ref, dict]]:
        if instances.mode == "for_each":
            keys = dict(self._for_each_keys(instances.address_base, body["for_each"]))
            return [(ref, {"each": {"key": k, "value": keys.get(k)}}) for k, ref in instances.refs.items()]
        if instances.mode == "count":
            return [(ref, {"count": {"index": i}}) for i, ref in instances.refs.items()]
        return [(instances.single(), {})]

    # ------------------------------------------------------------ output

    def all_resources(self) -> list[RawResource]:
        out: list[RawResource] = []
        for rtype, rname, body, file in self.resources:
            instances = self.resource(rtype, rname)
            for ref, bindings in self._bindings_for(instances, body):
                out.append(self._raw_resource(ref.address, rtype, body, file, bindings))
        for name in self.modules:
            child = self.child(name)
            members = [child] if isinstance(child, ModuleRef) else list(child.refs.values())
            for member in members:
                out.extend(member.instance.all_resources())
        return out

    def _raw_resource(self, address: str, rtype: str, body: dict, file: str, bindings: dict) -> RawResource:
        scope = self.scope.child(bindings)
        attrs: dict[str, Any] = {}
        for key, raw in body.items():
            if key in _META_KEYS:
                continue
            try:
                attrs[key] = to_plain(evaluate_raw(raw, scope))
            except (EvalError, ParseError, TypeError, ValueError, KeyError, IndexError) as e:
                attrs[key] = raw
                self.warnings.add(f"{address}.{key}: not evaluated ({e})")
        for label, items in self._dynamic_blocks(address, body.get("dynamic", []), scope).items():
            attrs.setdefault(label, []).extend(items)
        return RawResource(
            address=address, type=rtype, attrs=attrs,
            references=[a for a in addresses_in(attrs) if a != address],
            region=self.regions.get(_provider_alias(body.get("provider")), self.regions.get("aws")),
            source_file=file,
        )

    def _dynamic_blocks(self, address: str, blocks: list, scope: Scope) -> dict[str, list]:
        out: dict[str, list] = {}
        for block in blocks:
            for label, body in _labelled(block):
                try:
                    pairs = iterate(evaluate_raw(body.get("for_each"), scope))
                    content = (body.get("content") or [{}])[0]
                    for key, value in pairs:
                        inner = scope.child({label: {"key": key, "value": value}})
                        out.setdefault(label, []).append(to_plain(evaluate_raw(content, inner)))
                except (EvalError, ParseError, TypeError, ValueError, KeyError) as e:
                    self.warnings.add(f"{address}: dynamic '{label}' block skipped ({e})")
        return out


# ------------------------------------------------------------------ helpers

def _labelled(block: dict) -> list[tuple[str, Any]]:
    """{'"name"': {...}} → [("name", {...})], skipping python-hcl2 metadata keys."""
    return [(unquote_key(k), v) for k, v in block.items() if k not in ("__is_block__", "__comments__")]


def _literal(raw: Any) -> str | None:
    if isinstance(raw, str):
        return unquote_key(raw)
    return None


def _provider_alias(raw: Any) -> str:
    """`provider = aws.db` arrives as "${aws.db}" → "aws.db"; absent → "aws"."""
    if isinstance(raw, str) and (m := _PROVIDER_REF.match(raw)):
        return f"{m.group(1)}.{m.group(2)}"
    return "aws"


def _provider_map(raw: Any) -> dict[str, str]:
    """`providers = { aws = aws.db }` → {"aws": "aws.db"}; `{ aws = aws }` → {"aws": "aws"}."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for child_alias, parent in raw.items():
        child_key = unquote_key(child_alias)
        if isinstance(parent, str):
            m = _PROVIDER_REF.match(parent)
            out[child_key] = f"{m.group(1)}.{m.group(2)}" if m else unquote_key(parent)
    return out
