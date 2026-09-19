# Pipeline Refresh Runbook — the canonical stage order

The complete, ordered sequence for loading a new batch of documents. **Every stage below
runs on every refresh** — the runbook exists because optional-looking stages (notably
event_rename, single-name backfill, materiality scoring) were silently skipped in past
refreshes, and each omission quietly degrades a downstream layer. Executed successfully
2026-08-24/25 and 2026-09-09/10 (see `docs/reports/_derived/manifest.md` for what each
refresh changed).

Conventions: `DC` = `docker compose -f docker-compose.laptop.yml -f docker-compose.laptop.embed.yml
run --rm embed` (the embed overlay has the GPU, the models, and `./data` + the repo bind-mounted).
Batch-API stages need the host proxy: `python -m uvicorn server.main:app --host 0.0.0.0 --port 7001`.
Run every `batch_queue_runner` with `--stall-timeout 0` (the default stall detector cancels
healthy-but-queued OpenAI batches). Size `--max-concurrent` against your OpenAI **Batch Queue
Limit** (dashboard → Limits → gpt-4o-mini → "Batch queue limit", in tokens): each
cluster_deconflict batch ≈ **0.43M enqueued tokens**, so safe concurrency =
`floor(BQL / 0.43M)`. Exceeding it = HTTP 429 "Enqueued token limit reached" and failed
batches (Tier 3 ≈ 40M ⇒ ~15–25 is safe; the default of 5 is always safe).
`WINDOW` below = first ingest date … last ingest date.

## 0. Preflight
- `docker ps` — db healthy; host proxy up on 7001; `nvidia-smi` — if the GPU is busy with the
  user's other work, run model steps with `-e CUDA_VISIBLE_DEVICES=` (CPU).
  The 7001 proxy does NOT survive between sessions — verify it responds
  (`curl -s -o /dev/null -w '%{http_code}' localhost:7001/docs`) before submitting any batch
  stage; a dead proxy fails every job at upload with
  `HTTPConnectionPool(host='host.docker.internal', port=7001)`. Recovery: restart the proxy,
  `UPDATE batch_jobs SET status='preparing', error_message=NULL, submitted_at=NULL WHERE ...`,
  re-run the queue runner.
- Check overlap: if the new export re-covers already-clustered dates with materially more
  docs, roll back Stage-1 event artifacts for those dates first (backup schema + delete
  clusters/fully-inside events/straddling mentions, null straddling material_score — recipe in
  the 2026-08-24 run, `backup_20260824` schema).

## 1. Ingest
`DC python -m services.pipeline.ingestion.dsr --source local --no-embed`
(never `--relocate` in the container — Windows bind-mount rename fails; `mv` the JSON files to
`data/processed/` from the host afterwards). Verify: Loaded/Skipped/Errors counts, doc total.

## 2. Embed documents
`DC python services/pipeline/embeddings/embed_missing_documents.py --yes --batch-size 6`
(GPU; 4–8 batch on the RTX 2060). Verify the stats block says Missing: 0.

## 3. Rename generic event names  ← the historically-skipped stage
`DC python -u services/pipeline/batch/batch_prepare.py --job-type event_rename \
    --start-date WINDOW_START --end-date WINDOW_END --recurring-min 10`
then queue-runner + process. Fills `raw_events.specific_event_name` for recurring umbrella
labels ("Belt and Road Initiative", "SCO Summit", …). Clustering reads
`COALESCE(specific_event_name, event_name)` — skip this and the event layer accumulates
same-name duplicate masters that deconfliction correctly refuses to merge.
`batch_cluster_events` now prints a loud warning if a window still has >5% un-renamed
recurring names.

## 4. Events Stage 1 (daily)
- `DC python services/pipeline/events/batch_cluster_events.py --influencers --start-date … --end-date …`
- `batch_prepare --job-type cluster_deconflict --start-date … --end-date …` → queue → process
- `DC python services/pipeline/events/backfill_single_name_clusters.py` (the batch path skips
  single-name clusters by design; this creates their canonical events)
