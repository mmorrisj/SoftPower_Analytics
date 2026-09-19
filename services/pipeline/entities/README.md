# Entity Extraction Pipeline

Two-stage entity extraction and consolidation pipeline for tracking influential actors in soft power strategies.

## Overview

This pipeline extracts named entities (persons, organizations, companies, locations) from diplomatic documents and builds a knowledge graph of relationships between entities. It follows the same architecture as the event processing pipeline with two distinct stages:

**Stage 1: Daily Entity Extraction** - Extract and cluster entity mentions per day
**Stage 2: Batch Consolidation** - Resolve entities across the entire dataset (entity resolution)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ENTITY EXTRACTION PIPELINE                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  STAGE 1: Daily Processing (Per-Day Entity Detection)               │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │  1A. extract_daily_entities.py                              │   │
│  │      ├─> Read documents.distilled_text                      │   │
│  │      ├─> LLM extracts entities with roles & context         │   │
│  │      └─> Save to raw_entities table                         │   │
│  │                                                              │   │
│  │  1B. cluster_daily_entities.py                              │   │
│  │      ├─> Load raw_entities for (country, date, type)        │   │
│  │      ├─> Generate embeddings (SentenceTransformer)          │   │
│  │      ├─> DBSCAN clustering (eps=0.12)                       │   │
│  │      └─> Save to entity_clusters table                      │   │
│  │                                                              │   │
│  │  1C. llm_deconflict_entity_clusters.py                      │   │
│  │      ├─> Load entity_clusters (llm_deconflicted=FALSE)      │   │
│  │      ├─> LLM validates clusters (name variations)           │   │
│  │      ├─> Create canonical_entities                          │   │
│  │      └─> Create daily_entity_mentions (atomic with sp)      │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  STAGE 2: Batch Consolidation (Cross-Date Entity Resolution)        │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │  2A. consolidate_all_entities.py                            │   │
│  │      ├─> Load ALL canonical_entities for country            │   │
│  │      ├─> Compute pairwise similarity (cosine ≥ 0.88)        │   │
│  │      ├─> Find connected components (graph)                  │   │
│  │      └─> Set master_entity_id for child entities            │   │
│  │                                                              │   │
│  │  2B. llm_deconflict_canonical_entities.py                   │   │
│  │      ├─> Load master entities with children                 │   │
│  │      ├─> LLM validates groupings (same entity?)             │   │
│  │      ├─> Pick best canonical name                           │   │
│  │      └─> Mark llm_validated=TRUE                            │   │
│  │                                                              │   │
│  │  2C. merge_canonical_entities.py                            │   │
│  │      ├─> Reassign daily_entity_mentions to master           │   │
│  │      ├─> Merge metadata (categories, recipients)            │   │
│  │      └─> Delete empty child entities                        │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  RELATIONSHIP EXTRACTION (Graph Building)                            │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │  build_entity_cooccurrence.py                               │   │
│  │      ├─> Find co-occurring entities in shared documents     │   │
│  │      └─> Save edges to entity_relationships table           │   │
│  │                                                              │   │
│  │  classify_entity_relationships.py                           │   │
│  │      ├─> LLM classifies each co-occurrence edge             │   │
│  │      └─> Sets relationship_type + description               │   │
│  └────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Database Schema

### Core Tables

#### `raw_entities`
Raw entity mentions extracted from documents (before clustering).

```sql
doc_id                TEXT (FK → documents)
entity_name           TEXT
entity_type           ENUM (person, organization, company, location)
role                  TEXT (e.g., "Foreign Minister", "CEO")
country_affiliation   TEXT
context_snippet       TEXT (surrounding text)
```

#### `entity_clusters`
Daily clustering results (Stage 1B).

```sql
id                    UUID
initiating_country    TEXT
cluster_date          DATE
entity_type           ENUM
batch_number          INTEGER (for LLM batch processing)
cluster_id            INTEGER (DBSCAN label)
entity_names          TEXT[] (all names in cluster)
doc_ids               TEXT[] (source documents)
cluster_size          INTEGER
is_noise              BOOLEAN (DBSCAN noise flag)
centroid_embedding    FLOAT[] (cluster centroid)
representative_name   TEXT (most central name)
llm_deconflicted      BOOLEAN
refined_clusters      JSONB (LLM results)
```

