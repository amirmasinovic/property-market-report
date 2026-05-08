"""
db_init.py
Database initialiser for the Property Market Reporting platform.
Creates the DuckDB database and loads the governed suburb universe.
Run this once to set up the database, and again if you need to reset it.
"""

import duckdb
import pandas as pd
from pathlib import Path
import yaml
import logging
from datetime import datetime

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).resolve().parents[2]
CONFIG_DIR  = REPO_ROOT / "config"
DB_PATH     = Path("C:/PMR/data/curated/property_market.duckdb")
LOG_DIR     = Path("C:/PMR/data/ops")

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "db_init.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def load_config():
    """Load regions and thresholds config files."""
    with open(CONFIG_DIR / "regions.yml") as f:
        regions_cfg = yaml.safe_load(f)
    with open(CONFIG_DIR / "thresholds.yml") as f:
        thresholds_cfg = yaml.safe_load(f)
    return regions_cfg, thresholds_cfg


def create_schema(con):
    """Create all governed tables in DuckDB."""
    log.info("Creating database schema...")

    con.execute("""
        CREATE TABLE IF NOT EXISTS regions (
            region_id      VARCHAR PRIMARY KEY,
            region_name    VARCHAR NOT NULL,
            display_name   VARCHAR NOT NULL,
            active         BOOLEAN NOT NULL DEFAULT true,
            created_at     TIMESTAMP DEFAULT current_timestamp
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS suburbs (
            suburb_id      VARCHAR PRIMARY KEY,
            suburb_name    VARCHAR NOT NULL,
            postcode       INTEGER NOT NULL,
            region_id      VARCHAR NOT NULL REFERENCES regions(region_id),
            active         BOOLEAN NOT NULL DEFAULT true,
            created_at     TIMESTAMP DEFAULT current_timestamp
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS source_runs (
            run_id                  VARCHAR PRIMARY KEY,
            source_file             VARCHAR NOT NULL,
            schema_family           VARCHAR NOT NULL,
            file_year               INTEGER NOT NULL,
            started_at              TIMESTAMP NOT NULL,
            completed_at            TIMESTAMP,
            status                  VARCHAR,
            row_count_raw           INTEGER DEFAULT 0,
            row_count_loaded        INTEGER DEFAULT 0,
            row_count_quarantined   INTEGER DEFAULT 0,
            checksum                VARCHAR,
            parser_version          VARCHAR,
            error_summary           VARCHAR
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            property_id             VARCHAR PRIMARY KEY,
            canonical_address_key   VARCHAR NOT NULL,
            unit_number             VARCHAR,
            street_number           VARCHAR,
            street_name             VARCHAR NOT NULL,
            suburb_id               VARCHAR NOT NULL REFERENCES suburbs(suburb_id),
            postcode                VARCHAR NOT NULL,
            property_category       VARCHAR,
            zone_code               VARCHAR,
            property_description    VARCHAR,
            is_strata               BOOLEAN DEFAULT false,
            land_size_sqm           DOUBLE,
            first_seen_date         DATE,
            created_at              TIMESTAMP DEFAULT current_timestamp
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS property_lot_references (
            reference_id        VARCHAR PRIMARY KEY,
            property_id         VARCHAR NOT NULL REFERENCES properties(property_id),
            lot_number          VARCHAR,
            plan_number         VARCHAR,
            plan_type           VARCHAR,
            raw_reference_text  VARCHAR NOT NULL,
            source_run_id       VARCHAR NOT NULL REFERENCES source_runs(run_id),
            created_at          TIMESTAMP DEFAULT current_timestamp
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id              VARCHAR PRIMARY KEY,
            property_id                 VARCHAR NOT NULL REFERENCES properties(property_id),
            contract_date               DATE NOT NULL,
            settlement_date             DATE,
            sale_price                  BIGINT NOT NULL,
            is_residential              BOOLEAN DEFAULT false,
            is_vacant_land              BOOLEAN DEFAULT false,
            is_commercial               BOOLEAN DEFAULT false,
            is_rural                    BOOLEAN DEFAULT false,
            land_size_sqm               DOUBLE,
            transaction_classification  VARCHAR NOT NULL,
            source_authority_rank       INTEGER DEFAULT 1,
            source_run_id               VARCHAR NOT NULL REFERENCES source_runs(run_id),
            created_at                  TIMESTAMP DEFAULT current_timestamp
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS data_quality_issues (
            issue_id        VARCHAR PRIMARY KEY,
            source_run_id   VARCHAR NOT NULL REFERENCES source_runs(run_id),
            source_file     VARCHAR NOT NULL,
            record_type     VARCHAR,
            raw_line        VARCHAR,
            reason_code     VARCHAR NOT NULL,
            reason_detail   VARCHAR,
            detected_at     TIMESTAMP DEFAULT current_timestamp
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS suburb_weekly_metrics (
            metric_id               VARCHAR PRIMARY KEY,
            suburb_id               VARCHAR NOT NULL REFERENCES suburbs(suburb_id),
            week_start              DATE NOT NULL,
            confirmed_sales_count   INTEGER NOT NULL DEFAULT 0,
            avg_sale_price          DOUBLE,
            active_median_price     DOUBLE,
            active_window_label     VARCHAR,
            active_window_weeks     INTEGER,
            confidence_status       VARCHAR,
            land_sales_count        INTEGER DEFAULT 0,
            median_price_per_land_sqm DOUBLE,
            created_at              TIMESTAMP DEFAULT current_timestamp,
            UNIQUE (suburb_id, week_start)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS suburb_annual_chart_series (
            series_id               VARCHAR PRIMARY KEY,
            suburb_id               VARCHAR NOT NULL REFERENCES suburbs(suburb_id),
            region_id               VARCHAR NOT NULL REFERENCES regions(region_id),
            chart_year              INTEGER NOT NULL,
            annual_sales_count      INTEGER NOT NULL DEFAULT 0,
            annual_median_price     DOUBLE,
            annual_regional_median  DOUBLE,
            meets_threshold         BOOLEAN DEFAULT false,
            gap_year                BOOLEAN DEFAULT false,
            created_at              TIMESTAMP DEFAULT current_timestamp,
            UNIQUE (suburb_id, chart_year)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS suburb_trend_metrics (
            trend_id                VARCHAR PRIMARY KEY,
            suburb_id               VARCHAR NOT NULL REFERENCES suburbs(suburb_id),
            week_start              DATE NOT NULL,
            window_12w_count        INTEGER DEFAULT 0,
            median_12w              DOUBLE,
            window_26w_count        INTEGER DEFAULT 0,
            median_26w              DOUBLE,
            window_52w_count        INTEGER DEFAULT 0,
            median_52w              DOUBLE,
            window_104w_count       INTEGER DEFAULT 0,
            median_104w             DOUBLE,
            window_260w_count       INTEGER DEFAULT 0,
            median_260w             DOUBLE,
            active_window_label     VARCHAR,
            active_median           DOUBLE,
            created_at              TIMESTAMP DEFAULT current_timestamp,
            UNIQUE (suburb_id, week_start)
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS load_file_register (
            register_id     VARCHAR PRIMARY KEY,
            source_file     VARCHAR NOT NULL UNIQUE,
            schema_family   VARCHAR NOT NULL,
            file_year       INTEGER NOT NULL,
            loaded_at       TIMESTAMP NOT NULL,
            row_count       INTEGER DEFAULT 0,
            load_status     VARCHAR NOT NULL
        )
    """)

    log.info("Schema created successfully.")


