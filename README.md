# Simplified architecture using only free services
- Lambda: 1M free requests/month (not listed but included)
- DynamoDB: 25GB free storage + 25 RCU/WCU
- S3: Store translation history as JSON files
- API Gateway: 1M free requests/month

# Bedrock Translation Tool - Unified Enterprise Application

This is a complete enterprise-grade translation tool built on AWS using Amazon Bedrock for AI-powered translations, with load balancing, auto-scaling, and comprehensive data persistence.

## Architecture

- **Frontend**: Flask web application on AWS Elastic Beanstalk with auto-scaling EC2 instances
- **Backend**: AWS Lambda function for AI processing using Amazon Bedrock
- **Load Balancer**: Application Load Balancer for traffic distribution
- **Data Layer**: DynamoDB for translation history, S3 for data artifacts
- **Network**: VPC with public/private subnets for security

## Components

### Files
- `translate.py`: Lambda function handler for translation API
- `frontend.py`: Flask web application
- `index.html`: Legacy root copy of UI template (kept for compatibility)
- `templates/index.html`: Web interface
- `bedrock-translation-template.yaml`: CloudFormation template for full infrastructure
- `requirements.txt`: Python dependencies for frontend and local testing
- `app.py`: SentinelCase Lambda (separate case management system)

## Production hardening implemented

- Strict request validation for translation endpoint (`POST` only, idempotency key format checks, max text length guard).
- Configurable CORS (`ALLOWED_ORIGIN`) and request tracing (`X-Request-Id` response header).
- Improved idempotency placeholder cleanup safety in DynamoDB.
- More resilient frontend API calls with retries, timeout handling, and explicit error mapping.
- `/health` endpoint for load balancer and uptime checks.
- Basic automated tests for request validation and core handler paths.

## Deployment

### Prerequisites
1. AWS CLI configured with appropriate permissions
2. EC2 Key Pair created in target region
3. Amazon Bedrock model access enabled (anthropic.claude-3-sonnet-20240229-v1:0)
4. S3 bucket for code artifacts

### Steps
1. **Package frontend code**:
   ```bash
   zip -r frontend.zip frontend.py templates/ requirements.txt
   aws s3 cp frontend.zip s3://your-bucket/frontend.zip
   ```

2. **Package Lambda code**:
   ```bash
   zip translate.zip translate.py
   aws s3 cp translate.zip s3://your-bucket/translate.zip
   ```

3. **Deploy CloudFormation stack**:
   ```bash
   aws cloudformation create-stack \
     --stack-name bedrock-translator-enterprise \
     --template-body file://bedrock-translation-template.yaml \
     --parameters ParameterKey=KeyPairName,ParameterValue=your-key-pair \
     --capabilities CAPABILITY_IAM \
     --region us-east-1
   ```

4. **Update Lambda code** (after stack creation):
   ```bash
   aws lambda update-function-code \
     --function-name bedrock-translation-dev \
     --s3-bucket your-bucket \
     --s3-key translate.zip
   ```

## Usage

1. Access the Elastic Beanstalk URL from stack outputs
2. Enter text to translate, select target language, and optional context
3. Click "Translate" to get AI-powered translation
4. Translations are stored in DynamoDB and S3 for history and artifacts

## API Usage

```bash
curl -X POST https://your-api-id.execute-api.region.amazonaws.com/prod/translate \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: request-123" \
  -d '{
    "text": "Hello, world!",
    "target_language": "Spanish",
    "context": "Formal greeting"
  }'
```

## Monitoring

- ALB request count and latency
- Lambda invocation metrics
- DynamoDB read/write capacity
- EC2 instance health and CPU utilization
- Auto-scaling based on CPU utilization (70% target)

## Cost Estimation

Monthly costs: $40-85
- EC2 (t3.micro): $15-45
- Lambda: $0.20
- DynamoDB: $5-20
- S3: $0.25
- ALB: $20

## Security

- IAM least privilege access
- VPC network isolation
- S3 public access blocked
- DynamoDB encryption at rest
- Security groups restricting traffic

## Disaster Recovery

- DynamoDB point-in-time recovery
- S3 versioning and cross-region replication
- CloudFormation stack backups

## Compliance

- Encryption in transit and at rest
- Comprehensive tagging for governance
- Audit logging capabilities

## Next Steps

1. Enable Bedrock model access in your AWS account
2. Configure domain and SSL certificates
3. Set up monitoring with CloudWatch dashboards
4. Implement authentication if needed
5. Add more languages and models


## Local development quick start

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run frontend locally:
   ```bash
   python frontend.py
   ```
3. Run tests:
   ```bash
   pytest -q
   ```