- Sweep stragglers inline: `llm_deconflict_clusters.py --influencers --start-date … --end-date …`
- Verify: clusters in window 100% `llm_deconflicted`; new canonical events all have vectors.

## 5. Events Stage 2 (cross-day)
- `python services/pipeline/events/consolidate_all_events.py --country C --start-date (WINDOW_START − 30d) --force`
  per influencer (host is fine at window scale; `--start-date` limits the re-cluster).
- **Reset validation on masters that gained children** (merge absorbs children of ANY
  validated master — without this reset, new children merge unvalidated):
  `UPDATE canonical_events m SET llm_validated=false, llm_validated_at=NULL WHERE
   m.master_event_id IS NULL AND coalesce(m.llm_validated,false) AND EXISTS
   (SELECT 1 FROM canonical_events c WHERE c.master_event_id = m.id);`
- `batch_prepare --job-type canonical_deconflict --all-unprocessed` → queue → process
- Flatten chains, then `merge_canonical_events.py --influencers`:
  `UPDATE canonical_events c SET master_event_id = p.master_event_id FROM canonical_events p
   WHERE p.id = c.master_event_id AND p.master_event_id IS NOT NULL;`
- `batch_prepare --job-type score_materiality --all-unprocessed` → queue → process
  (**not automatic** — skipping leaves new events with NULL material_score, silently gutting
  every high-material analysis).

## 6. Entities
- `DC …/entities/cluster_daily_entities.py --country C --start-date … --end-date … --force` per influencer
- `batch_prepare --job-type entity_deconflict --country C --start-date … --end-date …` → queue → process
- Inline single-name sweep: `llm_deconflict_entity_clusters.py --country C --start-date … --end-date …`
- `reembed_entities --include-null` → `consolidate_all_entities.py --country C --force` →
  reset validation (same UPDATE pattern on canonical_entities) →
  `batch_prepare --job-type canonical_entity_deconflict --all-unprocessed` → queue → process →
  `merge_canonical_entities.py --influencers` → `reembed_entities --include-null`
- `link_entities_to_events.py --country C` → `build_entity_cooccurrence.py --country C --force`
- `batch_prepare --job-type classify_entity_relationships --country C` and
  `--job-type generate_entity_descriptions --country C` → queue → process

## 7. Summaries
Delete summaries whose period overlaps the window (`period_end >= WINDOW_START`) so partial
periods regenerate, then per level daily → weekly → monthly → yearly:
`batch_prepare --job-type generate_X_summary --country C --start-date … --end-date …` → queue → process.
Then `--job-type score_summary_materiality` (**requires `--country`** — silently selects
nothing without it) → queue → process. Then
`embed_event_summaries.py --yes --batch-size 8` (CPU is fine) and delete orphaned summary
vectors (rows in the four `*_event_embeddings` collections whose summary_id no longer exists).

## 8. Analytics + reports
Bump the window constants (`_derived/build_analytics.py` END, `build_us.py` inline date,
`build_theater.py` END + month range, `analyze_theater.py` captions + two ranges,
`gen_recipient_reports.py` header) → run `build_analytics`, `build_us`, `build_subcat_clean`,
`build_theater`, `build_entities` → `analyze_theater`, `analyze_initiator` ×4, `analyze_us`,
`analyze_category` ×3, `analyze_recipient` ×17, `gen_recipient_reports` → review prose against
the new numbers (**cite distinct-document figures from stats.json — never sum
`provenance_intensity` across category cells, that double-counts multi-category docs**) →
update `INSIGHT_REPORT_PROMPT.md` Part F/I inventory + `manifest.md` note → commit.

## 9. Ship
Refresh base-image digest pins (`docker pull` + inspect), bump version refs, release via
`scripts/docker/push-to-registry.sh registry mmorrisj X.Y.Z`; export the data delta with
`scripts/db_delta_export.py --output-dir ./db_delta_YYYYMMDD --doc-date-from WINDOW_START`
(text-only transfer; `--tables` produces a partial bundle, e.g. event-layer-only — see
`docs/ENTERPRISE_DELTA_RUNBOOK.md`).
