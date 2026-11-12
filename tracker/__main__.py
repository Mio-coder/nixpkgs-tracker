"""Run with python -m tracker serve or python -m tracker refresh."""

import argparse
import json
import os
import sys
from pathlib import Path

from .github import GitHub
from .refresh import Refresher
from .store import COMMON_TARGETS, Store
from .web import make_server


def main(argv=None):
    parser = argparse.ArgumentParser(description='A local Nixpkgs PR watchlist.')
    parser.add_argument('command', choices=['serve', 'refresh'])
    parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'nixpkgs-pr-tracker')
    parser.add_argument('--token-file', type=Path, help='File containing a GitHub token (never put the token in Nix source).')
    parser.add_argument('--config', type=Path, help='JSON configuration: availableTargets and initialTargets.')
    parser.add_argument('--port', type=int, default=8732)
    parser.add_argument('--schedule-label', default='Manual checks only')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        config = json.loads(args.config.read_text()) if args.config else {}
        if not isinstance(config, dict):
            raise ValueError('Configuration must be a JSON object.')
        if not 1 <= args.port <= 65535:
            raise ValueError('Port must be between 1 and 65535.')
        for key in ('availableTargets', 'initialTargets'):
            if key in config and not isinstance(config[key], list):
                raise ValueError(f'{key} must be a list of branch names.')
        store = Store(args.state_dir, config.get('availableTargets', COMMON_TARGETS), config.get('initialTargets', ['nixos-unstable']))
        refresher = Refresher(store, GitHub(store, args.token_file))
        if args.command == 'refresh':
            result = refresher.run()
            print(json.dumps(result))
            return 1 if result['status'] in ('partial', 'error') else 0
        server = make_server(store, refresher, args.port, args.schedule_label)
        print(f'Nixpkgs tracker: http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (OSError, ValueError) as exc:
        print(f'Cannot start tracker: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
