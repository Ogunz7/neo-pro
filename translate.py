import json
import os
import re
import boto3
import logging
from datetime import datetime
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

bedrock = boto3.client('bedrock-runtime')
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

MAX_TEXT_LENGTH = 5_000
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_\-:.]{1,128}$")


class ValidationError(ValueError):
    """Raised when request validation fails."""


class ConfigurationError(RuntimeError):
    """Raised when service configuration is invalid."""


def build_api_response(status_code, body, request_id=None):
    """Constructs a standard API Gateway proxy response."""
    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': os.environ.get('ALLOWED_ORIGIN', '*'),
        'Access-Control-Allow-Headers': 'Content-Type,Idempotency-Key',
        'Access-Control-Allow-Methods': 'OPTIONS,POST'
    }
    if request_id:
        headers['X-Request-Id'] = request_id

    return {
        'statusCode': status_code,
        'headers': headers,
        'body': json.dumps(body)
    }


def parse_and_validate_request(event):
    """Parses and validates the incoming request body and headers."""
    method = (event.get('httpMethod') or '').upper()
    if method and method != 'POST':
        raise ValidationError('Only POST is supported for this endpoint.')

    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError as exc:
        raise ValidationError('Invalid JSON in request body.') from exc

    headers_in = event.get('headers') or {}
    headers = {k.lower(): v for k, v in headers_in.items()}
    idempotency_key = (headers.get('idempotency-key') or '').strip()

    if not idempotency_key:
        raise ValidationError("Header 'Idempotency-Key' is required.")
    if not IDEMPOTENCY_KEY_PATTERN.match(idempotency_key):
        raise ValidationError(
            "Header 'Idempotency-Key' must be 1-128 characters and only contain letters, numbers, '_', '-', ':', and '.'."
        )

    text = body.get('text', '').strip()
    target_language = body.get('target_language', '').strip()
    context = body.get('context', '')

    if not text or not target_language:
        raise ValidationError('`text` and `target_language` are required fields.')
    if len(text) > MAX_TEXT_LENGTH:
        raise ValidationError(f'`text` exceeds maximum length of {MAX_TEXT_LENGTH} characters.')

    return text, target_language, context, idempotency_key


def get_app_config():
    """Loads and validates required environment variables."""
    model_id = os.environ.get('MODEL_ID')
    table_name = os.environ.get('DYNAMODB_TABLE')
    bucket_name = os.environ.get('S3_BUCKET')

    if not all([model_id, table_name, bucket_name]):
        LOGGER.error('FATAL: Missing one or more environment variables: MODEL_ID, DYNAMODB_TABLE, S3_BUCKET')
        raise ConfigurationError('Internal server configuration error.')

    return {
        'model_id': model_id,
        'table_name': table_name,
        'bucket_name': bucket_name
    }


def invoke_translation_model(text, target_language, context, model_id):
    """Invokes the Bedrock model and returns the translation."""
    prompt = f"Translate the following text to {target_language}. Context: {context}\n\nText: {text}\n\nTranslation:"

    LOGGER.info('Invoking model %s for translation to %s.', model_id, target_language)

    response = bedrock.invoke_model(
        modelId=model_id,
        body=json.dumps({
            'prompt': prompt,
            'max_tokens_to_sample': 1000,
            'temperature': 0.7
        }),
        contentType='application/json',
        accept='application/json'
    )

    response_body = json.loads(response['body'].read())
    completion = response_body.get('completion', '').strip()
    if not completion:
        raise RuntimeError('Model returned an empty translation response.')
    return completion


def store_translation_artifacts(item, table_name, bucket_name):
    """Stores the translation item in DynamoDB and S3."""
    LOGGER.info('Storing translation record %s in DynamoDB and S3.', item['id'])

    table = dynamodb.Table(table_name)
    table.put_item(Item=item)

    s3_key = f"translations/{datetime.utcnow().strftime('%Y/%m/%d')}/{item['id']}.json"
    s3.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=json.dumps(item),
        ContentType='application/json'
    )


def cleanup_placeholder(table, idempotency_key):
    """Best-effort cleanup for a processing placeholder item."""
    if table and idempotency_key:
        table.delete_item(
            Key={'id': idempotency_key},
            ConditionExpression='#status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'PROCESSING'}
        )


def handler(event, context):
    """
    Lambda handler for Bedrock Translation API.
    Handles POST /translate to perform AI-powered text translation with idempotency.
    """
    request_id = getattr(context, 'aws_request_id', 'local-request')
    LOGGER.info('Received request %s', request_id)

    idempotency_key = None
    placeholder_created = False
    table = None

    try:
        if (event.get('httpMethod') or '').upper() == 'OPTIONS':
            return build_api_response(200, {'ok': True}, request_id=request_id)

        text, target_language, req_context, idempotency_key = parse_and_validate_request(event)

        config = get_app_config()
        table = dynamodb.Table(config['table_name'])

        response = table.get_item(Key={'id': idempotency_key})
        item = response.get('Item')

        if item:
            status = item.get('status', 'COMPLETED')
            if status == 'COMPLETED':
                LOGGER.info('Idempotency key %s found. Returning cached response.', idempotency_key)
                response_body = {
                    'translation': item.get('translation'),
                    'id': item.get('id'),
                    'target_language': item.get('target_language')
                }
                return build_api_response(200, response_body, request_id=request_id)
            if status == 'PROCESSING':
                LOGGER.warning('Idempotency key %s is currently being processed.', idempotency_key)
                return build_api_response(409, {'error': 'Conflict', 'message': 'Request with this idempotency key is already in progress.'}, request_id=request_id)

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
                LOGGER.warning('Idempotency race condition for key %s.', idempotency_key)
                return build_api_response(409, {'error': 'Conflict', 'message': 'Request with this idempotency key is already in progress.'}, request_id=request_id)
            raise

        translation = invoke_translation_model(text, target_language, req_context, config['model_id'])

        final_item = {
            'id': idempotency_key,
            'original_text': text,
            'translation': translation,
            'target_language': target_language,
            'context': req_context,
            'timestamp': datetime.utcnow().isoformat(),
            'model_id': config['model_id'],
            'status': 'COMPLETED'
        }
        store_translation_artifacts(final_item, config['table_name'], config['bucket_name'])
        placeholder_created = False

        response_body = {
            'translation': translation,
            'id': final_item['id'],
            'target_language': target_language
        }
        return build_api_response(200, response_body, request_id=request_id)

    except ValidationError as e:
        LOGGER.warning('Validation error: %s', str(e))
        return build_api_response(400, {'error': 'Bad Request', 'message': str(e)}, request_id=request_id)
    except ConfigurationError as e:
        LOGGER.error('Configuration error: %s', str(e))
        return build_api_response(500, {'error': str(e), 'reference': request_id}, request_id=request_id)
    except ClientError as e:
        if placeholder_created:
            LOGGER.error('ClientError for key %s. Cleaning up placeholder.', idempotency_key)
            cleanup_placeholder(table, idempotency_key)
        error_code = e.response.get('Error', {}).get('Code')
        LOGGER.error('A boto3 client error occurred: %s - %s', error_code, str(e))
        return build_api_response(500, {'error': 'Translation service failed', 'reference': request_id}, request_id=request_id)
    except Exception as e:
        if placeholder_created:
            LOGGER.error('Exception for key %s. Cleaning up placeholder.', idempotency_key)
            cleanup_placeholder(table, idempotency_key)
        LOGGER.error('An unexpected error occurred: %s', str(e), exc_info=True)
        return build_api_response(500, {'error': 'Internal Server Error', 'reference': request_id}, request_id=request_id)
