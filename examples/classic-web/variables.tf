variable "region" {
  type    = string
  default = "us-east-1"
}

variable "ami_id" {
  type    = string
  default = "ami-0c02fb55956c7d316" # Amazon Linux 2023, us-east-1
}

variable "certificate_arn" {
  type    = string
  default = "arn:aws:acm:us-east-1:123456789012:certificate/example"
}

variable "db_password" {
  type      = string
  sensitive = true
  default   = "change-me"
}
