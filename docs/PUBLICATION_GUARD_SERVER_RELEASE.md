# Publication guard server release

This package is a separate infrastructure change dependent on publication-guard
PR #35. Its immutable runtime source is
`b0cae3886b30ef741a84dffbf8bd3673b540b414`. The package installs only
`services/publication.py`, `storage/repository.py` and the new additive migration
`storage/migrations/005_publication_claims.sql`. It does not use `git pull`, reset
or checkout over the server's modified working tree, install dependencies, change
the model/source hosts, or run the completed private Telegram probe again.

The 277KB payload includes a clean isolated copy of the pinned runtime and its
offline workflow script. It contains no production environment, database, media,
credentials or Git directory. The manifest/payload/script SHA-256 values must be
checked against the exact release commit. Do not execute a branch-tip download.

## Current observed baseline

Operator-provided output on 26–28 October recorded 106/106 matching profile files,
`gpt-4o-mini`, fetching allowed for `wakeflot.ru`, 72 stored receipt records and
items 626/649 discarded. Item 650 was `ready_to_publish`, scheduled at
`2027-10-05T06:00:00+00:00`; the server clock showed October 2026. The service
`parser-news-bot` used PID 716 and `/opt/bot3/parser-new-bot/bot_aiogram.py`;
the process-name/path scan found only that process. These are operator-provided
observations, not an independent SSH inventory of every possible external sender.
Every package run rechecks the local baseline; these observations never grant
permission to discard a later change or resend a message.

## Check mode

Run `scripts/deploy_publication_guard.py --check --package-base URL` using the
server virtual environment, as root from `/opt/bot3/parser-new-bot`. `URL` is the
pinned release's `scripts/releases/publication-guard` directory on HTTPS.

The default is also check mode. It fetches only pinned, hash-checked package
artifacts and writes temporary staging plus a deployment lock; it does not stop
the service, modify live code/environment, apply a production migration or send.
It validates:

- Current 106-file profile or the already-installed 107-file profile; independent
  changes and an unexpectedly present new migration stop the operation.
- Model/database location, active service entry point, and a bounded local
  process inventory. It also checks cron/systemd configuration for direct
  references to this bot outside its canonical unit. This cannot prove that an
  unrelated remote deployment or an indirect wrapper never sends to the channel;
  review such independent writers before activation.
- Package paths, archive members, hashes and Python syntax. The isolated workflow
  runs under the existing virtual environment with live environment variables
  stripped and socket connections blocked even during imports. Both changed and
  unchanged source cases must pass with fake Telegram/OpenAI transports.
- SQLite integrity and a migration rehearsal on an in-memory backup. No original
  business table, owner text, status or receipt changes. Unknown migration history
  and lookalike claim schemas fail closed. Memory staging is capped at 256MB.
- Every queued item has a valid schedule more than 15 minutes in the future.
  Unscheduled/due items and unresolved claims block a new installation; their
  statuses are never changed to make an installation pass.

Re-running `--check` after SSH disconnect is safe. It does not retry a delivery.
An incomplete prior deployment journal blocks another attempt and needs review.

## Apply mode: separate operational step

`--apply` is an explicit service/code/database change and must not be substituted
for a requested preliminary check. Review PR #35 and this infrastructure PR,
their exact-head CI and the fresh server check before providing apply commands.
The old main/editorial integration path remains #7 → #8; a pinned overlay does
not merge those PRs or prove that secrets have been rotated. No apply command is
run by this task's local/Docker validation.

The installer takes an exclusive lock, saves original file bytes/permissions,
records a persistent journal, stops the canonical service and verifies that no
matching sender remains. It rechecks source/configuration/database snapshots
after the stop and creates a mode-0600 SQLite backup through SQLite's backup API.
The containing backup directory is mode 0700; `.env` is never copied or changed.

Writes use atomic replacement and this order: new publication service first,
new repository second, new migration last. Before the matching repository/schema
exists, the upgraded publication entry point fails before a Telegram send. The
new migration inherits the existing migration file's permissions and ownership.
The installer applies only migration 005, checks that all original business data
is unchanged, repeats isolated smoke, then records `activation_started` durably
before starting the service. Health, protected items and receipt/claim state are
checked after activation. It does not send a test publication.

## Recovery boundaries

Before activation, an ordinary failure can restore original files and restart
the old service only when the business/configuration state is unchanged, no claim
exists and there is no independent file edit. The additive empty schema/marker
may remain; the database is never restored from backup. Code rollback preserves
original bytes, ownership and permissions, including CRLF where present.

After activation starts, any failure stops the service and retains the upgraded
code, claims, receipts and full current database. The journal becomes
`failed_paused`; the installer does not restore old code or restart automatically.
If the service cannot be confirmed stopped, its output does not claim that it is.
Keep publication stopped while reviewing actual delivery evidence. Do not delete
claims, rewind the database/queue, run an old overlay rollback, or repeat probe
6817 to recover an interrupted deployment.

After a hard kill/power interruption, the persistent journal blocks replay of the
installer. A reboot might have started the installed service, so treat activation
as uncertain until its runtime and database evidence are inspected. There is no
automatic resume/rollback flag that bypasses this boundary. Original code, file
metadata and a pre-install database backup are retained for supervised recovery.

## Validation

Local/CI command, from the isolated worktree:

```text
python -m pytest -q --tb=short tests/test_publication_guard_release.py
```

Unit cases fake systemd/process control but use real temporary SQLite, the actual
source payload, both migration schemas, backups and journal. They cover hash/path
tampering, unknown migrations, wrong claim constraints, queue deadlines, extra
writers, data preservation, idempotency, independent changes during stop and
failures on both sides of activation. The separate payload workflow test executes
the real isolated script with no live environment or network. Docker validation
uses `--network none`, a temporary filesystem and no production data or mounts.