#### `canonical_entities`
Canonical entity representations across time.

```sql
id                    UUID
master_entity_id      UUID (FK → canonical_entities, for Stage 2)
llm_validated         BOOLEAN (Stage 2B validation)
canonical_name        TEXT (best name)
entity_type           ENUM
initiating_country    TEXT
primary_role          ENUM (government_official, diplomat, etc.)
country_affiliations  TEXT[]
alternative_names     TEXT[] (aliases, variations)
first_mention_date    DATE
last_mention_date     DATE
total_mention_days    INTEGER
total_documents       INTEGER
primary_categories    JSONB {category: count}
primary_recipients    JSONB {country: count}
associated_events     TEXT[] (event IDs)
entity_description    TEXT (LLM-generated bio)
key_activities        JSONB
embedding_vector      FLOAT[]
```

#### `daily_entity_mentions`
Daily consolidation of entity activity.

```sql
id                    UUID
canonical_entity_id   UUID (FK → canonical_entities)
initiating_country    TEXT
mention_date          DATE
document_count        INTEGER
primary_role_this_day TEXT
activities_this_day   TEXT
doc_ids               TEXT[]
associated_event_ids  TEXT[] (events on this day)
```

#### `entity_relationships`
Directed relationships between entities (for graph visualization).

```sql
id                      UUID
entity_from_id          UUID (FK → canonical_entities)
entity_to_id            UUID (FK → canonical_entities)
relationship_type       VARCHAR(100)
  Examples:
    - works_with (person-person)
    - employed_by (person-organization)
    - leads (person-organization)
    - partnered_with (org-org)
    - located_in (org/person-location)
co_occurrence_count     INTEGER
first_co_occurrence     DATE
last_co_occurrence      DATE
primary_categories      JSONB {category: count}
relationship_description TEXT (LLM-generated)
source_doc_ids          TEXT[]
```

## Usage

### Stage 1: Daily Entity Extraction

#### 1A. Extract Entities from Documents

```bash
# Extract entities for China in August 2024
python services/pipeline/entities/extract_daily_entities.py \
    --country China \
    --start-date 2024-08-01 \
    --end-date 2024-08-31

# Check extraction status
python services/pipeline/entities/extract_daily_entities.py \
    --country China \
    --status

# Force reprocess documents
python services/pipeline/entities/extract_daily_entities.py \
    --country China \
    --start-date 2024-08-01 \
    --end-date 2024-08-31 \
    --force
```

**Output**: Populates `raw_entities` table with entity mentions

#### 1B. Cluster Entity Mentions

```bash
# Cluster all entity types
python services/pipeline/entities/cluster_daily_entities.py \
    --country China \
    --start-date 2024-08-01 \
    --end-date 2024-08-31

# Cluster specific entity type only
python services/pipeline/entities/cluster_daily_entities.py \
    --country China \
    --start-date 2024-08-01 \
    --end-date 2024-08-31 \
    --entity-type person

# Check clustering status
python services/pipeline/entities/cluster_daily_entities.py \
    --country China \
    --status

# Custom clustering parameters (tighter clustering)
python services/pipeline/entities/cluster_daily_entities.py \
    --country China \
    --start-date 2024-08-01 \
    --end-date 2024-08-31 \
    --eps 0.10 \
    --min-samples 2
```

**Output**: Populates `entity_clusters` table

#### 1C. LLM Deconflict Entity Clusters

```bash
# Process all pending clusters
python services/pipeline/entities/llm_deconflict_entity_clusters.py \
    --country China \
    --start-date 2024-08-01 \
    --end-date 2024-08-31

# Process specific entity type
python services/pipeline/entities/llm_deconflict_entity_clusters.py \
    --country China \
    --start-date 2024-08-01 \
    --end-date 2024-08-31 \
    --entity-type person
```

Flags: `--country`, `--start-date`, `--end-date` (all three required), `--entity-type`, `--checkpoint-frequency`, `--dry-run`. Incremental by design: only clusters with `llm_deconflicted=FALSE` are processed, so re-running the same range resumes automatically.

