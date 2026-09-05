"""Evaluates hcl_expr ASTs against a module's scope — pragmatically.

We are not Terraform: we never talk to a provider, so a reference to another
resource can't produce a real value. Instead it produces a `Ref` (address +
attribute path), which flows through string templates, lists, `merge()` and
`jsonencode()` unchanged and is finally serialised as the "${address.attr}"
placeholder described in core/refs.py. Everything that *can* be computed from
literals, variables and locals is computed (so `for_each` keys, availability
zones, table names and Lambda memory sizes come out as real values).

Values the evaluator can produce, besides ordinary str / int / float / bool /
None / list / dict:

    Ref                 another resource's attribute
    ResourceInstances   the expansion of one `resource` block (single, for_each or count)
    ModuleRef / ModuleGroup   one module instance / a for_each'd module block
    TemplateFile        the result of templatefile(); kept unrendered
    Unresolved          something we could not compute (unknown function, remote data, ...)

Unknown functions, missing attributes and type mismatches raise EvalError; the
loader turns that into a warning on the attribute, never a crash.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iacsim.core.refs import placeholder
from iacsim.parsers.terraform.hcl_expr import AST, ParseError, parse_attribute


class EvalError(Exception):
    pass


# ------------------------------------------------------------------ value types

@dataclass(frozen=True)
class Ref:
    address: str
    attr: tuple[str, ...] = ()

    def placeholder(self) -> str:
        return placeholder(self.address, ".".join(self.attr))


@dataclass
class ResourceInstances:
    """All instances of one resource block, keyed by for_each key / count index."""
    address_base: str
    mode: str                                   # "single" | "for_each" | "count"
    refs: dict[Any, Ref] = field(default_factory=dict)

    def single(self) -> Ref:
        if self.mode == "single":
            return next(iter(self.refs.values()))
        raise EvalError(f"{self.address_base} has multiple instances; index it with a key")


@dataclass
class ModuleRef:
    instance: Any                               # loader.ModuleInstance (typed loosely to avoid a cycle)


@dataclass
class ModuleGroup:
    refs: dict[Any, ModuleRef]


@dataclass
class TemplateFile:
    path: str
    vars: dict[str, Any]


@dataclass(frozen=True)
class Unresolved:
    text: str

    def placeholder(self) -> str:
        return "${" + self.text + "}"


@dataclass
class Namespace:
    """`var`, `local`, `module`, `path`, or a resource type like `aws_subnet`."""
    kind: str
    module: Any


# ------------------------------------------------------------------ scope

class Scope:
    def __init__(self, module: Any, bindings: dict[str, Any] | None = None) -> None:
        self.module = module
        self.bindings = bindings or {}

    def child(self, extra: dict[str, Any]) -> Scope:
        return Scope(self.module, {**self.bindings, **extra})

    def lookup(self, name: str) -> Any:
        if name in self.bindings:
            return self.bindings[name]
        if name in ("var", "local", "module", "path", "terraform"):
            return Namespace(name, self.module)
        if name == "data":
            where = self.module.address_prefix.rstrip(".") or "root module"
            self.module.warnings.add(
                f"{where}: data.* sources are not evaluated; references to data.X stay unresolved")
            return Unresolved("data")
        if "_" in name:                                   # provider resource type, e.g. aws_subnet
            return Namespace(name, self.module)
        raise EvalError(f"unknown identifier '{name}'")


# ------------------------------------------------------------------ evaluation

def evaluate_raw(raw: Any, scope: Scope) -> Any:
    """python-hcl2 value (string expression, literal, nested dict/list) → evaluated value."""
    if isinstance(raw, dict):
        return {unquote_key(k): evaluate_raw(v, scope) for k, v in raw.items()
                if k not in ("__is_block__", "__comments__")}
    if isinstance(raw, list):
        return [evaluate_raw(v, scope) for v in raw]
    ast = parse_attribute(raw)
    if ast is None:
        return raw
    return evaluate(ast, scope)


def unquote_key(key: str) -> str:
    return key[1:-1] if len(key) >= 2 and key[0] == key[-1] == '"' else key


def evaluate(ast: AST, scope: Scope) -> Any:
    kind = ast[0]
    if kind in ("num", "bool"):
        return ast[1]
    if kind == "null":
        return None
    if kind == "str":
        return _template(ast[1], scope)
    if kind == "ident":
        return scope.lookup(ast[1])
    if kind == "get":
        return get_attr(evaluate(ast[1], scope), ast[2])
    if kind == "index":
        return get_index(evaluate(ast[1], scope), evaluate(ast[2], scope))
    if kind == "call":
        return _call(ast[1], ast[2], scope)
    if kind == "list":
        return [evaluate(item, scope) for item in ast[1]]
    if kind == "object":
        return {stringify(evaluate(k, scope)): evaluate(v, scope) for k, v in ast[1]}
    if kind == "for_list":
        _, key_var, value_var, coll, value_expr, cond = ast
        return [evaluate(value_expr, s) for s in _for_scopes(coll, key_var, value_var, cond, scope)]
    if kind == "for_map":
        _, key_var, value_var, coll, key_expr, value_expr, cond = ast
        return {stringify(evaluate(key_expr, s)): evaluate(value_expr, s)
                for s in _for_scopes(coll, key_var, value_var, cond, scope)}
    if kind == "cond":
        return evaluate(ast[2], scope) if truthy(evaluate(ast[1], scope)) else evaluate(ast[3], scope)
    if kind == "bin":
        return _binary(ast[1], evaluate(ast[2], scope), evaluate(ast[3], scope))
    if kind == "unary":
        operand = evaluate(ast[2], scope)
        if ast[1] == "!":
            return not truthy(operand)
        return -_number(operand)
    if kind == "splat":
        return _splat(evaluate(ast[1], scope))
    raise EvalError(f"unknown AST node {kind}")


def _template(parts: list, scope: Scope) -> Any:
    if len(parts) == 1 and not isinstance(parts[0], str):
        return evaluate(parts[0], scope)                  # "${x}" alone yields x itself
    return "".join(p if isinstance(p, str) else stringify(evaluate(p, scope)) for p in parts)


def _for_scopes(coll_ast: AST, key_var: str | None, value_var: str, cond: AST | None, scope: Scope):
    for key, value in iterate(evaluate(coll_ast, scope)):
        bindings = {value_var: value}
        if key_var:
            bindings[key_var] = key
        inner = scope.child(bindings)
        if cond is None or truthy(evaluate(cond, inner)):
            yield inner


def iterate(value: Any) -> list[tuple[Any, Any]]:
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        return list(enumerate(value))
    if isinstance(value, ResourceInstances):
        return list(value.refs.items())
    if isinstance(value, ModuleGroup):
        return list(value.refs.items())
    if isinstance(value, Unresolved):
        raise EvalError(f"cannot iterate unresolved value {value.text}")
    raise EvalError(f"cannot iterate over {type(value).__name__}")


# ------------------------------------------------------------------ attribute / index access

def get_attr(value: Any, name: str) -> Any:
    if isinstance(value, Namespace):
        return _namespace_attr(value, name)
    if isinstance(value, Ref):
        return Ref(value.address, value.attr + (name,))
    if isinstance(value, ResourceInstances):
        return get_attr(value.single(), name)
    if isinstance(value, ModuleRef):
        return value.instance.output(name)
    if isinstance(value, dict):
        if name in value:
            return value[name]
        raise EvalError(f"object has no attribute '{name}'")
    if isinstance(value, list):                       # after [*] / .*: map the attribute over the elements
        return [get_attr(v, name) for v in value]
    if isinstance(value, Unresolved):
        return Unresolved(f"{value.text}.{name}")
    if value is None:
        raise EvalError(f"attribute '{name}' of null")
    raise EvalError(f"cannot read attribute '{name}' of {type(value).__name__}")


def _namespace_attr(ns: Namespace, name: str) -> Any:
    m = ns.module
    if ns.kind == "var":
        return m.input(name)
    if ns.kind == "local":
        return m.local(name)
    if ns.kind == "module":
        return m.child(name)
    if ns.kind == "path":
        return {"module": str(m.dir), "root": str(m.root_dir), "cwd": str(m.root_dir)}[name]
    if ns.kind == "terraform":
        if name == "workspace":                      # "default" unless parsers.terraform.workspace is set
            return m.workspace
        raise EvalError(f"terraform.{name} is not available")
    return m.resource(ns.kind, name)


def get_index(value: Any, key: Any) -> Any:
    if isinstance(value, ResourceInstances):
        lookup = int(key) if value.mode == "count" else key
        if lookup in value.refs:
            return value.refs[lookup]
        raise EvalError(f"{value.address_base} has no instance {key!r}")
    if isinstance(value, ModuleGroup):
        if key in value.refs:
            return value.refs[key]
        raise EvalError(f"module has no instance {key!r}")
    if isinstance(value, Ref):
        return Ref(value.address, value.attr[:-1] + (f"{value.attr[-1]}[{json.dumps(key)}]",)) \
            if value.attr else Ref(value.address, (f"[{json.dumps(key)}]",))
    if isinstance(value, dict):
        if key in value:
            return value[key]
        raise EvalError(f"map has no key {key!r}")
    if isinstance(value, list):
        try:
            return value[int(key)]
        except (IndexError, ValueError, TypeError):
            raise EvalError(f"list index {key!r} out of range") from None
    if isinstance(value, Unresolved):
        return Unresolved(f"{value.text}[{json.dumps(key)}]")
    raise EvalError(f"cannot index {type(value).__name__}")


def _splat(value: Any) -> list:
    if isinstance(value, ResourceInstances):
        return list(value.refs.values())
    if isinstance(value, list):
        return value
    raise EvalError("splat on non-list")


# ------------------------------------------------------------------ operators

def truthy(value: Any) -> bool:
    if isinstance(value, (Ref, Unresolved)):
        raise EvalError("condition depends on an unresolved value")
    return bool(value)


def _number(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            return int(value) if isinstance(value, str) and value.isdigit() else float(value)
        except (TypeError, ValueError):
            raise EvalError(f"expected a number, got {type(value).__name__}") from None
    return value


def _binary(op: str, a: Any, b: Any) -> Any:
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "&&":
        return truthy(a) and truthy(b)
    if op == "||":
        return truthy(a) or truthy(b)
    x, y = _number(a), _number(b)
    return {"+": x + y, "-": x - y, "*": x * y, "/": x / y if y else float("nan"),
            "%": x % y if y else float("nan"),
            "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


# ------------------------------------------------------------------ serialisation

def stringify(value: Any) -> str:
    """How a value appears inside a string template."""
    if isinstance(value, str):
        return value
    if isinstance(value, (Ref, Unresolved)):
        return value.placeholder()
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(to_plain(value), default=str)


def to_plain(value: Any) -> Any:
    """Evaluated value → JSON-friendly structure for RawResource.attrs."""
    if isinstance(value, (Ref, Unresolved)):
        return value.placeholder()
    if isinstance(value, TemplateFile):
        return {"__templatefile__": {"path": value.path, "vars": to_plain(value.vars)}}
    if isinstance(value, ResourceInstances):
        refs = [r.placeholder() for r in value.refs.values()]
        return refs[0] if value.mode == "single" else refs
    if isinstance(value, ModuleRef):
        return "${" + value.instance.address_prefix.rstrip(".") + "}"
    if isinstance(value, ModuleGroup):
        return [to_plain(r) for r in value.refs.values()]
    if isinstance(value, Namespace):
        return "${" + value.kind + "}"
    if isinstance(value, dict):
        return {stringify(k) if not isinstance(k, str) else k: to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    return value


# ------------------------------------------------------------------ functions

def _call(name: str, arg_asts: list[AST], scope: Scope) -> Any:
    if name == "try":
        last_error: Exception | None = None
        for arg in arg_asts:
            try:
                return evaluate(arg, scope)
            except (EvalError, ParseError) as e:
                last_error = e
        raise EvalError(f"try(): every alternative failed ({last_error})")
    if name == "can":
        try:
            evaluate(arg_asts[0], scope)
            return True
        except (EvalError, ParseError):
            return False
    args = [evaluate(a, scope) for a in arg_asts]
    if name == "templatefile":
        return TemplateFile(_module_path(scope, args[0]), args[1] if len(args) > 1 else {})
    if name == "file":
        return _read_file(scope, args[0])
    if name == "fileexists":
        return _file_path(scope, args[0]).is_file()
    fn = FUNCTIONS.get(name)
    if fn is None:
        raise EvalError(f"unknown function {name}()")
    return fn(*args)


def _file_path(scope: Scope, raw: Any) -> Path:
    """Terraform resolves file paths against the working directory (the root
    module); `${path.module}/x` is already absolute by the time it gets here."""
    text = stringify(raw)
    path = Path(text)
    if path.is_absolute():
        return path
    for base in (scope.module.dir, scope.module.root_dir):
        if (base / path).exists():
            return base / path
    return scope.module.root_dir / path


def _module_path(scope: Scope, raw: Any) -> str:
    return str(_file_path(scope, raw))


def _read_file(scope: Scope, raw: Any) -> Any:
    path = _file_path(scope, raw)
    try:
        return path.read_text()
    except OSError:
        return Unresolved(f"file({stringify(raw)})")


def _zipmap(keys: Any, values: Any) -> dict:
    return dict(zip([stringify(k) for k in _to_list(keys)], _to_list(values), strict=False))


def _merge(*maps: Any) -> dict:
    out: dict = {}
    for m in maps:
        if isinstance(m, dict):
            out.update(m)
        elif m is not None:
            raise EvalError("merge() expects maps")
    return out


def _flatten(value: Any) -> list:
    out: list = []
    for item in value if isinstance(value, list) else [value]:
        out.extend(_flatten(item) if isinstance(item, list) else [item])
    return out


def _to_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, (ResourceInstances, ModuleGroup)):
        return list(value.refs.values())
    raise EvalError("expected a list or map")


def _length(value: Any) -> int:
    if isinstance(value, (str, list, dict)):
        return len(value)
    if isinstance(value, (ResourceInstances, ModuleGroup)):
        return len(value.refs)
    raise EvalError("length() of unresolved value")


def _index(coll: Any, item: Any) -> int:
    try:
        return _to_list(coll).index(item)
    except ValueError:
        raise EvalError("index(): item not found") from None


def _lookup(m: Any, key: Any, default: Any = None) -> Any:
    if isinstance(m, dict):
        return m.get(key, default)
    raise EvalError("lookup() on non-map")


def _format(fmt: str, *args: Any) -> str:
    out = fmt
    for a in args:
        out = out.replace("%s", stringify(a), 1).replace("%d", stringify(a), 1)
    return out


def _unresolved_fn(name: str) -> Callable[..., Unresolved]:
    return lambda *args: Unresolved(f"{name}({', '.join(stringify(a) for a in args)})")


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "merge": _merge,
    "flatten": _flatten,
    "toset": _to_list,
    "tolist": _to_list,
    "tomap": lambda v: v,
    "length": _length,
    "index": _index,
    "title": lambda s: str(s).title(),
    "lower": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "trimspace": lambda s: str(s).strip(),
    "replace": lambda s, old, new: stringify(s).replace(stringify(old), stringify(new)),
    "join": lambda sep, items: stringify(sep).join(stringify(i) for i in _to_list(items)),
    "split": lambda sep, s: stringify(s).split(stringify(sep)),
    "concat": lambda *lists: [x for lst in lists for x in _to_list(lst)],
    "keys": lambda m: list(m.keys()) if isinstance(m, dict) else _raise("keys() on non-map"),
    "values": lambda m: list(m.values()) if isinstance(m, dict) else _raise("values() on non-map"),
    "lookup": _lookup,
    "contains": lambda lst, item: item in _to_list(lst),
    "coalesce": lambda *args: next((a for a in args if a not in (None, "")), None),
    "tostring": stringify,
    "tonumber": _number,
    "tobool": truthy,
    "format": _format,
    "jsonencode": lambda v: v,                     # kept structured on purpose (see RawResource docs)
    "jsondecode": lambda s: json.loads(s) if isinstance(s, str) else s,
    "abs": lambda n: abs(_number(n)),
    "max": lambda *n: max(_number(x) for x in n),
    "min": lambda *n: min(_number(x) for x in n),
    "element": lambda lst, i: _to_list(lst)[int(i) % len(_to_list(lst))],
    "compact": lambda lst: [x for x in _to_list(lst) if x not in (None, "")],
    "distinct": lambda lst: list(dict.fromkeys(_to_list(lst))),
    "sort": lambda lst: sorted(_to_list(lst), key=stringify),
    "reverse": lambda lst: list(reversed(_to_list(lst))),
    "zipmap": _zipmap,
    "trimprefix": lambda s, p: stringify(s).removeprefix(stringify(p)),
    "trimsuffix": lambda s, x: stringify(s)[:-len(stringify(x))] if stringify(x) and stringify(s).endswith(stringify(x)) else stringify(s),
    "trim": lambda s, chars: stringify(s).strip(stringify(chars)),
    "startswith": lambda s, p: stringify(s).startswith(stringify(p)),
    "endswith": lambda s, x: stringify(s).endswith(stringify(x)),
    "substr": lambda s, offset, length: stringify(s)[int(offset):] if int(length) < 0 else stringify(s)[int(offset):int(offset) + int(length)],
    "strrev": lambda s: stringify(s)[::-1],
    "chomp": lambda s: stringify(s).rstrip("\r\n"),
    "one": lambda lst: (_to_list(lst) or [None])[0],
    "try_element": lambda lst, i: _to_list(lst)[int(i)] if int(i) < len(_to_list(lst)) else None,
    "slice": lambda lst, a, b: _to_list(lst)[int(a):int(b)],
    "setproduct": lambda *lists: [list(t) for t in __import__("itertools").product(*[_to_list(x) for x in lists])],
    "filebase64": _unresolved_fn("filebase64"),
    "filemd5": _unresolved_fn("filemd5"),
    "filesha256": _unresolved_fn("filesha256"),
    "fileset": _unresolved_fn("fileset"),
    "templatestring": _unresolved_fn("templatestring"),
    "cidrsubnet": _unresolved_fn("cidrsubnet"),
    "cidrhost": _unresolved_fn("cidrhost"),
    "uuid": _unresolved_fn("uuid"),
    "timestamp": _unresolved_fn("timestamp"),
    "base64encode": _unresolved_fn("base64encode"),
    "sha256": _unresolved_fn("sha256"),
    "md5": _unresolved_fn("md5"),
}


def _raise(msg: str) -> Any:
    raise EvalError(msg)


