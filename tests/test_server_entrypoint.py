"""Exercise the historical path actually configured by the production service."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class ServerEntrypointTests(unittest.TestCase):
    def test_import_aliases_use_same_app(self):
        import cloud_app
        from cosight_server.deep_research.main import app
        self.assertIs(app, cloud_app.app)

    def test_service_command_exposes_modern_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            environment = dict(os.environ, COSIGHT_DATA_DIR=directory, COSIGHT_HOST='127.0.0.1')
            process = subprocess.Popen([sys.executable, str(ROOT/'cosight_server/deep_research/main.py'), '-p', str(port)], cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                import json
                for _ in range(100):
                    self.assertIsNone(process.poll(), 'Server exited before becoming ready')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=.5) as response:
                            self.assertEqual(json.load(response)['mode'], 'multi-user-study')
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(.1)
                else:
                    self.fail('Server startup timed out')
                for route in ('runs', 'today-plan', 'materials', 'study-schedule'):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(f'http://127.0.0.1:{port}/api/nae-deep-research/v1/deep-research/{route}')
                    self.assertEqual(caught.exception.code, 401, route)
            finally:
                process.terminate()
                process.wait(timeout=15)

if __name__ == '__main__':
    unittest.main()
