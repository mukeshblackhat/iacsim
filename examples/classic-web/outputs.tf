output "url" {
  value = "https://${module.load_balancer.dns_name}"
}
