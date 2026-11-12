import json
import io
import tempfile
import threading
import time
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from tracker.github import GitHub, APIError, RateLimited
from tracker.refresh import Refresher
from tracker.store import Store


class FixtureAPI:
    def __init__(self):
        self.responses = {}
        self.requests = []
        self.url = 'https://api.github.test'
        self.patch = patch('tracker.github.urlopen', self.open)
        self.patch.start()

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        path = url.path + ('?' + url.query if url.query else '')
        self.requests.append((path, {k.lower(): v for k, v in request.header_items()}))
        value = self.responses.get(path, (404, {}, {}))
        if callable(value):
            value = value(request)
        status, headers, body = value
        message = Message()
        for key, value in headers.items():
            message[key] = value
        stream = io.BytesIO(json.dumps(body).encode())
        if status >= 300:
            raise HTTPError(request.full_url, status, 'fixture response', message, stream)
        stream.headers = message
        return stream

    def close(self):
        self.patch.stop()


def merged(sha='abcdef'):
    return {'title': 'Example package update', 'state': 'closed', 'draft': False,
            'merged_at': '2026-01-01T00:00:00Z', 'merge_commit_sha': sha, 'base': {'ref': 'master'}}


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name), ['master', 'nixos-unstable'], ['master', 'nixos-unstable'])
        self.api = FixtureAPI()
        self.addCleanup(self.api.close)
        self.client = GitHub(self.store, api_base=self.api.url, min_interval=0)
        self.worker = Refresher(self.store, self.client)
        self.api.responses['/pulls/123'] = (200, {}, merged())

    def test_branch_comparison_direction(self):
        self.store.add('123')
        for status, expected in [('behind', 'present'), ('identical', 'present'),
                                 ('ahead', 'absent'), ('diverged', 'absent')]:
            with self.subTest(status=status):
                for branch in ['master', 'nixos-unstable']:
                    self.api.responses[f'/compare/{branch}...abcdef?per_page=1'] = (200, {}, {'status': status})
                self.assertEqual(self.worker.run()['status'], 'complete')
                self.assertEqual(self.store.items()[0]['branches']['nixos-unstable']['status'], expected)

    def test_open_and_closed_prs_do_not_compare_unmerged_sha(self):
        self.store.add('123')
        for state, draft, expected in [('open', False, 'open'), ('open', True, 'draft'), ('closed', False, 'closed')]:
            data = merged()
            data.update(state=state, draft=draft, merged_at=None)
            self.api.responses['/pulls/123'] = (200, {}, data)
            self.worker.run()
            item = self.store.items()[0]
            self.assertEqual(item['state'], expected)
            self.assertEqual(item['branches']['master']['status'], 'waiting')
        self.assertEqual([x[0] for x in self.api.requests], ['/pulls/123'] * 3)

    def test_missing_merge_sha_is_error_not_arrival(self):
        self.store.add('123')
        self.api.responses['/pulls/123'] = (200, {}, merged(None))
        self.assertEqual(self.worker.run()['status'], 'partial')
        self.assertTrue(self.store.items()[0]['error'])
        self.assertEqual(len(self.api.requests), 1)

    def test_etag_revalidation_reuses_persisted_body(self):
        self.api.responses['/pulls/123'] = (200, {'ETag': '"v1"'}, merged())
        first = self.client.get('/pulls/123')
        self.api.responses['/pulls/123'] = (304, {}, None)
        client = GitHub(self.store, api_base=self.api.url, min_interval=0)
        self.assertEqual(client.get('/pulls/123'), first)
        self.assertEqual(self.api.requests[-1][1].get('if-none-match'), '"v1"')

    def test_rate_limit_stops_batch_and_survives_restart(self):
        self.store.add('124')
        self.store.add('123')
        self.api.responses['/pulls/123'] = (403, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': str(int(time.time()) + 600)}, {'message': 'rate limit'})
        self.assertEqual(self.worker.run()['status'], 'cooldown')
        self.assertEqual(len(self.api.requests), 1)
        client = GitHub(self.store, api_base=self.api.url, min_interval=0)
        with self.assertRaises(RateLimited):
            client.get('/pulls/124')
        self.assertEqual(len(self.api.requests), 1)

    def test_retry_after_is_respected_for_secondary_limit(self):
        self.api.responses['/pulls/123'] = (429, {'Retry-After': '120'}, {'message': 'slow down'})
        before = time.time()
        with self.assertRaises(RateLimited):
            self.client.get('/pulls/123')
        self.assertGreaterEqual(self.store.get_setting('cooldown_until'), before + 119)

    def test_non_rate_403_does_not_create_cooldown(self):
        self.api.responses['/pulls/123'] = (403, {}, {'message': 'Resource not accessible'})
        with self.assertRaises(APIError):
            self.client.get('/pulls/123')
        self.assertFalse(self.store.get_setting('cooldown_until'))

    def test_partial_failure_keeps_good_results(self):
        self.store.add('123')
        self.api.responses['/compare/master...abcdef?per_page=1'] = (200, {}, {'status': 'behind'})
        self.api.responses['/compare/nixos-unstable...abcdef?per_page=1'] = (200, {}, {'status': 'ahead'})
        self.worker.run()
        before = self.store.items()[0]
        self.api.responses['/compare/nixos-unstable...abcdef?per_page=1'] = (500, {}, {})
        self.assertEqual(self.worker.run()['status'], 'partial')
        item = self.store.items()[0]
        self.assertEqual(item['last_success'], before['last_success'])
        self.assertEqual(item['branches']['master']['status'], 'present')
        self.assertEqual(item['branches']['nixos-unstable']['status'], 'absent')
        self.assertTrue(item['branches']['nixos-unstable']['error'])

    def test_background_lock_and_selection_snapshot(self):
        self.store.add('123')
        entered, release = threading.Event(), threading.Event()

        def paused(_):
            entered.set()
            release.wait(5)
            return 200, {}, merged()

        self.api.responses['/pulls/123'] = paused
        for branch in ['master', 'nixos-unstable']:
            self.api.responses[f'/compare/{branch}...abcdef?per_page=1'] = (200, {}, {'status': 'behind'})
        self.assertEqual(self.worker.start()['status'], 'started')
        self.assertTrue(entered.wait(3))
        try:
            self.assertTrue(self.worker.status()['running'])
            other = Refresher(self.store, self.client)
            self.assertEqual(other.run()['status'], 'busy')
            self.store.set_targets(['master'])
        finally:
            release.set()
        self.worker.thread.join(5)
        self.assertFalse(self.worker.status()['running'])
        self.assertIn('/compare/nixos-unstable...abcdef?per_page=1', [x[0] for x in self.api.requests])
        self.assertEqual(list(self.store.items()[0]['branches']), ['master'])

    def test_empty_watchlist_makes_no_requests(self):
        self.assertEqual(self.worker.run()['checked'], 0)
        self.assertEqual(self.api.requests, [])

    def test_remove_during_refresh_does_not_resurrect(self):
        self.store.add('123')

        def removed(_):
            self.store.remove(123)
            self.store.add('123')
            return 200, {}, merged()

        self.api.responses['/pulls/123'] = removed
        self.worker.run()
        self.assertEqual(self.store.items()[0]['title'], '')


if __name__ == '__main__':
    unittest.main()
