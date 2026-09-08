# gcp-web — the "good" baseline; the GCP twin of ../classic-web.
#
#   internet → global forwarding rule → HTTPS proxy → URL map → backend service
#            → serverless NEG → Cloud Run (us-central1) → Cloud SQL Postgres (us-central1)
#                                                       → Memorystore Redis (us-central1)
#
# Everything in one region. Compare with ../gcp-web-bad, which is the same
# stack with the database moved to another region.
#
# The five load-balancer resources are one Google Front End drawn as five
# nodes: iacsim prices every hop between them at 0 ms and charges the internet
# → forwarding-rule hop once (docs/gcp/01-DECISIONS.md G3, G9).
#
# No iacsim.yaml: the default `provider: auto` sees google_* resources and picks
# the gcp normaliser (examples/iacsim.yaml documents the key).

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
  zone    = "${var.region}-a"
}

locals {
  name = "gcp-web"
}

# ---------------------------------------------------------------- network
# One VPC. Cloud Run egresses into it; Cloud SQL and Memorystore get private
# addresses on it through private services access.

resource "google_compute_network" "vpc" {
  name                    = local.name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "app" {
  name          = "${local.name}-app"
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.0.0.0/24"
}

resource "google_compute_global_address" "psa" {
  name          = "${local.name}-psa"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa.name]
}

# ---------------------------------------------------------------- identity
# The service account Cloud Run runs as, and the one grant that lets it open a
# Cloud SQL connection — the evidence the gcp_iam_binding rule reads.

resource "google_service_account" "web" {
  account_id   = "${local.name}-web"
  display_name = "gcp-web Cloud Run service"
}

resource "google_project_iam_member" "web_cloudsql_client" {
  project = var.project
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.web.email}"
}

# ---------------------------------------------------------------- compute

resource "google_cloud_run_v2_service" "web" {
  name     = "${local.name}-web"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.web.email

    scaling {
      max_instance_count = 10
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.app.id
      }
    }

    containers {
      image = var.image

      # The app reaches Postgres through the Cloud SQL Auth Proxy socket that
      # the `volumes` block below mounts; both name the instance.
      env {
        name  = "DB_INSTANCE"
        value = google_sql_database_instance.db.connection_name
      }
      env {
        name  = "DB_NAME"
        value = google_sql_database.app.name
      }
      env {
        name  = "REDIS_HOST"
        value = google_redis_instance.cache.host
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.db.connection_name]
      }
    }
  }
}

# ---------------------------------------------------------------- datastores

resource "google_sql_database_instance" "db" {
  name             = "${local.name}-db"
  region           = var.region
  database_version = "POSTGRES_16"
  depends_on       = [google_service_networking_connection.psa]

  settings {
    tier              = "db-custom-2-7680"
    availability_type = "REGIONAL" # HA across zones: no single zone to place it in

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }
  }
}

resource "google_sql_database" "app" {
  name     = "app"
  instance = google_sql_database_instance.db.name
}

resource "google_redis_instance" "cache" {
  name               = "${local.name}-cache"
  region             = var.region
  tier               = "BASIC"
  memory_size_gb     = 1
  authorized_network = google_compute_network.vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
  depends_on         = [google_service_networking_connection.psa]
}

# ---------------------------------------------------------------- load balancer
# The global external HTTPS load balancer, resource by resource, head last.

resource "google_compute_region_network_endpoint_group" "web" {
  name                  = "${local.name}-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.web.name
  }
}

resource "google_compute_backend_service" "web" {
  name                  = "${local.name}-backend"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.web.id
  }
}

resource "google_compute_url_map" "web" {
  name            = local.name
  default_service = google_compute_backend_service.web.id
}

resource "google_compute_managed_ssl_certificate" "web" {
  name = local.name

  managed {
    domains = [var.domain]
  }
}

resource "google_compute_target_https_proxy" "web" {
  name             = local.name
  url_map          = google_compute_url_map.web.id
  ssl_certificates = [google_compute_managed_ssl_certificate.web.id]
}

resource "google_compute_global_address" "web" {
  name = "${local.name}-ip"
}

resource "google_compute_global_forwarding_rule" "web" {
  name                  = "${local.name}-https"
  ip_protocol           = "TCP"
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_https_proxy.web.id
  ip_address            = google_compute_global_address.web.id
}
