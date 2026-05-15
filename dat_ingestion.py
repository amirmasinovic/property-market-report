"""
DAT File Parallel Ingestion Module
====================================
Version: v1.2

Loads NSW Valuer General DAT files into DuckDB using:
  - Threading: multiple cores parse files simultaneously
  - Batch inserts: pandas DataFrame -> DuckDB (fast bulk load)
  - Incremental tracking: skips files already loaded (safe to re-run)
  - Progress reporting: shows files processed, records loaded, time remaining

Designed for two use cases:
  1. Historical bulk load (one-time): all files 1990-2025
  2. Weekly incremental load: only new/unprocessed files

Usage:
  python dat_ingestion.py --source "C:\\PMR\\Source Data from Raw" --db "C:\\PMR\\pmr.duckdb"
  python dat_ingestion.py --source "C:\\PMR\\Source Data from Raw" --db "C:\\PMR\\pmr.duckdb" --incremental
"""

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import pandas as pd

from dat_parser import parse_dat_file, parse_filename

logger = logging.getLogger(__name__)

# Configuration
BATCH_SIZE   = 50_000
MAX_WORKERS  = None
CHUNK_SIZE   = 200

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingested_files (
    source_file         TEXT NOT NULL,
    file_release_date   DATE,
    district_code       TEXT,
    records_loaded      INTEGER,
    ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_file)
);

CREATE SEQUENCE IF NOT EXISTS raw_sales_id_seq;

CREATE TABLE IF NOT EXISTS raw_sales (
    id                  INTEGER DEFAULT nextval('raw_sales_id_seq') PRIMARY KEY,
    district_code       TEXT NOT NULL,
    property_id         TEXT NOT NULL,
    sequence_no         TEXT,
    unit_no             TEXT,
    strata_lot_no       TEXT,
    street_no           TEXT,
    street_name         TEXT,
    suburb              TEXT NOT NULL,
    postcode            TEXT NOT NULL,
    land_size_sqm       DOUBLE,
    contract_date       DATE NOT NULL,
    settlement_date     DATE,
    sale_price          INTEGER,
    zone_code           TEXT,
    property_category   TEXT,
    property_description TEXT,
    property_type       TEXT,
    strata_plan_no      TEXT,
    council_area_code   TEXT,
    dealing_number      TEXT,
    source_file         TEXT NOT NULL,
    file_release_date   DATE,
    UNIQUE (dealing_number, district_code)
);

