"""Loads a Terraform module directory (and its local child modules) and expands
every `resource` block into concrete instances.

One ModuleInstance per module *instance* — a `module "x"` block with
`for_each` yields one ModuleInstance per key. Each instance owns:

    variables   name → default (raw)          inputs from the parent win
    locals      name → raw expression         evaluated lazily, memoised
    outputs     name → raw expression         evaluated lazily, memoised
    modules     name → block                  children, created lazily
    resources   [(type, name, body)]          expanded lazily, memoised
    regions     provider key → region string      (provider blocks + `providers` map)
    zones       provider key → zone string        (GCP provider blocks carry one)

A provider key is the provider's own name — "aws", "google", "google-beta" —
or its aliased form, "aws.db" / "google-beta.eu". A resource with no
`provider =` uses the provider its type names: `google_sql_database_instance`
→ "google", `aws_lambda_function` → "aws".

Root-only inputs: `terraform.tfvars` / `*.auto.tfvars` (+ `.json`) override
variable defaults; `workspace` backs `terraform.workspace` (default "default");
`default_region` is the `--region` fallback when no provider region resolves.
`*.tf.json` and `.tofu` files load like `.tf`. Registry/git module sources
are followed through `.terraform/modules/modules.json` when `terraform init`
has run; otherwise they warn and are skipped.

Everything is lazy so that cross-module references (compute needs the
database address, the database needs compute's security group) resolve
without ordering — a reference is just a Ref, it never forces the target to
be evaluated. Cycles that *do* need a value (a for_each that depends on
itself) are caught and reported as a warning.
"""

from __future__ import annotations

import json
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
from iacsim.parsers.terraform.tfjson import load_tf_json, load_tfvars_json

# Providers whose resources live in a region even when the block does not say so
# (the region then comes from the environment, which we cannot see) — the only ones
# worth a "region not resolved" warning. A `provider "kubernetes" { host = … }` or
# `provider "random" {}` has no region to resolve, so it is silent.
_REGIONAL_PROVIDERS = frozenset({"aws", "google", "google-beta"})

_META_KEYS = ("for_each", "count", "provider", "providers", "dynamic", "lifecycle", "source",
              "connection", "provisioner", "timeouts", "__is_block__", "__comments__")
_SOURCE_GLOBS = ("*.tf", "*.tofu", "*.tf.json")
_TFVARS_GLOBS = ("terraform.tfvars", "terraform.tfvars.json", "*.auto.tfvars", "*.auto.tfvars.json")
REMOTE_MODULE_HINT = ("resources inside it are not seen; run `terraform init` so iacsim can follow "
                      ".terraform/modules/modules.json")
