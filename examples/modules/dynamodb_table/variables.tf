variable "name" { type = string }
variable "hash_key" { type = string }
variable "range_key" {
  type    = string
  default = null
}
variable "attributes" {
  type        = map(string)
  description = "attribute name => type (S/N/B); must cover every key and GSI key"
}
variable "gsis" {
  type    = map(object({ hash_key = string, range_key = optional(string) }))
  default = {}
}
variable "ttl_attribute" {
  type    = string
  default = null
}
