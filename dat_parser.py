"""
NSW Valuer General DAT File Parser
===================================
Version: v1.0

Parses B records from NSW Valuer General property sales DAT files.

File format: semicolon-delimited, multiple record types per file.
We only extract B records (property sale records).

B record field mapping (0-indexed, semicolon-delimited):
  [0]  record_type       = 'B'
  [1]  district_code
  [2]  property_id
  [3]  sequence_no
  [4]  file_datetime     (YYYYMMDD HH:MM)
  [5]  unit_no           (empty = freehold, value = unit/strata)
  [6]  strata_lot_no     (empty = freehold, value = strata lot number)
  [7]  street_no
  [8]  street_name
  [9]  suburb
  [10] postcode
  [11] land_size         (numeric, empty if unknown)
  [12] land_size_unit    (M = sqm, H = hectares, empty if no land_size)
  [13] contract_date     (YYYYMMDD)
  [14] settlement_date   (YYYYMMDD)
  [15] sale_price
  [16] zone_code
  [17] property_category (R=Residential, V=Vacant Land, 3 or other = Commercial/Other)
  [18] property_description
  [19] strata_plan_no    (populated for strata, empty for freehold)
  [20] council_area_code (populated for freehold, empty for strata)
  [21] (reserved/empty)
  [22] strata_interest_pct
  [23] dealing_number

Property type classification:
  - unit_no (field 5) OR strata_lot_no (field 6) is non-empty → 'UNIT'
  - property_category = 'V' OR description = 'VACANT LAND'    → 'VACANT LAND'
  - property_category = 'R' and no strata indicators          → 'HOUSE'
  - otherwise                                                  → 'OTHER'

Land size normalisation:
  - Unit M (sqm): use as-is
  - Unit H (hectares): multiply by 10000 to convert to sqm

Date parsing:
  - YYYYMMDD format → Python date object
  - Empty or invalid → None

Sale price:
  - Stored as integer in the file
  - Zero or missing → excluded from analytics (not a valid sale)
"""

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Field indices (B record)
# ─────────────────────────────────────────────
F_RECORD_TYPE     = 0
F_DISTRICT_CODE   = 1
F_PROPERTY_ID     = 2
F_SEQUENCE_NO     = 3
F_FILE_DATETIME   = 4
F_UNIT_NO         = 5
F_STRATA_LOT_NO   = 6
F_STREET_NO       = 7
F_STREET_NAME     = 8
F_SUBURB          = 9
F_POSTCODE        = 10
F_LAND_SIZE       = 11
F_LAND_SIZE_UNIT  = 12
F_CONTRACT_DATE   = 13
F_SETTLEMENT_DATE = 14
F_SALE_PRICE      = 15
F_ZONE_CODE       = 16
F_PROPERTY_CAT    = 17
F_PROPERTY_DESC   = 18
F_STRATA_PLAN_NO  = 19
F_COUNCIL_AREA    = 20
F_STRATA_INTEREST = 22
F_DEALING_NUMBER  = 23

MIN_EXPECTED_FIELDS = 24


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _parse_date(raw: str) -> Optional[date]:
    """Parse YYYYMMDD string to date. Returns None if empty or invalid."""
    raw = raw.strip()
    if not raw or len(raw) < 8:
        return None
    try:
        return datetime.strptime(raw[:8], "%Y%m%d").date()
    except ValueError:
        logger.debug("Could not parse date: %r", raw)
        return None


def _parse_land_size_sqm(size_raw: str, unit_raw: str) -> Optional[float]:
    """
    Parse land size and normalise to square metres.
    Returns None if size is missing or zero.
    """
    size_raw = size_raw.strip()
    unit_raw = unit_raw.strip().upper()
    if not size_raw:
        return None
    try:
        size = float(size_raw)
    except ValueError:
        return None
    if size <= 0:
        return None
    if unit_raw == 'H':
        return size * 10000.0   # hectares → sqm
    return size                  # already sqm


def _classify_property_type(unit_no: str, strata_lot_no: str,
                              category: str, description: str) -> str:
    """
    Classify a property into one of four types:
      UNIT, HOUSE, VACANT LAND, OTHER
    """
    is_strata = bool(unit_no.strip() or strata_lot_no.strip())
    if is_strata:
        return 'UNIT'
    cat = category.strip().upper()
    desc = description.strip().upper()
    if cat == 'V' or desc == 'VACANT LAND':
        return 'VACANT LAND'
    if cat == 'R':
        return 'HOUSE'
    return 'OTHER'