_PROVIDER_REF = re.compile(r"^\$\{([\w-]+)(?:\.([\w-]+))?\}$")   # "${aws.db}", "${google-beta.eu}"
_PROVIDER_BARE = re.compile(r"^[\w-]+$")                          # "google-beta" — hcl2 leaves it unwrapped


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
        workspace: str | None = None,
        default_region: str | None = None,
        module_key: str = "",
    ) -> None:
        self.dir = dir
        self.root_dir = root_dir
        self.warnings = warnings
        self.address_prefix = address_prefix           # "" for root, 'module.x["k"].' for children
        self.parent = parent
        self.input_exprs = input_exprs or {}
        self.parent_scope = parent_scope
        self.scope = Scope(self)
        self.workspace = workspace or (parent.workspace if parent else "default")
        self.default_region = default_region or (parent.default_region if parent else None)
        self.module_key = module_key                   # "vpc" / "vpc.subnets" — the key modules.json uses
        self.installed_modules = parent.installed_modules if parent else _installed_modules(root_dir)
        self.var_values: dict[str, Any] = {} if parent else _load_tfvars(dir, warnings)

        self.variables: dict[str, Any] = {}
        self.locals: dict[str, Any] = {}
        self.outputs: dict[str, Any] = {}
        self.modules: dict[str, dict] = {}
        self.resources: list[tuple[str, str, dict, str]] = []     # type, name, body, file
        self._by_type_name: dict[tuple[str, str], tuple[dict, str]] = {}   # (type, name) → (body, file)
        self.provider_blocks: list[dict] = []
        self._load_files()

        self._inputs: dict[str, Any] = {}
        self._locals: dict[str, Any] = {}
        self._outputs: dict[str, Any] = {}
        self._children: dict[str, ModuleRef | ModuleGroup] = {}
        self._instances: dict[str, ResourceInstances] = {}
        self._in_progress: set[str] = set()

        providers = provider_map or {}
        self.regions = self._resolve_regions(providers)
        self.zones = self._resolve_zones(providers)

    # ------------------------------------------------------------ loading

    def _load_files(self) -> None:
        files = sorted(f for pattern in _SOURCE_GLOBS for f in self.dir.glob(pattern))
        for file in files:
            try:
                doc = _load_document(file)
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
                        first = self._by_type_name.get((rtype, rname))
                        if first is not None:
                            self.warnings.add(
                                f"duplicate resource {rtype}.{rname} in {first[1]} and {file}; first wins")
                            continue
                        self._by_type_name[(rtype, rname)] = (body, str(file))
                        self.resources.append((rtype, rname, body, str(file)))

    def _resolve_regions(self, provider_map: dict[str, str]) -> dict[str, str | None]:
        """Provider key → region, for every provider this module configures — not
        just "aws". A block whose region does not resolve falls back to
        `default_region` (`--region`) and warns when there is none."""
        regions = _inherited(self.parent.regions if self.parent else {}, provider_map)
        for block in self.provider_blocks:
            key = _provider_key(block)
            region = self._provider_setting(block, "region")
            if not isinstance(region, str):
                region = self.default_region
                if region is None and (block["name"] in _REGIONAL_PROVIDERS or "region" in block):
                    self.warnings.add(
                        f"{self.address_prefix or 'root'} provider {key}: region not resolved; "
                        "pass --region (parsers.terraform.region) to set a fallback")
            regions[key] = region
        return regions

    def _resolve_zones(self, provider_map: dict[str, str]) -> dict[str, str | None]:
        """Provider key → zone, resolved exactly like the regions. GCP provider
        blocks carry `zone` ("us-central1-a") beside `region`; AWS blocks have no
        such argument, so an AWS parse leaves this map empty. Unresolved zones are
        simply absent — there is no `--zone` fallback and no warning, because a GCP
        resource usually states its own zone and the provider's is only a default."""
        zones = _inherited(self.parent.zones if self.parent else {}, provider_map)
        for block in self.provider_blocks:
            zone = self._provider_setting(block, "zone")
            if isinstance(zone, str):
                zones[_provider_key(block)] = zone
        return zones

    def _provider_setting(self, block: dict, key: str) -> Any:
        try:
            return evaluate_raw(block.get(key), self.scope)
        except (EvalError, ParseError) as e:
            return Unresolved(str(e))

    # ------------------------------------------------------------ lazy lookups

    def input(self, name: str) -> Any:
        if name in self._inputs:
            return self._inputs[name]
        if name in self.input_exprs and self.parent_scope is not None:
            value = evaluate_raw(self.input_exprs[name], self.parent_scope)
        elif name in self.var_values:
            value = evaluate_raw(self.var_values[name], self.scope)
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
            found = self._by_type_name.get((rtype, name))
            if found is None:
                raise EvalError(f"unknown resource {key}")
            body = found[0]
            self._instances[key] = self._guarded(key, lambda: self._expand(rtype, name, body))
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
        child_key = f"{self.module_key}.{name}" if self.module_key else name
        if source.startswith((".", "/")):
            child_dir = (self.dir / source).resolve()
        elif child_key in self.installed_modules:
            child_dir = self.installed_modules[child_key]
        else:
            self.warnings.add(f"module '{name}': remote source '{source}'; {REMOTE_MODULE_HINT}")
            return ModuleGroup({})
        if not child_dir.is_dir():
            self.warnings.add(f"module '{name}': source directory {child_dir} not found; skipped")
            return ModuleGroup({})

        provider_map = _provider_map(body.get("providers"))
        inputs = {k: v for k, v in body.items() if k not in _META_KEYS}

        def make(prefix: str, bindings: dict[str, Any]) -> ModuleRef:
            inst = ModuleInstance(child_dir, self.root_dir, self.warnings, prefix, self, inputs,
                                  self.scope.child(bindings), provider_map, module_key=child_key)
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
                attrs[key] = to_plain(self._evaluate_block(address, raw, scope))
            except (EvalError, ParseError, TypeError, ValueError, KeyError, IndexError) as e:
                attrs[key] = raw
                self.warnings.add(f"{address}.{key}: not evaluated ({e})")
        for label, items in self._dynamic_blocks(address, body.get("dynamic", []), scope).items():
            attrs.setdefault(label, []).extend(items)
        alias = _provider_alias(body.get("provider"), rtype)
        zone = self.zones.get(alias)
        if zone is not None:
            # RawResource has no zone field, so the provider's zone travels in
            # `attrs` under a reserved key; a resource that states its own `zone`
            # is untouched, and the normaliser prefers that one anyway.
            attrs.setdefault("_provider_zone", zone)
        region = self.regions.get(alias)
        return RawResource(
            address=address, type=rtype, attrs=attrs,
            references=[a for a in addresses_in(attrs) if a != address],
            region=region if region is not None else self.default_region,
            source_file=file,
        )

    def _evaluate_block(self, address: str, raw: Any, scope: Scope) -> Any:
        """`evaluate_raw`, plus `dynamic` blocks nested inside a *static* block —
        `template { containers { dynamic "env" { … } } }` (Cloud Run) — expanded in
        place. `evaluate_raw` alone leaves such a `dynamic` list verbatim in the
        attrs, so the env vars it declares are invisible to every inference rule."""
        if isinstance(raw, list):
            return [self._evaluate_block(address, v, scope) for v in raw]
        if isinstance(raw, dict):
            nested = raw.get("dynamic")
            has_dynamic = isinstance(nested, list) and all(isinstance(b, dict) for b in nested)
            out = {unquote_key(k): self._evaluate_block(address, v, scope) for k, v in raw.items()
                   if k not in ("__is_block__", "__comments__") and not (has_dynamic and k == "dynamic")}
            if has_dynamic:
                for label, items in self._dynamic_blocks(address, nested, scope).items():
                    out.setdefault(label, []).extend(items)
            return out
        return evaluate_raw(raw, scope)

    def _dynamic_blocks(self, address: str, blocks: list, scope: Scope) -> dict[str, list]:
        """Expand `dynamic "label" { for_each = … content { … } }` blocks, recursively:
        a `content` may itself contain `dynamic` blocks (the iterator variable is the label)."""
        out: dict[str, list] = {}
        for block in blocks:
            for label, body in _labelled(block):
                try:
                    pairs = iterate(evaluate_raw(body.get("for_each"), scope))
                    content = (body.get("content") or [{}])[0]
                    iterator = _literal(body.get("iterator")) or label
                    for key, value in pairs:
                        inner = scope.child({iterator: {"key": key, "value": value}})
                        plain = {k: to_plain(self._evaluate_block(address, v, inner)) for k, v in content.items()
                                 if k not in ("dynamic", "__is_block__", "__comments__")}
                        nested = self._dynamic_blocks(address, content.get("dynamic", []), inner)
                        for sub_label, items in nested.items():
                            plain.setdefault(sub_label, []).extend(items)
                        out.setdefault(label, []).append(plain)
                except (EvalError, ParseError, TypeError, ValueError, KeyError) as e:
                    self.warnings.add(f"{address}: dynamic '{label}' block skipped ({e})")
        return out


