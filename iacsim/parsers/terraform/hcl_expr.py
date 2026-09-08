"""A small parser for HCL2 *expressions* — the `${...}` parts python-hcl2 leaves as text.

python-hcl2 turns a .tf file into JSON-ish Python, but any attribute that is
not a plain literal comes back as the raw expression string, e.g.
`"${merge(local.a, {X = var.y})}"` or `"${[for s in aws_subnet.public : s.id]}"`.
This module turns such strings into a tiny AST that evaluator.py can walk.

It covers what real Terraform modules use day to day — references, index and
attribute access, function calls, string templates, list/object literals,
for-expressions, arithmetic/comparison/logic, and the `a ? b : c` conditional.
Anything else raises ParseError, which the evaluator turns into a warning.

AST nodes are plain tuples; the first element is the kind:
    ("num", 1)  ("str", [part, ...])  ("bool", True)  ("null",)
    ("ident", "var")  ("get", obj, "name")  ("index", obj, key)  ("call", "fn", [args])
    ("list", [items])  ("object", [(key_ast, value_ast)])
    ("for_list", key_var, value_var, coll, value_expr, cond)
    ("for_map",  key_var, value_var, coll, key_expr, value_expr, cond)
    ("cond", test, a, b)  ("bin", op, left, right)  ("unary", op, operand)
    ("splat", obj)
A "str" part is either a literal str or a nested AST (an interpolation).
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any

AST = tuple


class ParseError(ValueError):
    pass


# ------------------------------------------------------------------ tokenizer

@dataclass
class Token:
    kind: str          # ident | num | str | op
    value: Any
    pos: int


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_NUM = re.compile(r"\d+(\.\d+)?")
_OPS = ["=>", "==", "!=", "<=", ">=", "&&", "||", "...", "?", ":", ".", ",", "(", ")",
        "[", "]", "{", "}", "=", "+", "-", "*", "/", "%", "<", ">", "!"]


def tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
        elif ch == '"':
            parts, i = _scan_string(src, i)
            tokens.append(Token("str", parts, i))
        elif m := _NUM.match(src, i):
            text = m.group(0)
            tokens.append(Token("num", float(text) if "." in text else int(text), i))
            i = m.end()
        elif m := _IDENT.match(src, i):
            tokens.append(Token("ident", m.group(0), i))
            i = m.end()
        else:
            for op in _OPS:
                if src.startswith(op, i):
                    tokens.append(Token("op", op, i))
                    i += len(op)
                    break
            else:
                raise ParseError(f"unexpected character {ch!r} at {i} in {src!r}")
    return tokens


def _scan_string(src: str, start: int) -> tuple[list, int]:
    """Read a quoted string starting at src[start] == '"'. Returns its parts
    (literal strings and nested expression ASTs for ${...}) and the end index."""
    parts: list = []
    buf: list[str] = []
    i = start + 1
    while i < len(src):
        ch = src[i]
        if ch == "\\" and i + 1 < len(src):
            buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(src[i + 1], src[i + 1]))
            i += 2
        elif src.startswith("$${", i):
            # Terraform's escape: literal "${", not an interpolation. Kept as written
            # (`$${`) so downstream readers of heredoc bodies — a Workflows YAML with
            # `$${sys.get_env(...)}` expressions — see exactly the source text.
            buf.append("$${")
            i += 3
        elif src.startswith("${", i):
            if buf:
                parts.append("".join(buf))
                buf = []
            inner, i = _scan_interpolation(src, i + 2)
            parts.append(parse(inner))
        elif src.startswith("%{", i):
            raise ParseError("template directives (%{ }) are not supported")
        elif ch == '"':
            if buf:
                parts.append("".join(buf))
            return parts, i + 1
        else:
            buf.append(ch)
            i += 1
    raise ParseError(f"unterminated string starting at {start}")


def _scan_interpolation(src: str, i: int) -> tuple[str, int]:
    """From just after '${', return the expression source up to the matching '}'."""
    depth, start = 1, i
    while i < len(src):
        ch = src[i]
        if ch == '"':                       # skip nested strings wholesale
            _, i = _scan_string(src, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i], i + 1
        i += 1
    raise ParseError("unterminated ${ interpolation")


# ------------------------------------------------------------------ parser

class _Parser:
    def __init__(self, tokens: list[Token], src: str) -> None:
        self.toks = tokens
        self.src = src
        self.i = 0

    # -- token helpers
    def peek(self, offset: int = 0) -> Token | None:
        j = self.i + offset
        return self.toks[j] if j < len(self.toks) else None

    def at(self, kind: str, value: Any = None) -> bool:
        t = self.peek()
        return t is not None and t.kind == kind and (value is None or t.value == value)

    def take(self, kind: str, value: Any = None) -> Token:
        if not self.at(kind, value):
            got = self.peek()
            raise ParseError(f"expected {value or kind} at {got.pos if got else 'end'} in {self.src!r}")
        t = self.toks[self.i]
        self.i += 1
        return t

    def accept(self, kind: str, value: Any = None) -> bool:
        if self.at(kind, value):
            self.i += 1
            return True
        return False

    # -- grammar, lowest precedence first
    def expr(self) -> AST:
        test = self.logical_or()
        if self.accept("op", "?"):
            a = self.expr()
            self.take("op", ":")
            b = self.expr()
            return ("cond", test, a, b)
        return test

    def logical_or(self) -> AST:
        left = self.logical_and()
        while self.accept("op", "||"):
            left = ("bin", "||", left, self.logical_and())
        return left

    def logical_and(self) -> AST:
        left = self.equality()
        while self.accept("op", "&&"):
            left = ("bin", "&&", left, self.equality())
        return left

    def equality(self) -> AST:
        left = self.comparison()
        while self.at("op", "==") or self.at("op", "!="):
            op = self.take("op").value
            left = ("bin", op, left, self.comparison())
        return left

    def comparison(self) -> AST:
        left = self.additive()
        while any(self.at("op", o) for o in ("<", ">", "<=", ">=")):
            op = self.take("op").value
            left = ("bin", op, left, self.additive())
        return left

    def additive(self) -> AST:
        left = self.multiplicative()
        while self.at("op", "+") or self.at("op", "-"):
            op = self.take("op").value
            left = ("bin", op, left, self.multiplicative())
        return left

    def multiplicative(self) -> AST:
        left = self.unary()
        while any(self.at("op", o) for o in ("*", "/", "%")):
            op = self.take("op").value
            left = ("bin", op, left, self.unary())
        return left

    def unary(self) -> AST:
        if self.at("op", "!") or self.at("op", "-"):
            op = self.take("op").value
            return ("unary", op, self.unary())
        return self.postfix()

    def postfix(self) -> AST:
        node = self.primary()
        while True:
            if self.accept("op", "."):
                if self.at("ident"):
                    node = ("get", node, self.take("ident").value)
                elif self.at("num"):                      # list.0 legacy index
                    node = ("index", node, ("num", self.take("num").value))
                elif self.accept("op", "*"):
                    node = ("splat", node)
                else:
                    raise ParseError(f"bad attribute access in {self.src!r}")
            elif self.accept("op", "["):
                if self.accept("op", "*"):
                    node = ("splat", node)
                else:
                    node = ("index", node, self.expr())
                self.take("op", "]")
            else:
                return node

    def primary(self) -> AST:
        t = self.peek()
        if t is None:
            raise ParseError(f"unexpected end of expression in {self.src!r}")
        if t.kind == "num":
            self.i += 1
            return ("num", t.value)
        if t.kind == "str":
            self.i += 1
            return ("str", t.value)
        if t.kind == "ident":
            self.i += 1
            if t.value in ("true", "false"):
                return ("bool", t.value == "true")
            if t.value == "null":
                return ("null",)
            if self.at("op", "("):
                return self.call(t.value)
            return ("ident", t.value)
        if self.accept("op", "("):
            node = self.expr()
            self.take("op", ")")
            return node
        if self.at("op", "["):
            return self.list_or_for()
        if self.at("op", "{"):
            return self.object_or_for()
        raise ParseError(f"unexpected token {t.value!r} at {t.pos} in {self.src!r}")

    def call(self, name: str) -> AST:
        self.take("op", "(")
        args: list[AST] = []
        while not self.at("op", ")"):
            args.append(self.expr())
            self.accept("op", "...")
            if not self.accept("op", ","):
                break
        self.take("op", ")")
        return ("call", name, args)

    def list_or_for(self) -> AST:
        self.take("op", "[")
        if self.at("ident", "for"):
            return self.for_expr(closing="]")
        items: list[AST] = []
        while not self.at("op", "]"):
            items.append(self.expr())
            if not self.accept("op", ","):
                break
        self.take("op", "]")
        return ("list", items)

    def object_or_for(self) -> AST:
        self.take("op", "{")
        if self.at("ident", "for"):
            return self.for_expr(closing="}")
        pairs: list[tuple[AST, AST]] = []
        while not self.at("op", "}"):
            key = self.object_key()
            if not (self.accept("op", "=") or self.accept("op", ":")):
                raise ParseError(f"expected '=' after object key in {self.src!r}")
            pairs.append((key, self.expr()))
            self.accept("op", ",")
        self.take("op", "}")
        return ("object", pairs)

    def object_key(self) -> AST:
        if self.at("ident"):
            return ("str", [self.take("ident").value])
        if self.accept("op", "("):
            node = self.expr()
            self.take("op", ")")
            return node
        return self.primary()

    def for_expr(self, closing: str) -> AST:
        self.take("ident", "for")
        first = self.take("ident").value
        second = None
        if self.accept("op", ","):
            second = self.take("ident").value
        self.take("ident", "in")
        coll = self.expr()
        self.take("op", ":")
        key_var, value_var = (first, second) if second else (None, first)
        if closing == "]":
            value_expr = self.expr()
            cond = self.expr() if self.accept("ident", "if") else None
            self.take("op", "]")
            return ("for_list", key_var, value_var, coll, value_expr, cond)
        key_expr = self.expr()
        self.take("op", "=>")
        value_expr = self.expr()
        self.accept("op", "...")
        cond = self.expr() if self.accept("ident", "if") else None
        self.take("op", "}")
        return ("for_map", key_var, value_var, coll, key_expr, value_expr, cond)


def parse(src: str) -> AST:
    p = _Parser(tokenize(src), src)
    node = p.expr()
    if p.peek() is not None:
        raise ParseError(f"trailing tokens at {p.peek().pos} in {src!r}")
    return node


def parse_attribute(raw: Any) -> AST | None:
    """Turn a python-hcl2 attribute value into an AST, or None if it is already a literal.

    python-hcl2 conventions: literal strings arrive as '"text"' (quotes kept);
    expressions as '${...}'; strings with interpolation as '"a${b}c"'; bare
    words (e.g. `providers = { aws = aws }`) as the word itself.

    Memoised on the raw string: the same expression text (`"${each.value}"`,
    `"${var.region}"`) is parsed once per process. ASTs are read-only tuples,
    so callers share them safely.
    """
    if not isinstance(raw, str):
        return None
    return _parse_attribute_str(raw)


@functools.lru_cache(maxsize=4096)
def _parse_attribute_str(raw: str) -> AST | None:
    if raw.startswith("${"):
        inner, end = _scan_interpolation(raw, 2)
        if end == len(raw):
            return parse(inner)
        raise ParseError(f"unexpected text after expression in {raw!r}")
    if raw.startswith('"') and raw.endswith('"'):
        parts, end = _scan_string(raw, 0)
        if end == len(raw):
            return ("str", parts)
    return None