def _parse_sale_price(raw: str) -> Optional[int]:
    """Parse sale price. Returns None if missing or zero."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        price = int(raw)
        return price if price > 0 else None
    except ValueError:
        return None


# ─────────────────────────────────────────────
# File name parser
# ─────────────────────────────────────────────

_FILENAME_RE = re.compile(
    r'^(\d{3})_SALES_DATA_NNME_(\d{2})(\d{2})(\d{4})\.DAT$',
    re.IGNORECASE
)

def parse_filename(filename: str) -> Optional[dict]:
    """
    Extract district_code and file_release_date from filename.
    Pattern: {DDD}_SALES_DATA_NNME_{DDMMYYYY}.DAT

    Returns dict with keys: district_code, file_release_date
    Returns None if filename doesn't match the pattern.

    NOTE: The file_release_date is the publication date, NOT the transaction date.
    Always use contract_date from the B record for reporting.
    """
    name = os.path.basename(filename)
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    district_code = m.group(1)
    day, month, year = int(m.group(2)), int(m.group(3)), int(m.group(4))
    try:
        file_release_date = date(year, month, day)
    except ValueError:
        return None
    return {
        'district_code': district_code,
        'file_release_date': file_release_date,
    }


def detect_file_era(filepath: str) -> str:
    """
    Detect which era a DAT file belongs to based on folder year.
    This is informational only — the parser logic is the same for all eras.

      'ANNUAL'      : 1990–2000 (one file per year, e.g. ARCHIVE_SALES_YYYY.DAT)
      'TRANSITIONAL': 2001 (mixed annual + weekly)
      'WEEKLY'      : 2002–present (weekly incremental files)

    Detection is based on the filename pattern:
      ARCHIVE_SALES_* → ANNUAL or TRANSITIONAL
      {DDD}_SALES_DATA_NNME_* → WEEKLY
    """
    name = os.path.basename(filepath).upper()
    if name.startswith('ARCHIVE_SALES_'):
        # Could be annual (1990-2000) or the 2001 archive component
        return 'ANNUAL'
    if _FILENAME_RE.match(os.path.basename(filepath)):
        return 'WEEKLY'
    return 'UNKNOWN'


# ─────────────────────────────────────────────
# Core record parser
# ─────────────────────────────────────────────

def parse_b_record(fields: list[str], source_file: str,
                   file_release_date: Optional[date] = None) -> Optional[dict]:
    """
    Parse a single B record (already split by semicolon) into a dict.

    Returns None if the record is malformed or lacks the minimum required data
    (suburb, postcode, and a valid contract_date are mandatory for a usable record).

    The returned dict contains:
      district_code, property_id, sequence_no,
      unit_no, strata_lot_no,
      street_no, street_name,
      suburb, postcode,
      land_size_sqm,
      contract_date, settlement_date,
      sale_price,
      zone_code, property_category, property_description,
      property_type,           ← derived classification
      strata_plan_no, council_area_code, dealing_number,
      source_file, file_release_date
    """
    if len(fields) < MIN_EXPECTED_FIELDS:
        logger.debug("B record has only %d fields (expected %d): %s",
                     len(fields), MIN_EXPECTED_FIELDS, source_file)
        return None

    # Mandatory fields
    suburb = fields[F_SUBURB].strip()
    postcode = fields[F_POSTCODE].strip()
    contract_date = _parse_date(fields[F_CONTRACT_DATE])

    if not suburb:
        logger.debug("Skipping B record: missing suburb in %s", source_file)
        return None
    if not postcode:
        logger.debug("Skipping B record: missing postcode in %s (suburb=%s)", source_file, suburb)
        return None
    if contract_date is None:
        logger.debug("Skipping B record: missing contract_date in %s (suburb=%s)", source_file, suburb)
        return None

    # Optional fields
    unit_no      = fields[F_UNIT_NO].strip()
    strata_lot   = fields[F_STRATA_LOT_NO].strip()
    street_no    = fields[F_STREET_NO].strip()
    street_name  = fields[F_STREET_NAME].strip()
    zone_code    = fields[F_ZONE_CODE].strip()
    category     = fields[F_PROPERTY_CAT].strip()
    description  = fields[F_PROPERTY_DESC].strip()
    strata_plan  = fields[F_STRATA_PLAN_NO].strip()
    council_area = fields[F_COUNCIL_AREA].strip() if len(fields) > F_COUNCIL_AREA else ''
    dealing_no   = fields[F_DEALING_NUMBER].strip() if len(fields) > F_DEALING_NUMBER else ''

    land_size_sqm    = _parse_land_size_sqm(fields[F_LAND_SIZE], fields[F_LAND_SIZE_UNIT])
    settlement_date  = _parse_date(fields[F_SETTLEMENT_DATE])
    sale_price       = _parse_sale_price(fields[F_SALE_PRICE])
    property_type    = _classify_property_type(unit_no, strata_lot, category, description)

    return {
        'district_code':       fields[F_DISTRICT_CODE].strip(),
        'property_id':         fields[F_PROPERTY_ID].strip(),
        'sequence_no':         fields[F_SEQUENCE_NO].strip(),
        'unit_no':             unit_no,
        'strata_lot_no':       strata_lot,
        'street_no':           street_no,
        'street_name':         street_name,
        'suburb':              suburb,
        'postcode':            postcode,
        'land_size_sqm':       land_size_sqm,
        'contract_date':       contract_date,
        'settlement_date':     settlement_date,
        'sale_price':          sale_price,
        'zone_code':           zone_code,
        'property_category':   category,
        'property_description': description,
        'property_type':       property_type,
        'strata_plan_no':      strata_plan,
        'council_area_code':   council_area,
        'dealing_number':      dealing_no,
        'source_file':         os.path.basename(source_file),
        'file_release_date':   file_release_date,
    }


# ─────────────────────────────────────────────
# File-level parser — the main entry point
# ─────────────────────────────────────────────

def parse_dat_file(filepath: str) -> Iterator[dict]:
    """
    Parse all B records from a single DAT file.
    Yields one dict per valid sale record.

    Handles:
      - All eras: annual (1990–2000), transitional (2001), weekly (2002+)
      - Encoding: tries UTF-8 first, falls back to latin-1
      - Empty files and files with only header/footer records
      - Malformed lines (skipped with a debug log)

    The caller is responsible for filtering records by suburb/postcode
    against the approved suburb universe.
    """
    filepath = str(filepath)

    # Extract metadata from filename (weekly files only — annual files use a different name pattern)
    file_meta = parse_filename(filepath)
    file_release_date = file_meta['file_release_date'] if file_meta else None

    records_yielded = 0
    lines_seen = 0
    b_lines = 0

    try:
        try:
            fh = open(filepath, encoding='utf-8', errors='strict')
            lines = fh.readlines()
            fh.close()
        except UnicodeDecodeError:
            fh = open(filepath, encoding='latin-1')
            lines = fh.readlines()
            fh.close()
    except OSError as exc:
        logger.error("Cannot open DAT file %s: %s", filepath, exc)
        return

    for line in lines:
        lines_seen += 1
        line = line.rstrip('\r\n')
        if not line:
            continue

        # Only process B records
        if not line.startswith('B;'):
            continue

        b_lines += 1
        fields = line.split(';')

        record = parse_b_record(fields, filepath, file_release_date)
        if record is not None:
            records_yielded += 1
            yield record

    logger.debug(
        "Parsed %s: %d lines, %d B-records, %d valid records yielded",
        os.path.basename(filepath), lines_seen, b_lines, records_yielded
    )


# ─────────────────────────────────────────────
# Directory scanner — processes all DAT files under a root folder
# ─────────────────────────────────────────────

def scan_dat_directory(root_dir: str) -> Iterator[dict]:
    """
    Recursively walk root_dir and yield all valid sale records
    from every DAT file found.

    Files are processed in filesystem order within each directory.
    Year folders (1990, 1991, … 2025) are traversed in sorted order
    so historical data loads chronologically.

    Each yielded dict includes 'source_file' and 'file_release_date'
    for auditability.
    """
    root = Path(root_dir)
    if not root.exists():
        logger.error("Root directory does not exist: %s", root_dir)
        return

    # Collect all DAT files, sorted by path so year folders load in order
    dat_files = sorted(root.rglob('*.DAT'))
    # Also handle lowercase extension
    dat_files_lower = sorted(root.rglob('*.dat'))
    # Merge and deduplicate
    all_files = sorted(set(dat_files) | set(dat_files_lower))

    logger.info("Found %d DAT files under %s", len(all_files), root_dir)

    for filepath in all_files:
        yield from parse_dat_file(str(filepath))


# ─────────────────────────────────────────────
# Quick inspection utility (not used in production pipeline)
# ─────────────────────────────────────────────

def inspect_file(filepath: str, max_records: int = 10) -> None:
    """
    Print the first N records from a DAT file in human-readable form.
    Useful for diagnosing a specific file without running the full pipeline.
    """
    print(f"\nInspecting: {filepath}")
    print(f"Filename meta: {parse_filename(filepath)}")
    print(f"Era: {detect_file_era(filepath)}")
    print("-" * 60)
    for i, record in enumerate(parse_dat_file(filepath)):
        if i >= max_records:
            break
        for key, val in record.items():
            print(f"  {key:<25} {val}")
        print()


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python dat_parser.py <path_to_dat_file> [max_records]")
        sys.exit(1)
    logging.basicConfig(level=logging.DEBUG)
    max_r = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    inspect_file(sys.argv[1], max_records=max_r)
