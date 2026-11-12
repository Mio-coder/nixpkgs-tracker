# Nixpkgs PR watchlist

A personal tracker for the wait between a PR being merged and reaching your
chosen Nixpkgs branches. Runs on your own NixOS machine.

- Star PRs by number or GitHub URL; favourites survive restarts.
- Choose multiple branches from a configurable checklist.
- Check one PR or the entire list now; systemd checks every six hours when installed.
- Read cached results offline. Failures keep the last successful observation.
- No nixpkgs checkout, cloud service, frontend build step or third-party Python dependencies.

## Try it

```sh
nix run path:. -- serve --state-dir .state
```

Open **http://127.0.0.1:8732**. This standalone command only checks when you ask;
install the NixOS module below for background checks. Stop with Ctrl+C.

With Python 3.11+ already installed:

```sh
python3 -m tracker serve --state-dir .state
```

To use a token in standalone mode, append `--token-file /absolute/path/to/token`.
The file contains only the token. A second terminal can refresh the same state:

```sh
python3 -m tracker refresh --state-dir .state --token-file /absolute/path/to/token
```

## NixOS installation

Add this project as an input in your system flake (replace the path):

```nix
inputs.pr-tracker.url = "path:/home/you/code/nixpkgs_pr_tracker";
inputs.pr-tracker.inputs.nixpkgs.follows = "nixpkgs";
```

Include its module in the `modules` list of your `nixosSystem`:

```nix
inputs.pr-tracker.nixosModules.default
```

Then add this to a NixOS configuration module:

```nix
services.nixpkgs-pr-tracker = {
  enable = true;
  tokenFile = "/var/lib/secrets/nixpkgs-pr-tracker-token";

  availableTargets = [
    "staging"
    "staging-next"
    "master"
    "nixos-unstable-small"
    "nixpkgs-unstable"
    "nixos-unstable"
    "nixos-26.05"       # Example explicit stable branch; choose your release.
    "nixos-26.05-small"
  ];
  initialTargets = [ "nixos-unstable" ];
  # Default: checks at 00:00, 06:00, 12:00, 18:00, plus up to 5 minutes jitter.
  # refreshCalendar = "daily";
  # port = 8732;
};
```

Alternatively, import `./nix/module.nix` from an existing local NixOS configuration.
The module packages the application using that configuration's `pkgs`.

Create the token file before activating the module:

```sh
sudo install -d -m 0700 /var/lib/secrets
sudo touch /var/lib/secrets/nixpkgs-pr-tracker-token
sudo chmod 0600 /var/lib/secrets/nixpkgs-pr-tracker-token
sudoedit /var/lib/secrets/nixpkgs-pr-tracker-token
```

Paste only the token into the editor and save. Use a token that can read public
repository data; no GitHub write access is needed. Omit `tokenFile` to run without
authentication. **Keep the path quoted as a string. Do not put the token itself,
`builtins.readFile` of the token, or a Nix path literal into your configuration.**
Systemd reads the file with `LoadCredential` and gives it to each service at runtime.
Restart the web service after rotating the token.

Rebuild your NixOS configuration in your usual way, then open
**http://127.0.0.1:8732**. No firewall port is opened. This project does not run
`nixos-rebuild` for you.

Useful commands:

```sh
systemctl status nixpkgs-pr-tracker
systemctl list-timers nixpkgs-pr-tracker-refresh.timer
sudo systemctl start nixpkgs-pr-tracker-refresh.service
journalctl -u nixpkgs-pr-tracker-refresh -n 30
```

The timer works with the browser closed. It cannot run while your computer is off;
`Persistent=true` catches up on missed calendar checks after boot. Standalone and
NixOS deployments use different state directories unless you configure them alike.

## Targets and results

Use **Save targets** after changing the checklist. Saved selections override
`initialTargets` and survive service restarts. Newly selected branches are marked
**Not checked** until the next check. Removing a branch from `availableTargets`
removes it from the saved selection; if none remain, `initialTargets` is used.

For standalone use, create a JSON configuration and pass `--config targets.json`
to both `serve` and `refresh`:

```json
{
  "availableTargets": ["master", "nixos-unstable", "nixos-26.05"],
  "initialTargets": ["nixos-unstable", "nixos-26.05"]
}
```

**Arrived** means the PR's merge commit is an ancestor of the target branch head.
The checker uses GitHub's `branch...mergeSHA` comparison: `behind`/`identical` means
present; `ahead`/`diverged` means absent. A cherry-picked backport has a different
commit and is not inferred to be the same change. Branch arrival does not mean
your installed system has been updated.

Open/draft PRs await merging. Closed, unmerged PRs are labelled separately. Network
or API errors retain cached results and mark them stale. Unstarring removes only
the local tracking entry, not the PR on GitHub.

## API budget and background activity

A merged PR normally costs one metadata request plus one request per selected
branch; an open PR costs one. GitHub's normal REST allowance is 60 requests/hour
per IP without a token and 5,000/hour per authenticated user, shared with other
apps using that account. The tracker sends requests sequentially, caches ETags,
and persists rate-limit cooldowns. **Check now** also respects a cooldown.
Conditional authenticated requests returning 304 do not consume the primary
allowance. See [GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
and [polling guidance](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).

No background browser interval contacts GitHub. The page reads saved data on
opening, when it regains focus, or when you click **Reload saved results**. While a
check is active it polls only the local service to display progress. A process
lock prevents the timer and manual actions from checking concurrently. If you add
a PR during another check, it is saved immediately and checked on the next run.

## Data and recovery

NixOS state lives in `/var/lib/nixpkgs-pr-tracker`; standalone state defaults to
`$XDG_STATE_HOME/nixpkgs-pr-tracker` (normally `~/.local/state/nixpkgs-pr-tracker`).
This contains SQLite favourites/settings/cached results and a lock file. Tokens
are never stored in SQLite or sent to the browser. For a simple file backup, stop
the web service and timer, wait for any running refresh to finish, then copy the
state directory. Restart the units afterwards.

During development in the restricted Codex workspace, the top-level `.git` mount
was read-only. Recovery commits were made in a separate repository, recorded in
`checkpoints/repository-path.txt`, and exported to `checkpoints/tracker.bundle`.
If that bundle is present, restore a normal repository in a new directory with:

```sh
git clone checkpoints/tracker.bundle ../nixpkgs-pr-tracker-recovered
```

The bundle contains source and history, not tokens, local state, or the reference
clones. It is a local recovery copy; copy it off-machine for an independent backup.

## Development and verification

```sh
nix develop
python -m unittest discover -s tests -v
node --check tracker/static/app.js
node tests/test_ui.js
nix flake check path:.
```

Tests use real SQLite databases, browser-state regression tests with controlled
HTTP timing, the real HTTP parser over byte streams, and
controlled responses at the external HTTP transport boundary. A separate live
loopback test is skipped only when the environment denies network sockets.
The package runs the Python suite during its build; flake checks also assert the
NixOS module's shared state/credentials and timer settings.

The initial lock uses the nixpkgs pin from the supplied ocfox reference. System
flakes should use `inputs.pr-tracker.inputs.nixpkgs.follows = "nixpkgs"`; update
the standalone pin with `nix flake update nixpkgs` when desired.

Inspired by [ocfox/nixpkgs-tracker](https://github.com/ocfox/nixpkgs-tracker) and
[qyliss/pr-tracker](https://git.qyliss.net/pr-tracker). Reference repositories are
unchanged and retain their own licenses. This application is an independent MIT
implementation of the approved design.
