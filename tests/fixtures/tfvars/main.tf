variable "size" {
  default = "small"
}
variable "region" {
  default = "us-east-1"
}
variable "extra" {
  default = "d"
}
provider "aws" {
  region = var.region
}
resource "aws_instance" "web" {
  instance_type = var.size
  tags          = { extra = var.extra }
}
