import io
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

from tracker.github import GitHub
from tracker.refresh import Refresher
from tracker.store import Store
from tracker.web import make_handler, make_server


class MemorySocket:
    """Transport only: the real HTTP handler parses and dispatches every byte."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, *args):
        return self.input

    def sendall(self, data):
        self.output.write(data)

    def settimeout(self, value):
        pass


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name), ['master', 'nixos-unstable'], ['nixos-unstable'])
        self.worker = Refresher(self.store, GitHub(self.store))
        self.handler = make_handler(self.store, self.worker, schedule_label='Every 6 hours')

    def request(self, path='/api/state', method='GET', data=None, headers=None, raw=None):
        body = json.dumps(data).encode() if data is not None else b''
        fields = {'Host': '127.0.0.1:8732'}
        if method == 'POST':
            fields.update({'Content-Type': 'application/json', 'Content-Length': str(len(body)),
                           'Origin': 'http://127.0.0.1:8732', 'X-Tracker-Request': '1'})
        fields.update(headers or {})
        request = raw or (f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n').encode() + body
        transport = MemorySocket(request)
        self.handler(transport, ('127.0.0.1', 12345), SimpleNamespace(server_port=8732))
        response = transport.output.getvalue()
        head, body = response.split(b'\r\n\r\n', 1)
        return int(head.split(b' ')[1]), body, head

    def test_state_is_cached_and_never_exposes_token(self):
        self.worker.client.token = 'secret-test-token'
        self.store.add('123')
        status, body, _ = self.request()
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(state['prs'][0]['number'], 123)
        self.assertTrue(state['token_configured'])
        self.assertNotIn(b'secret-test-token', body)
        self.assertIsNone(state['prs'][0]['last_attempt'])

    def test_add_and_remove_work_while_another_refresh_holds_lock(self):
        lock = self.worker._lock()
        self.addCleanup(lock.close)
        status, body, _ = self.request('/api/prs', 'POST', {'pr': 'https://github.com/NixOS/nixpkgs/pull/123'})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)['refresh']['status'], 'busy')
        self.assertEqual(self.store.items()[0]['number'], 123)
        self.assertEqual(self.request('/api/remove', 'POST', {'number': 123})[0], 200)
        self.assertEqual(self.store.items(), [])

    def test_targets_saved_without_starting_refresh(self):
        self.assertEqual(self.request('/api/targets', 'POST', {'targets': ['master', 'nixos-unstable']})[0], 200)
        self.assertEqual(self.store.settings()['targets'], ['master', 'nixos-unstable'])
        self.assertFalse(self.worker.status()['running'])
        self.assertEqual(self.request('/api/targets', 'POST', {'targets': []})[0], 400)

    def test_rejects_cross_origin_and_dns_rebinding(self):
        for headers in [{'Host': 'evil.test:8732'}, {'Origin': 'https://evil.test'},
                        {'Origin': 'null'}, {'X-Tracker-Request': ''}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/prs', 'POST', {'pr': 123}, headers)[0], 403)
        self.assertEqual(self.request(headers={'Host': 'evil.test:8732'})[0], 403)
        self.assertEqual(self.store.items(), [])

    def test_bad_payloads_and_oversized_requests_rejected(self):
        for data in [[], '123', None, {'pr': 'https://github.com/other/repo/pull/123'}]:
            with self.subTest(data=data):
                self.assertEqual(self.request('/api/prs', 'POST', data)[0], 400)
        self.assertEqual(self.request('/api/prs', 'POST', {'pr': 1}, {'Content-Length': '20000'})[0], 413)
        self.assertEqual(self.request('/api/prs', 'POST', {'pr': 1}, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('/api/prs', 'POST', {'pr': 1}, {'Transfer-Encoding': 'chunked'})[0], 400)

    def test_static_files_are_allowlisted_and_have_security_headers(self):
        for path in ['/', '/app.js', '/style.css']:
            status, body, headers = self.request(path)
            self.assertEqual(status, 200)
            self.assertGreater(len(body), 0)
            self.assertIn(b'Content-Security-Policy:', headers)
        self.assertEqual(self.request('/../tracker.sqlite3')[0], 404)

    def test_live_loopback_server(self):
        try:
            probe = socket.socket()
            probe.close()
        except PermissionError:
            self.skipTest('Environment blocks network sockets; HTTP byte-stream tests still run.')
        server = make_server(self.store, self.worker, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/state') as response:
                self.assertEqual(json.load(response)['prs'], [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
