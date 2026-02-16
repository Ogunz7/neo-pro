import json
import os
import logging
from datetime import datetime, timezone
from uuid import uuid4
import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

DYNAMODB = boto3.resource('dynamodb')
CASE_TABLE_NAME = os.environ.get('CASE_TABLE')
AUDIT_TABLE_NAME = os.environ.get('AUDIT_TABLE')

CASE_TABLE = DYNAMODB.Table(CASE_TABLE_NAME) if CASE_TABLE_NAME else None
AUDIT_TABLE = DYNAMODB.Table(AUDIT_TABLE_NAME) if AUDIT_TABLE_NAME else None

def handler(event, context):
    """
    SentinelCase Lambda Handler
    Manages case lifecycle and creates immutable audit trails.
    """
    request_id = context.aws_request_id
    LOGGER.info({
        "message": "Received request",
        "requestId": request_id,
        "event": event
    })

    if not CASE_TABLE or not AUDIT_TABLE:
        LOGGER.error("FATAL: Table environment variables not set.")
        return api_response(500, {"error": "Internal server configuration error."})

    http_method = event.get('httpMethod')
    path = event.get('path')

    try:
        if http_method == 'POST' and path == '/cases':
            return create_case(json.loads(event['body']), request_id)
        elif http_method == 'GET' and '/cases/' in path:
            case_id = path.split('/')[-1]
            return get_case(case_id)
        elif http_method == 'PUT' and '/cases/' in path:
            case_id = path.split('/')[-1]
            return update_case(case_id, json.loads(event['body']), request_id)
        else:
            return api_response(404, {"error": "Not Found"})

    except json.JSONDecodeError:
        return api_response(400, {"error": "Invalid JSON in request body."})
    except ClientError as e:
        LOGGER.error(f"DynamoDB ClientError: {e.response['Error']['Message']}")
        return api_response(500, {"error": "Database operation failed."})
    except Exception as e:
        LOGGER.error(f"Unexpected error: {str(e)}", exc_info=True)
        return api_response(500, {"error": "An internal server error occurred."})

def create_case(body, request_id):
    """Creates a new case and an initial audit log."""
    case_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    # Basic validation
    if 'investigatorId' not in body or 'details' not in body:
        return api_response(400, {"error": "Missing required fields: investigatorId, details."})

    item = {
        'CaseId': case_id,
        'InvestigatorId': body['investigatorId'],
        'Status': 'OPEN',
        'Details': body['details'],
        'CreatedAt': timestamp,
        'UpdatedAt': timestamp
    }

    CASE_TABLE.put_item(Item=item)
    log_audit(case_id, 'CREATE', item, request_id, details="Case created.")

    LOGGER.info(f"Successfully created case {case_id}")
    return api_response(201, item)

def get_case(case_id):
    """Retrieves a single case by its ID."""
    response = CASE_TABLE.get_item(Key={'CaseId': case_id})
    item = response.get('Item')

    if not item:
        return api_response(404, {"error": f"Case with ID {case_id} not found."})

    return api_response(200, item)

def update_case(case_id, body, request_id):
    """Updates an existing case and logs the changes."""
    timestamp = datetime.now(timezone.utc).isoformat()

    # Get current state for audit log
    get_response = CASE_TABLE.get_item(Key={'CaseId': case_id})
    old_item = get_response.get('Item')
    if not old_item:
        return api_response(404, {"error": f"Case with ID {case_id} not found."})

    # Construct update expression
    update_expression = "SET UpdatedAt = :ts"
    expression_attribute_values = {':ts': timestamp}
    updatable_fields = ['Status', 'Details', 'InvestigatorId']

    for field in updatable_fields:
        if field in body:
            update_expression += f", {field} = :{field.lower()}"
            expression_attribute_values[f":{field.lower()}"] = body[field]

    response = CASE_TABLE.update_item(
        Key={'CaseId': case_id},
        UpdateExpression=update_expression,
        ExpressionAttributeValues=expression_attribute_values,
        ReturnValues="ALL_NEW"
    )
    updated_item = response['Attributes']

    # Log the change to the audit table
    change_details = {
        "old_values": {k: old_item.get(k) for k in body if k in old_item and k in updatable_fields},
        "new_values": {k: updated_item.get(k) for k in body if k in updated_item and k in updatable_fields}
    }
    log_audit(case_id, 'UPDATE', updated_item, request_id, details=f"Case updated: {', '.join(body.keys())}", change_data=change_details)

    LOGGER.info(f"Successfully updated case {case_id}")
    return api_response(200, updated_item)

def log_audit(case_id, action, item_context, request_id, details, change_data=None):
    """Writes an immutable record to the audit table."""
    timestamp = datetime.now(timezone.utc).isoformat()
    audit_item = {
        'CaseId': case_id,
        'Timestamp': timestamp,
        'Action': action,
        'Details': details,
        'RequestId': request_id,
        'Principal': item_context.get('InvestigatorId', 'SYSTEM'), # Or from authorizer context
    }
    if change_data:
        audit_item['ChangeData'] = change_data

    AUDIT_TABLE.put_item(Item=audit_item)

def api_response(status_code, body):
    """Constructs a standard API Gateway proxy response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*' # Be more restrictive in production
        },
        'body': json.dumps(body)
    }