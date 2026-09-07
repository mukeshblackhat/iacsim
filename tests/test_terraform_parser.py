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
              policy = jsonencode({ Statement = [{ Action = ["dynamodb:GetItem"],
                                                   Resource = [for t in module.t : t.arn] }] })
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


# ---------------------------------------------------------------- WP5: real-world shapes

FIXTURES = Path(__file__).parent / "fixtures"


def _parse(name: str, **options):
    return _by_address(TerraformParser(**options).parse(FIXTURES / name))


def test_tf_json_is_read_like_hcl():
    raws = _parse("tf-json")
    fn = raws["aws_lambda_function.api"]
    assert fn.attrs["function_name"] == "api-orders"
    assert fn.attrs["environment"]["variables"]["TABLE"] == "${aws_dynamodb_table.orders.name}"
    assert fn.attrs["environment"]["variables"]["GREETING"] == 'hello "world"'
    assert "aws_dynamodb_table.orders" in fn.references
    assert raws["aws_dynamodb_table.orders"].attrs["name"] == "orders-table"
    assert TerraformParser.detect(FIXTURES / "tf-json")


def test_tfvars_override_defaults_in_terraform_order():
    web = _parse("tfvars")["aws_instance.web"]
    assert web.attrs["instance_type"] == "large"          # a.auto.tfvars beats terraform.tfvars beats the default
    assert web.attrs["tags"]["extra"] == "from-terraform-tfvars"
    assert web.region == "eu-west-1"                       # z.auto.tfvars.json


def test_terraform_workspace_defaults_and_can_be_set():
    assert _parse("workspace")["aws_s3_bucket.b"].attrs["bucket"] == "logs-default-111"
    assert _parse("workspace", workspace="prod")["aws_s3_bucket.b"].attrs["bucket"] == "logs-prod-999"


def test_nested_dynamic_blocks_expand_with_iterator():
    rules = _parse("nested-dynamic")["aws_lb_listener.l"].attrs["rule"]
    assert [r["port"] for r in rules] == [80, 443]
    assert [[p["value"] for p in r["path"]] for r in rules] == [["/a", "/b"], ["/c"]]


def test_module_count_and_for_each_over_objects():
    raws = _parse("module-count")
    assert raws["module.worker[0].aws_sqs_queue.q"].attrs["name"] == "q-0"
    assert raws["module.worker[1].aws_sqs_queue.q"].attrs["name"] == "q-1"
    assert raws['aws_lambda_function.fn["beta"]'].attrs["memory_size"] == 512


def test_registry_modules_are_followed_through_modules_json():
    result = TerraformParser().parse(FIXTURES / "installed-modules")
    raws = _by_address(result)
    assert raws["module.vpc.aws_vpc.this"].attrs["tags"]["Name"] == "net"
    remote = [w for w in result.warnings if "remote source" in w]
    assert len(remote) == 1 and "module 'missing'" in remote[0] and "terraform init" in remote[0]


def test_region_fallback_option_applies_when_provider_region_is_unresolved(tmp_path):
    root = _project(tmp_path, {"main.tf": 'variable "r" {}\nprovider "aws" { region = var.r }\n'
                                          'resource "aws_s3_bucket" "b" { bucket = "x" }\n'})
    without = TerraformParser().parse(root)
    assert _by_address(without)["aws_s3_bucket.b"].region is None
    assert any("pass --region" in w for w in without.warnings)
    with_region = TerraformParser(region="ap-south-1").parse(root)
    assert _by_address(with_region)["aws_s3_bucket.b"].region == "ap-south-1"
    assert not any("pass --region" in w for w in with_region.warnings)


def test_provisioner_and_connection_blocks_are_skipped(tmp_path):
    root = _project(tmp_path, {"main.tf": 'provider "aws" { region = "us-east-1" }\nresource "aws_instance" "w" {\n'
                                          '  instance_type = "t3.micro"\n  connection { host = self.public_ip }\n'
                                          '  provisioner "remote-exec" { inline = ["echo hi"] }\n}\n'})
    result = TerraformParser().parse(root)
    assert result.warnings == []
    assert "connection" not in _by_address(result)["aws_instance.w"].attrs


# ---------------------------------------------------------------- WP1: any provider, not just aws

def test_gcp_provider_blocks_reach_google_resources_and_aws_keeps_its_own():
    result = TerraformParser().parse(FIXTURES / "gcp")
    raws = _by_address(result)
    assert result.warnings == []
    assert raws["google_cloud_run_v2_service.api"].region == "us-central1"        # provider "google"
    assert raws["google_compute_instance.beta"].region == "europe-west1"          # provider = google-beta (bare)
    assert raws["google_sql_database_instance.eu"].region == "europe-west4"       # provider = google-beta.eu
    assert raws["aws_s3_bucket.assets"].region == "us-east-1"                     # mixed dir: aws is untouched


def test_provider_zone_rides_along_in_attrs_when_the_provider_declares_one():
    raws = _parse("gcp")
    assert raws["google_cloud_run_v2_service.api"].attrs["_provider_zone"] == "us-central1-a"
    assert "_provider_zone" not in raws["google_compute_instance.beta"].attrs     # google-beta sets no zone
    assert "_provider_zone" not in raws["aws_s3_bucket.assets"].attrs


def test_hyphenated_providers_map_into_child_modules():
    raws = _parse("gcp")
    assert raws["module.svc.google_cloud_run_v2_service.this"].region == "us-central1"   # google = google
    assert raws["module.svc.google_cloud_run_v2_service.this"].attrs["_provider_zone"] == "us-central1-a"
    assert raws["module.svc.google_compute_instance.this"].region == "europe-west4"      # google-beta = google-beta.eu


def test_region_fallback_option_applies_to_google_providers_too(tmp_path):
    root = _project(tmp_path, {"main.tf": 'variable "r" {}\nprovider "google" { region = var.r }\n'
                                          'resource "google_storage_bucket" "b" { name = "x" }\n'})
    without = TerraformParser().parse(root)
    assert _by_address(without)["google_storage_bucket.b"].region is None
    assert any("provider google: region not resolved" in w and "pass --region" in w for w in without.warnings)
    with_region = TerraformParser(region="europe-west2").parse(root)
    assert _by_address(with_region)["google_storage_bucket.b"].region == "europe-west2"
    assert not any("pass --region" in w for w in with_region.warnings)


def test_region_fallback_applies_when_a_provider_block_is_absent_entirely(tmp_path):
    root = _project(tmp_path, {"main.tf": 'resource "google_cloud_run_v2_service" "s" { name = "s" }\n'
                                          'resource "aws_s3_bucket" "b" { bucket = "x" }\n'})
    raws = _by_address(TerraformParser(region="asia-south1").parse(root))
    assert raws["google_cloud_run_v2_service.s"].region == "asia-south1"
    assert raws["aws_s3_bucket.b"].region == "asia-south1"