**Output**: Creates `canonical_entities` and `daily_entity_mentions`

---

### Stage 2: Batch Consolidation (Entity Resolution)

#### 2A-0. Embed Canonical Entities (Prerequisite)

Stage 2A consolidation matches on `canonical_entities.embedding_vector`, so embed first:

```bash
# Embed entities missing an embedding
python services/pipeline/entities/embed_canonical_entities.py --country China

# Check embedding status / re-embed everything
python services/pipeline/entities/embed_canonical_entities.py --country China --status
python services/pipeline/entities/embed_canonical_entities.py --influencers --force
```

Flags: `--country`, `--influencers`, `--status`, `--dry-run`, `--force`, `--batch-size` (default 100)

**Output**: Populates `canonical_entities.embedding_vector`

#### 2A. Consolidate Entities Across Dates

```bash
# Consolidate all entities for China
python services/pipeline/entities/consolidate_all_entities.py \
    --country China

# Custom similarity threshold (stricter matching)
python services/pipeline/entities/consolidate_all_entities.py \
    --country China \
    --similarity-threshold 0.90

# Reset existing consolidations before re-running
python services/pipeline/entities/consolidate_all_entities.py \
    --country China \
    --force

# Dry run
python services/pipeline/entities/consolidate_all_entities.py \
    --country China \
    --dry-run
```

Flags: `--country`, `--influencers`, `--similarity-threshold` (default 0.88), `--dry-run`, `--force`

**Output**: Sets `master_entity_id` for child entities

#### 2B. LLM Validate Entity Consolidation

```bash
# Validate all pending entity groups
python services/pipeline/entities/llm_deconflict_canonical_entities.py \
    --country China

# Resume from checkpoint (skip already-validated groups)
python services/pipeline/entities/llm_deconflict_canonical_entities.py \
    --country China \
    --resume

# Force reprocessing of all groups
python services/pipeline/entities/llm_deconflict_canonical_entities.py \
    --country China \
    --force
```

Flags: `--country`, `--influencers`, `--all`, `--dry-run`, `--verbose`, `--resume`, `--force`, `--batch-size` (default 10)

**Output**: Marks `llm_validated=TRUE` for validated groups

#### 2C. Merge Canonical Entities

```bash
# Merge all validated entity groups
python services/pipeline/entities/merge_canonical_entities.py \
    --country China

# Dry run (show what would be merged)
python services/pipeline/entities/merge_canonical_entities.py \
    --country China \
    --dry-run
```

Flags: `--country`, `--influencers`, `--dry-run`, `--verbose`

**Output**: Consolidates `daily_entity_mentions`, deletes empty child entities

---

### Relationship Extraction (Graph Building)

Two-step process: build the co-occurrence network, then classify the edges with the LLM.

#### Step 1: Build Co-occurrence Network

```bash
# Build edges from shared documents
python services/pipeline/entities/build_entity_cooccurrence.py --country China

# Stricter co-occurrence threshold
python services/pipeline/entities/build_entity_cooccurrence.py \
    --country China \
    --min-cooccurrence 3

# Rebuild from scratch
python services/pipeline/entities/build_entity_cooccurrence.py --country China --force
```

Flags: `--country`, `--influencers`, `--dry-run`, `--force` (delete existing relationships before rebuilding), `--min-cooccurrence` (minimum shared documents, default 2), `--batch-size` (default 500), `--verbose`

**Output**: Populates `entity_relationships` (type `co_occurrence`, with `co_occurrence_count`, date range, `source_doc_ids`)

#### Step 2: Classify Relationships

```bash
# LLM classifies unclassified edges
python services/pipeline/entities/classify_entity_relationships.py --country China

# Reclassify already-classified edges
python services/pipeline/entities/classify_entity_relationships.py --country China --force
```

Flags: `--country`, `--influencers`, `--dry-run`, `--force` (reclassify already-classified), `--min-cooccurrence` (only classify edges with ≥ N shared documents, default 2), `--batch-size` (default 20), `--verbose`

**Output**: Sets `entity_relationships.relationship_type` and `relationship_description`

