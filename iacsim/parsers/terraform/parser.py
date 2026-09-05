"""Terraform (.tf, HCL2) → RawResources.                                   [M1 ✅]

Pipeline inside this package:

    hcl2.load()  →  loader.ModuleInstance (blocks, lazy scopes, module tree)
                 →  hcl_expr.parse() + evaluator.evaluate() per attribute
                 →  RawResource per expanded instance, with "${address.attr}" placeholders

Handled: multiple .tf files; `variable` defaults and module inputs; `locals`;
local `module` sources (recursively) including `for_each` / `count` on modules;
`for_each` / `count` on resources; `dynamic` blocks; `templatefile()` (kept as
{"__templatefile__": {path, vars}}); `jsonencode()` (kept structured);
provider aliases via `provider = aws.x` and `providers = { aws = aws.x }` →
RawResource.region.

Not handled (warning, never a crash): remote module sources, `data` sources
(one warning per module; the referencing attribute keeps a placeholder),
duplicate `resource` labels (first wins), `%{ }` template directives,
provider-computed functions (cidrsubnet, file, …), splat on unresolved
values, count/for_each that depend on unresolved values.

Addresses follow Terraform: module.<name>[<key>].<type>.<name>[<key>].
"""

from __future__ import annotations

from pathlib import Path

from iacsim.core.interfaces import PARSERS, Parser
from iacsim.core.models import RawResources
from iacsim.parsers.terraform.loader import ModuleInstance, Warnings


@PARSERS.register("terraform")
class TerraformParser(Parser):
    @classmethod
    def detect(cls, path: Path) -> bool:
        return path.is_dir() and any(path.glob("*.tf"))

    def parse(self, path: Path) -> RawResources:
        root_dir = Path(path).resolve()
        warnings = Warnings()
        root = ModuleInstance(root_dir, root_dir, warnings)
        resources = root.all_resources()
        return RawResources(resources=resources, format="terraform", root_path=str(root_dir),
                            warnings=warnings.items)
