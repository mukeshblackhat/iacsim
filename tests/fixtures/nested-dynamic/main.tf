locals {
  listeners = { http = { port = 80, paths = ["/a", "/b"] }, https = { port = 443, paths = ["/c"] } }
}
provider "aws" {
  region = "us-east-1"
}
resource "aws_lb" "l" {
  name = "l"
}
resource "aws_lb_listener" "l" {
  load_balancer_arn = aws_lb.l.arn
  dynamic "rule" {
    for_each = local.listeners
    iterator = each_listener
    content {
      port = each_listener.value.port
      dynamic "path" {
        for_each = each_listener.value.paths
        content {
          value = path.value
        }
      }
    }
  }
}
