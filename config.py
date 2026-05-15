# PMR Pipeline Configuration
# ===========================
# Edit these paths before running the pipeline for the first time.
# This file is the only thing you need to change between machines.

import os

SOURCE_DIR        = r"C:\PMR\Source Data from Raw"
DB_PATH           = r"C:\PMR\pmr.duckdb"
JSON_OUTPUT_DIR   = r"C:\PMR\repo\data\published\reports"
GITHUB_REPO_DIR   = r"C:\PMR\repo"
EXCEL_OUTPUT_PATH = r"C:\PMR\pmr_diagnostic.xlsx"

INCREMENTAL            = True
WORKER_PROCESSES       = None
AUTO_PUSH_TO_GITHUB    = True
GITHUB_COMMIT_PREFIX   = "PMR weekly update"