# Durable publication guard

This change is prepared for review. It is not deployed and does not authorize a
new test message. The model stays `gpt-4o-mini`; existing private receipt 6817
must not be replayed.

## Behavior

The scheduler and manual `/publish` use the same repository claim. Immediately
before delivery a short `BEGIN IMMEDIATE` transaction reserves the source item
in `publication_claims`. Only approved/ready/retry items without an existing
receipt or claim qualify. The primary key is the item ID, including across
destinations: changing channel does not bypass an unresolved or completed send.
No database transaction stays open while waiting for Telegram.

A claim never expires automatically. A worker crash, cancellation, lost reply,
transport error, server error or unclassified exception cannot unlock it.
Unknown delivery changes the item to `error` and logs
`publication_delivery_unknown`, without copying the arbitrary error body into
that log. Claims left `sending` after a crash also block later workers.

Only an explicit Telegram rejection permits retry. Immediate transport retry is
restricted to rate-limit rejection, respecting Telegram's `retry_after` value.
Media format/URL fallback is allowed after a definite rejection; an unknown
photo/video result must not trigger another upload or a fallback text post.
If media was accepted and the following long text fails, the claim is retained
even when the text failure is a definite rejection. A partial post needs review.

After a complete ACK, one SQLite transaction creates the publication receipt,
sets the item to `published`, marks the claim delivered and writes
`publication_sent` with the publication ID and claim token. A database failure
rolls back all four changes but preserves the earlier claim. Later Sheets/site
synchronization is outside this transaction; its failure cannot resend the post.

This prevents automatic duplicate attempts by upgraded workers sharing this
database. It cannot supply exactly-once delivery across Telegram and SQLite,
recover a lost Telegram ACK, prevent a separate old deployment from sending, or
prove how many people viewed a post. It does not activate the measurement contract
in the separate evidence PR. `save_publication` remains available for imports and
fixtures; all live `PublicationService` delivery uses the guarded completion path.

## Validation

Use an isolated checkout, with no production `.env` or database:

```text
python -m pytest -q tests/test_publication_claims.py tests/test_publication_service.py
```

The new suite uses real temporary SQLite connections plus fake Telegram methods.
It exercises concurrent service instances, worker cancellation/restart, legacy
receipts, rejection versus timeout, partial media delivery, failed bookkeeping
and external synchronization. Docker runs use `--network none`.

## Future rollout and recovery

Before any rollout, require green CI and review, recheck the live baseline,
inventory all publication workers, stop every old sender, and back up code plus
SQLite using its backup API. Migration `005_publication_claims` is additive and
idempotent, with no backfill or modification of historical publications. Existing
receipts are checked directly when claiming an item. Deploy repository, service,
migration and isolated probe adapter together. Test with fake transport first;
the completed private probe is not an acceptance step to repeat.

Inspect `sending`/`unknown` claims and actual Telegram evidence before any manual
recovery. Do not delete claims, change a queue status, or wait for a timeout to
force an automatic retry. A persistent claim is an intentional stop for review,
not proof that delivery failed. No automatic reset/replay tool is included.

Rollback before first sending: stop workers and restore the previous code; the
unused additive table may remain. After any guarded send starts, preserve the
database, claims and receipts. Keep publication disabled while reconciling all
unresolved claims; old code ignores these guards, so restoring it while a queue
is active is unsafe. Never restore an older queue/database backup after sends.
The safe fallback is paused publication until reconciliation or a forward fix.
