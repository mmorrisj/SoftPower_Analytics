# Ingestion Service

Web-based ingestion (Phase 1) shipped from the design in
`docs/archive/INGESTION_UI_DESIGN_2026-06.md`. What exists:

- **`ingestion_service.py`** — validation/ingestion workers behind the ingestion API.
- **`dsr_core.py`** — DSR parsing/flattening extracted from `dsr.py` (light import for the
  server; `dsr.py` re-exports it for the CLI).
- **`server/routers/ingestion.py`** — the `/api/ingestion/*` router.
- **`client/src/pages/DataIngestionPage.tsx`** — the React `/ingestion` page: drop in a
  `results.json` (DSR extract), review, run, and watch progress.
- **`IngestionJob` model** (`shared/models/models.py`) + the `ingestion_jobs` table
  migration (`alembic/versions/20260612_add_ingestion_jobs_table.py`) — persistent
  history of every ingestion run.

**Still pending:** `atom.csv` uploads get a validation report only — running them awaits
the Phase 3 extraction rework (see the archived design doc). Use the CLI
(`atom_pipeline.py`) for ATOM CSV ingestion in the meantime.

The CLI paths (`dsr.py`, `atom_pipeline.py`) remain supported; see `CLAUDE.md` and
`docs/PIPELINE_REFRESH_RUNBOOK.md` for the refresh sequence.
