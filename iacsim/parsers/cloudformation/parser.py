"""CloudFormation template (JSON/YAML) → RawResources.                    [M5 ✅]

This is what CDK (`cdk synth` → cdk.out/*.template.json), SAM and the
Serverless Framework emit, so one adapter covers all three.

Pipeline inside this package:

    template.load_template()      JSON, or YAML with !Ref / !GetAtt / !Sub … short tags
      → intrinsics.resolve()      Ref / GetAtt / Join / Sub / Select / If … → "${type.LogicalId.attr}"
      → canonical.*               AWS::X::Y → aws_x_y, PascalCase → snake_case, per-type aliases,
                                  synthetic integration / attachment resources,
                                  literal env-var names → placeholders
      → RawResource per logical id, address = "<terraform type>.<LogicalId>"

Because addresses, types and attribute names come out Terraform-shaped, the
AWS normaliser and all inference rules run unchanged. Step Functions
`DefinitionString` (a Fn::Join of ASL fragments and Lambda ARNs) is
reassembled into one JSON string whose ARNs are placeholders, which is
exactly what the step_functions rule parses.

Region: `parsers.cloudformation.region` in iacsim.yaml / `--region`, else the
first region literal found in the template (CDK bakes it into ARNs), else
us-east-1. Parameters use their Default. Conditions are not evaluated
(Fn::If takes the true branch, with a warning).

Correctness check: `iacsim diff examples/foosh-serverless examples/foosh-cfn
--align-by label` — the real Foosh template vs its hand-written Terraform twin.
"""

from __future__ import annotations

from pathlib import Path

from iacsim.core.interfaces import PARSERS, Parser
from iacsim.core.models import RawResource, RawResources
from iacsim.core.refs import addresses_in
from iacsim.parsers.cloudformation.canonical import (
    canonical_attrs,
    canonical_type,
    resolve_physical_names,
    synthetic_resources,
)
from iacsim.parsers.cloudformation.intrinsics import Context, resolve
from iacsim.parsers.cloudformation.template import (
    DEFAULT_REGION,
    guess_region,
    load_template,
    stack_name,
    template_files,
)


@PARSERS.register("cloudformation")
class CloudFormationParser(Parser):
    @classmethod
    def detect(cls, path: Path) -> bool:
        return bool(template_files(path))

    def parse(self, path: Path) -> RawResources:
        path = Path(path)
        files = template_files(path)
        if not files:
            raise ValueError(f"no CloudFormation template found at {path}")

        warnings: list[str] = []
        resources: list[RawResource] = []
        seen: dict[str, str] = {}
        for file in files:
            for raw in self._parse_file(file, warnings):
                if raw.address in seen:
                    warnings.append(f"{file.name}: {raw.address} also defined in {seen[raw.address]}; keeping the first")
                    continue
                seen[raw.address] = file.name
                resources.append(raw)

        resolve_physical_names(resources)
        for raw in resources:
            raw.references = addresses_in(raw.attrs)
        return RawResources(resources=resources, format="cloudformation", root_path=str(path), warnings=warnings)

    def _parse_file(self, file: Path, warnings: list[str]) -> list[RawResource]:
        doc = load_template(file)
        declared = doc.get("Resources") or {}
        region = self.options.get("region") or guess_region(file.read_text()) or DEFAULT_REGION
        ctx = Context(
            types={lid: canonical_type(res.get("Type", "")) for lid, res in declared.items()},
            parameters={name: spec.get("Default") for name, spec in (doc.get("Parameters") or {}).items()},
            region=region, stack_name=stack_name(file), warnings=warnings,
        )

        out: list[RawResource] = []
        for logical_id, res in declared.items():
            ctype = ctx.types[logical_id]
            attrs = canonical_attrs(ctype, resolve(res.get("Properties") or {}, ctx))
            attrs["cfn_type"] = res.get("Type")
            depends = res.get("DependsOn") or []
            attrs["depends_on"] = [ctx.address(d) for d in ([depends] if isinstance(depends, str) else depends) if d in ctx.types]
            raw = RawResource(address=ctx.address(logical_id), type=ctype, attrs=attrs,
                              region=region, source_file=str(file))
            out.append(raw)
            out.extend(synthetic_resources(raw))
        return out
