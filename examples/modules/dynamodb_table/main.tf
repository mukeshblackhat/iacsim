# dynamodb_table — on-demand table with optional sort key and GSIs.

resource "aws_dynamodb_table" "this" {
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = var.hash_key
  range_key    = var.range_key

  dynamic "attribute" {
    for_each = var.attributes
    content {
      name = attribute.key
      type = attribute.value
    }
  }

  dynamic "global_secondary_index" {
    for_each = var.gsis
    content {
      name            = global_secondary_index.key
      hash_key        = global_secondary_index.value.hash_key
      range_key       = try(global_secondary_index.value.range_key, null)
      projection_type = "ALL"
    }
  }

  dynamic "ttl" {
    for_each = var.ttl_attribute == null ? [] : [1]
    content {
      attribute_name = var.ttl_attribute
      enabled        = true
    }
  }

  point_in_time_recovery { enabled = true }
}
