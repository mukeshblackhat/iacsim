variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "target_port" {
  type    = number
  default = 8080
}

variable "certificate_arn" {
  type = string
}
