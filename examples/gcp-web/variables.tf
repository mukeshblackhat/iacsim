variable "project" {
  type    = string
  default = "gcp-web-demo"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "domain" {
  type    = string
  default = "web.example.com"
}
