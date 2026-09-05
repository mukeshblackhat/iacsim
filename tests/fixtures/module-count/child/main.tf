variable "index" {}
resource "aws_sqs_queue" "q" {
  name = "q-${var.index}"
}
output "name" {
  value = aws_sqs_queue.q.name
}