# ------------------------------------------------------------------ helpers

_DOC_CACHE: dict[tuple[str, int], dict] = {}


def _load_document(file: Path) -> dict:
    """Parse one source file, cached by (path, mtime). A module used by N
    `module` blocks (or N `for_each` keys) is parsed once, not N times — the
    loader only *reads* the returned blocks, never mutates them, so sharing
    the parsed document between instances is safe."""
    key = (str(file.resolve()), file.stat().st_mtime_ns)
    doc = _DOC_CACHE.get(key)
    if doc is None:
        if file.name.endswith(".json"):
            doc = load_tf_json(file)
        else:
            with open(file) as fh:
                doc = hcl2.load(fh)
        _DOC_CACHE[key] = doc
    return doc


def _load_tfvars(root: Path, warnings: Warnings) -> dict[str, Any]:
    """terraform.tfvars then *.auto.tfvars (lexical order), later files win — Terraform's own order."""
    values: dict[str, Any] = {}
    files = [f for pattern in _TFVARS_GLOBS for f in sorted(root.glob(pattern))]
    for file in files:
        try:
            if file.name.endswith(".json"):
                doc = load_tfvars_json(file)
            else:
                with open(file) as fh:
                    doc = hcl2.load(fh)
        except Exception as e:  # noqa: BLE001
            warnings.add(f"{file}: could not parse ({str(e).splitlines()[0]})")
            continue
        values.update({k: v for k, v in doc.items() if k not in ("__is_block__", "__comments__")})
    return values