---

### Additional Scripts

- **embed_canonical_entities.py** — Generates `canonical_entities.embedding_vector`; prerequisite for Stage 2A (see above). Flags: `--country`, `--influencers`, `--status`, `--dry-run`, `--force`, `--batch-size`.
- **link_entities_to_events.py** — Links entity mentions to canonical events; writes `daily_entity_mentions.associated_event_ids` and `canonical_entities.associated_events`. Flags: `--country`, `--influencers`, `--dry-run`, `--force`, `--batch-size` (default 500), `--verbose`.
- **generate_entity_descriptions.py** — LLM-generated profiles; writes `canonical_entities.entity_description` and `key_activities`. Flags: `--country`, `--influencers`, `--dry-run`, `--force`, `--resume` (default behavior), `--batch-size` (default 20), `--min-docs` (default 3), `--verbose`.
- **wipe_entity_tables.py** — ⚠️ Deletes entity pipeline data (optionally scoped). Flags: `--country`, `--start-date`, `--end-date`, `--dry-run`, `--yes`.

---

## Complete Processing Workflow

Process entities for China from August-December 2024:

```bash
# Stage 1A: Extract raw entities
python services/pipeline/entities/extract_daily_entities.py \
    --country China --start-date 2024-08-01 --end-date 2024-12-31

# Stage 1B: Cluster entities per day
python services/pipeline/entities/cluster_daily_entities.py \
    --country China --start-date 2024-08-01 --end-date 2024-12-31

# Stage 1C: LLM deconflict clusters
python services/pipeline/entities/llm_deconflict_entity_clusters.py \
    --country China --start-date 2024-08-01 --end-date 2024-12-31

# Stage 2A-0: Embed canonical entities (prerequisite for consolidation)
python services/pipeline/entities/embed_canonical_entities.py \
    --country China

# Stage 2A: Consolidate across all dates
python services/pipeline/entities/consolidate_all_entities.py \
    --country China

# Stage 2B: LLM validate consolidation
python services/pipeline/entities/llm_deconflict_canonical_entities.py \
    --country China

# Stage 2C: Merge into master entities
python services/pipeline/entities/merge_canonical_entities.py \
    --country China

# Build the relationship graph
python services/pipeline/entities/build_entity_cooccurrence.py --country China
python services/pipeline/entities/classify_entity_relationships.py --country China
```

---

## Querying Entity Data

### Get Top Influencers

```sql
-- Top 50 most mentioned persons for China
SELECT
    canonical_name,
    primary_role,
    country_affiliations,
    total_documents,
    total_mention_days,
    first_mention_date,
    last_mention_date,
    primary_categories,
    primary_recipients
FROM canonical_entities
WHERE initiating_country = 'China'
  AND entity_type = 'person'
  AND master_entity_id IS NULL  -- Only master entities
ORDER BY total_documents DESC
LIMIT 50;
```

### Get Entity Network Graph

```sql
-- Entity relationship network (for graph visualization)
SELECT
    from_entity.canonical_name as from_name,
    from_entity.entity_type as from_type,
    to_entity.canonical_name as to_name,
    to_entity.entity_type as to_type,
    er.relationship_type,
    er.co_occurrence_count,
    er.primary_categories,
    er.relationship_description
FROM entity_relationships er
JOIN canonical_entities from_entity ON er.entity_from_id = from_entity.id
JOIN canonical_entities to_entity ON er.entity_to_id = to_entity.id
WHERE from_entity.initiating_country = 'China'
  AND er.co_occurrence_count >= 3  -- Filter weak connections
ORDER BY er.co_occurrence_count DESC;
```

### Get Entity Timeline

```sql
-- Activity timeline for a specific entity
SELECT
    dem.mention_date,
    dem.document_count,
    dem.primary_role_this_day,
    dem.activities_this_day,
    dem.associated_event_ids
FROM daily_entity_mentions dem
JOIN canonical_entities ce ON dem.canonical_entity_id = ce.id
WHERE ce.canonical_name = 'Wang Yi'
  AND ce.initiating_country = 'China'
ORDER BY dem.mention_date;
```

