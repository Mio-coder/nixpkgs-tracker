"""Read-only GitHub REST client with durable caching and rate-limit backoff."""

import json
import socket
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class APIError(Exception):
    pass


class RateLimited(APIError):
    def __init__(self, until):
        self.until = until
        super().__init__('GitHub rate limit reached. Checks are paused until the cooldown expires.')


class GitHub:
    def __init__(self, store, token_file=None, *, api_base='https://api.github.com/repos/NixOS/nixpkgs', min_interval=0.25):
        self.store = store
        self.api_base = api_base.rstrip('/')
        self.token = Path(token_file).read_text().strip() if token_file else ''
        if any(c.isspace() for c in self.token):
            raise ValueError('The GitHub token file must contain one token.')
        self.min_interval = min_interval
        self.last_request = 0

    def _rate_limit(self, headers):
        now = time.time()
        until = now + 60
        try:
            until = max(until, float(headers.get('X-RateLimit-Reset', '0')) + 1)
        except ValueError:
            pass
        retry = headers.get('Retry-After', '')
        try:
            until = max(until, now + float(retry))
        except ValueError:
            try:
                date = parsedate_to_datetime(retry)
                until = max(until, date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp())
            except (ValueError, TypeError, OverflowError):
                pass
        # Retain the later deadline when the server returns multiple constraints.
        until = max(until, self.store.get_setting('cooldown_until', 0))
        self.store.put_setting('cooldown_until', until)
        return RateLimited(until)

    def get(self, path):
        until = self.store.get_setting('cooldown_until', 0)
        if until > time.time():
            raise RateLimited(until)
        etag, cached = self.store.cached_response(path)
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'local-nixpkgs-pr-tracker/0.1'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if etag:
            headers['If-None-Match'] = etag
        delay = self.min_interval - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        request = Request(self.api_base + path, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                # Compare responses can contain a large commit list; use per_page=1
                # at the caller and a bounded read as a final resource guard.
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise APIError('GitHub returned an unexpectedly large response.')
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise APIError('GitHub returned an unexpected response.')
                self.store.cache_response(path, response.headers.get('ETag'), data)
                return data
        except HTTPError as exc:
            with exc:
                if exc.code == 304 and cached is not None:
                    return cached
                message = exc.read(8192).decode('utf-8', errors='replace').lower()
                if (exc.code == 429 or (exc.code == 403 and (
                        exc.headers.get('X-RateLimit-Remaining') == '0'
                        or exc.headers.get('Retry-After') or 'rate limit' in message))):
                    raise self._rate_limit(exc.headers) from None
                messages = {401: 'GitHub rejected the token. Update the token file and restart the service.',
                            403: 'GitHub denied access to this resource.',
                            404: 'PR, commit or target branch not found on GitHub.',
                            422: 'GitHub could not compare this commit and branch.'}
                raise APIError(messages.get(exc.code, f'GitHub request failed (HTTP {exc.code}).')) from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise APIError('Cannot reach GitHub. Cached results are still available.') from None
        except (ValueError, UnicodeError):
            raise APIError('GitHub returned invalid JSON.') from None
