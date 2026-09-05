module "orders" {
  source     = "../modules/dynamodb_table"
  name       = "${local.name}-orders"
  hash_key   = "order_id"
  attributes = { order_id = "S" }
}
