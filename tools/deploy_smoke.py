"""Run on the server from the project root; never print model credentials."""
import asyncio
import os
from pathlib import Path

import requests
import websockets
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
base = 'http://127.0.0.1:7788'
for path in (
    '/cosight/', '/cosight/js/main.js',
    '/api/nae-deep-research/v1/deep-research/settings',
    '/api/nae-deep-research/v1/deep-research/materials',
    '/api/nae-deep-research/v1/deep-research/study-schedule',
    '/api/nae-deep-research/v1/deep-research/today-plan',
):
    response = requests.get(base + path, timeout=15)
    print('HTTP', response.status_code, path, flush=True)
    response.raise_for_status()


async def check_websocket():
    uri = 'ws://127.0.0.1:7788/api/openans-support-chatbot/v1/robot/wss/messages?lang=zh'
    async with websockets.connect(uri, open_timeout=15):
        print('WebSocket handshake OK', flush=True)


asyncio.run(check_websocket())
if os.environ.get('CHECK_MODEL') == '1':
    url = os.environ['API_BASE_URL'].rstrip('/') + '/chat/completions'
    response = requests.post(url, headers={
        'Authorization': 'Bearer ' + os.environ['API_KEY'],
        'Content-Type': 'application/json',
    }, json={
        'model': os.environ['MODEL_NAME'],
        'messages': [{'role': 'user', 'content': 'Reply with OK only.'}],
        'max_tokens': 64,
        'stream': False,
    }, timeout=60)
    print('Model HTTP status', response.status_code, flush=True)
    response.raise_for_status()
    result = response.json()
    print('Model returned choices:', bool(result.get('choices')), flush=True)
