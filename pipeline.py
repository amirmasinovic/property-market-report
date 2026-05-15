"""
PMR Pipeline Runner
====================
Version: v1.0

The single command you run each week to update all reports.

What it does, in order:
  1. Ingest new DAT files into DuckDB (incremental by default)
  2. Rebuild the analytics tables (suburb metrics, price performance)
  3. Export JSON report files
  4. Push JSON files to GitHub
  5. Export Excel diagnostic workbook

Usage — weekly update (only processes new files):
  python pipeline.py

Usage — initial historical load (first time, all files):
  python pipeline.py --full

Usage — analytics + export only (skip ingestion, re-export existing data):
  python pipeline.py --no-ingest

Usage — check what would be processed without doing anything:
  python pipeline.py --dry-run
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# All modules must be in the same directory as this script
import config
from dat_ingestion import run_ingestion
from analytics import run_analytics
from json_exporter import export_all
from excel_exporter import export_excel

# ─────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────

def setup_logging(log_dir: str = None) -> None:
    """Configure logging to both console and a daily log file."""
    handlers = [logging.StreamHandler(sys.stdout)]

    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_file = os.path.join(
            log_dir,
            f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# GitHub push
# ─────────────────────────────────────────────

def push_to_github(repo_dir: str, commit_message: str) -> bool:
    """
    Stage the reports folder, commit, and push to GitHub.

    Requires:
      - git installed and on PATH
      - The repo_dir to be a valid git repository
      - The remote 'origin' to be configured
      - Either SSH keys or credential manager set up for authentication

    Returns True on success, False on failure.
    """
    try:
        def run_git(args: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git"] + args,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )

        # Check if there is anything to commit
        status = subprocess.run(
            ["git", "status", "--porcelain", "reports/"],
            cwd=repo_dir, capture_output=True, text=True
        )
        if not status.stdout.strip():
            logger.info("GitHub: no changes in reports/ — nothing to push")
            return True

        logger.info("GitHub: staging reports/...")
        run_git(["add", "reports/"])

        logger.info("GitHub: committing...")
        run_git(["commit", "-m", commit_message])

        logger.info("GitHub: pushing to origin...")
        run_git(["push", "origin", "main"])

        logger.info("GitHub: push successful")
        return True

    except subprocess.CalledProcessError as exc:
        logger.error("GitHub push failed: %s", exc.stderr.strip())
        logger.error("Fix the git issue and push manually from: %s", repo_dir)
        logger.error("Command: git add reports/ && git commit -m '%s' && git push", commit_message)
        return False
    except FileNotFoundError:
        logger.error("git not found on PATH. Install git for Windows and retry.")
        return False


# ─────────────────────────────────────────────
# Dry run — show what would be processed
# ─────────────────────────────────────────────

def dry_run() -> None:
    """Show what the pipeline would process without actually running."""
    from dat_ingestion import find_dat_files, setup_database, get_already_ingested

    print("\n── DRY RUN ──────────────────────────────────────────────")
    print(f"  Source dir : {config.SOURCE_DIR}")
    print(f"  Database   : {config.DB_PATH}")
    print(f"  JSON output: {config.JSON_OUTPUT_DIR}")
    print(f"  GitHub repo: {config.GITHUB_REPO_DIR}")
    print(f"  Mode       : {'Incremental' if config.INCREMENTAL else 'Full reload'}")
    print()

    all_files = find_dat_files(config.SOURCE_DIR)
    print(f"  Total DAT files found: {len(all_files):,}")

    if Path(config.DB_PATH).exists():
        conn = setup_database(config.DB_PATH)
        already_done = get_already_ingested(conn)
        conn.close()
        pending = [f for f in all_files
                   if os.path.basename(str(f)) not in already_done]
        print(f"  Already ingested    : {len(already_done):,}")
        print(f"  Pending (new files) : {len(pending):,}")
    else:
        print(f"  Database not found — all {len(all_files):,} files would be ingested")

    print("─────────────────────────────────────────────────────────\n")


# ─────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────

def run_pipeline(
    do_ingest: bool = True,
    full_reload: bool = False,
    do_push: bool = None,
) -> bool:
    """
    Execute the full pipeline.

    Args:
        do_ingest:    Whether to run the DAT ingestion stage
        full_reload:  If True, reprocess all files (ignores incremental flag)
        do_push:      Whether to push to GitHub (None = use config.AUTO_PUSH_TO_GITHUB)

    Returns:
        True if pipeline completed without critical errors.
    """
    if do_push is None:
        do_push = config.AUTO_PUSH_TO_GITHUB

    pipeline_start = time.time()
    run_date = datetime.now()
    success = True

    print()
    print("═" * 60)
    print("  PMR Pipeline")
    print(f"  {run_date.strftime('%A %d %B %Y  %H:%M')}")
    print("═" * 60)

    # ── Stage 1: Ingestion ────────────────────────────────────────
    if do_ingest:
        print("\n[1/4] Ingesting DAT files...")
        incremental = not full_reload and config.INCREMENTAL
        try:
            ingest_summary = run_ingestion(
                source_dir=config.SOURCE_DIR,
                db_path=config.DB_PATH,
                incremental=incremental,
                max_workers=config.WORKER_PROCESSES,
            )
            files  = ingest_summary.get("files_processed", 0)
            loaded = ingest_summary.get("records_loaded", 0)
            secs   = ingest_summary.get("elapsed_seconds", 0)
            print(f"     {files:,} files processed | {loaded:,} records loaded | {secs:.0f}s")
        except Exception as exc:
            logger.error("Ingestion failed: %s", exc, exc_info=True)
            print(f"     FAILED: {exc}")
            success = False
            # Continue — analytics can still run on existing data
    else:
        print("\n[1/4] Ingestion skipped (--no-ingest)")

    # ── Stage 2: Analytics ────────────────────────────────────────
    print("\n[2/4] Rebuilding analytics...")
    try:
        analytics_summary = run_analytics(db_path=config.DB_PATH)
        mapped  = analytics_summary.get("mapped_sales_rows", 0)
        annual  = analytics_summary.get("annual_metrics_rows", 0)
        secs    = analytics_summary.get("elapsed_seconds", 0)
        print(f"     {mapped:,} in-scope sales | {annual:,} suburb-year rows | {secs:.1f}s")
    except Exception as exc:
        logger.error("Analytics failed: %s", exc, exc_info=True)
        print(f"     FAILED: {exc}")
        success = False

    # ── Stage 3: JSON export ──────────────────────────────────────
    print("\n[3/4] Exporting JSON report files...")
    try:
        export_summary = export_all(
            db_path=config.DB_PATH,
            output_dir=config.JSON_OUTPUT_DIR,
        )
        total_files = export_summary.get("total_files", 0)
        secs        = export_summary.get("elapsed_seconds", 0)
        print(f"     {total_files} files written to {config.JSON_OUTPUT_DIR} | {secs:.1f}s")
    except Exception as exc:
        logger.error("JSON export failed: %s", exc, exc_info=True)
        print(f"     FAILED: {exc}")
        success = False
        do_push = False  # Don't push broken JSON

    # ── Stage 3b: GitHub push ─────────────────────────────────────
    if do_push:
        commit_msg = (
            f"{config.GITHUB_COMMIT_PREFIX} "
            f"{run_date.strftime('%Y-%m-%d %H:%M')}"
        )
        pushed = push_to_github(config.GITHUB_REPO_DIR, commit_msg)
        if not pushed:
            print(f"     GitHub push failed — push manually when ready")
    else:
        print("\n      GitHub push skipped")

    # ── Stage 4: Excel export ─────────────────────────────────────
    print("\n[4/4] Exporting Excel diagnostic workbook...")
    try:
        excel_summary = export_excel(
            db_path=config.DB_PATH,
            output_path=config.EXCEL_OUTPUT_PATH,
        )
        if "error" in excel_summary:
            print(f"     Skipped: {excel_summary['error']}")
            print(f"     Install with: pip install openpyxl")
        else:
            secs = excel_summary.get("elapsed_seconds", 0)
            print(f"     Saved to {config.EXCEL_OUTPUT_PATH} | {secs:.1f}s")
    except Exception as exc:
        logger.error("Excel export failed: %s", exc, exc_info=True)
        print(f"     FAILED: {exc}")
        # Non-critical — don't mark pipeline as failed

    # ── Summary ───────────────────────────────────────────────────
    total_elapsed = time.time() - pipeline_start
    status = "COMPLETE" if success else "COMPLETED WITH ERRORS"

    print()
    print("─" * 60)
    print(f"  {status}  —  {total_elapsed:.0f}s total")
    print("─" * 60)
    print()

    return success


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PMR weekly pipeline — ingests DAT files, builds analytics, exports reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py              # weekly incremental update
  python pipeline.py --full       # full reload (first run)
  python pipeline.py --no-ingest  # re-export existing data only
  python pipeline.py --dry-run    # preview without running
  python pipeline.py --no-push    # run without GitHub push
        """
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Full reload: reprocess all DAT files (use for initial load)"
    )
    parser.add_argument(
        "--no-ingest", action="store_true",
        help="Skip ingestion stage; rebuild analytics and re-export only"
    )
    parser.add_argument(
        "--no-push", action="store_true",
        help="Skip GitHub push"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be processed without running"
    )
    parser.add_argument(
        "--log-dir", default=None,
        help="Directory to save log files (default: no log file)"
    )
    args = parser.parse_args()

    # Logging
    log_dir = args.log_dir or os.path.join(os.path.dirname(config.DB_PATH), "logs")
    setup_logging(log_dir)

    # Dry run
    if args.dry_run:
        dry_run()
        return

    # Validate config before running
    errors = []
    if not os.path.isdir(config.SOURCE_DIR):
        errors.append(f"SOURCE_DIR not found: {config.SOURCE_DIR}")
    if not os.path.isdir(os.path.dirname(config.JSON_OUTPUT_DIR) or "."):
        errors.append(f"JSON_OUTPUT_DIR parent not found: {config.JSON_OUTPUT_DIR}")

    if errors and not args.no_ingest:
        print("\nConfiguration errors:")
        for e in errors:
            print(f"  ✗ {e}")
        print("\nEdit config.py to fix these paths before running.\n")
        sys.exit(1)

    # Run
    success = run_pipeline(
        do_ingest=not args.no_ingest,
        full_reload=args.full,
        do_push=not args.no_push,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
