# Workflow outputs (generated images / videos). Public-read, 7-day lifecycle on downloads/.

resource "aws_s3_bucket" "outputs" {
  bucket = "${local.prefix}-outputs-${local.suffix}"
}

resource "aws_s3_bucket_versioning" "outputs" {
  bucket = aws_s3_bucket.outputs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "outputs" {
  bucket = aws_s3_bucket.outputs.id
  rule {
    id     = "DeleteDownloadsAfter7Days"
    status = "Enabled"
    filter { prefix = "downloads/" }
    expiration { days = 7 }
  }
}

resource "aws_s3_bucket_policy" "public_read" {
  bucket = aws_s3_bucket.outputs.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Sid = "PublicReadGetObject", Effect = "Allow", Principal = "*",
                   Action = "s3:GetObject", Resource = "${aws_s3_bucket.outputs.arn}/*" }]
  })
}

locals {
  bucket_env = {
    OUTPUT_BUCKET      = aws_s3_bucket.outputs.bucket
    OUTPUT_BUCKET_NAME = aws_s3_bucket.outputs.bucket
  }
  s3_policy = {
    Effect   = "Allow"
    Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    Resource = [aws_s3_bucket.outputs.arn, "${aws_s3_bucket.outputs.arn}/*"]
  }
}
