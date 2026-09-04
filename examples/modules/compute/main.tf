# compute — one EC2 web server per subnet, all registered in the ALB's
# target group. Database connection details are baked in via user_data,
# which is the reference the simulator uses to infer "web → db".

resource "aws_security_group" "this" {
  name   = "${var.name}-web"
  vpc_id = var.vpc_id

  ingress {
    from_port       = var.port
    to_port         = var.port
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
  }
}

resource "aws_instance" "this" {
  for_each = var.subnets_by_az

  ami                    = var.ami_id
  instance_type          = var.instance_type
  subnet_id              = each.value
  vpc_security_group_ids = [aws_security_group.this.id]

  user_data = templatefile("${path.module}/user_data.sh", {
    db_host = var.db_host
    db_name = var.db_name
  })

  tags = { Name = "${var.name}-${each.key}" }
}

resource "aws_lb_target_group_attachment" "this" {
  for_each = aws_instance.this

  target_group_arn = var.target_group_arn
  target_id        = each.value.id
  port             = var.port
}
