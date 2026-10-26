# Publication evidence contract v1 — proposed for Site and intake review

This contract separates stored delivery evidence from business conversion data.
The read-only auditor is `scripts/audit_publication_funnel.py`. It sends no
messages, loads no application credentials and changes no database or runtime.
The contract has not yet been accepted or implemented by Site/Tourism/Coaching.

## Identifiers and evidence

| Field | Meaning | Authority |
| --- | --- | --- |
| `publication_id` | `producer:publications.id`, e.g. `parser-news:42` | One production writer/database history |
| `source_item_id` | Original Parser `items.id`; never a publication ID | Parser database |
| `receipt_key` | SHA-256 of producer, stored destination and message ID | Read-only ledger reconciliation |
| `telegram_message_id` | Message ID recorded after the Telegram response | Stored acknowledgement record |
| `publication_url` | Observed destination URL, if available | Telegram/Site acknowledgement; currently absent in the ledger |
| `transition_id` | Opaque ID for one server-observed CTA transition | Site/redirect server |
| `request_id` | Opaque ID of a durably accepted request | Tourism/Coaching intake server |

The producer namespace must identify one database history and bot identity.
Use distinct namespaces for independent databases. Do not reuse a namespace
after restoring/replacing its ledger without reconciling IDs. Rollback restores
code and overlay, not historical receipts or a publication queue for replay.

The existing integer `publications.id` is assigned after acknowledgement. It is
usable for auditing existing rows; it does not solve allocating an identifier
before a new CTA is rendered. Before activating tracking, the writer and Site
must agree a durable pre-send publication intent/ID, map it to the acknowledged
ledger row, and preserve it across retries. This PR does not alter the writer,
CTA links, Site routes or intake storage.

Do not substitute `items.link`, `source_url` or a Sheets `PUBLISHED` value for
the actual destination URL. No public permalink is invented for a private chat.
The auditor exports a destination fingerprint rather than a raw chat ID. It
cannot resolve numeric IDs and username aliases as the same destination without
additional platform evidence. Current source/final text is not proof of what
was sent: `source_revision_at_delivery` and `final_text_sha256_at_delivery`
remain null until those immutable values are captured at delivery.

## Server event export

Optional input is UTF-8 JSONL with one event per line. Required common fields:
`schema_version=1`, `event_id`, `event_type`, `event_source`, `publication_id`
and timezone-qualified ISO-8601 `occurred_at`. IDs are opaque strings of up to
128 characters from `[A-Za-z0-9_.:-]`, starting with an alphanumeric character.
Contacts and form text are outside this contract and are not exported.

| Event | Required origin | Extra fields |
| --- | --- | --- |
| `view` | `site_server` | none; counts events, not unique people |
| `click` | `site_server` | `transition_id` |
| `request_created` | `intake_server` | `transition_id`, `request_id` |

Example adapter records — test values, not observed business events:

```json
{"schema_version":1,"event_id":"click-example","event_type":"click","event_source":"site_server","publication_id":"parser-news:42","transition_id":"transition-example","occurred_at":"2026-10-25T10:00:00Z"}
{"schema_version":1,"event_id":"request-example","event_type":"request_created","event_source":"intake_server","publication_id":"parser-news:42","transition_id":"transition-example","request_id":"request-example","occurred_at":"2026-10-25T10:01:00Z"}
```

Attribution requires an eligible stored receipt, the same publication and
transition, and request creation at or after that transition. Repeated event
IDs are deduplicated. Conflicting event IDs, transitions, request identities,
or receipt/source bindings are quarantined. Duplicate bookkeeping rows for
one receipt/source get one canonical ID plus aliases; they do not count as
extra messages. Requests without a matching transition receive no attribution.

`event_source` is a declared origin, not authentication. A local JSON export
alone does not establish server provenance or completeness. The auditor labels
these counts `provided_input_only` and attributable requests as **candidates**.
Trusted server exports and producer acceptance are still required before calling
them business KPIs. Export hashes and event-time bounds support later review.

## Private probes and missing measurements

Pass existing private probe receipt files explicitly with repeatable
`--probe-receipt` arguments. They are read without API calls or retries, marked
`private_smoke`, and excluded from business receipts even if copied into the
production publications table. A `send_started` record remains uncertain and
does not authorize another send. A confirmed smoke message is QA evidence, not
a lead, customer conversion or proof of a production-channel publication.

Without an event export, per-publication counts are null, not zero. With a
provided empty export, zero describes only that export. The auditor never
fetches live analytics or infers requests from Sheets statuses, model text,
source links, or private probe success.

## Read-only use and acceptance

From the project root, using the appropriate local Python or Docker adapter:

```text
python -B scripts/audit_publication_funnel.py --database PATH_TO_DATA_DB
python -B scripts/audit_publication_funnel.py --database PATH_TO_DATA_DB --events SERVER_EXPORT.jsonl --probe-receipt EXISTING_PRIVATE_RECEIPT.json
python -m pytest -q tests/test_publication_funnel.py
```

On the VPS the execution directory is `/opt/bot3/parser-new-bot` and Python is
`venv/bin/python`. Use a SHA-256-verified copy of the audit script. The known
receipt path is recorded in the release reconciliation document. These commands
are not deployment or send commands. A missing database is rejected rather than
created. The real migration schema is exercised by tests, together with
read-only bytes, aliases, duplicate/conflict handling, chronology and CLI output.

Before activation: one publication writer/serialized claim; immutable source,
final-text and ACK provenance; stable ID before CTA; actual destination URL;
accepted server event exports from Site and intake; privacy/retention review;
fresh runtime identity and explicit release approval. KB intake, payments and
model migration remain separate work.
