#!/usr/bin/env python3
"""
Export an incremental data bundle (gzipped CSV + manifest) for transfer to another
SoftPower database — the text-only alternative to db_export.py's pg_dump chunks.

The bundle describes, per table, how the importer should apply it:
  replace          delete every row in the target table, then load the CSV
                   (small, fully-rebuilt derived tables: events, entities, summaries,
                   analytics.* ...)
  replace_by_key   load the CSV into a staging table, delete target rows whose key
                   matches a staged key, then insert the staged rows (document-layer
                   tables filtered to a doc_id window; idempotent, no duplicates)
  replace_where    delete target rows matching a SQL predicate, then load the CSV
                   (e.g. the summary-embedding collections in langchain_pg_embedding)

Every file is gzipped CSV written by COPY TO (header row = column list), so the
importer needs nothing but psycopg2. Files stay far below common transfer caps;
use --max-mb to split any table into numbered parts.

Usage:
    python scripts/db_delta_export.py --output-dir ./db_delta_20260826 --doc-date-from 2026-07-21
    python scripts/db_delta_export.py --output-dir ./x --doc-date-from 2026-07-21 --no-vectors
    python scripts/db_delta_export.py --output-dir ./x --doc-date-from 2026-07-21 --dry-run

Connection: DATABASE_URL, or POSTGRES_* / DB_HOST / DB_PORT from the environment (.env).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # pragma: no cover - dotenv is optional
    pass

SUMMARY_COLLECTIONS = ("daily_event_embeddings", "weekly_event_embeddings",
                       "monthly_event_embeddings", "yearly_event_embeddings")
DOC_COLLECTION = "chunk_embeddings"


def connect():
    url = os.getenv("DATABASE_URL")
    if url:
        return psycopg2.connect(url.replace("postgresql+psycopg2://", "postgresql://"))
    return psycopg2.connect(
        host=os.getenv("DB_HOST") or os.getenv("POSTGRES_HOST") or "localhost",
        port=os.getenv("DB_PORT") or os.getenv("POSTGRES_PORT") or "5432",
        user=os.getenv("POSTGRES_USER"), password=os.getenv("POSTGRES_PASSWORD"),
        dbname=os.getenv("POSTGRES_DB"),
    )


def plan(doc_date_from: str, with_vectors: bool, include_batch_jobs: bool):
    """Return the ordered list of bundle entries (parents before children)."""
    doc_set = f"SELECT doc_id FROM public.documents WHERE date >= '{doc_date_from}'"
    entries = [
        # ---- document layer: replace by doc_id inside the date window
        dict(schema="public", table="documents", mode="replace_by_key", key=["doc_id"],
             where=f"date >= '{doc_date_from}'"),
    ]
    for t in ("categories", "subcategories", "initiating_countries", "recipient_countries",
              "raw_events", "raw_entities"):
        entries.append(dict(schema="public", table=t, mode="replace_by_key", key=["doc_id"],
                            where=f"doc_id IN ({doc_set})"))
    # document vectors for the window (one row per doc in the chunk collection)
    entries.append(dict(
        schema="public", table="langchain_pg_embedding", mode="replace_by_key",
        key_expr="cmetadata->>'doc_id'", key=["doc_id"],
        where=(f"collection_id = (SELECT uuid FROM public.langchain_pg_collection WHERE name='{DOC_COLLECTION}') "
               f"AND cmetadata->>'doc_id' IN ({doc_set})"),
        file_stem="langchain_pg_embedding__chunk_delta",
        scope_where=f"collection_id = (SELECT uuid FROM public.langchain_pg_collection WHERE name='{DOC_COLLECTION}')",
    ))
    # ---- derived layers: whole-table replace (children deleted first by the importer)
    exclude = {} if with_vectors else {
        "canonical_events": ["embedding_vector"], "canonical_entities": ["embedding_vector"]}
    for t in ("canonical_events", "daily_event_mentions", "event_summaries", "event_source_links",
              "canonical_entities", "daily_entity_mentions", "entity_relationships"):
        entries.append(dict(schema="public", table=t, mode="replace", exclude_columns=exclude.get(t, [])))
    if include_batch_jobs:
        entries.append(dict(schema="public", table="batch_jobs", mode="replace"))
    # summary vectors: replace the four summary collections wholesale
    coll = ", ".join(f"'{c}'" for c in SUMMARY_COLLECTIONS)
    entries.append(dict(
        schema="public", table="langchain_pg_embedding", mode="replace_where",
        where=f"collection_id IN (SELECT uuid FROM public.langchain_pg_collection WHERE name IN ({coll}))",
        file_stem="langchain_pg_embedding__summary_collections",
    ))
    return entries


def analytics_entries(cur):
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='analytics' "
                "AND table_type='BASE TABLE' ORDER BY 1")
    return [dict(schema="analytics", table=r[0], mode="replace", create_if_missing=True) for r in cur.fetchall()]


def columns(cur, schema, table, exclude):
    cur.execute("SELECT column_name, data_type, udt_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position", (schema, table))
    cols = [(n, dt, udt) for n, dt, udt in cur.fetchall() if n not in exclude]
    return cols


def ddl_for(cur, schema, table):
    """Minimal CREATE TABLE for analytics objects (no constraints needed there)."""
    cur.execute("""SELECT column_name, format_type(a.atttypid, a.atttypmod)
                   FROM information_schema.columns c
                   JOIN pg_attribute a ON a.attname = c.column_name
                   JOIN pg_class r ON r.oid = a.attrelid AND r.relname = c.table_name
                   JOIN pg_namespace n ON n.oid = r.relnamespace AND n.nspname = c.table_schema
                   WHERE c.table_schema=%s AND c.table_name=%s ORDER BY c.ordinal_position""", (schema, table))
    cols = ", ".join(f'"{n}" {t}' for n, t in cur.fetchall())
    return f'CREATE SCHEMA IF NOT EXISTS "{schema}"; CREATE TABLE IF NOT EXISTS "{schema}"."{table}" ({cols});'


def export_entry(conn, e, out_dir: Path, max_bytes: int, dry_run: bool):
    cur = conn.cursor()
    cols = columns(cur, e["schema"], e["table"], set(e.get("exclude_columns", [])))
    col_list = ", ".join(f'"{c[0]}"' for c in cols)
    where = f" WHERE {e['where']}" if e.get("where") else ""
    sql = f'SELECT {col_list} FROM "{e["schema"]}"."{e["table"]}"{where}'
    cur.execute(f"SELECT count(*) FROM ({sql}) s")
    rows = cur.fetchone()[0]
    stem = e.get("file_stem") or f'{e["schema"]}.{e["table"]}'
    e.update(columns=[c[0] for c in cols], rows=rows, files=[])
    if e.get("create_if_missing"):
        e["ddl"] = ddl_for(cur, e["schema"], e["table"])
    if dry_run:
        return
    # stream COPY output through gzip, splitting into parts at max_bytes of *compressed* data
    part, written, header = 1, 0, None
    gz, fh = None, None

    def open_part():
        nonlocal gz, fh, part, written
        p = out_dir / f"{stem}.part{part:03d}.csv.gz"
        fh = open(p, "wb"); gz = gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=6)
        written = 0
        e["files"].append(p.name)
        if header is not None and part > 1:
            gz.write(header)

    class Sink(io.RawIOBase):
        def writable(self): return True
        def write(self, b):
            nonlocal header, written, part
            data = bytes(b)
            if header is None:
                nl = data.find(b"\n"); header = data[:nl + 1]
            gz.write(data); written += len(data)
            if fh.tell() >= max_bytes:
                gz.close(); fh.close(); part += 1; open_part()
            return len(data)

    open_part()
    cur.copy_expert(f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')", Sink())
    gz.close(); fh.close()
    for name in e["files"]:
        p = out_dir / name
        e.setdefault("sha256", {})[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        e.setdefault("bytes", {})[name] = p.stat().st_size


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--doc-date-from", required=True, help="documents with date >= this are exported (replace-by-key)")
    ap.add_argument("--no-vectors", action="store_true", help="omit canonical_events/entities embedding vectors (re-embed on target)")
    ap.add_argument("--include-batch-jobs", action="store_true")
    ap.add_argument("--max-mb", type=int, default=250, help="split any gzipped file above this size (default 250)")
    ap.add_argument("--dry-run", action="store_true", help="count rows only, write nothing")
    args = ap.parse_args()

    out = Path(args.output_dir)
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        if (out / "delta_manifest.json").exists():
            sys.exit(f"ERROR: {out}/delta_manifest.json already exists — use a fresh directory")
    conn = connect(); conn.set_session(readonly=True, autocommit=False)
    cur = conn.cursor()
    entries = plan(args.doc_date_from, not args.no_vectors, args.include_batch_jobs) + analytics_entries(cur)
    cur.execute("SELECT count(*), max(date) FROM public.documents"); ndocs, maxdate = cur.fetchone()
    print(f"Source: {ndocs:,} documents through {maxdate}; window from {args.doc_date_from}; "
          f"{'DRY RUN' if args.dry_run else 'writing to ' + str(out)}")
    for i, e in enumerate(entries, 1):
        export_entry(conn, e, out, args.max_mb * 1024 * 1024, args.dry_run)
        size = sum(e.get("bytes", {}).values())
        print(f"  [{i:2d}/{len(entries)}] {e['schema']}.{e['table']:<26s} {e['mode']:<15s} {e['rows']:>9,} rows"
              + (f"  {size/1e6:8.1f} MB gz in {len(e['files'])} file(s)" if not args.dry_run else ""))
    if args.dry_run:
        return
    manifest = dict(format="softpower-delta-csv/1", created_at=datetime.now(timezone.utc).isoformat(),
                    source_documents=ndocs, source_max_date=str(maxdate), doc_date_from=args.doc_date_from,
                    with_vectors=not args.no_vectors, entries=entries)
    (out / "delta_manifest.json").write_text(json.dumps(manifest, indent=1, default=str) + "\n", encoding="utf-8")
    total = sum(sum(e.get("bytes", {}).values()) for e in entries)
    print(f"\nBundle: {len(entries)} entries, {total/1e6:.1f} MB gzipped -> {out}/delta_manifest.json")
    print("Import with: python scripts/db_delta_import.py --input-dir <dir> [--dry-run]")


if __name__ == "__main__":
    main()
