# Event Processing Pipeline Flow

**Last Updated**: 2026-09
**Visual Diagram**: See [EVENT_PIPELINE_DIAGRAM.drawio](EVENT_PIPELINE_DIAGRAM.drawio)

---

## Overview

The event processing pipeline uses a **two-stage batch consolidation approach**:
- **Stage 1**: Daily event detection and clustering
- **Stage 2**: Cross-date consolidation and merging
- **Stage 3** (optional): Materiality scoring of master events

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         INPUT DATA (Preserved)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  📄 documents          →    📋 raw_events (flattened from documents)        │
└─────────────────────────────────────────────────────────────────────────────┘
                                        ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STAGE 1: DAILY PROCESSING                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────┐         ┌──────────────────────────┐        │
│  │ 1. batch_cluster_events  │    →    │  🗂️ event_clusters      │        │
│  │                          │         │                          │        │
│  │ • Groups raw_events/day  │         │  • cluster_id            │        │
│  │ • DBSCAN clustering      │         │  • cluster_date          │        │
│  │ • Embedding similarity   │         │  • batch_number          │        │
│  │ • Creates batches        │         │  • event_names[]         │        │
│  │                          │         │  • doc_ids[]             │        │
│  │ Input: raw_events        │         │  • llm_deconflicted      │        │
│  │ Output: event_clusters   │         │  • refined_clusters      │        │
│  └──────────────────────────┘         └──────────────────────────┘        │
│                                                ↓                            │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │ 2. llm_deconflict_clusters.py                                    │     │
│  │                                                                   │     │
│  │ • LLM validates clusters                                          │     │
│  │ • Groups duplicate event names                                    │     │
│  │ • Creates canonical_events                                        │     │
│  │ • Creates daily_event_mentions                                    │     │
│  │ • ⚠️ ATOMIC: Uses savepoints - both created or neither           │     │
│  │                                                                   │     │
│  │ Input: event_clusters                                             │     │
│  │ Output: canonical_events + daily_event_mentions                   │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                   ↓                         ↓              │
│        ┌─────────────────────────────┐    ┌──────────────────────────┐   │
│        │ 📌 canonical_events         │    │ 📅 daily_event_mentions  │   │
│        │                             │    │                          │   │
│        │ • canonical_name            │    │ • canonical_event_id     │   │
│        │ • initiating_country        │    │ • mention_date           │   │
│        │ • first_mention_date        │    │ • doc_ids[]              │   │
│        │ • last_mention_date         │    │ • article_count          │   │
│        │ • embedding_vector          │    │ • consolidated_headline  │   │
│        │ • alternative_names[]       │    │                          │   │
│        │ • primary_categories{}      │    │ ✅ Recipients tracked    │   │
│        │ • primary_recipients{}      │    │    in canonical_events   │   │
│        │ • master_event_id (NULL)    │    │                          │   │
│        │ • llm_validated (FALSE)     │    │                          │   │
│        └─────────────────────────────┘    └──────────────────────────┘   │
│                                                                             │
│  ✅ Result: Each unique event per day gets its own canonical_event         │
│  ⚠️  At this point: Events are NOT linked across days                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                        ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│            STAGE 2: BATCH CONSOLIDATION (Cross-Date Grouping)               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │ 3. consolidate_all_events.py                                     │     │
│  │                                                                   │     │
│  │ • Processes ENTIRE dataset (not per-day)                          │     │
│  │ • HDBSCAN over composite distance:                                │     │
│  │   alpha*name-embedding + beta*recipient + gamma*temporal          │     │
│  │ • Hard temporal gate: pairs > --max-days-gate (30d) apart         │     │
│  │   can NEVER cluster together                                      │     │
│  │ • Sets master_event_id — CANDIDATE groupings only, not final      │     │
│  │   until llm_deconflict_canonical_events.py sets llm_validated     │     │
│  │                                                                   │     │
│  │ Input: canonical_events (all dates)                               │     │
│  │ Output: candidate master-child hierarchy                          │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                   ↓                                         │
│                      Master-Child Hierarchy:                                │
│                                                                             │
│              Master Event (master_event_id = NULL)                          │
│              ├── Child Event 1 (2024-08-01)                                │
│              ├── Child Event 2 (2024-08-05)                                │
│              ├── Child Event 3 (2024-08-12)                                │
│              └── Child Event 4 (2024-08-20)                                │
│                                   ↓                                         │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │ 4. llm_deconflict_canonical_events.py                            │     │
│  │                                                                   │     │
│  │ • LLM validates groupings                                         │     │
│  │ • Verifies same real-world event                                  │     │
│  │ • Picks best canonical name                                       │     │
│  │ • Splits incorrect groups                                         │     │
│  │ • Sets llm_validated = TRUE                                       │     │
│  │                                                                   │     │
│  │ Input: master-child groups                                        │     │
│  │ Output: validated groups                                          │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                   ↓                                         │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │ 5. merge_canonical_events.py                                     │     │
│  │                                                                   │     │
│  │ • Consolidates daily_event_mentions from children to master       │     │
│  │ • Merges child mentions → master                                  │     │
│  │ • Updates master date ranges (first/last_mention_date)            │     │
│  │ • Deletes empty child canonical_events                            │     │
│  │                                                                   │     │
│  │ Input: validated master-child groups                              │     │
│  │ Output: multi-day master events                                   │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                   ↓                                         │
│                         Final Structure:                                    │
│                                                                             │
│       Master Event:                                                         │
│       • canonical_name: "Best Name"                                         │
│       • first_mention_date: 2024-08-01                                     │
│       • last_mention_date: 2024-08-20                                      │
│       • master_event_id: NULL                                              │
│       • llm_validated: TRUE                                                │
│                                                                             │
│       Daily Mentions (all linked to master):                               │
│       • 2024-08-01: doc_ids[25 documents]                                  │
│       • 2024-08-05: doc_ids[18 documents]                                  │
│       • 2024-08-12: doc_ids[32 documents]                                  │
│       • 2024-08-20: doc_ids[15 documents]                                  │
│                                                                             │
│  ✅ Result: Master events span multiple days                               │
│  ✅ All daily_event_mentions linked to masters                             │
│  ✅ Child events deleted (clean data)                                      │
│  ✅ Query-ready for dashboard                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Database Tables

