provider "google" {
  project = "demo"
  region  = "us-central1"
  zone    = "us-central1-a"
}

provider "google-beta" {
  region = "europe-west1"
}

provider "google-beta" {
  alias  = "eu"
  region = "europe-west4"
}

provider "aws" {
  region = "us-east-1"
}

resource "google_cloud_run_v2_service" "api" {
  name = "api"
}

resource "google_compute_instance" "beta" {
  provider     = google-beta
  name         = "beta"
  machine_type = "e2-small"
}

resource "google_sql_database_instance" "eu" {
  provider = google-beta.eu
  name     = "eu-db"
}

resource "aws_s3_bucket" "assets" {
  bucket = "assets"
}

module "svc" {
  source    = "./mod"
  providers = { google = google, google-beta = google-beta.eu }
  name      = "worker"
}
