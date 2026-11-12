"""Loopback-only HTTP UI. No external assets or third-party web framework."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .store import parse_pr

STATIC = Path(__file__).parent / 'static'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


def make_handler(store, refresher, schedule_label='Manual checks only'):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'NixpkgsTracker/0.1'

        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *args):
            # Avoid logging user-provided paths or payloads.
            pass

        def reply(self, status, value, content_type='application/json; charset=utf-8'):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self, mutate=False):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            host = self.headers.get('Host', '')
            origins = self.headers.get_all('Origin', [])
            valid = len(self.headers.get_all('Host', [])) == 1 and host in hosts
            if origins:
                valid = valid and origins == ['http://' + host]
            if mutate:
                valid = valid and self.headers.get('X-Tracker-Request') == '1'
            if not valid:
                self.reply(403, {'error': 'This dashboard accepts requests from its own localhost page only.'})
            return valid

        def do_GET(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            if path == '/api/state':
                self.reply(200, {'prs': store.items(), **store.settings(), **refresher.status(),
                                 'token_configured': bool(refresher.client.token), 'schedule': schedule_label})
            elif path in ASSETS:
                filename, content_type = ASSETS[path]
                self.reply(200, (STATIC / filename).read_bytes(), content_type)
            else:
                self.reply(404, {'error': 'Not found.'})

        def do_POST(self):
            if not self.allowed(mutate=True):
                return
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                self.reply(400, {'error': 'Send one Content-Length header and a JSON body.'})
                return
            if self.headers.get_content_type() != 'application/json':
                self.reply(415, {'error': 'Send application/json.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length > 16384:
                    self.reply(413, {'error': 'Request is too large.'})
                    return
                if length <= 0:
                    raise ValueError('Send a JSON object.')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('Send a JSON object.')
                path = urlsplit(self.path).path
                if path == '/api/prs':
                    item = store.add(payload.get('pr'))
                    self.reply(201, {'number': item['number'], 'refresh': refresher.start(item['number'])})
                elif path == '/api/remove':
                    store.remove(payload.get('number'))
                    self.reply(200, {'status': 'removed'})
                elif path == '/api/targets':
                    self.reply(200, store.set_targets(payload.get('targets')))
                elif path == '/api/refresh':
                    number = parse_pr(payload['number']) if 'number' in payload else None
                    if number is not None and not any(x['number'] == number for x in store.items()):
                        self.reply(404, {'error': 'Add this PR to your watchlist first.'})
                        return
                    self.reply(202, refresher.start(number))
                else:
                    self.reply(404, {'error': 'Not found.'})
            except (ValueError, UnicodeError) as exc:
                message = 'Send valid JSON.' if isinstance(exc, (json.JSONDecodeError, UnicodeError)) else str(exc)
                self.reply(400, {'error': message})
            except (TimeoutError, OSError):
                self.reply(500, {'error': 'Could not complete the request. Check the local service and state directory.'})
            except Exception:
                print('Request failed unexpectedly; check state directory access.', file=sys.stderr)
                self.reply(500, {'error': 'The local service could not complete the request.'})

    return Handler


def make_server(store, refresher, port=8732, schedule_label='Manual checks only'):
    return ThreadingHTTPServer(('127.0.0.1', port), make_handler(store, refresher, schedule_label))
