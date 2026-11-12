import tempfile
import unittest
from pathlib import Path

from tracker.store import Store, parse_pr


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = Store(self.path, ['master', 'nixos-unstable'], ['nixos-unstable'])

    def test_input_accepts_only_nixpkgs_prs(self):
        for raw in ['123', '#123', ' https://github.com/NixOS/nixpkgs/pull/123 ',
                    'https://github.com/NixOS/nixpkgs/pull/123/files']:
            self.assertEqual(parse_pr(raw), 123)
        for raw in ['', '0', '-1', True, '12.0', 'https://evil.test/github.com/NixOS/nixpkgs/pull/123',
                    'https://github.com/other/repo/pull/123', '1' * 100, 'https://github.com/NixOS/nixpkgs/pull/123x']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_pr(raw)

    def test_favourites_persist_and_duplicate_add_is_idempotent(self):
        first = self.store.add('#123')
        self.assertEqual(self.store.add('123')['id'], first['id'])
        reopened = Store(self.path, ['master', 'nixos-unstable'], ['master'])
        self.assertEqual([x['number'] for x in reopened.items()], [123])
        self.assertEqual(reopened.settings()['targets'], ['nixos-unstable'])

    def test_removed_and_readded_pr_rejects_old_worker_result(self):
        first = self.store.add('123')
        self.store.remove(123)
        second = self.store.add('123')
        self.assertNotEqual(first['id'], second['id'])
        self.store.save_result(first['id'], {'title': 'old', 'state': 'merged'}, {}, None)
        self.assertNotEqual(self.store.items()[0]['title'], 'old')

    def test_selection_persists_and_new_target_is_unknown(self):
        self.store.add('123')
        self.store.set_targets(['master', 'nixos-unstable'])
        reopened = Store(self.path, ['master', 'nixos-unstable'], ['master'])
        self.assertEqual(reopened.settings()['targets'], ['master', 'nixos-unstable'])
        self.assertEqual(reopened.items()[0]['branches']['master']['status'], 'unknown')
        for targets in [[], ['unknown'], 'master', ['master', 1]]:
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                self.store.set_targets(targets)

    def test_removed_config_targets_fall_back_to_initial_selection(self):
        self.store.set_targets(['master'])
        reopened = Store(self.path, ['nixos-unstable'], ['nixos-unstable'])
        self.assertEqual(reopened.settings()['targets'], ['nixos-unstable'])

    def test_failure_retains_last_good_branch_result(self):
        pr = self.store.add('123')
        self.store.save_result(pr['id'], {'title': 'Hello', 'state': 'merged', 'base': 'master', 'merge_sha': 'abc'},
                               {'nixos-unstable': {'status': 'present'}}, None)
        before = self.store.items()[0]
        self.store.save_result(pr['id'], None, {'nixos-unstable': {'error': 'Network unavailable'}}, 'Check failed')
        after = self.store.items()[0]
        self.assertEqual(after['title'], 'Hello')
        self.assertEqual(after['last_success'], before['last_success'])
        self.assertEqual(after['branches']['nixos-unstable']['status'], 'present')
        self.assertEqual(after['branches']['nixos-unstable']['error'], 'Network unavailable')
        self.assertEqual(after['error'], 'Check failed')

    def test_invalid_initial_config_rejected(self):
        for available, defaults in [([], []), (['master'], []), (['master'], ['nope']), (['bad..branch'], ['bad..branch'])]:
            with self.subTest(available=available), self.assertRaises(ValueError):
                Store(self.path, available, defaults)


if __name__ == '__main__':
    unittest.main()
