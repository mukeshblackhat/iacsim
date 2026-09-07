variable "name" {}

resource "google_cloud_run_v2_service" "this" {
  name = var.name
}

resource "google_compute_instance" "this" {
  provider = google-beta
  name     = "${var.name}-vm"
}
