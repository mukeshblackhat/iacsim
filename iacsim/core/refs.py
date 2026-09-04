"""The reference convention shared by parsers and inference rules.

Parsers cannot know the runtime value of `aws_vpc.this.id`, so when a resource
attribute mentions another resource, the evaluated attribute holds a
placeholder string:

    "${<address>.<attr path>}"            e.g. "${module.database.aws_db_instance.this.address}"
    "arn:...:${module.api.aws_lambda_function.this.arn}/invocations"   (embedded)

An *address* is Terraform's own form — `module.<name>[<key>].` prefixes followed
by `<type>.<name>[<key>]`; the attribute path is whatever follows. This module
turns those placeholders back into (address, attr) pairs so inference rules can
ask "which resources does this attribute mention?" without importing a parser.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


@dataclass(frozen=True)
class ResourceRef:
    address: str
    attr: str          # "" when the placeholder names the resource itself


def placeholder(address: str, attr: str = "") -> str:
    return "${" + address + ("." + attr if attr else "") + "}"


def split_address(text: str) -> ResourceRef:
    """'module.a["k"].aws_x.y["z"].attr.sub' → (module.a["k"].aws_x.y["z"], "attr.sub").

    Segments are dot-separated but a dot inside [...] belongs to the key.
    Address grammar: (module NAME)* TYPE NAME, each NAME optionally indexed.
    """
    segments = _split_dots(text)
    i = 0
    while i + 1 < len(segments) and _bare(segments[i]) == "module":
        i += 2
    address_end = min(i + 2, len(segments))
    address = ".".join(segments[:address_end])
    attr = ".".join(segments[address_end:])
    return ResourceRef(address, attr)


def references_in(value: Any) -> list[ResourceRef]:
    """Every placeholder reachable inside a (possibly nested) attribute value, in order."""
    return list(_walk(value))


def addresses_in(value: Any) -> list[str]:
    seen: dict[str, None] = {}
    for ref in references_in(value):
        seen.setdefault(ref.address)
    return list(seen)


# ------------------------------------------------------------------ helpers

def _walk(value: Any) -> Iterator[ResourceRef]:
    if isinstance(value, str):
        for m in _PLACEHOLDER.finditer(value):
            inner = m.group(1)
            if _looks_like_address(inner):
                yield split_address(inner)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(k)
            yield from _walk(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk(v)


def _looks_like_address(text: str) -> bool:
    first = _bare(_split_dots(text)[0])
    return first == "module" or "_" in first      # provider types are like aws_x


def _bare(segment: str) -> str:
    return segment.split("[", 1)[0]


def _split_dots(text: str) -> list[str]:
    out, buf, depth = [], [], 0
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "." and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out
