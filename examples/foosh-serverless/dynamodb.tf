# Ten tables, exactly as in unified_workflow_stack.py::create_dynamodb_tables.
# Keys and GSIs are kept because the api Lambda queries the GSIs. Table names
# follow config/environments/staging.json — two of them do not follow the
# TitleCase(key) pattern, hence `name_override`.

locals {
  tables = {
    workflows = {
      name_override = "AsyncWorkflows${title(local.suffix)}"
      hash_key   = "id"
      attributes = { id = "S", workspace_id = "S", created_at = "S" }
      gsis       = { "workspace-created-at-index" = { hash_key = "workspace_id", range_key = "created_at" } }
    }
    executions = {
      name_override = "WorkflowExecutions${title(local.suffix)}SF"
      hash_key   = "id"
      attributes = { id = "S", workflow_id = "S", created_at = "S" }
      gsis       = { "workflow-id-index" = { hash_key = "workflow_id", range_key = "created_at" } }
    }
    checkout_sessions = {
      hash_key   = "session_id"
      attributes = { session_id = "S" }
      ttl        = "ttl"
    }
    credit_transactions = {
      hash_key   = "transaction_id"
      attributes = { transaction_id = "S", user_id = "S", created_at = "S" }
      gsis       = { "user-id-index" = { hash_key = "user_id", range_key = "created_at" } }
    }
    workspaces = {
      hash_key   = "id"
      attributes = { id = "S" }
    }
    workspace_members = {
      hash_key   = "workspace_id"
      range_key  = "user_id"
      attributes = { workspace_id = "S", user_id = "S" }
      gsis       = { "user-workspaces-index" = { hash_key = "user_id", range_key = "workspace_id" } }
    }
    public_workflow_shares = {
      hash_key   = "share_id"
      attributes = { share_id = "S", is_published = "S", published_at = "S" }
      gsis       = { "published-workflows-index" = { hash_key = "is_published", range_key = "published_at" } }
    }
    payment_idempotency = {
      hash_key   = "payment_id"
      attributes = { payment_id = "S" }
      ttl        = "ttl"
    }
    published_apps = {
      hash_key   = "app_id"
      range_key  = "version"
      attributes = { app_id = "S", version = "S", workspace_id = "S", created_at = "S", is_latest_version = "S", status = "S", workflow_id = "S", app_name = "S" }
      gsis = {
        "workspace-created-at-index"  = { hash_key = "workspace_id", range_key = "created_at" }
        "app-id-latest-version-index" = { hash_key = "app_id", range_key = "is_latest_version" }
        "workspace-status-index"      = { hash_key = "workspace_id", range_key = "status" }
        "workflow-id-index"           = { hash_key = "workflow_id" }
        "workspace-app-name-index"    = { hash_key = "workspace_id", range_key = "app_name" }
      }
    }
    app_executions = {
      hash_key   = "execution_id"
      attributes = { execution_id = "S", app_id = "S", created_at = "S", workspace_id = "S", version = "S" }
      gsis = {
        "app-id-created-at-index"         = { hash_key = "app_id", range_key = "created_at" }
        "workspace-id-created-at-index"   = { hash_key = "workspace_id", range_key = "created_at" }
        "app-id-version-created-at-index" = { hash_key = "app_id", range_key = "version" }
      }
    }
  }
}

module "table" {
  source   = "../modules/dynamodb_table"
  for_each = local.tables

  name          = try(each.value.name_override, "${replace(title(replace(each.key, "_", " ")), " ", "")}${title(local.suffix)}")
  hash_key      = each.value.hash_key
  range_key     = try(each.value.range_key, null)
  attributes    = each.value.attributes
  gsis          = try(each.value.gsis, {})
  ttl_attribute = try(each.value.ttl, null)
}

# Env vars every Lambda receives (Foosh's ConfigLoader.get_environment_variables)
locals {
  table_env = {
    WORKFLOWS_TABLE           = module.table["workflows"].name
    EXECUTIONS_TABLE          = module.table["executions"].name
    CREDIT_TRANSACTIONS_TABLE = module.table["credit_transactions"].name
    WORKSPACES_TABLE          = module.table["workspaces"].name
    WORKSPACE_MEMBERS_TABLE   = module.table["workspace_members"].name
    PUBLIC_SHARES_TABLE       = module.table["public_workflow_shares"].name
    CHECKOUT_SESSIONS_TABLE   = module.table["checkout_sessions"].name
    PAYMENT_IDEMPOTENCY_TABLE = module.table["payment_idempotency"].name
    PUBLISHED_APPS_TABLE      = module.table["published_apps"].name
    APP_EXECUTIONS_TABLE      = module.table["app_executions"].name
  }

  # grant_permissions(): every Lambda may hit every table and every GSI
  dynamodb_policy = {
    Effect = "Allow"
    Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
              "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem"]
    Resource = flatten([for t in module.table : [t.arn, "${t.arn}/index/*"]])
  }
}
