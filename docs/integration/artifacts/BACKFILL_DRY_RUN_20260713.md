# Blog media backfill dry-run (20260713)

- rows: 2
- proposed_writes: 0
- csv: `docs/integration/artifacts/BACKFILL_DRY_RUN_20260713.csv`

## Flag counts

- `has_structured_video`: 1
- `invalid_media`: 1
- `local_path`: 1
- `missing_source_attribution`: 1
- `video_only_in_content`: 1

## Rows

- id=sample-1 slug=sample-youtube flags=video_only_in_content,invalid_media,local_path,missing_source_attribution media_status=missing
- id=sample-2 slug=sample-ok flags=has_structured_video media_status=external_video

**Guardrail:** mass Sheet writeback запрещён до Owner/GM GO.
