"""One worker shared by manual requests and the systemd timer."""

import fcntl
import threading
import time
from urllib.parse import quote

from .github import APIError, RateLimited


class Refresher:
    def __init__(self, store, client):
        self.store = store
        self.client = client
        self.thread = None

    def _lock(self):
        lock = (self.store.path / 'refresh.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return None
        return lock

    def status(self):
        lock = self._lock()
        running = lock is None
        if lock is not None:
            lock.close()
        return {'running': running, 'last_refresh': self.store.get_setting('last_refresh'),
                'cooldown_until': self.store.get_setting('cooldown_until', 0)}

    def start(self, number=None):
        lock = self._lock()
        if lock is None:
            return {'status': 'busy'}
        self.thread = threading.Thread(target=self._execute, args=(lock, number), daemon=True)
        try:
            self.thread.start()
        except Exception:
            lock.close()
            raise
        return {'status': 'started'}

    def run(self, number=None):
        lock = self._lock()
        if lock is None:
            return {'status': 'busy', 'checked': 0}
        return self._execute(lock, number)

    def _execute(self, lock, number):
        report = {'status': 'running', 'started_at': time.time(), 'finished_at': None, 'checked': 0, 'total': 0, 'error': None}
        try:
            targets = self.store.settings()['targets']
            items = [item for item in self.store.items() if number is None or item['number'] == number]
            report['total'] = len(items)
            self.store.put_setting('last_refresh', report)
            for item in items:
                metadata, branches, error = None, {}, None
                limited = False
                try:
                    data = self.client.get(f'/pulls/{item["number"]}')
                    metadata = self._metadata(data)
                    if metadata['state'] == 'merged':
                        sha = metadata['merge_sha']
                        if not sha:
                            raise APIError('PR is merged, but GitHub has not supplied a merge commit yet.')
                        for index, branch in enumerate(targets):
                            try:
                                result = self.client.get(f'/compare/{quote(branch, safe="")}...{quote(sha, safe="")}?per_page=1')
                                status = result.get('status')
                                if status not in ('behind', 'identical', 'ahead', 'diverged'):
                                    raise APIError('GitHub returned an unknown comparison status.')
                                branches[branch] = {'status': 'present' if status in ('behind', 'identical') else 'absent'}
                            except APIError as exc:
                                branches[branch] = {'error': str(exc)}
                                error = str(exc)
                                if isinstance(exc, RateLimited):
                                    for remaining in targets[index + 1:]:
                                        branches[remaining] = {'error': str(exc)}
                                    limited = True
                                    break
                except APIError as exc:
                    error = str(exc)
                    limited = isinstance(exc, RateLimited)
                self.store.save_result(item['id'], metadata, branches, error)
                report['checked'] += 1
                if error:
                    report['error'] = error
                self.store.put_setting('last_refresh', report)
                if limited:
                    report['status'] = 'cooldown'
                    break
            else:
                report['status'] = 'partial' if report['error'] else 'complete'
        except Exception:
            # Do not leak file paths, credentials or raw remote data into the UI.
            report.update(status='error', error='Refresh stopped unexpectedly. Check the service configuration and state directory.')
        finally:
            report['finished_at'] = time.time()
            try:
                self.store.put_setting('last_refresh', report)
            finally:
                lock.close()
        return report

    @staticmethod
    def _metadata(data):
        try:
            title, base = data['title'], data['base']['ref']
            sha = data.get('merge_commit_sha')
            if not isinstance(title, str) or not isinstance(base, str) or (sha is not None and not isinstance(sha, str)):
                raise ValueError
            if data.get('merged_at'):
                state = 'merged'
            elif data['state'] == 'closed':
                state = 'closed'
            elif data['state'] == 'open':
                state = 'draft' if data.get('draft') else 'open'
            else:
                raise ValueError
            return {'title': title, 'base': base, 'state': state, 'merge_sha': sha}
        except (KeyError, TypeError, ValueError):
            raise APIError('GitHub returned incomplete PR details.') from None
