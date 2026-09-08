output "url" {
  value = "https://${var.domain}"
}

output "ip_address" {
  value = google_compute_global_address.web.address
}