### Input Tables (Preserved)
- **documents** - Source documents from ingestion
- **raw_events** - Flattened event data extracted from documents

### Stage 1 Tables
- **event_clusters** - Daily DBSCAN clustering results
- **canonical_events** - Deduplicated events (one per unique event per day initially)
  - **primary_recipients** (JSONB): `{"Egypt": 25, "Kenya": 12}` - Recipient countries with doc counts
  - **primary_categories** (JSONB): `{"Economic": 18, "Diplomacy": 19}` - Categories with doc counts
  - **alternative_names** (ARRAY): Different headlines for same event
- **daily_event_mentions** - Links events to source documents with doc_ids array

### Stage 2 Tables
- Same tables as Stage 1, but with:
  - **canonical_events.master_event_id** set to group related events
  - **canonical_events.llm_validated** set to TRUE after validation
  - Child canonical_events deleted after merge
  - All daily_event_mentions linked to master events

---

## Key Relationships

### Stage 1: Daily Events
```sql
-- Each day gets separate canonical_events
canonical_events WHERE first_mention_date = last_mention_date

-- Each canonical_event has daily_event_mentions
daily_event_mentions.canonical_event_id → canonical_events.id
```

### Stage 2: Multi-Day Events
```sql
-- Master-child hierarchy
canonical_events.master_event_id → canonical_events.id (self-FK)

-- Masters have NULL master_event_id
canonical_events WHERE master_event_id IS NULL

-- After merge: All daily_event_mentions point to masters
SELECT * FROM canonical_events m
JOIN daily_event_mentions dem ON dem.canonical_event_id = m.id
WHERE m.master_event_id IS NULL
```

---

## Critical Implementation Details

### ⚠️ Atomic Transaction (2026-01-06 Fix)

**Problem**: llm_deconflict_clusters.py was creating orphaned canonical_events without daily_event_mentions

**Solution**: Use SQLAlchemy savepoints (nested transactions)
```python
savepoint = session.begin_nested()
try:
    # Create canonical_event
    session.add(canonical_event)
    session.flush()

    # Create daily_event_mention (MUST succeed)
    session.add(daily_mention)
    session.flush()

    # Both succeeded - commit savepoint
    savepoint.commit()
except Exception:
    # Failed - rollback ONLY this event
    savepoint.rollback()
    raise
```

