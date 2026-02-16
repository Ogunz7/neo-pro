import json
import os
import boto3
import logging
from datetime import datetime
from uuid import uuid4
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

bedrock = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

def build_api_response(status_code, body):
    """Constructs a standard API Gateway proxy response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*' # WARNING: Should be restricted to a specific domain in production
        },
        'body': json.dumps(body)
    }

def parse_and_validate_request(event):
    """Parses and validates the incoming request body and headers."""
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body.")

    # API Gateway headers are case-insensitive and keys are lowercased
    headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
    idempotency_key = headers.get('idempotency-key')

    if not idempotency_key:
        raise ValueError("Header 'Idempotency-Key' is required.")

    text = body.get('text', '').strip()
    target_language = body.get('target_language', '').strip()
    context = body.get('context', '')

    if not text or not target_language:
        raise ValueError('`text` and `target_language` are required fields.')
    
    return text, target_language, context, idempotency_key

def get_app_config():
    """Loads and validates required environment variables."""
    model_id = os.environ.get('MODEL_ID')
    table_name = os.environ.get('DYNAMODB_TABLE')
    bucket_name = os.environ.get('S3_BUCKET')

    if not all([model_id, table_name, bucket_name]):
        LOGGER.error("FATAL: Missing one or more environment variables: MODEL_ID, DYNAMODB_TABLE, S3_BUCKET")
        raise ValueError('Internal server configuration error.')
    
    return {
        "model_id": model_id,
        "table_name": table_name,
        "bucket_name": bucket_name
    }

def invoke_translation_model(text, target_language, context, model_id):
    """Invokes the Bedrock model and returns the translation."""
    prompt = f"Translate the following text to {target_language}. Context: {context}\n\nText: {text}\n\nTranslation:"
    
    LOGGER.info(f"Invoking model {model_id} for translation to {target_language}.")
    
    response = bedrock.invoke_model(
        modelId=model_id,
        body=json.dumps({
            "prompt": prompt,
            "max_tokens_to_sample": 1000,
            "temperature": 0.7
        }),
        contentType='application/json',
        accept='application/json'
    )
    
    response_body = json.loads(response['body'].read())
    return response_body.get('completion', '').strip()

def store_translation_artifacts(item, table_name, bucket_name):
    """Stores the translation item in DynamoDB and S3."""
    LOGGER.info(f"Storing translation record {item['id']} in DynamoDB and S3.")
    
    table = dynamodb.Table(table_name)
    table.put_item(Item=item)

    s3_key = f"translations/{datetime.utcnow().strftime('%Y/%m/%d')}/{item['id']}.json"
    s3.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=json.dumps(item),
        ContentType='application/json'
    )

def handler(event, context):
    """
    Lambda handler for Bedrock Translation API.
    Handles POST /translate to perform AI-powered text translation with idempotency.
    """
    request_id = context.aws_request_id
    LOGGER.info(f"Received request {request_id}")

    idempotency_key = None
    placeholder_created = False
    table = None

    try:
        # 1. Parse and Validate request, including Idempotency-Key header
        text, target_language, req_context, idempotency_key = parse_and_validate_request(event)

        # 2. Load Configuration and get table resource
        config = get_app_config()
        table = dynamodb.Table(config["table_name"])
        
        # 3. Handle Idempotency: Check for an existing record
        response = table.get_item(Key={'id': idempotency_key})
        item = response.get('Item')

        if item:
            status = item.get('status', 'COMPLETED') # Default to completed for backward compatibility
            if status == 'COMPLETED':
                LOGGER.info(f"Idempotency key {idempotency_key} found. Returning cached response.")
                response_body = {
                    'translation': item.get('translation'),
                    'id': item.get('id'),
                    'target_language': item.get('target_language')
                }
                return build_api_response(200, response_body)
            elif status == 'PROCESSING':
                LOGGER.warning(f"Idempotency key {idempotency_key} is currently being processed.")
                return build_api_response(409, {'error': 'Conflict', 'message': 'Request with this idempotency key is already in progress.'})

        # 4. Create a placeholder item to lock the key for this request
        try:
            placeholder_item = {
                'id': idempotency_key,
                'status': 'PROCESSING',
                'timestamp': datetime.utcnow().isoformat()
            }
            table.put_item(
                Item=placeholder_item,
                ConditionExpression='attribute_not_exists(id)'
            )
            placeholder_created = True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                LOGGER.warning(f"Idempotency race condition for key {idempotency_key}.")
                return build_api_response(409, {'error': 'Conflict', 'message': 'Request with this idempotency key is already in progress.'})
            raise # Re-raise other boto3 errors

        # 5. Invoke Model
        translation = invoke_translation_model(text, target_language, req_context, config["model_id"])

        # 6. Prepare and Store Final Artifacts, overwriting the placeholder
        final_item = {
            'id': idempotency_key,
            'original_text': text,
            'translation': translation,
            'target_language': target_language,
            'context': req_context,
            'timestamp': datetime.utcnow().isoformat(),
            'model_id': config["model_id"],
            'status': 'COMPLETED'
        }
        store_translation_artifacts(final_item, config["table_name"], config["bucket_name"])
        placeholder_created = False # The lock is now a permanent record, no need to clean up

        # 7. Return Success Response
        response_body = {
            'translation': translation,
            'id': final_item['id'],
            'target_language': target_language
        }
        return build_api_response(200, response_body)

    except ValueError as e:
        LOGGER.warning(f"Validation or configuration error: {str(e)}")
        if 'required' in str(e) or 'Invalid JSON' in str(e):
            return build_api_response(400, {'error': 'Bad Request', 'message': str(e)})
        else: # Configuration error
            return build_api_response(500, {'error': str(e), 'reference': request_id})
    except ClientError as e:
        if placeholder_created and table and idempotency_key:
            LOGGER.error(f"ClientError for key {idempotency_key}. Cleaning up placeholder.")
            table.delete_item(Key={'id': idempotency_key})
        error_code = e.response.get("Error", {}).get("Code")
        LOGGER.error(f"A boto3 client error occurred: {error_code} - {str(e)}")
        return build_api_response(500, {'error': 'Translation service failed', 'reference': request_id})
    except Exception as e:
        if placeholder_created and table and idempotency_key:
            LOGGER.error(f"Exception for key {idempotency_key}. Cleaning up placeholder.")
            table.delete_item(Key={'id': idempotency_key})
        LOGGER.error(f"An unexpected error occurred: {str(e)}", exc_info=True)
        return build_api_response(500, {'error': 'Internal Server Error', 'reference': request_id})