"""Parser unit tests on tiny in-memory Terraform projects (no examples needed)."""

from pathlib import Path

from iacsim.parsers.terraform.parser import TerraformParser


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def _by_address(raw):
    return {r.address: r for r in raw.resources}


def test_for_each_expands_addresses_and_binds_each(tmp_path):
    root = _project(tmp_path, {"main.tf": '''
        locals { zones = ["a", "b"] }
        resource "aws_subnet" "s" {
          for_each          = toset(local.zones)
          availability_zone = "us-east-1${each.value}"
        }
        resource "aws_instance" "i" {
          for_each  = aws_subnet.s
          subnet_id = each.value.id
        }
    '''})
    raw = TerraformParser().parse(root)
    by = _by_address(raw)
    assert set(by) == {'aws_subnet.s["a"]', 'aws_subnet.s["b"]', 'aws_instance.i["a"]', 'aws_instance.i["b"]'}
    assert by['aws_subnet.s["b"]'].attrs["availability_zone"] == "us-east-1b"
    assert by['aws_instance.i["a"]'].attrs["subnet_id"] == '${aws_subnet.s["a"].id}'
    assert by['aws_instance.i["a"]'].references == ['aws_subnet.s["a"]']
    assert raw.warnings == []


def test_count_and_conditional(tmp_path):
    root = _project(tmp_path, {"main.tf": '''
        variable "enabled" { default = true }
        resource "aws_sqs_queue" "q" {
          count = var.enabled ? 2 : 0
          name  = "q-${count.index}"
        }
    '''})
    by = _by_address(TerraformParser().parse(root))
    assert set(by) == {"aws_sqs_queue.q[0]", "aws_sqs_queue.q[1]"}
    assert by["aws_sqs_queue.q[1]"].attrs["name"] == "q-1"


def test_module_addresses_outputs_and_provider_alias(tmp_path):
    root = _project(tmp_path, {
        "main.tf": '''
            provider "aws" { region = "us-east-1" }
            provider "aws" { alias = "eu"  region = "eu-west-1" }
            module "db" {
              source    = "./mod"
              providers = { aws = aws.eu }
              name      = "x"
            }
            resource "aws_instance" "web" {
              user_data = templatefile("${path.module}/u.sh", { host = module.db.address })
            }
        ''',
        "mod/main.tf": '''
            variable "name" {}
            resource "aws_db_instance" "this" { identifier = "${var.name}-db" }
            output "address" { value = aws_db_instance.this.address }
        ''',
    })
    raw = TerraformParser().parse(root)
    by = _by_address(raw)
    db = by["module.db.aws_db_instance.this"]
    assert db.region == "eu-west-1" and db.attrs["identifier"] == "x-db"
    web = by["aws_instance.web"]
    assert web.region == "us-east-1"
    tf = web.attrs["user_data"]["__templatefile__"]
    assert tf["path"].endswith("/u.sh")
    assert tf["vars"]["host"] == "${module.db.aws_db_instance.this.address}"
    assert web.references == ["module.db.aws_db_instance.this"]


def test_module_for_each_and_structured_jsonencode(tmp_path):
    root = _project(tmp_path, {
        "main.tf": '''
            locals { tables = { a = { key = "id" }, b = { key = "pk" } } }
            module "t" {
              source   = "./tbl"
              for_each = local.tables
              name     = "${title(each.key)}Tbl"
              hash_key = each.value.key
            }
            resource "aws_iam_role_policy" "p" {
              role   = "r"
              policy = jsonencode({ Statement = [{ Action = ["dynamodb:GetItem"], Resource = [for t in module.t : t.arn] }] })
            }
        ''',
        "tbl/main.tf": '''
            variable "name" {}
            variable "hash_key" {}
            resource "aws_dynamodb_table" "this" { name = var.name  hash_key = var.hash_key }
            output "arn" { value = aws_dynamodb_table.this.arn }
        ''',
    })
    by = _by_address(TerraformParser().parse(root))
    assert by['module.t["a"].aws_dynamodb_table.this'].attrs == {"name": "ATbl", "hash_key": "id"}
    policy = by["aws_iam_role_policy.p"].attrs["policy"]
    assert policy["Statement"][0]["Resource"] == [
        '${module.t["a"].aws_dynamodb_table.this.arn}', '${module.t["b"].aws_dynamodb_table.this.arn}']


def test_unresolvable_is_a_warning_not_a_crash(tmp_path):
    root = _project(tmp_path, {"main.tf": '''
        resource "aws_subnet" "s" {
          cidr_block = cidrsubnet("10.0.0.0/16", 8, 1)
          vpc_id     = data.aws_vpc.main.id
          nope       = mystery(1)
        }
        module "remote" { source = "terraform-aws-modules/vpc/aws" }
    '''})
    raw = TerraformParser().parse(root)
    s = raw.resources[0]
    assert s.attrs["cidr_block"].startswith("${cidrsubnet(")
    assert s.attrs["vpc_id"] == "${data.aws_vpc.main.id}"
    assert s.attrs["nope"] == "${mystery(1)}"                    # raw text kept
    assert any("mystery" in w for w in raw.warnings)
    assert any("remote source" in w for w in raw.warnings)


def test_data_sources_warn_once_per_module_and_stay_unresolved(tmp_path):
    root = _project(tmp_path, {"main.tf": '''
        data "aws_caller_identity" "me" {}
        data "aws_region" "here" {}
        resource "aws_s3_bucket" "b" {
          bucket = "x-${data.aws_caller_identity.me.account_id}-${data.aws_region.here.name}"
        }
    '''})
    raw = TerraformParser().parse(root)
    data_warnings = [w for w in raw.warnings if "data.* sources are not evaluated" in w]
    assert len(data_warnings) == 1 and data_warnings[0].startswith("root module:")
    assert "${" in _by_address(raw)["aws_s3_bucket.b"].attrs["bucket"]      # placeholder kept, no crash


def test_duplicate_resource_labels_warn_and_first_wins(tmp_path):
    root = _project(tmp_path, {
        "a.tf": 'resource "aws_sqs_queue" "q" { name = "first" }',
        "b.tf": 'resource "aws_sqs_queue" "q" { name = "second" }',
    })
    raw = TerraformParser().parse(root)
    assert [r.attrs["name"] for r in raw.resources] == ["first"]
    assert any(w.startswith("duplicate resource aws_sqs_queue.q in") and w.endswith("first wins") for w in raw.warnings)
