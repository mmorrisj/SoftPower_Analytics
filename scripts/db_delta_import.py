#!/usr/bin/env python3
"""
Apply an incremental bundle produced by db_delta_export.py (gzipped CSV + manifest)
to a SoftPower database. Runs as ONE transaction: either every table is applied or
nothing changes. Needs only psycopg2 — run it on the host or inside the app container:

    python scripts/db_delta_import.py --input-dir ./db_delta_20260826 --dry-run   # validate + counts, then ROLLBACK
    python scripts/db_delta_import.py --input-dir ./db_delta_20260826             # apply

    # inside the enterprise app container (bundle copied with `docker cp`):
    docker cp db_delta_20260826 sp_prod_app:/tmp/
    docker exec sp_prod_app python scripts/db_delta_import.py --input-dir /tmp/db_delta_20260826

Per-table modes (from the manifest): replace | replace_by_key | replace_where — see
db_delta_export.py. Whole-table replaces are applied children-first so foreign keys
are never violated; foreign-key checks stay ON.

Connection: DATABASE_URL, or POSTGRES_* / DB_HOST / DB_PORT from the environment.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg2

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # pragma: no cover
    pass

# children before parents for the DELETE phase of whole-table replaces
DELETE_ORDER = ["event_source_links", "daily_event_mentions", "event_summaries",
                "entity_relationships", "daily_entity_mentions", "canonical_entities",
                "canonical_events", "batch_jobs"]
# parents first; the keyed DELETE phase runs this list in reverse (children first)
KEY_DELETE_ORDER = ["documents", "categories", "subcategories", "initiating_countries",
                    "recipient_countries", "raw_events", "raw_entities", "langchain_pg_embedding"]


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


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def verify_files(in_dir: Path, entries):
    problems = []
    for e in entries:
        for f in e["files"]:
            p = in_dir / f
            if not p.exists():
                problems.append(f"missing {f}"); continue
            if hashlib.sha256(p.read_bytes()).hexdigest() != e["sha256"][f]:
                problems.append(f"checksum mismatch {f}")
    return problems


def copy_files(cur, target_sql: str, cols, in_dir: Path, files):
    """COPY each gzipped part into target_sql (a staging or real table)."""
    col_list = ", ".join(q(c) for c in cols)
    for f in files:
        with gzip.open(in_dir / f, "rb") as fh:
            cur.copy_expert(f"COPY {target_sql} ({col_list}) FROM STDIN WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')", fh)


def table_exists(cur, schema, table):
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s", (schema, table))
    return cur.fetchone() is not None


def apply(conn, in_dir: Path, entries, verbose=True):
    cur = conn.cursor()
    cur.execute("SET LOCAL statement_timeout = 0")
    # phase 0: create missing analytics tables
    for e in entries:
        if e.get("create_if_missing") and not table_exists(cur, e["schema"], e["table"]):
            cur.execute(e["ddl"]); print(f"  created {e['schema']}.{e['table']}")
    # phase 1: delete whole-table replaces, children first
    replace = [e for e in entries if e["mode"] == "replace"]
    order = {n: i for i, n in enumerate(DELETE_ORDER)}
    for e in sorted(replace, key=lambda e: order.get(e["table"], len(order))):
        cur.execute(f'DELETE FROM {q(e["schema"])}.{q(e["table"])}')
        e["deleted"] = cur.rowcount
    # phase 2a: stage every replace_by_key CSV, then delete matching target rows children-first
    keyed = [e for e in entries if e["mode"] == "replace_by_key"]
    key_order = {n: i for i, n in enumerate(KEY_DELETE_ORDER)}
    for e in keyed:
        tgt = f'{q(e["schema"])}.{q(e["table"])}'
        e["_stage"] = f'stage_{e["table"]}_{abs(hash(e.get("file_stem", e["table"]))) % 10**6}'
        cur.execute(f"CREATE TEMP TABLE {e['_stage']} (LIKE {tgt} INCLUDING DEFAULTS) ON COMMIT DROP")
        for c in e.get("exclude_columns", []):
            cur.execute(f"ALTER TABLE {e['_stage']} DROP COLUMN {q(c)}")
        copy_files(cur, e["_stage"], e["columns"], in_dir, e["files"])
    for e in sorted(keyed, key=lambda e: key_order.get(e["table"], -1), reverse=True):
        tgt = f'{q(e["schema"])}.{q(e["table"])}'
        key_expr = e.get("key_expr") or ", ".join(q(k) for k in e["key"])
        scope = f" AND ({e['scope_where']})" if e.get("scope_where") else ""
        cur.execute(f"DELETE FROM {tgt} WHERE ({key_expr}) IN (SELECT {key_expr} FROM {e['_stage']}){scope}")
        e["deleted"] = cur.rowcount
    # phase 2b: apply every entry in manifest order (parents before children)
    for e in entries:
        tgt = f'{q(e["schema"])}.{q(e["table"])}'
        cols = e["columns"]
        col_list = ", ".join(q(c) for c in cols)
        if e["mode"] == "replace":
            copy_files(cur, tgt, cols, in_dir, e["files"])
            e["inserted"] = e["rows"]
        elif e["mode"] == "replace_where":
            cur.execute(f"DELETE FROM {tgt} WHERE {e['where']}"); e["deleted"] = cur.rowcount
            copy_files(cur, tgt, cols, in_dir, e["files"]); e["inserted"] = e["rows"]
        elif e["mode"] == "replace_by_key":
            cur.execute(f"INSERT INTO {tgt} ({col_list}) SELECT {col_list} FROM {e['_stage']}")
            e["inserted"] = cur.rowcount
        else:
            raise ValueError(f"unknown mode {e['mode']}")
        if verbose:
            print(f"  {e['schema']}.{e['table']:<26s} {e['mode']:<15s} -{e.get('deleted', 0):>8,} +{e.get('inserted', 0):>8,}")
    # phase 3: sanity — every replaced table now holds exactly the exported rows
    for e in replace:
        cur.execute(f'SELECT count(*) FROM {q(e["schema"])}.{q(e["table"])}')
        n = cur.fetchone()[0]
        if n != e["rows"]:
            raise RuntimeError(f"{e['schema']}.{e['table']}: expected {e['rows']} rows after replace, found {n}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--dry-run", action="store_true", help="apply inside a transaction, print counts, then ROLLBACK")
    ap.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()
    in_dir = Path(args.input_dir)
    manifest = json.loads((in_dir / "delta_manifest.json").read_text(encoding="utf-8"))
    entries = manifest["entries"]
    print(f"Bundle: {manifest['format']} created {manifest['created_at']} | source {manifest['source_documents']:,} docs "
          f"through {manifest['source_max_date']} | doc window from {manifest['doc_date_from']} | vectors={manifest['with_vectors']}")
    problems = verify_files(in_dir, entries)
    if problems:
        sys.exit("ERROR: " + "; ".join(problems))
    print(f"  {len(entries)} entries, all files present and checksums OK")
    conn = connect(); conn.autocommit = False
    cur = conn.cursor(); cur.execute("SELECT current_database(), count(*) FROM public.documents"); db, n = cur.fetchone()
    print(f"Target: database {db} with {n:,} documents")
    if not args.dry_run and not args.yes:
        if input("Apply bundle? [y/N]: ").strip().lower() != "y":
            sys.exit("aborted")
    try:
        apply(conn, in_dir, entries)
        if args.dry_run:
            conn.rollback(); print("\nDRY RUN — everything rolled back; the bundle applies cleanly.")
        else:
            conn.commit()
            cur = conn.cursor(); cur.execute("SELECT count(*), max(date) FROM public.documents")
            print("\nCOMMITTED. documents now:", cur.fetchone())
    except Exception as exc:
        conn.rollback(); sys.exit(f"\nFAILED (rolled back, nothing changed): {exc}")


if __name__ == "__main__":
    main()