**Result**: Guaranteed atomicity - both objects created or neither

### Query Patterns

**Get master event with all mentions:**
```sql
SELECT m.*, dem.*
FROM canonical_events m
JOIN daily_event_mentions dem ON dem.canonical_event_id = m.id
WHERE m.master_event_id IS NULL
  AND m.canonical_name = 'Event Name'
ORDER BY dem.mention_date
```

**Get all documents for a master event:**
```sql
SELECT DISTINCT unnest(dem.doc_ids) as doc_id
FROM canonical_events m
JOIN daily_event_mentions dem ON dem.canonical_event_id = m.id
WHERE m.master_event_id IS NULL
  AND m.canonical_name = 'Event Name'
```

---

## Diagnostic & Utility Tools

### Pipeline Monitoring
- **check_pipeline_coverage.py** - View complete pipeline state
- **find_unprocessed_dates.py** - Find dates missing processing
- **diagnose_consolidation.py** - Check consolidation statistics

### Event Investigation
- **debug_single_event.py** - Deep dive into single event
- **query_master_events.py** - Query master events and documents
- **show_event_docs.py** - Show documents linked to event

### Data Management
- **wipe_event_tables.py** - ⚠️ Reset pipeline (preserves raw_events)
- **export_event_tables.py** - Backup to parquet
- **import_event_tables.py** - Restore from parquet

---

## Processing Commands

### Stage 1: Daily Processing
```bash
# Cluster events for date range
docker exec -it api-service python services/pipeline/events/batch_cluster_events.py \
  --country "United States" --start-date 2024-08-01 --end-date 2024-08-31

# LLM deconflict and create canonical events
docker exec -it api-service python services/pipeline/events/llm_deconflict_clusters.py \
  --country "United States" --start-date 2024-08-01 --end-date 2024-08-31
```

### Stage 2: Batch Consolidation
```bash
# Propose candidate groupings (HDBSCAN + composite distance, 30-day temporal gate)
docker compose -f docker-compose.preprocessing.yml run --rm preprocessing \
  python services/pipeline/events/consolidate_all_events.py \
  --country "United States" --force

# LLM validate groupings (sets llm_validated — groupings are candidates until this runs)
docker compose -f docker-compose.preprocessing.yml run --rm preprocessing \
  python services/pipeline/events/llm_deconflict_canonical_events.py \
  --country "United States"

# Merge into multi-day events
docker compose -f docker-compose.preprocessing.yml run --rm preprocessing \
  python services/pipeline/events/merge_canonical_events.py \
  --country "United States"
```

Key `consolidate_all_events.py` flags: `--alpha` 0.4 / `--beta` 0.2 / `--gamma` 0.4 (composite-distance weights), `--max-days-gate` 30 (hard temporal gate — it does NOT group freely across all dates), `--min-cluster-size` 2, `--force`, `--start-date` (incremental mode). See `services/PIPELINE_REFERENCE.md` for the full reference.

### Diagnostics
```bash
# Check pipeline state
docker exec -it api-service python services/pipeline/events/check_pipeline_coverage.py \
  --country "United States"

# Query top master events
docker exec -it api-service python services/pipeline/events/query_master_events.py \
  --list-top 20 --country "United States"
```

---

## Stage 3: Materiality Scoring (Optional)

After batch consolidation creates multi-day master events, you can score their materiality.

**Script**: [score_canonical_event_materiality.py](score_canonical_event_materiality.py)

**Purpose**: Assign materiality scores (1-10) to canonical events measuring concrete/substantive nature versus symbolic/rhetorical gestures

**Why This Matters**: Enables tracking the materiality of specific initiatives over their entire lifecycle, distinguishing between:
- High-materiality events (7-10): Infrastructure projects, financial commitments, tangible deliverables
- Mixed materiality (4-6): Agreements with unclear implementation, capacity building
- Low-materiality events (1-3): Symbolic statements, cultural events without concrete commitments

**Process**:
1. Loads master canonical events (WHERE master_event_id IS NULL)
2. Formats event data: consolidated_description, key_facts, categories, recipients
3. LLM scores each event on 1-10 scale using specialized materiality prompt
4. Saves material_score and material_justification to canonical_events table

