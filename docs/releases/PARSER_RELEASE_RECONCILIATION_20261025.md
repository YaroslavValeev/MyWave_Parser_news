# Parser News release reconciliation — 25 October 2026

Delegation: `mywave-2026-10-25-parser-v1`. This is a release/evidence review,
with no authority for production deployment, new messages, payments or model
changes. The baseline is `gpt-4o-mini`.

Sources: the Owner's confirmed logs in this chat; the 18 October draft letter
`08-parser-news.md`; repository files and GitHub metadata/checks reread on
25 October. The draft letter is background evidence, not independent proof of
current production state.

## Release matrix

| Source | Exact SHA | CI evidence | Runtime consequence |
| --- | --- | --- | --- |
| `main` | `a2c5d439212cf22d22771435fabe334017999002` | [push run 23662772285](https://github.com/YaroslavValeev/MyWave_Parser_news/actions/runs/23662772285), failure dated 27 March | Not the accepted editorial runtime |
| [#7 security](https://github.com/YaroslavValeev/MyWave_Parser_news/pull/7) | `c7d6a37974e9a30257241c12f90c54d5ebb4539e` | [run 29316291965](https://github.com/YaroslavValeev/MyWave_Parser_news/actions/runs/29316291965): tree secret scan and history report passed; no runtime build job | First integration step; rotation/release gate remains |
| [#8 editorial](https://github.com/YaroslavValeev/MyWave_Parser_news/pull/8) | `07975c262a6864919729cc6be4e193bc57b3f629` | [run 37911355601](https://github.com/YaroslavValeev/MyWave_Parser_news/actions/runs/37911355601): build, tree scan and history report passed; 277 + 65 + 6 + 3 = 351 tests on 9 October | Accepted source for the last verified 106-file runtime profile |
| [#6 combined](https://github.com/YaroslavValeev/MyWave_Parser_news/pull/6) | `a023557cf3fa54008cdb79098cf926d535b9bf8e` | [run 29308914777](https://github.com/YaroslavValeev/MyWave_Parser_news/actions/runs/29308914777): build and secret jobs passed | Overlapping alternative; HOLD, do not apply in addition to #7/#8 |
| [#11 model draft](https://github.com/YaroslavValeev/MyWave_Parser_news/pull/11) | `90aed57cefcd3078d1a0266a0f712dc3505e1093` | Draft, outside this release | No model change |

`main`, #7 and #8 remain separate refs. The selected path is **#7 then #8**.
After an approved #7 merge, retarget #8 to the resulting main/base and rerun
checks for the exact reviewed merge/head. Existing green results do not attest
to that future merge. No broad PR is merged, rebased or retargeted by this task.
The history job is report-only; its green status is not proof of credential
rotation or permission to rewrite history.

Measured overlap relative to #7: #6 changes 102 paths, #8 changes 206, all 102
#6 paths are present in #8, and #6 has no exclusive path. Blob differences and
the immutable overlay hashes are in
[parser-editorial-20261025.json](parser-editorial-20261025.json).
Path coverage does not assert identical behaviour; mixing the two pipelines is
not an accepted release strategy.

## Runtime and overlay

The local editorial tree was clean at `07975c2`; all 106 files matched the
LF-normalized profile in `scripts/audit_reconciled_runtime.py`. New work is in
a separate worktree based on that exact commit. This PR changes no runtime
module or existing installer/overlay, and adds no publication writer.

The chronological overlay chain is editorial-4o-mini → article-links →
summary-conditions → runtime-alignment → manual-final-post. It is a record of
installed transitions, not an instruction to replay all packages. Each has
fixed pre/post and guard hashes. `docs/EDITORIAL_SERVER_RELEASE.md` describes
an earlier stage of this chain; consult the later alignment/manual-editor
artifacts and the machine manifest before choosing an applicable transition.

The last Owner-confirmed editor backup is
`/opt/bot3/backups/editorial-release-20261009T101224.982073Z`.
The server kept Git head `06fcfb43dda9ee5ac9139ed8bf043bed9f25f651` with its local
files; matching runtime hashes did not mean a clean server Git checkout.
Recorded package versions were openai 2.28.0, httpx 0.28.1, aiogram 3.30.0 and
aiosqlite 0.22.1. These are historical observations, not a complete dependency
lock or a current production measurement.

An authorized read-only SSH attempt on 25 October failed during banner exchange
before any remote command executed. Current service/PID, Git identity, package
versions, database, receipts and single-consumer state were not refreshed. No
reset/checkout, restart, rollout, source fetch, or Telegram request was made.

## Existing receipt and actual measurements

The Owner's successful result recorded message **6817**, manual text verified,
source item 626, one private text post, unchanged production records, zero
production DB writes, and zero OpenAI requests. Repetition returned
`already_sent` with zero additional messages.

Existing receipt:
`/opt/bot3/backups/live-final-post-ca1a1d151c0683bea8a09fc6.json`.
First-run log: `/opt/bot3/backups/live-final-run.G4tNqj`.
These files were not reread from the VPS on 25 October. The receipt is a
historical private smoke test; it has no production `publication_id` and no
associated Site transition or request. It is not counted as a current business
publication or conversion. It must not be resent to refresh evidence.

| Measure | Evidence available |
| --- | --- |
| Private smoke posts | 1, historical operator-confirmed ACK |
| Manual text match | true, historical returned-text comparison |
| Extra messages on repeated probe | 0, historical `already_sent` result |
| Current production delivery receipts | unknown; current DB not read |
| Current Site view events / CTA transitions | unknown; no current server exports |
| Accepted or attributable requests | unknown; no request-service evidence |

Historical Sheets counts in the 18 October letter are not reused as current
KPIs. Missing measurements are null, not zero. The new read-only auditor
reconciles actual stored receipts and explicitly supplied server-event adapters;
provided events remain candidates until their origin/completeness is reviewed.

## Defect addressed and acceptance gates

The missing audit/measurement contract allowed publication status, source URL,
private delivery smoke and business conversion to be conflated. The bounded PR
adds a read-only evidence join and the proposed
[publication contract](../PUBLICATION_MEASUREMENT_CONTRACT.md). It does not
activate tracking, manufacture public URLs or request IDs, or retrofit a source
revision from current NLP text into an old receipt.

Source inspection also finds separate publication entry points in the scheduler
and `/publish`. The parse lock is not a publication lock. A single process alone
does not establish serialized publication claims. Before accepting a business
release, demonstrate one writer/serialized claim, and durable atomic receipt
bookkeeping. Existing `_handle_success` updates status, saves a publication and
logs it in separate transactions; hardening that writer is a separate remaining
gate, not a guarantee supplied by this read-only tool.

Remaining gates: Owner rotation/release approval; #7→#8 integration checks;
fresh VPS identity/receipts; one writer; delivery-time source/final-text hashes;
durable publication ID before CTA; actual receipt/URL propagation; Site and
Tourism/Coaching acceptance of the event contract; trusted event exports with a
request ID. KB intake and #11 remain separate tasks. Rollback never replays a
message or restores old queue/receipt state to generate another delivery.

Validation commands run locally from the isolated worktree:
`python -m pytest -q tests/test_publication_funnel.py`. Docker validation uses
the same tests and the existing editor/publication suites with network disabled
and test adapters. The final task response records the exact PR/head and exits.

For a future read-only VPS refresh, run the verified audit helper with
`--database /opt/bot3/parser-new-bot/data.db --probe-receipt` followed by the
existing receipt path, from `/opt/bot3/parser-new-bot` using `venv/bin/python`.
This does not grant deployment or sending permission. Receipt `send_started`
or an interrupted command requires evidence inspection, not a new probe run.

Validation completed on 26 October: 20 new tests pass locally; the isolated
Docker build succeeds and 91 of 92 selected tests pass with networking disabled.
The existing `test_offline_smoke_does_not_enter_sheets_or_media_with_enabled_configuration`
exceeds its 60-second subprocess timeout both in the new image and in the unchanged
`manual-final-release-test` baseline image. This is an unresolved baseline
validation limitation; it is not reported as a passing integration test.
