"""Persistent watchlist, settings and HTTP cache. Each operation owns its connection."""

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

COMMON_TARGETS = ['staging', 'staging-next', 'master', 'nixos-unstable-small',
                  'nixpkgs-unstable', 'nixos-unstable']


def parse_pr(raw):
    if not isinstance(raw, (str, int)) or isinstance(raw, bool):
        raise ValueError('Enter a NixOS/nixpkgs PR number or GitHub PR URL.')
    value = str(raw).strip()
    if value.startswith('https://'):
        url = urlsplit(value)
        match = re.fullmatch(r'/NixOS/nixpkgs/pull/([0-9]+)(?:/(?:files|commits|checks))?/?', url.path, re.I)
        if url.netloc.lower() != 'github.com' or not match:
            raise ValueError('Use a pull request from github.com/NixOS/nixpkgs.')
        value = match.group(1)
    if not re.fullmatch(r'#?[0-9]{1,10}', value):
        raise ValueError('Enter a positive PR number, such as #512987.')
    number = int(value.lstrip('#'))
    if not 0 < number <= 2_147_483_647:
        raise ValueError('PR number is outside the supported range.')
    return number


def valid_branch(branch):
    return (isinstance(branch, str) and 0 < len(branch) <= 200
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch)
            and not any(x in branch for x in ['..', '//', '/.', '.lock'])
            and not branch.endswith(('/', '.')))


class Store:
    def __init__(self, path, available=None, defaults=None):
        self.path = Path(path)
        self.available = list(COMMON_TARGETS if available is None else available)
        self.defaults = list(['nixos-unstable'] if defaults is None else defaults)
        if (not self.available or not all(valid_branch(x) for x in self.available)
                or not self.defaults or not all(x in self.available for x in self.defaults)):
            raise ValueError('Available targets and initial targets must be nonempty valid branch lists; initial targets must be available.')
        self.available = list(dict.fromkeys(self.available))
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db_path = self.path / 'tracker.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS prs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number INTEGER UNIQUE NOT NULL,
                    title TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'unknown',
                    base TEXT, merge_sha TEXT, added_at REAL NOT NULL,
                    last_attempt REAL, last_success REAL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS branches (
                    pr_id INTEGER NOT NULL REFERENCES prs(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown',
                    checked_at REAL, error TEXT, PRIMARY KEY(pr_id, name)
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS http_cache (path TEXT PRIMARY KEY, etag TEXT, body TEXT NOT NULL);
            ''')
            saved = self._setting(db, 'targets', self.defaults)
            selected = [x for x in saved if x in self.available] or self.defaults
            self._put_setting(db, 'targets', selected)
        self.db_path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _setting(db, key, default=None):
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    @staticmethod
    def _put_setting(db, key, value):
        db.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', (key, json.dumps(value)))

    def get_setting(self, key, default=None):
        with self.connect() as db:
            return self._setting(db, key, default)

    def put_setting(self, key, value):
        with self.connect() as db:
            self._put_setting(db, key, value)

    def settings(self):
        return {'available_targets': self.available, 'targets': self.get_setting('targets', self.defaults)}

    def set_targets(self, targets):
        if (not isinstance(targets, list) or not targets
                or not all(isinstance(x, str) and x in self.available for x in targets)):
            raise ValueError('Select at least one target from the available branches.')
        self.put_setting('targets', list(dict.fromkeys(targets)))
        return self.settings()

    def add(self, raw):
        number = parse_pr(raw)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO prs(number, added_at) VALUES (?, ?)', (number, time.time()))
            return dict(db.execute('SELECT * FROM prs WHERE number = ?', (number,)).fetchone())

    def remove(self, number):
        with self.connect() as db:
            db.execute('DELETE FROM prs WHERE number = ?', (parse_pr(number),))

    def items(self):
        with self.connect() as db:
            targets = self._setting(db, 'targets', self.defaults)
            items = []
            for row in db.execute('SELECT * FROM prs ORDER BY added_at DESC, id DESC'):
                item = dict(row)
                cached = {b['name']: dict(b) for b in db.execute('SELECT * FROM branches WHERE pr_id = ?', (row['id'],))}
                item['branches'] = {}
                for target in targets:
                    branch = cached.get(target, {'status': 'unknown', 'checked_at': None, 'error': None})
                    if row['state'] in ('open', 'draft', 'closed'):
                        branch = {'status': 'waiting', 'checked_at': row['last_success'], 'error': None}
                    item['branches'][target] = {k: branch[k] for k in ('status', 'checked_at', 'error')}
                items.append(item)
            return items

    def save_result(self, pr_id, metadata, branches, error):
        now = time.time()
        with self.connect() as db:
            # Acquire a write transaction before checking identity: remove/re-add
            # must not attach an old request's results to a new watchlist entry.
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM prs WHERE id = ?', (pr_id,)).fetchone():
                return
            db.execute('UPDATE prs SET last_attempt = ?, error = ? WHERE id = ?', (now, error, pr_id))
            if metadata is not None:
                db.execute('UPDATE prs SET title=?, state=?, base=?, merge_sha=? WHERE id=?',
                           (metadata.get('title', ''), metadata['state'], metadata.get('base'), metadata.get('merge_sha'), pr_id))
            if not error:
                db.execute('UPDATE prs SET last_success = ? WHERE id = ?', (now, pr_id))
            for name, result in branches.items():
                db.execute('INSERT OR IGNORE INTO branches(pr_id, name) VALUES (?, ?)', (pr_id, name))
                if result.get('error'):
                    db.execute('UPDATE branches SET error=? WHERE pr_id=? AND name=?', (result['error'], pr_id, name))
                else:
                    db.execute('UPDATE branches SET status=?, checked_at=?, error=NULL WHERE pr_id=? AND name=?',
                               (result['status'], now, pr_id, name))

    def cached_response(self, path):
        with self.connect() as db:
            row = db.execute('SELECT etag, body FROM http_cache WHERE path=?', (path,)).fetchone()
            return (row['etag'], json.loads(row['body'])) if row else (None, None)

    def cache_response(self, path, etag, body):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?)', (path, etag, json.dumps(body)))
            # Bound disk usage when branch heads/merge SHAs change over time.
            db.execute('DELETE FROM http_cache WHERE rowid NOT IN (SELECT rowid FROM http_cache ORDER BY rowid DESC LIMIT 2000)')
