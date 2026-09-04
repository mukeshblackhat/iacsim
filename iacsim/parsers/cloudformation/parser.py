"""CloudFormation template (JSON/YAML) → RawResources.                       [M5]

This is what CDK (`cdk synth` → cdk.out/*.template.json), SAM and the
Serverless Framework emit, so one adapter covers all three.

  1. Load the template (JSON, or YAML with the !Ref / !GetAtt short tags).
  2. Each entry in `Resources` → RawResource(address=LogicalId, type="AWS::...").
  3. References: `Ref`, `Fn::GetAtt`, `Fn::Sub` `${LogicalId}`, `DependsOn`.
  4. Step Functions `DefinitionString` is kept verbatim in attrs so the
     step_functions inference rule can read the real state machine.

Test fixture: examples/foosh-cfn must produce the same InfraGraph as
examples/foosh-serverless (the hand-written Terraform twin).
"""

from __future__ import annotations

from pathlib import Path

from iacsim.core.interfaces import PARSERS, Parser
from iacsim.core.models import RawResources

TEMPLATE_MARKERS = ("AWSTemplateFormatVersion", "Resources")


@PARSERS.register("cloudformation")
class CloudFormationParser(Parser):
    @classmethod
    def detect(cls, path: Path) -> bool:
        candidates = [path] if path.is_file() else list(path.glob("*.template.json")) + list(path.glob("template.y*ml"))
        for file in candidates:
            try:
                head = file.read_text()[:4000]
            except OSError:
                continue
            if any(marker in head for marker in TEMPLATE_MARKERS):
                return True
        return False

    def parse(self, path: Path) -> RawResources:
        raise NotImplementedError("M5: CloudFormation parser — see module docstring for the plan")
