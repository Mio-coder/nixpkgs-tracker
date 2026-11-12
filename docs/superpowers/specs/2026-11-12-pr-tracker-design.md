# Local Nixpkgs PR tracker

## Intent and agreed choices

Build a personal tracker on the user's own NixOS machine, inspired by
`reference/nixpkgs-tracker` and `reference/pr-tracker`. The user wants to
star PRs, check their progress on demand, and refresh infrequently in the
background. They chose GitHub API tracking and accept use of a GitHub token.
Targets must be selectable in the dashboard from a list of common branches,
with multiple selections, like ocfox's nixpkgs-tracker.

## Approaches

1. **GitHub API plus local storage (chosen):** fetch PR metadata and compare
   merge commits against selected branches. No local nixpkgs clone; subject
   to GitHub availability and rate limits. Best fit for a personal watchlist.
2. **Local Git ancestry checks:** use a maintained nixpkgs checkout, as the
   qyliss tracker does. Fewer remote comparison calls but more storage and
   Git fetching. Not selected.

## Proposed defaults (builder's choices, subject to design approval)

- Python standard-library backend, SQLite database, and a small HTML/CSS/JS
  dashboard. No frontend build pipeline or external runtime Python packages.
- Nix flake package and NixOS module. Local HTTP service bound to
  `127.0.0.1:8732`; systemd timer invokes a separate one-shot refresh command
  every six hours, with a short randomized delay. The dashboard service is
  idle between requests; no tight polling loop.
- SQLite and cached results live under `/var/lib/nixpkgs-pr-tracker` in the
  module deployment. Standalone commands accept a configurable state path.
- Default selected target is `nixos-unstable`. A dashboard checklist offers
  multiple common targets: `staging`, `staging-next`, `master`,
  `nixos-unstable-small`, `nixpkgs-unstable`, `nixos-unstable`, and configured
  stable release targets. The NixOS module supplies the available list and
  initial selections, including explicit stable branch names; do not infer
  the current release from the date. SQLite persists dashboard selections.
  Initial selections apply only when no saved settings exist. A single
  saved selection applies to all PRs and both scheduled and manual checks.
- Optional token file is supplied through systemd credentials; store neither
  its contents in the Nix store nor in the browser/database/logs. Standalone
  commands accept a token file too.

## Dashboard and saved data

Accept a positive PR number, `#number`, or an exact NixOS/nixpkgs GitHub PR
URL. Reject other repositories rather than silently tracking the same number
in nixpkgs. Adding a PR stars it and starts a check; duplicate adds are safe.

Show a persistent watchlist with title, GitHub link, open/draft/merged/closed
state, target branch statuses, last successful check and any refresh error.
Provide unstar/remove, check-one, and check-all actions. Removing a favourite
only removes its local tracking record. Display an empty state and clear
loading/error states. Retain cached results across restarts and offline use.

Show the target checklist with an explicit Save targets action and require
at least one selection. Saving changes does not trigger GitHub requests;
new targets display as not checked until the next scheduled or manual check.
Hide deselected targets from cards and omit them from subsequent checks.
Each refresh snapshots its target selection at start, so changes made during
a refresh apply to the next run. Keep cached results keyed by PR and branch.
If module configuration removes a saved target, reconcile selections against
the available list and fall back to configured initial selections when none
remain. Validate that the available list and initial selections are nonempty
and that initial selections belong to the available list.

Per-branch states are present, not present, unknown/error, or waiting for
merge. An API failure must never appear as proof a PR is absent. Clearly mark
old results after a failed refresh and distinguish last attempt from last
success. Viewing the dashboard reads local results without implicitly
triggering GitHub requests. Refresh activity may briefly poll the local API
while a user-initiated check is running.

## GitHub checks and resource use

Use the REST pull-request endpoint for metadata. Only merged PRs with a
merge commit SHA need branch comparisons. Compare `branch...mergeSHA`:
`behind` or `identical` means the branch contains the merge commit; `ahead`
or `diverged` means it does not. This establishes commit ancestry, not
semantic equivalence after cherry-picks, or the contents of the user's
installed system. Release PRs are checked against the configured targets
rather than assumed to have arrived merely because they merged.

Use sequential requests, finite timeouts, conditional requests where
supported, and a shared persisted cooldown informed by GitHub's reset and
Retry-After headers. On rate limiting, stop the batch and report when another
attempt is allowed; never repeatedly retry in a tight loop. A later scheduled
or manual check resumes after the cooldown. An optional token increases the
budget but is not required for a small list.

One refresh worker at a time across the service and timer, enforced with a
process lock. Manual requests return promptly with running/busy status.
Database writes use short transactions; removing a PR while a check is in
progress must not resurrect it. No automatic GitHub requests for an empty
watchlist. Completed PRs stay visible and continue to be checked on the
configured schedule, avoiding permanent assumptions about branch history.

## NixOS integration

Expose enable, package, port, available targets, initial selected targets,
refresh schedule and optional token-file path. Run under a dedicated
unprivileged service account, with
StateDirectory and restrictive credential/state permissions. The web service
and refresh unit share state and credentials. Configure a persistent timer
so missed calendar checks can run after the machine is powered on; checks
cannot run while it is off. The service remains local with no firewall port
opening. Ship an example NixOS configuration and standalone run instructions.

Local mutation endpoints require same-origin protection and validated Host;
use bounded request sizes and escaped rendering of GitHub-provided text.
Never expose the token through API responses.

## Scope and exclusions

Deliver the working dashboard, API checker, persistent storage, tests, Nix
package/module and setup documentation. No cloud hosting, accounts, public
deployment, notifications, GitHub writes, automatic nixos-rebuild, or full
nixpkgs clone. Reference repositories remain unchanged; implement original
code informed by their behavior rather than copying their implementation.

## Validation

Test ancestry direction, PR URL validation, open/closed handling, rate-limit
cooldown, stale data preservation, persistent favourites, and refresh locking
using deterministic fixtures. Test target selection persistence, configuration
reconciliation, newly selected targets, and shared selections between manual
and scheduled checks. Exercise the actual local HTTP service for
add/list/check/remove, unsafe mutation rejection and credential non-exposure.
Validate the Nix package and evaluate the module/timer where tools and network
permit. A live read-only GitHub smoke check is useful if access is available;
report any unavailable verification explicitly. Do not activate NixOS or
modify the user's machine configuration as part of development.

## Remaining implementation discretion

The builder chooses exact schema, endpoint names, visual styling, internal
module boundaries, backoff details and test fixtures within this behavior.
No further product decisions are required for the initial version.
