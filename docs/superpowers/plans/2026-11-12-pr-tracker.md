# Local PR Tracker Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Ship the approved personal NixOS PR watchlist with background API checks.

**Architecture:** Python standard-library service and checker share SQLite state and a process lock. Static browser UI talks only to the loopback service. A NixOS timer runs the same checker used by manual refreshes.

**Tech Stack:** Python 3.11+, SQLite, HTML/CSS/JavaScript, Nix/systemd.

**Spec:** `docs/superpowers/specs/2026-11-12-pr-tracker-design.md` (approved, including target checklist).

## Global Constraints

- Loopback port 8732; six-hour scheduled refresh with jitter and persistence.
- No third-party Python runtime dependencies, GitHub writes or cloud hosting.
- Preserve references, credentials outside Nix store/browser/logs, cached results on failure.
- Multiple saved target selections, common list configurable through NixOS.
- Do not activate system configuration; provide runnable package and example.

## Review Focus

- Removing/re-adding a PR during refresh must not apply obsolete results.
- Rate limits and partial branch failures must preserve successful observations.
- Changing targets during refresh must not mix settings or discard cached data.
- Host/Origin checks must prevent other websites from mutating the loopback service.
- Nix web and timer processes must share the same state, lock and credentials.

## Task 1: Persistent watchlist and settings

Files: `tracker/__init__.py`, `tracker/store.py`, `tests/test_store.py`.
Interfaces: `Store(path, available, defaults)`; `add(raw)`, `remove(number)`,
`items()`, `settings()`, `set_targets(targets)`, `save_result(id, metadata, branches, error)`.

- [ ] Write tests for strict PR input parsing, duplicate adds, remove/re-add identity, persistence, target validation/reconciliation, and stale result retention.
- [ ] Run `python -m unittest discover -s tests -v`; expect missing implementation failures.
- [ ] Implement SQLite transactions, settings and result snapshots keyed by durable row identity and branch.
- [ ] Re-run suite; expect all store tests pass.

## Task 2: GitHub API and shared refresh worker

Files: `tracker/github.py`, `tracker/refresh.py`, `tests/test_refresh.py`.
Interfaces: `GitHub(store, token_file=None).get(path)` returns JSON;
`Refresher(store, client).run(number=None)` returns status, `start(number=None)` starts background work.

- [ ] Test real HTTP client against local fixture server: conditional requests, ancestry direction, timeouts/errors, retry/reset cooldown. Test refresh against real storage, missing SHA, partial failure, locking and removal during requests.
- [ ] Run suite; expect missing API/refresh failures.
- [ ] Implement API calls, persisted response cache/cooldown, per-PR error isolation, flock and serialized worker lifecycle.
- [ ] Run full suite; expect pass.

## Task 3: Local dashboard and HTTP interface

Files: `tracker/web.py`, `tracker/__main__.py`, `tracker/static/{index.html,app.js,style.css}`, `tests/test_web.py`.
Interfaces: `make_server(store, refresher, port)`; CLI subcommands `serve`, `refresh` with shared state/config/token flags. GET `/api/state`; POST `/api/prs`, `/api/remove`, `/api/refresh`, `/api/targets`.

- [ ] Write integration tests using real HTTP server: add/list/remove, selection persistence, invalid JSON/content length, Host/Origin protection, cached reads and token non-exposure.
- [ ] Run suite; expect missing web failures.
- [ ] Implement routes and CLI plus accessible watchlist, branch checklist, errors, last-check times and bounded active-refresh polling. Use textContent for untrusted text.
- [ ] Run suite and JavaScript syntax check; exercise UI in browser if available.

## Task 4: Nix package, module and documentation

Files: `flake.nix`, `flake.lock`, `nix/{package,module}.nix`, `tests/nixos-eval.nix`, `README.md`, `.gitignore`, `LICENSE`.
Interfaces: default package/app/devShell, `nixosModules.default`, service `services.nixpkgs-pr-tracker` with availableTargets/initialTargets options.

- [ ] Implement source-filtered package with unit tests, configurable module, dedicated account and credential loading, shared state, persistent six-hour timer and example setup.
- [ ] Run package build and module evaluation assertions for address, timer, credentials and settings; expect success or document concrete environment block.
- [ ] Run live read-only smoke check if network permits; document exact startup commands and verification limits.
- [ ] Request independent whole-project review, address important findings with regression tests, run final suite and record results.

## Execution ledger

Ruling: Execute inline after design approval; implementation is already authorized. No additional plan approval is needed.

Ruling: Workspace .git is an empty read-only sandbox mount and git reports no repository. Work in the supplied directory; no commits/worktree operations possible. Keep references unchanged.

Pre-flight: Store interfaces feed both refresh and HTTP; both use one persistent configuration and lock path. CLI passes the same arguments to web and timer.
