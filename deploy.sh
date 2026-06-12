#!/usr/bin/env bash
# deploy.sh — Build and deploy data-lens to AWS ECS + RDS
#
# Prerequisites:
#   aws CLI configured (aws configure)
#   Docker running
#   jq installed (brew install jq)
#
# Usage:
#   ./deploy.sh                   # deploy to staging
#   ENV=production ./deploy.sh    # deploy to production

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────

APP_NAME="data-lens"
ENV="${ENV:-staging}"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

ECR_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${APP_NAME}"
IMAGE_TAG=$(git rev-parse --short HEAD)
IMAGE_URI="${ECR_REPO}:${IMAGE_TAG}"

ECS_CLUSTER="${APP_NAME}-${ENV}"
ECS_SERVICE="${APP_NAME}-${ENV}-service"
TASK_FAMILY="${APP_NAME}-${ENV}"
CONTAINER_PORT=8000

# ─── Helper functions ─────────────────────────────────────────────────────────

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }
check_tool() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed."; }

# ─── Preflight checks ─────────────────────────────────────────────────────────

check_tool docker
check_tool aws
check_tool jq

log "Deploying ${APP_NAME}:${IMAGE_TAG} to ${ENV} (${AWS_REGION})"

# ─── 1. Create ECR repository (idempotent) ────────────────────────────────────

log "Ensuring ECR repository exists..."
aws ecr describe-repositories --repository-names "${APP_NAME}" --region "${AWS_REGION}" \
  >/dev/null 2>&1 || \
  aws ecr create-repository \
    --repository-name "${APP_NAME}" \
    --region "${AWS_REGION}" \
    --image-scanning-configuration scanOnPush=true \
    >/dev/null

# ─── 2. Build and push Docker image ──────────────────────────────────────────

log "Authenticating Docker with ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin \
  "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

log "Building image ${IMAGE_URI}..."
docker build --platform linux/amd64 -t "${IMAGE_URI}" .

log "Pushing image..."
docker push "${IMAGE_URI}"
docker tag "${IMAGE_URI}" "${ECR_REPO}:latest"
docker push "${ECR_REPO}:latest"

# ─── 3. Fetch RDS endpoint ────────────────────────────────────────────────────

log "Fetching RDS endpoint..."
RDS_ENDPOINT=$(aws rds describe-db-instances \
  --query "DBInstances[?DBInstanceIdentifier=='${APP_NAME}-${ENV}'].Endpoint.Address" \
  --output text --region "${AWS_REGION}" 2>/dev/null || echo "")

if [[ -z "${RDS_ENDPOINT}" ]]; then
  log "WARNING: No RDS instance found for ${APP_NAME}-${ENV}."
  log "Set DATABASE_URL manually or create an RDS instance named '${APP_NAME}-${ENV}'."
  DATABASE_URL="${DATABASE_URL:-postgresql://postgres:CHANGE_ME@localhost:5432/data_lens}"
else
  # Retrieve password from SSM Parameter Store
  DB_PASSWORD=$(aws ssm get-parameter \
    --name "/${APP_NAME}/${ENV}/db_password" \
    --with-decryption --query Parameter.Value --output text 2>/dev/null || echo "CHANGE_ME")
  DATABASE_URL="postgresql://postgres:${DB_PASSWORD}@${RDS_ENDPOINT}:5432/data_lens"
  log "RDS endpoint: ${RDS_ENDPOINT}"
fi

# ─── 4. Register new ECS task definition ─────────────────────────────────────

log "Registering ECS task definition..."
TASK_DEF=$(cat <<EOF
{
  "family": "${TASK_FAMILY}",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::${AWS_ACCOUNT_ID}:role/ecsTaskExecutionRole",
  "containerDefinitions": [
    {
      "name": "${APP_NAME}",
      "image": "${IMAGE_URI}",
      "portMappings": [{"containerPort": ${CONTAINER_PORT}, "protocol": "tcp"}],
      "environment": [
        {"name": "DATABASE_URL", "value": "${DATABASE_URL}"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/${APP_NAME}-${ENV}",
          "awslogs-region": "${AWS_REGION}",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -sf http://localhost:${CONTAINER_PORT}/docs || exit 1"],
        "interval": 30,
        "timeout": 5,
        "retries": 3
      }
    }
  ]
}
EOF
)

NEW_TASK_ARN=$(aws ecs register-task-definition \
  --cli-input-json "${TASK_DEF}" \
  --region "${AWS_REGION}" \
  --query "taskDefinition.taskDefinitionArn" --output text)

log "Registered task: ${NEW_TASK_ARN}"

# ─── 5. Create CloudWatch log group (idempotent) ─────────────────────────────

aws logs create-log-group \
  --log-group-name "/ecs/${APP_NAME}-${ENV}" \
  --region "${AWS_REGION}" 2>/dev/null || true

# ─── 6. Update ECS service ───────────────────────────────────────────────────

# Check if service exists
SERVICE_STATUS=$(aws ecs describe-services \
  --cluster "${ECS_CLUSTER}" \
  --services "${ECS_SERVICE}" \
  --region "${AWS_REGION}" \
  --query "services[0].status" --output text 2>/dev/null || echo "MISSING")

if [[ "${SERVICE_STATUS}" == "ACTIVE" ]]; then
  log "Updating ECS service ${ECS_SERVICE}..."
  aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${ECS_SERVICE}" \
    --task-definition "${NEW_TASK_ARN}" \
    --force-new-deployment \
    --region "${AWS_REGION}" \
    >/dev/null
else
  log "Service not found — create it manually or via Terraform/CDK."
  log "Task definition registered: ${NEW_TASK_ARN}"
  log "Cluster: ${ECS_CLUSTER}"
  exit 0
fi

# ─── 7. Wait for deployment to stabilise ─────────────────────────────────────

log "Waiting for service to stabilise (this may take 2–5 minutes)..."
aws ecs wait services-stable \
  --cluster "${ECS_CLUSTER}" \
  --services "${ECS_SERVICE}" \
  --region "${AWS_REGION}"

log "Deployment complete. Image: ${IMAGE_URI}"
