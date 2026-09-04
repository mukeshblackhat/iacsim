variable "name" {
  type = string
}

variable "cidr_block" {
  type    = string
  default = "10.0.0.0/16"
}

variable "availability_zones" {
  type        = list(string)
  description = "AZs to spread subnets across, e.g. [\"us-east-1a\", \"us-east-1b\"]"
}
