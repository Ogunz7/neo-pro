import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class DummyClientError(Exception):
    def __init__(self, response=None):
        self.response = response or {'Error': {'Code': 'Unknown'}}


# Lightweight stubs to import translate.py without boto3 installed.
if 'boto3' not in sys.modules:
    sys.modules['boto3'] = types.SimpleNamespace(
        client=lambda *_args, **_kwargs: object(),
        resource=lambda *_args, **_kwargs: object(),
    )
if 'botocore' not in sys.modules:
    botocore = types.ModuleType('botocore')
    exceptions = types.ModuleType('botocore.exceptions')
    exceptions.ClientError = DummyClientError
    botocore.exceptions = exceptions
    sys.modules['botocore'] = botocore
    sys.modules['botocore.exceptions'] = exceptions

import translate


class DummyTable:
    def __init__(self, item=None):
        self.item = item

    def get_item(self, Key):
        if self.item:
            return {'Item': self.item}
        return {}


def test_parse_and_validate_request_success():
    event = {
        'httpMethod': 'POST',
        'body': json.dumps({'text': 'Hello', 'target_language': 'Spanish', 'context': 'formal'}),
        'headers': {'Idempotency-Key': 'abc-123'}
    }

    text, target_language, context, key = translate.parse_and_validate_request(event)

    assert text == 'Hello'
    assert target_language == 'Spanish'
    assert context == 'formal'
    assert key == 'abc-123'


@pytest.mark.parametrize(
    'event,error',
    [
        ({'httpMethod': 'GET', 'body': '{}', 'headers': {'Idempotency-Key': 'k'}}, 'Only POST is supported'),
        ({'httpMethod': 'POST', 'body': '{invalid', 'headers': {'Idempotency-Key': 'k'}}, 'Invalid JSON'),
        ({'httpMethod': 'POST', 'body': '{}', 'headers': {}}, 'Idempotency-Key'),
    ],
)
def test_parse_and_validate_request_failures(event, error):
    with pytest.raises(translate.ValidationError) as exc:
        translate.parse_and_validate_request(event)
    assert error in str(exc.value)


def test_handler_options_returns_ok():
    event = {'httpMethod': 'OPTIONS', 'headers': {}, 'body': '{}'}
    ctx = SimpleNamespace(aws_request_id='req-1')

    response = translate.handler(event, ctx)

    assert response['statusCode'] == 200
    parsed = json.loads(response['body'])
    assert parsed['ok'] is True
    assert response['headers']['X-Request-Id'] == 'req-1'


def test_handler_returns_cached_result(monkeypatch):
    cached = {
        'id': 'key-1',
        'translation': 'Hola',
        'target_language': 'Spanish',
        'status': 'COMPLETED',
    }

    class DummyDynamo:
        @staticmethod
        def Table(_):
            return DummyTable(item=cached)

    monkeypatch.setattr(translate, 'dynamodb', DummyDynamo())
    monkeypatch.setenv('MODEL_ID', 'm')
    monkeypatch.setenv('DYNAMODB_TABLE', 't')
    monkeypatch.setenv('S3_BUCKET', 'b')

    event = {
        'httpMethod': 'POST',
        'body': json.dumps({'text': 'Hello', 'target_language': 'Spanish'}),
        'headers': {'Idempotency-Key': 'key-1'}
    }
    ctx = SimpleNamespace(aws_request_id='req-2')

    response = translate.handler(event, ctx)

    assert response['statusCode'] == 200
    parsed = json.loads(response['body'])
    assert parsed['translation'] == 'Hola'
