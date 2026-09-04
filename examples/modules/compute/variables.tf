variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnets_by_az" {
  type        = map(string)
  description = "az => subnet id; one instance is created per entry"
}

variable "ami_id" {
  type = string
}

variable "instance_type" {
  type    = string
  default = "t3.medium"
}

variable "port" {
  type    = number
  default = 8080
}

variable "alb_security_group_id" {
  type = string
}

variable "target_group_arn" {
  type = string
}

variable "db_host" {
  type = string
}

variable "db_name" {
  type = string
}
