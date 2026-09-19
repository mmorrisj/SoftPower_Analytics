# Enterprise Delta Update Runbook — text-only data transfer

Use this when the enterprise box must be brought up to the laptop's data state but
binary `pg_dump` chunks cannot be transferred. The bundle is gzipped CSV + a JSON
manifest, split at 250 MB per file by default (`--max-mb`), which keeps every file far
under the 500 MB transfer cap. It is produced by `scripts/db_delta_export.py` and applied
in **one transaction** by `scripts/db_delta_import.py` (needs only `psycopg2`).

## What a delta bundle contains

| Layer | Mode | Notes |
|---|---|---|
| `documents` + `categories`, `subcategories`, `initiating_countries`, `recipient_countries`, `raw_events`, `raw_entities` | replace-by-`doc_id` inside a date window | Idempotent: rows for the window's doc_ids are deleted and re-inserted, so an overlap with already-loaded documents is harmless. |
| Document vectors (`langchain_pg_embedding`, collection `chunk_embeddings`) | replace-by-doc_id | Same window. |
| `canonical_events`, `daily_event_mentions`, `event_summaries`, `event_source_links`, `canonical_entities`, `daily_entity_mentions`, `entity_relationships` | whole-table replace (vectors included) | Small, fully derived tables — replaced wholesale, children deleted first. |
| Summary vectors (`langchain_pg_embedding`, the four `*_event_embeddings` collections) | replace-where | Collections are replaced wholesale. |
| `analytics.*` (report base tables) | whole-table replace | Created if missing. |
| `event_clusters`, `entity_clusters`, `batch_jobs` | **not shipped** | Pipeline intermediates; the app does not read them (`--include-batch-jobs` adds the last). |

Nothing needs re-embedding on the target unless the bundle was produced with
`--no-vectors`.

## Producing the bundle (laptop)

```bash
# window start = first date of the new export (overlap with existing data is fine)
python scripts/db_delta_export.py --output-dir ./db_delta_YYYYMMDD --doc-date-from 2026-07-21
python scripts/db_delta_export.py --output-dir ./x --doc-date-from 2026-07-21 --dry-run   # counts only
```

Options: `--max-mb 250` (split threshold per gzipped file; default 250),
`--no-vectors` (smaller bundle; then run `reembed_events.py --include-null`,
`reembed_entities.py --include-null` and `embed_event_summaries.py --yes` on the target),
`--include-batch-jobs`, `--tables` (partial bundle — see below).

### Partial bundles: `--tables`

`--tables` limits the bundle to a comma-separated list of tables, named bare
(`canonical_events`) or schema-qualified (`analytics.some_table`). Because replaced
parents must ship together with their FK dependents, the exporter enforces the
dependency closure and refuses a list that strands a dependent. Example — an
event-layer-only bundle:

```bash
python scripts/db_delta_export.py --output-dir ./db_delta_YYYYMMDD \
    --tables canonical_events,daily_event_mentions,event_summaries,event_source_links
```

The output directory holds `delta_manifest.json` (per-table mode, columns, row counts,
SHA-256 per file) and `<schema>.<table>.partNNN.csv.gz` files. Transfer the whole directory.

## Applying the bundle (enterprise)

Prerequisites: the target schema must be at the same Alembic revision as the source —
run `alembic current` on both sides and compare before applying. Deploy the matching
app image first or after — the data load is independent of the image.

```bash
# 1. copy the directory into the app container
docker cp db_delta_YYYYMMDD sp_prod_app:/tmp/

# 2. validate: applies everything inside a transaction, prints per-table -deleted/+inserted, then ROLLS BACK
docker exec sp_prod_app python scripts/db_delta_import.py --input-dir /tmp/db_delta_YYYYMMDD --dry-run

# 3. apply (same command without --dry-run; add --yes to skip the prompt)
docker exec sp_prod_app python scripts/db_delta_import.py --input-dir /tmp/db_delta_YYYYMMDD --yes
```

> **Images ≤ 2.0.1 only:** those predate the importer shipping in the image, so also
> copy it in first: `docker cp scripts/db_delta_import.py sp_prod_app:/app/scripts/db_delta_import.py`.
> From 2.0.2 the script is already inside the container.

Running on the host instead of the container works the same way with the repo's `.env`
(`DATABASE_URL` or `POSTGRES_*`/`DB_HOST`); only `psycopg2` and `python-dotenv` are needed.

The importer verifies checksums first, applies whole-table replaces children-first with
foreign keys enforced, and fails closed: any error rolls back and leaves the database
exactly as it was. After a successful apply it prints the new document count and max date.

## Post-apply checks

```sql
SELECT count(*), max(date) FROM documents;                       -- expect the bundle's source figures
SELECT count(*) FROM canonical_events WHERE master_event_id IS NULL;
SELECT count(*) FROM canonical_entities;
SELECT c.name, count(*) FROM langchain_pg_embedding e JOIN langchain_pg_collection c ON c.uuid=e.collection_id GROUP BY 1;
```

Then restart the app (`docker restart sp_prod_app`) so any cached counts refresh.
