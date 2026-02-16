from flask import Flask, render_template, request, jsonify
import requests
import os

app = Flask(__name__)

# Get API endpoint from environment
API_ENDPOINT = os.environ.get('API_ENDPOINT', 'https://your-api-id.execute-api.region.amazonaws.com/prod/translate')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/translate', methods=['POST'])
def translate():
    try:
        idempotency_key = request.headers.get('Idempotency-Key')
        if not idempotency_key:
            return jsonify({'error': 'Client error: Idempotency-Key header is required.'}), 400

        data = {
            'text': request.form['text'],
            'target_language': request.form['target_language'],
            'context': request.form.get('context', '')
        }

        headers = {
            'Content-Type': 'application/json',
            'Idempotency-Key': idempotency_key
        }

        # Call the translation API
        response = requests.post(API_ENDPOINT, json=data, headers=headers, timeout=30)
        result = response.json()
        status_code = response.status_code

        return jsonify(result), status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # This block is for local development only.
    # In production, a WSGI server like Gunicorn should be used.
    app.run(debug=True, host='0.0.0.0', port=5000)