def _installed_modules(root: Path) -> dict[str, Path]:
    """`.terraform/modules/modules.json` (written by `terraform init`): module key → local dir."""
    manifest = root / ".terraform" / "modules" / "modules.json"
    if not manifest.is_file():
        return {}
    try:
        entries = json.loads(manifest.read_text()).get("Modules", [])
    except (OSError, ValueError):
        return {}
    return {m["Key"]: (root / m["Dir"]).resolve() for m in entries if m.get("Key") and m.get("Dir")}


def _labelled(block: dict) -> list[tuple[str, Any]]:
    """{'"name"': {...}} → [("name", {...})], skipping python-hcl2 metadata keys."""
    return [(unquote_key(k), v) for k, v in block.items() if k not in ("__is_block__", "__comments__")]


def _literal(raw: Any) -> str | None:
    if isinstance(raw, str):
        return unquote_key(raw)
    return None


def _provider_key(block: dict) -> str:
    """A provider block's key in the regions/zones maps: "google", "aws.db"."""
    alias = _literal(block.get("alias"))
    return f"{block['name']}.{alias}" if alias else block["name"]


def _provider_ref(raw: str) -> str | None:
    """"${aws.db}" → "aws.db", "${aws}" → "aws"; anything else → None."""
    m = _PROVIDER_REF.match(raw)
    return ".".join(g for g in m.groups() if g) if m else None


def _provider_alias(raw: Any, rtype: str) -> str:
    """The regions/zones key one resource resolves to.

    `provider = aws.db` arrives as "${aws.db}"; a bare `provider = google-beta`
    arrives unwrapped, with no "${}" at all. With no `provider =`, the resource
    uses the provider its own type names — "google_compute_instance" → "google".
    """
    if isinstance(raw, str):
        if ref := _provider_ref(raw):
            return ref
        if _PROVIDER_BARE.match(raw):
            return raw
    return rtype.split("_", 1)[0]


def _provider_map(raw: Any) -> dict[str, str]:
    """`providers = { aws = aws.db }` → {"aws": "aws.db"}; `{ aws = aws }` → {"aws": "aws"};
    hyphens survive on both sides: `{ google-beta = google-beta.eu }`."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for child_alias, parent in raw.items():
        if isinstance(parent, str):
            out[unquote_key(child_alias)] = _provider_ref(parent) or unquote_key(parent)
    return out


def _inherited(parent: dict[str, str | None], provider_map: dict[str, str]) -> dict[str, str | None]:
    """What a child module starts from: every un-aliased provider of its parent
    ("aws", "google" — aliases are only ever passed explicitly), then whatever
    `providers = { google-beta = google-beta.eu }` remaps onto the child's keys."""
    values = {key: value for key, value in parent.items() if "." not in key}
    values.update({child: parent.get(parent_key) for child, parent_key in provider_map.items()})
    return values
