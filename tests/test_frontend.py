import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import frontend


def test_health_endpoint():
    client = frontend.app.test_client()
    response = client.get('/health')

    assert response.status_code == 200
    assert response.get_json() == {'status': 'ok'}


def test_index_renders_template():
    client = frontend.app.test_client()
    response = client.get('/')

    assert response.status_code == 200
    assert b'Bedrock Translation Tool' in response.data
