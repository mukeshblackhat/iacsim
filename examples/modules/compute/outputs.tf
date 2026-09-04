output "instance_ids" {
  value = { for az, i in aws_instance.this : az => i.id }
}

output "security_group_id" {
  value = aws_security_group.this.id
}