**Database Fields**:
- `canonical_events.material_score` - NUMERIC(3,1) score from 1.0 to 10.0
- `canonical_events.material_justification` - TEXT explanation of score

**Flags**: `--country` / `--influencers` (one required), `--min-days` (default 1), `--min-articles` (default 5), `--rescore`, `--dry-run`, `--quiet`

**Usage**:
```bash
# Score all canonical events for a country
python services/pipeline/events/score_canonical_event_materiality.py --country China

# Score all influencer countries
python services/pipeline/events/score_canonical_event_materiality.py --influencers

# Only score events with 3+ days of mentions (recommended for initial run)
python services/pipeline/events/score_canonical_event_materiality.py --influencers --min-days 3

# Rescore existing events
python services/pipeline/events/score_canonical_event_materiality.py --country China --rescore

# Test without saving to database
python services/pipeline/events/score_canonical_event_materiality.py --country China --dry-run
```

**Example Scores**:
- Al-Abdaliya Photovoltaic Project: 8.0 - "Major infrastructure project with renewable energy deliverables"
- Beijing-hosted Saudi-Iranian rapprochement: 6.5 - "Significant diplomatic engagement with economic potential"
- Arbaeen Pilgrimage Hospitality: 3.0 - "Primarily symbolic gesture of goodwill and cultural diplomacy"

**Note**: For large runs, prefer the Batch API path (`score_materiality` job type) over this synchronous script — see `services/PIPELINE_REFERENCE.md`.

---

## Design Rationale

### Why Two Stages?

**Stage 1: Daily Processing**
- Processes events as they arrive (day by day)
- Fast clustering within single day
- Creates baseline canonical events
- Enables incremental processing

**Stage 2: Batch Consolidation**
- Has full dataset context for temporal linking
- Embedding similarity works better with complete data
- LLM validation more reliable with full event history
- Clearer separation of concerns

### Why Not Real-Time Temporal Linking?

The system uses batch consolidation instead of real-time temporal linking because:

1. **Better Context**: Batch processing sees all events, not just recent ones
2. **Higher Accuracy**: LLM can compare events across entire timeline
3. **Simpler Logic**: No complex lookback windows or sliding windows
4. **Easier Debugging**: Clear stage boundaries for troubleshooting
5. **Better Performance**: One consolidation pass vs. continuous updates

---

## Common Issues & Solutions

### Issue: Orphaned Canonical Events
**Symptom**: canonical_events exist without daily_event_mentions
**Cause**: Bug in llm_deconflict_clusters.py (FIXED 2026-01-06)
**Solution**:
1. Fixed in code with savepoints
2. Run wipe_event_tables.py and reprocess

### Issue: Zero Multi-Day References
**Symptom**: Master events show 0 documents
**Cause**: Query looking for deleted child events
**Solution**: Query daily_event_mentions linked to masters directly

### Issue: Missing Date Coverage
**Symptom**: Gaps in event timeline
**Cause**: batch_cluster_events.py not run for those dates
**Solution**: Run find_unprocessed_dates.py and process missing dates

---

## File Organization

### Core Pipeline Scripts
- batch_cluster_events.py
- llm_deconflict_clusters.py
- consolidate_all_events.py
- llm_deconflict_canonical_events.py
- llm_deconflict_canonical_events_parallel.py — auto-resuming, parallelizable (--chunk) variant of Stage 2B validation
- merge_canonical_events.py
- score_canonical_event_materiality.py — Stage 3 materiality scoring (see below)
- auto_queue_materiality.py — monitors one country's materiality run and auto-starts the next
- split_overaggregated_events.py — one-off cleanup: LLM-validated temporal split of over-aggregated canonical events
- extract_canonical_event_entities.py — extracts named entities from master canonical events into entities_mentioned

### Diagnostic Tools
- check_pipeline_coverage.py
- debug_single_event.py
- diagnose_consolidation.py
- find_unprocessed_dates.py
- query_master_events.py
- show_event_docs.py

### Data Management
- wipe_event_tables.py
- export_event_tables.py
- import_event_tables.py

---

**For Visual Diagram**: Open [EVENT_PIPELINE_DIAGRAM.drawio](EVENT_PIPELINE_DIAGRAM.drawio) in draw.io or VS Code with draw.io extension
