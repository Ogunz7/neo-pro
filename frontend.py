import logging
import os
from uuid import uuid4

import requests
from flask import Flask, jsonify, render_template, request
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

app = Flask(__name__)
logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO'))
LOGGER = logging.getLogger(__name__)

API_ENDPOINT = os.environ.get('API_ENDPOINT', 'https://your-api-id.execute-api.region.amazonaws.com/prod/translate')
REQUEST_TIMEOUT_SECONDS = int(os.environ.get('REQUEST_TIMEOUT_SECONDS', '30'))

session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=['POST']
)
session.mount('https://', HTTPAdapter(max_retries=retries))


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200


@app.route('/translate', methods=['POST'])
def translate():
    try:
        text = request.form.get('text', '').strip()
        target_language = request.form.get('target_language', '').strip()
        req_context = request.form.get('context', '')

        if not text or not target_language:
            return jsonify({'error': 'Client error: text and target_language are required.'}), 400

        idempotency_key = request.headers.get('Idempotency-Key') or str(uuid4())

        data = {
            'text': text,
            'target_language': target_language,
            'context': req_context
        }

        headers = {
            'Content-Type': 'application/json',
            'Idempotency-Key': idempotency_key
        }

        response = session.post(API_ENDPOINT, json=data, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)

        try:
            result = response.json()
        except ValueError:
            LOGGER.error('Translation API returned non-JSON response with status=%s', response.status_code)
            return jsonify({'error': 'Translation API returned invalid response format.'}), 502

        return jsonify(result), response.status_code

    except requests.Timeout:
        return jsonify({'error': 'Translation request timed out. Please try again.'}), 504
    except requests.RequestException as exc:
        LOGGER.error('Network error while calling translation API: %s', exc)
        return jsonify({'error': 'Failed to reach translation service.'}), 502
    except Exception as exc:
        LOGGER.exception('Unexpected server error')
        return jsonify({'error': 'Internal server error', 'message': str(exc)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