def seed_regions(con, regions_cfg):
    """Load regions from config into the database."""
    log.info("Seeding regions...")
    count = 0
    for r in regions_cfg["regions"]:
        existing = con.execute(
            "SELECT region_id FROM regions WHERE region_id = ?",
            [r["region_id"]]
        ).fetchone()
        if not existing:
            con.execute(
                "INSERT INTO regions (region_id, region_name, display_name, active) VALUES (?, ?, ?, ?)",
                [r["region_id"], r["region_name"], r["display_name"], r["active"]]
            )
            count += 1
    log.info(f"Seeded {count} regions.")


def seed_suburbs(con, regions_cfg):
    """Load suburbs from CSV into the database."""
    log.info("Seeding suburbs...")

    # Build region name to region_id lookup
    region_lookup = {r["region_name"]: r["region_id"] for r in regions_cfg["regions"]}

    suburbs_csv = CONFIG_DIR / "suburbs.csv"
    df = pd.read_csv(suburbs_csv)

    count = 0
    skipped = 0
    for _, row in df.iterrows():
        region_name = row["region_name"].strip()
        suburb_name = row["suburb"].strip()
        postcode    = int(row["postcode"])

        if region_name not in region_lookup:
            log.warning(f"Unknown region '{region_name}' for suburb '{suburb_name}' - skipping.")
            skipped += 1
            continue

        region_id = region_lookup[region_name]
        suburb_id = f"{region_id}_{suburb_name.lower().replace(' ', '_').replace('/', '_')}"

        existing = con.execute(
            "SELECT suburb_id FROM suburbs WHERE suburb_id = ?",
            [suburb_id]
        ).fetchone()

        if not existing:
            con.execute(
                "INSERT INTO suburbs (suburb_id, suburb_name, postcode, region_id, active) VALUES (?, ?, ?, ?, ?)",
                [suburb_id, suburb_name, postcode, region_id, True]
            )
            count += 1

    log.info(f"Seeded {count} suburbs. Skipped {skipped}.")


def verify(con):
    """Run basic verification checks after seeding."""
    log.info("Running verification checks...")

    region_count = con.execute("SELECT COUNT(*) FROM regions").fetchone()[0]
    suburb_count = con.execute("SELECT COUNT(*) FROM suburbs").fetchone()[0]

    log.info(f"Regions in database:  {region_count}")
    log.info(f"Suburbs in database:  {suburb_count}")

    # Show suburb counts per region
    results = con.execute("""
        SELECT r.region_name, COUNT(s.suburb_id) as suburb_count
        FROM regions r
        LEFT JOIN suburbs s ON r.region_id = s.region_id
        GROUP BY r.region_name
        ORDER BY r.region_name
    """).fetchall()

    for row in results:
        log.info(f"  {row[0]}: {row[1]} suburbs")

    expected_total = 40 + 26 + 26  # Eastern + Central Coast + Inner West
    if suburb_count == expected_total:
        log.info(f"Suburb count verified: {suburb_count} matches expected {expected_total}.")
    else:
        log.warning(f"Suburb count mismatch: got {suburb_count}, expected {expected_total}.")


def main():
    log.info("=" * 60)
    log.info("Property Market Reporting - Database Initialiser")
    log.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    # Ensure database directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    log.info(f"Database path: {DB_PATH}")

    con = duckdb.connect(str(DB_PATH))

    try:
        regions_cfg, thresholds_cfg = load_config()
        create_schema(con)
        seed_regions(con, regions_cfg)
        seed_suburbs(con, regions_cfg)
        verify(con)
        log.info("Database initialisation complete.")
    except Exception as e:
        log.error(f"Database initialisation failed: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()