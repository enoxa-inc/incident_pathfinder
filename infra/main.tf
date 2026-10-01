# Incident Pathfinder — sandbox account (402178233131), us-east-1.
# All resources are prefixed "incident-pathfinder-". The Gatepath MCP connection settings
# ({"url": ..., "token": ...}) live in the SSM SecureString /incident-pathfinder/gatepath-mcp
# (created outside Terraform, never in state or source).

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
  backend "s3" {
    bucket       = "incident-pathfinder-tfstate-402178233131"
    key          = "incident-pathfinder/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["402178233131"]
  default_tags {
    tags = {
      Project   = "incident-pathfinder"
      ManagedBy = "terraform"
    }
  }
}

variable "bedrock_model_id" {
  description = "Bedrock model or inference profile ID (Converse API with tool use)"
  type        = string
  default     = "us.amazon.nova-2-lite-v1:0"
}


data "aws_caller_identity" "current" {}

locals {
  prefix           = "incident-pathfinder"
  region           = "us-east-1"
  account_id       = data.aws_caller_identity.current.account_id
  fn_name          = "${local.prefix}-api"
  config_parameter = "/incident-pathfinder/gatepath-mcp"
}

# ============================================================== session state

resource "aws_dynamodb_table" "sessions" {
  name         = "${local.prefix}-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# ============================================================== Lambda

data "archive_file" "api" {
  type        = "zip"
  source_dir  = "${path.module}/../backend"
  output_path = "${path.module}/../.build/api.zip"
  excludes    = ["local_server.py", "app/__pycache__", "app/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.fn_name}"
  retention_in_days = 14
}

resource "aws_iam_role" "api" {
  name = "${local.prefix}-api-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Read-only by design: logs, own session table, Bedrock inference, the Gatepath settings. Nothing else.
resource "aws_iam_role_policy" "api" {
  name = "${local.prefix}-api-policy"
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.api.arn}:*"
      },
      {
        Sid      = "OwnSessionTable"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.sessions.arn
      },
      {
        Sid    = "BedrockInference"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:${local.account_id}:inference-profile/*",
        ]
      },
      {
        Sid      = "GatepathConfig"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${local.region}:${local.account_id}:parameter${local.config_parameter}"
      },
    ]
  })
}

resource "aws_lambda_function" "api" {
  function_name    = local.fn_name
  role             = aws_iam_role.api.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "app.handler.lambda_handler"
  filename         = data.archive_file.api.output_path
  source_code_hash = data.archive_file.api.output_base64sha256
  memory_size      = 512
  timeout          = 29
  environment {
    variables = {
      TABLE_NAME                = aws_dynamodb_table.sessions.name
      STORE_BACKEND             = "dynamodb"
      BEDROCK_MODEL_ID          = var.bedrock_model_id
      GATEPATH_CONFIG_PARAMETER = local.config_parameter
    }
  }
  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

# ============================================================== HTTP API (serves the UI and /api/chat)

resource "aws_apigatewayv2_api" "api" {
  name          = local.fn_name
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "routes" {
  for_each  = toset(["GET /", "POST /api/chat"])
  api_id    = aws_apigatewayv2_api.api.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.fn_name}"
  retention_in_days = 14
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_rate_limit  = 2
    throttling_burst_limit = 5
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    format = jsonencode({
      requestId = "$context.requestId", routeKey = "$context.routeKey", status = "$context.status",
      latency   = "$context.responseLatency", integrationError = "$context.integrationErrorMessage"
    })
  }
  depends_on = [aws_apigatewayv2_route.routes]
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowHttpApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

output "app_url" {
  value = "${aws_apigatewayv2_api.api.api_endpoint}/"
}

output "log_group" {
  value = aws_cloudwatch_log_group.api.name
}