CREATE INDEX IF NOT EXISTS idx_raw_sales_suburb   ON raw_sales (suburb);
CREATE INDEX IF NOT EXISTS idx_raw_sales_postcode ON raw_sales (postcode);
CREATE INDEX IF NOT EXISTS idx_raw_sales_contract ON raw_sales (contract_date);
CREATE INDEX IF NOT EXISTS idx_raw_sales_type     ON raw_sales (property_type);
"""


def setup_database(db_path: str) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(db_path)
    conn.execute(SCHEMA_SQL)
    conn.commit()
    return conn


def find_dat_files(root_dir: str) -> list:
    root = Path(root_dir)
    files = sorted(
        [p for p in root.rglob('*') if p.suffix.upper() == '.DAT'],
        key=lambda p: (p.parent.name, p.name)
    )
    return files


def get_already_ingested(conn: duckdb.DuckDBPyConnection) -> set:
    rows = conn.execute("SELECT source_file FROM ingested_files").fetchall()
    return {row[0] for row in rows}


def _parse_file_worker(filepath: str) -> tuple:
    try:
        records = list(parse_dat_file(filepath))
        return filepath, records
    except Exception:
        return filepath, []


def _insert_batch(conn: duckdb.DuckDBPyConnection, batch: list) -> int:
    if not batch:
        return 0

    df = pd.DataFrame([{
        'district_code':        r['district_code'],
        'property_id':          r['property_id'],
        'sequence_no':          r['sequence_no'],
        'unit_no':              r['unit_no'],
        'strata_lot_no':        r['strata_lot_no'],
        'street_no':            r['street_no'],
        'street_name':          r['street_name'],
        'suburb':               r['suburb'],
        'postcode':             r['postcode'],
        'land_size_sqm':        r['land_size_sqm'],
        'contract_date':        r['contract_date'],
        'settlement_date':      r['settlement_date'],
        'sale_price':           r['sale_price'],
        'zone_code':            r['zone_code'],
        'property_category':    r['property_category'],
        'property_description': r['property_description'],
        'property_type':        r['property_type'],
        'strata_plan_no':       r['strata_plan_no'],
        'council_area_code':    r['council_area_code'],
        'dealing_number':       r['dealing_number'],
        'source_file':          r['source_file'],
        'file_release_date':    r['file_release_date'],
    } for r in batch])

    before = conn.execute("SELECT COUNT(*) FROM raw_sales").fetchone()[0]
    conn.execute("""
        INSERT OR IGNORE INTO raw_sales
            (district_code, property_id, sequence_no, unit_no, strata_lot_no,
             street_no, street_name, suburb, postcode, land_size_sqm,
             contract_date, settlement_date, sale_price, zone_code,
             property_category, property_description, property_type,
             strata_plan_no, council_area_code, dealing_number,
             source_file, file_release_date)
        SELECT
            district_code, property_id, sequence_no, unit_no, strata_lot_no,
            street_no, street_name, suburb, postcode, land_size_sqm,
            contract_date, settlement_date, sale_price, zone_code,
            property_category, property_description, property_type,
            strata_plan_no, council_area_code, dealing_number,
            source_file, file_release_date
        FROM df
    """)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM raw_sales").fetchone()[0]
    return after - before


def _record_file_ingested(conn: duckdb.DuckDBPyConnection,
                           filepath: str, records_loaded: int) -> None:
    meta = parse_filename(filepath)
    conn.execute("""
        INSERT OR REPLACE INTO ingested_files
            (source_file, file_release_date, district_code, records_loaded)
        VALUES (?, ?, ?, ?)
    """, [
        os.path.basename(filepath),
        meta['file_release_date'] if meta else None,
        meta['district_code'] if meta else None,
        records_loaded,
    ])
    conn.commit()


def run_ingestion(
    source_dir: str,
    db_path: str,
    incremental: bool = True,
    max_workers: int = None,
    batch_size: int = BATCH_SIZE,
) -> dict:
    start_time = time.time()

    conn = setup_database(db_path)
    all_files = find_dat_files(source_dir)

    if not all_files:
        logger.error("No DAT files found under %s", source_dir)
        conn.close()
        return {}

    if incremental:
        already_done = get_already_ingested(conn)
        files_to_process = [f for f in all_files
                            if os.path.basename(str(f)) not in already_done]
        logger.info("Incremental mode: %d of %d files need processing",
                    len(files_to_process), len(all_files))
    else:
        files_to_process = all_files
        logger.info("Full load mode: processing all %d files", len(all_files))

    if not files_to_process:
        logger.info("Nothing to do — all files already ingested.")
        conn.close()
        return {'files_processed': 0, 'records_loaded': 0,
                'records_skipped': 0, 'elapsed_seconds': 0}

    total_files = len(files_to_process)
    files_done = 0
    records_loaded = 0
    records_parsed = 0
    pending_batch = []
    pending_file_counts = {}

    workers = max_workers or os.cpu_count() or 4
    logger.info("Using %d worker threads", workers)

    filepath_lookup = {os.path.basename(str(f)): str(f) for f in files_to_process}

    file_chunks = [
        files_to_process[i:i + CHUNK_SIZE]
        for i in range(0, len(files_to_process), CHUNK_SIZE)
    ]
    total_chunks = len(file_chunks)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for chunk_idx, chunk in enumerate(file_chunks):

            futures = {
                executor.submit(_parse_file_worker, str(f)): str(f)
                for f in chunk
            }

            for future in as_completed(futures):
                filepath, records = future.result()
                filename = os.path.basename(filepath)
                files_done += 1
                records_parsed += len(records)
                pending_file_counts[filename] = len(records)
                pending_batch.extend(records)

            if pending_batch:
                inserted = _insert_batch(conn, pending_batch)
                records_loaded += inserted

                for fname, count in pending_file_counts.items():
                    full_path = filepath_lookup.get(fname, fname)
                    _record_file_ingested(conn, full_path, count)

                pending_batch.clear()
                pending_file_counts.clear()

            if (chunk_idx + 1) % 5 == 0 or chunk_idx == total_chunks - 1:
                elapsed = time.time() - start_time
                rate = files_done / elapsed if elapsed > 0 else 0
                remaining = (total_files - files_done) / rate if rate > 0 else 0
                print(
                    f"  {files_done:>7}/{total_files} files | "
                    f"{records_loaded:>9,} records loaded | "
                    f"{elapsed/60:.1f}m elapsed | "
                    f"~{remaining/60:.0f}m remaining",
                    flush=True
                )

    conn.close()

    elapsed_total = time.time() - start_time
    summary = {
        'files_processed':    files_done,
        'records_parsed':     records_parsed,
        'records_loaded':     records_loaded,
        'records_skipped':    records_parsed - records_loaded,
        'elapsed_seconds':    elapsed_total,
        'rate_files_per_sec': files_done / elapsed_total if elapsed_total > 0 else 0,
    }

    logger.info(
        "Ingestion complete: %d files, %d records loaded, %d duplicates skipped, "
        "%.1f seconds (%.1f files/sec)",
        summary['files_processed'], summary['records_loaded'],
        summary['records_skipped'], summary['elapsed_seconds'],
        summary['rate_files_per_sec']
    )

    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Ingest NSW Valuer General DAT files into DuckDB"
    )
    parser.add_argument('--source', required=True)
    parser.add_argument('--db', required=True)
    parser.add_argument('--incremental', action='store_true', default=True)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE)
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S'
    )

    incremental = not args.full

    print(f"\nPMR Ingestion Pipeline")
    print(f"  Source : {args.source}")
    print(f"  DB     : {args.db}")
    print(f"  Mode   : {'Incremental' if incremental else 'Full reload'}")
    print(f"  Workers: {args.workers or os.cpu_count()} CPU threads")
    print()

    summary = run_ingestion(
        source_dir=args.source,
        db_path=args.db,
        incremental=incremental,
        max_workers=args.workers,
        batch_size=args.batch_size,
    )

    if summary:
        print(f"\n{'─'*50}")
        print(f"  Files processed : {summary['files_processed']:,}")
        print(f"  Records parsed  : {summary.get('records_parsed', 0):,}")
        print(f"  Records loaded  : {summary['records_loaded']:,}")
        print(f"  Duplicates skip : {summary['records_skipped']:,}")
        print(f"  Time elapsed    : {summary['elapsed_seconds']:.1f}s")
        print(f"  Rate            : {summary['rate_files_per_sec']:.1f} files/sec")
        print(f"{'─'*50}\n")


if __name__ == '__main__':
    main()