### Get Entities by Event

```sql
-- Find all entities associated with an event
SELECT
    ce.canonical_name,
    ce.entity_type,
    ce.primary_role,
    dem.primary_role_this_day,
    dem.activities_this_day
FROM canonical_entities ce
JOIN daily_entity_mentions dem ON ce.id = dem.canonical_entity_id
WHERE 'event_uuid_here' = ANY(dem.associated_event_ids);
```

---

## Key Design Patterns

### 1. Two-Stage Processing
- **Stage 1 (Daily)**: Fast processing within constrained scope (per-day, per-type)
- **Stage 2 (Batch)**: Comprehensive entity resolution across entire dataset
- **Why**: Separates real-time processing from comprehensive cross-dataset linking

### 2. Atomic Operations with Savepoints
```python
savepoint = session.begin_nested()
try:
    canonical_entity = CanonicalEntity(...)
    session.add(canonical_entity)
    session.flush()  # Get ID

    daily_mention = DailyEntityMention(...)
    session.add(daily_mention)
    session.flush()

    savepoint.commit()
except Exception:
    savepoint.rollback()  # Only this pair, not entire batch
    raise
```

### 3. Master-Child Hierarchy (Entity Resolution)
```sql
-- Stage 2A: Set master_entity_id
UPDATE canonical_entities
SET master_entity_id = :master_id
WHERE id = :child_id

-- Stage 2C: Reassign daily mentions
UPDATE daily_entity_mentions
SET canonical_entity_id = :master_id
WHERE canonical_entity_id = :child_id

-- Delete empty children
DELETE FROM canonical_entities WHERE id = :child_id
```

### 4. Checkpoint/Resume Architecture
```python
def process_with_checkpoints(clusters, checkpoint_freq=10):
    clusters_since_commit = 0
    for cluster in clusters:
        # Process cluster...
        clusters_since_commit += 1

        if clusters_since_commit >= checkpoint_freq:
            session.commit()
            clusters_since_commit = 0
```

---

## Configuration

### Clustering Parameters

```python
# Entity clustering (tighter than events - less name variation)
DEFAULT_EPS = 0.12  # vs 0.15 for events
DEFAULT_MIN_SAMPLES = 1

# Consolidation (stricter matching)
DEFAULT_SIMILARITY_THRESHOLD = 0.88
```

### Batch Sizes

```python
CLUSTER_BATCH_SIZE = 150  # Entities per LLM batch
DOCUMENT_BATCH_SIZE = 50  # Documents per commit
CHECKPOINT_FREQUENCY = 10  # Commits per checkpoint
```

---

## Troubleshooting

### Memory Issues with Large Datasets

```python
# Use chunked similarity computation
chunk_size = 1000
for start_idx in range(0, n, chunk_size):
    end_idx = min(start_idx + chunk_size, n)
    chunk = embeddings[start_idx:end_idx]
    similarities[start_idx:end_idx] = cosine_similarity(chunk, embeddings)
    del chunk
    gc.collect()
```

### Handling Failed LLM Calls

- Scripts use savepoints - failed entity clusters are rolled back individually
- Use `--resume` flag to skip already-processed clusters
- Check logs for specific error patterns

### Debugging Entity Resolution

```sql
-- Find entities that might need manual review
SELECT
    master.canonical_name as master_name,
    child.canonical_name as child_name,
    child.alternative_names,
    child.total_documents
FROM canonical_entities child
JOIN canonical_entities master ON child.master_entity_id = master.id
WHERE master.initiating_country = 'China'
  AND master.entity_type = 'person'
ORDER BY master.total_documents DESC;
```

---

## Next Steps

1. **Process Test Dataset**: Run on small date range first
   ```bash
   # Test with one month
   python services/pipeline/entities/extract_daily_entities.py \
       --country China --start-date 2024-08-01 --end-date 2024-08-31
   ```

2. **Build Visualizations**: Use entity graph data for network visualizations
   - Export to GraphML/JSON for tools like Gephi, Cytoscape
   - Build interactive dashboard with entity timelines
   - Create influence network charts

3. **Extend Analysis**: Link entities to events, track influence patterns over time
