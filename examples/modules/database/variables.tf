variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "availability_zone" {
  type = string
}

variable "instance_class" {
  type    = string
  default = "db.t3.medium"
}

variable "db_name" {
  type    = string
  default = "app"
}

variable "password" {
  type      = string
  sensitive = true
}

variable "allowed_security_group_ids" {
  type = list(string)
}
