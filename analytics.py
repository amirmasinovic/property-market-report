"""
Analytics Schema & Transform Module
=====================================
Version: v2.0

Builds the analytical layer on top of raw_sales.

Pipeline stages:
  1. load_suburb_reference()            → suburb_ref from suburb_universe.py
  2. build_mapped_sales()               → all property types, $/sqm computed
  3. build_annual_metrics()             → annual per suburb × property_type + ALL
  4. build_annual_regional_metrics()    → annual per region × property_type + ALL
  5. build_rolling_metrics()            → adaptive window tiles per suburb × type
  6. build_regional_rolling_metrics()   → adaptive window tiles per region × type
  7. build_price_performance()          → periods per suburb × type (nominal + pct)
  8. build_regional_price_performance() → periods per region × type
  9. build_available_types()            → enabled/disabled states + vacant land zones

Key changes from v1.0:
  - Residential-only filter REMOVED — all four property types (HOUSE, UNIT,
    VACANT LAND, OTHER) plus 'ALL' aggregate
  - property_type column used directly from raw_sales (parser-assigned)
  - Adaptive rolling window: expand until ≥100 sales, cap 52 weeks.
    Caution 20–99 at 52w. Suppress <20.
  - price_per_sqm computed at map stage, outlier-bounds from config
  - Vacant Land: chart + performance uses $/sqm not median price
  - Price performance: nominal ($) change + pct (%) per period
  - Zone breakdown table for vacant land zone filter toggle
  - All tables keyed on (suburb_id, property_type) for clean UI switching
"""

import logging
from typing import Optional
import duckdb

from suburb_universe import get_all_suburbs

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────

MIN_CHART_SALES    = 100   # Annual sales required for a year to appear in chart
MIN_ROLLING_SALES  = 100   # Target transactions for adaptive rolling window
CAUTION_FLOOR      = 20    # Min transactions before suppression at 52w cap
MIN_PERF_SALES     = 100   # Min transactions for price performance period
MIN_SQM_RECORDS    = 3     # Min records needed to publish $/sqm metric

WINDOW_STEPS_WEEKS = [4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52]

ALL_TYPES = ['All', 'HOUSE', 'UNIT', 'VACANT LAND', 'OTHER']


# ─────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────

ANALYTICS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS suburb_ref (
    suburb_id    INTEGER PRIMARY KEY,
    region       TEXT NOT NULL,
    suburb       TEXT NOT NULL,
    suburb_upper TEXT NOT NULL,
    postcode     INTEGER NOT NULL,
    postcode_str TEXT NOT NULL,
    UNIQUE (suburb_upper, postcode_str)
);

CREATE TABLE IF NOT EXISTS mapped_sales (
    id            INTEGER,
    suburb_id     INTEGER NOT NULL,
    region        TEXT NOT NULL,
    suburb        TEXT NOT NULL,
    postcode      INTEGER NOT NULL,
    property_type TEXT NOT NULL,
    zone_code     TEXT,
    contract_date DATE NOT NULL,
    contract_year INTEGER NOT NULL,
    sale_price    INTEGER NOT NULL,
    land_size_sqm DOUBLE,
    price_per_sqm DOUBLE,
    source_file   TEXT,
    FOREIGN KEY (suburb_id) REFERENCES suburb_ref(suburb_id)
);

CREATE TABLE IF NOT EXISTS annual_metrics (
    suburb_id        INTEGER NOT NULL,
    region           TEXT NOT NULL,
    suburb           TEXT NOT NULL,
    postcode         INTEGER NOT NULL,
    property_type    TEXT NOT NULL,
    contract_year    INTEGER NOT NULL,
    sales_count      INTEGER NOT NULL,
    median_price     DOUBLE,
    avg_price        DOUBLE,
    median_price_sqm DOUBLE,
    avg_price_sqm    DOUBLE,
    sqm_record_count INTEGER,
    PRIMARY KEY (suburb_id, property_type, contract_year)
);

CREATE TABLE IF NOT EXISTS annual_regional_metrics (
    region           TEXT NOT NULL,
    property_type    TEXT NOT NULL,
    contract_year    INTEGER NOT NULL,
    sales_count      INTEGER NOT NULL,
    median_price     DOUBLE,
    avg_price        DOUBLE,
    median_price_sqm DOUBLE,
    avg_price_sqm    DOUBLE,
    sqm_record_count INTEGER,
    suburb_count     INTEGER,
    PRIMARY KEY (region, property_type, contract_year)
);

-- Tile data: adaptive rolling window per suburb x property_type
CREATE TABLE IF NOT EXISTS rolling_metrics (
    suburb_id        INTEGER NOT NULL,
    region           TEXT NOT NULL,
    suburb           TEXT NOT NULL,
    postcode         INTEGER NOT NULL,
    property_type    TEXT NOT NULL,
    window_weeks     INTEGER,
    sales_count      INTEGER,
    is_caution       BOOLEAN NOT NULL DEFAULT FALSE,
    is_suppressed    BOOLEAN NOT NULL DEFAULT FALSE,
    median_price     DOUBLE,
    avg_price        DOUBLE,
    median_price_sqm DOUBLE,
    avg_price_sqm    DOUBLE,
    has_sqm_data     BOOLEAN NOT NULL DEFAULT FALSE,
    data_as_of       DATE,
    PRIMARY KEY (suburb_id, property_type)
);

CREATE TABLE IF NOT EXISTS regional_rolling_metrics (
    region           TEXT NOT NULL,
    property_type    TEXT NOT NULL,
    window_weeks     INTEGER,
    sales_count      INTEGER,
    is_caution       BOOLEAN NOT NULL DEFAULT FALSE,
    is_suppressed    BOOLEAN NOT NULL DEFAULT FALSE,
    median_price     DOUBLE,
    avg_price        DOUBLE,
    median_price_sqm DOUBLE,
    avg_price_sqm    DOUBLE,
    has_sqm_data     BOOLEAN NOT NULL DEFAULT FALSE,
    data_as_of       DATE,
    PRIMARY KEY (region, property_type)
);

-- Price performance per suburb x property_type.
-- NULL = threshold not met -> display N/A in UI.
-- VACANT LAND: nominal/pct changes use sqm values not raw price.
CREATE TABLE IF NOT EXISTS price_performance (
    suburb_id    INTEGER NOT NULL,
    region       TEXT NOT NULL,
    suburb       TEXT NOT NULL,
    postcode     INTEGER NOT NULL,
    property_type TEXT NOT NULL,
    sales_3m     INTEGER,  median_3m  DOUBLE,
    sales_12m    INTEGER,  median_12m DOUBLE,
    sales_3y     INTEGER,  median_3y  DOUBLE,
    sales_5y     INTEGER,  median_5y  DOUBLE,
    sales_10y    INTEGER,  median_10y DOUBLE,
    sales_20y    INTEGER,  median_20y DOUBLE,
    nominal_3m   DOUBLE,   pct_3m  DOUBLE,
    nominal_12m  DOUBLE,   pct_12m DOUBLE,
    nominal_3y   DOUBLE,   pct_3y  DOUBLE,
    nominal_5y   DOUBLE,   pct_5y  DOUBLE,
    nominal_10y  DOUBLE,   pct_10y DOUBLE,
    nominal_20y  DOUBLE,   pct_20y DOUBLE,
    as_of_date   DATE NOT NULL,
    PRIMARY KEY (suburb_id, property_type)
);

CREATE TABLE IF NOT EXISTS regional_price_performance (
    region        TEXT NOT NULL,
    property_type TEXT NOT NULL,
    sales_3m      INTEGER,  median_3m  DOUBLE,
    sales_12m     INTEGER,  median_12m DOUBLE,
    sales_3y      INTEGER,  median_3y  DOUBLE,
    sales_5y      INTEGER,  median_5y  DOUBLE,
    sales_10y     INTEGER,  median_10y DOUBLE,
    sales_20y     INTEGER,  median_20y DOUBLE,
    nominal_3m    DOUBLE,   pct_3m  DOUBLE,
    nominal_12m   DOUBLE,   pct_12m DOUBLE,
    nominal_3y    DOUBLE,   pct_3y  DOUBLE,
    nominal_5y    DOUBLE,   pct_5y  DOUBLE,
    nominal_10y   DOUBLE,   pct_10y DOUBLE,
    nominal_20y   DOUBLE,   pct_20y DOUBLE,
    as_of_date    DATE NOT NULL,
    PRIMARY KEY (region, property_type)
);

-- Which types have data per suburb/region (drives button enable/disable)
CREATE TABLE IF NOT EXISTS available_types (
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    property_type TEXT NOT NULL,
    is_available  BOOLEAN NOT NULL,
    sales_count   INTEGER,
    PRIMARY KEY (entity_type, entity_id, property_type)
);

-- Distinct zones for vacant land per suburb (feeds zone toggle)
CREATE TABLE IF NOT EXISTS vacant_land_zones (
    suburb_id   INTEGER NOT NULL,
    region      TEXT NOT NULL,
    suburb      TEXT NOT NULL,
    zone_code   TEXT NOT NULL,
    sales_count INTEGER NOT NULL,
    PRIMARY KEY (suburb_id, zone_code)
);
"""


# ─────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────

def _outlier_thresholds():
    try:
        import config
        return (getattr(config, 'LAND_SQM_MIN',      50),
                getattr(config, 'LAND_SQM_MAX',   50000),
                getattr(config, 'PRICE_SQM_MIN',    100),
                getattr(config, 'PRICE_SQM_MAX', 100000))
    except ImportError:
        return 50, 50000, 100, 100000


def _thresh(value, count, minimum):
    """Return value if count >= minimum, else None."""
    return value if (count is not None and count >= minimum) else None


def _nominal(a, b):
    if a is None or b is None:
        return None
    return round(a - b)


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100, 2)


def _run_rolling_query(conn, ref_date: str, weeks: int,
                        where_clause: str, params: list):
    """Run a single rolling window aggregation query."""
    days = weeks * 7
    return conn.execute(f"""
        SELECT
            COUNT(*),
            MEDIAN(sale_price),
            AVG(sale_price),
            MEDIAN(price_per_sqm) FILTER (WHERE price_per_sqm IS NOT NULL AND price_per_sqm > 0),
            AVG(price_per_sqm)    FILTER (WHERE price_per_sqm IS NOT NULL AND price_per_sqm > 0),
            COUNT(price_per_sqm)  FILTER (WHERE price_per_sqm IS NOT NULL AND price_per_sqm > 0)
        FROM mapped_sales
        WHERE contract_date > DATE '{ref_date}' - INTERVAL '{days} days'
          AND contract_date <= DATE '{ref_date}'
          {where_clause}
    """, params).fetchone()


def _build_rolling_row(pt: str, weeks: int, row,
                        is_caution: bool, is_suppressed: bool) -> dict:
    count   = row[0] or 0
    sqm_cnt = row[5] or 0
    ok      = not is_suppressed
    return {
        'property_type':    pt,
        'window_weeks':     weeks,
        'sales_count':      count,
        'is_caution':       is_caution,
        'is_suppressed':    is_suppressed,
        'median_price':     round(row[1]) if row[1] and ok else None,
        'avg_price':        round(row[2]) if row[2] and ok else None,
        'median_price_sqm': round(row[3], 2) if row[3] and ok else None,
        'avg_price_sqm':    round(row[4], 2) if row[4] and ok else None,
        'has_sqm_data':     sqm_cnt >= MIN_SQM_RECORDS,
    }


def _adaptive_window(conn, ref_date: str, where_clause: str, params: list,
                      pt: str) -> dict:
    """
    Find smallest window with >= MIN_ROLLING_SALES transactions.
    Falls back to 52-week result with caution/suppress flags.
    """
    last_row, last_weeks = None, 52

    for weeks in WINDOW_STEPS_WEEKS:
        row   = _run_rolling_query(conn, ref_date, weeks, where_clause, params)
        count = row[0] or 0
        last_row, last_weeks = row, weeks

        if count >= MIN_ROLLING_SALES:
            return _build_rolling_row(pt, weeks, row,
                                      is_caution=False, is_suppressed=False)

    # Exhausted all steps without reaching MIN_ROLLING_SALES
    count = last_row[0] or 0
    return _build_rolling_row(pt, last_weeks, last_row,
                               is_caution=(count >= CAUTION_FLOOR),
                               is_suppressed=(count < CAUTION_FLOOR))


# ─────────────────────────────────────────────────────────────────
# Stage 1: Suburb reference
# ─────────────────────────────────────────────────────────────────

def load_suburb_reference(conn) -> int:
    conn.execute("DELETE FROM mapped_sales")
    conn.execute("DELETE FROM suburb_ref")

    suburbs = get_all_suburbs()
    rows = [
        (i + 1, s["region"], s["suburb"], s["suburb_upper"],
         s["postcode"], s["postcode_str"])
        for i, s in enumerate(suburbs)
    ]
    conn.executemany(
        "INSERT INTO suburb_ref "
        "(suburb_id, region, suburb, suburb_upper, postcode, postcode_str) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM suburb_ref").fetchone()[0]
    logger.info("Loaded %d suburbs into suburb_ref", n)
    return n


# ─────────────────────────────────────────────────────────────────
# Stage 2: Mapped sales (all property types)
# ─────────────────────────────────────────────────────────────────

def build_mapped_sales(conn) -> int:
    """
    Map raw_sales to approved suburbs. All property types included.
    price_per_sqm computed with outlier suppression (sale kept, sqm nulled).
    """
    land_min, land_max, sqm_min, sqm_max = _outlier_thresholds()
    for _col in [
        "ALTER TABLE mapped_sales ADD COLUMN zone_code VARCHAR",
        "ALTER TABLE mapped_sales ADD COLUMN price_per_sqm DOUBLE",
    ]:
        try:
            conn.execute(_col)
        except Exception:
            pass
    conn.execute("DELETE FROM mapped_sales")
    conn.execute(f"""
        INSERT INTO mapped_sales
            (id, suburb_id, region, suburb, postcode, property_type, zone_code,
             contract_date, contract_year, sale_price, land_size_sqm, price_per_sqm, source_file)
        SELECT
            rs.id,
            sr.suburb_id,
            sr.region,
            sr.suburb,
            sr.postcode,
            rs.property_type,
            rs.zone_code,
            rs.contract_date,
            EXTRACT(YEAR FROM rs.contract_date)::INTEGER AS contract_year,
            rs.sale_price,
            CASE
                WHEN rs.land_size_sqm IS NULL
                  OR rs.land_size_sqm < {land_min}
                  OR rs.land_size_sqm > {land_max}  THEN NULL
                ELSE rs.land_size_sqm
            END AS land_size_sqm,
            CASE
                WHEN rs.land_size_sqm IS NULL
                  OR rs.land_size_sqm < {land_min}
                  OR rs.land_size_sqm > {land_max}  THEN NULL
                WHEN rs.sale_price::DOUBLE / rs.land_size_sqm < {sqm_min}
                  OR rs.sale_price::DOUBLE / rs.land_size_sqm > {sqm_max}  THEN NULL
                ELSE ROUND(rs.sale_price::DOUBLE / rs.land_size_sqm, 2)
            END AS price_per_sqm,
            rs.source_file
        FROM raw_sales rs
        JOIN suburb_ref sr
            ON UPPER(rs.suburb) = sr.suburb_upper
           AND rs.postcode       = sr.postcode_str
        WHERE rs.sale_price IS NOT NULL
          AND rs.sale_price > 0
          AND rs.property_type IS NOT NULL
          AND rs.property_type != ''
          AND rs.contract_date >= '1980-01-01'
          AND rs.contract_date <= CURRENT_DATE
    """)
    conn.commit()

    n = conn.execute("SELECT COUNT(*) FROM mapped_sales").fetchone()[0]
    logger.info(
        "Built mapped_sales: %d records | land bounds %d–%d sqm | "
        "price_sqm bounds $%d–$%d",
        n, land_min, land_max, sqm_min, sqm_max
    )
    return n


# ─────────────────────────────────────────────────────────────────
# Stage 3: Annual suburb metrics
# ─────────────────────────────────────────────────────────────────

def build_annual_metrics(conn) -> int:
    conn.execute("DELETE FROM annual_metrics")

    # Per property type
    conn.execute(f"""
        INSERT INTO annual_metrics
        SELECT
            suburb_id, region, suburb, postcode,
            property_type,
            contract_year,
            COUNT(*)                                                    AS sales_count,
            MEDIAN(sale_price)                                          AS median_price,
            AVG(sale_price)                                             AS avg_price,
            MEDIAN(price_per_sqm) FILTER (WHERE price_per_sqm IS NOT NULL) AS median_price_sqm,
            AVG(price_per_sqm)    FILTER (WHERE price_per_sqm IS NOT NULL) AS avg_price_sqm,
            COUNT(price_per_sqm)  FILTER (WHERE price_per_sqm IS NOT NULL) AS sqm_record_count
        FROM mapped_sales
        GROUP BY suburb_id, region, suburb, postcode, property_type, contract_year
        HAVING COUNT(*) >= {MIN_CHART_SALES}
    """)

    # ALL aggregate
    conn.execute(f"""
        INSERT INTO annual_metrics
        SELECT
            suburb_id, region, suburb, postcode,
            'All' AS property_type,
            contract_year,
            COUNT(*),
            MEDIAN(sale_price),
            AVG(sale_price),
            MEDIAN(price_per_sqm) FILTER (WHERE price_per_sqm IS NOT NULL),
            AVG(price_per_sqm)    FILTER (WHERE price_per_sqm IS NOT NULL),
            COUNT(price_per_sqm)  FILTER (WHERE price_per_sqm IS NOT NULL)
        FROM mapped_sales
        GROUP BY suburb_id, region, suburb, postcode, contract_year
        HAVING COUNT(*) >= {MIN_CHART_SALES}
    """)

    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM annual_metrics").fetchone()[0]
    logger.info("Built annual_metrics: %d suburb-type-year rows", n)
    return n


# ─────────────────────────────────────────────────────────────────
# Stage 4: Annual regional metrics
# ─────────────────────────────────────────────────────────────────

def build_annual_regional_metrics(conn) -> int:
    conn.execute("DROP TABLE IF EXISTS annual_regional_metrics")
    conn.execute("""
        CREATE TABLE annual_regional_metrics (
            region           TEXT NOT NULL,
            property_type    TEXT NOT NULL,
            contract_year    INTEGER NOT NULL,
            sales_count      INTEGER NOT NULL,
            median_price     DOUBLE,
            avg_price        DOUBLE,
            median_price_sqm DOUBLE,
            avg_price_sqm    DOUBLE,
            sqm_record_count INTEGER,
            suburb_count     INTEGER,
            PRIMARY KEY (region, property_type, contract_year)
        )
    """)

    for type_filter, pt_val in [("", "property_type"),
                                 ("", "'All'")]:
        if pt_val == "'All'":
            conn.execute(f"""
                INSERT INTO annual_regional_metrics
                SELECT
                    region, 'All', contract_year,
                    COUNT(*),
                    MEDIAN(sale_price), AVG(sale_price),
                    MEDIAN(price_per_sqm) FILTER (WHERE price_per_sqm IS NOT NULL),
                    AVG(price_per_sqm)    FILTER (WHERE price_per_sqm IS NOT NULL),
                    COUNT(price_per_sqm)  FILTER (WHERE price_per_sqm IS NOT NULL),
                    COUNT(DISTINCT suburb_id)
                FROM mapped_sales
                GROUP BY region, contract_year
                HAVING COUNT(*) >= {MIN_CHART_SALES}
            """)
        else:
            conn.execute(f"""
                INSERT INTO annual_regional_metrics
                SELECT
                    region, property_type, contract_year,
                    COUNT(*),
                    MEDIAN(sale_price), AVG(sale_price),
                    MEDIAN(price_per_sqm) FILTER (WHERE price_per_sqm IS NOT NULL),
                    AVG(price_per_sqm)    FILTER (WHERE price_per_sqm IS NOT NULL),
                    COUNT(price_per_sqm)  FILTER (WHERE price_per_sqm IS NOT NULL),
                    COUNT(DISTINCT suburb_id)
                FROM mapped_sales
                GROUP BY region, property_type, contract_year
                HAVING COUNT(*) >= {MIN_CHART_SALES}
            """)

    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM annual_regional_metrics").fetchone()[0]
    logger.info("Built annual_regional_metrics: %d rows", n)
    return n


# ─────────────────────────────────────────────────────────────────
# Stage 5: Rolling metrics — suburb level
# ─────────────────────────────────────────────────────────────────

def build_rolling_metrics(conn, ref_date: Optional[str] = None) -> int:
    if not ref_date:
        row = conn.execute("SELECT MAX(contract_date) FROM mapped_sales").fetchone()
        if not row or not row[0]:
            logger.warning("No data — skipping rolling metrics")
            return 0
        ref_date = str(row[0])

    logger.info("Building suburb rolling_metrics as of %s", ref_date)
    conn.execute("DELETE FROM rolling_metrics")

    suburb_rows = conn.execute(
        "SELECT suburb_id, region, suburb, postcode FROM suburb_ref ORDER BY suburb_id"
    ).fetchall()

    inserted = 0
    for suburb_id, region, suburb, postcode in suburb_rows:
        for pt in ALL_TYPES:
            if pt == "All":
                where = "AND suburb_id = ?"
                params = [suburb_id]
            else:
                where = "AND suburb_id = ? AND property_type = ?"
                params = [suburb_id, pt]

            m = _adaptive_window(conn, ref_date, where, params, pt)

            conn.execute("""
                INSERT INTO rolling_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CAST(? AS DATE))
            """, [suburb_id, region, suburb, postcode,
                  m['property_type'], m['window_weeks'], m['sales_count'],
                  m['is_caution'], m['is_suppressed'],
                  m['median_price'], m['avg_price'],
                  m['median_price_sqm'], m['avg_price_sqm'],
                  m['has_sqm_data'], ref_date])
            inserted += 1

    conn.commit()
    logger.info("Built rolling_metrics: %d rows", inserted)
    return inserted


# ─────────────────────────────────────────────────────────────────
# Stage 6: Rolling metrics — region level
# ─────────────────────────────────────────────────────────────────

def build_regional_rolling_metrics(conn, ref_date: Optional[str] = None) -> int:
    if not ref_date:
        row = conn.execute("SELECT MAX(contract_date) FROM mapped_sales").fetchone()
        if not row or not row[0]:
            return 0
        ref_date = str(row[0])

    conn.execute("DELETE FROM regional_rolling_metrics")

    regions = [r[0] for r in conn.execute(
        "SELECT DISTINCT region FROM suburb_ref ORDER BY region"
    ).fetchall()]

    inserted = 0
    for region in regions:
        for pt in ALL_TYPES:
            if pt == "All":
                where = "AND region = ?"
                params = [region]
            else:
                where = "AND region = ? AND property_type = ?"
                params = [region, pt]

            m = _adaptive_window(conn, ref_date, where, params, pt)

            conn.execute("""
                INSERT INTO regional_rolling_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,CAST(? AS DATE))
            """, [region, m['property_type'], m['window_weeks'], m['sales_count'],
                  m['is_caution'], m['is_suppressed'],
                  m['median_price'], m['avg_price'],
                  m['median_price_sqm'], m['avg_price_sqm'],
                  m['has_sqm_data'], ref_date])
            inserted += 1

    conn.commit()
    logger.info("Built regional_rolling_metrics: %d rows", inserted)
    return inserted


# ─────────────────────────────────────────────────────────────────
# Stage 7: Price performance — suburb level
# ─────────────────────────────────────────────────────────────────

def _perf_sql(entity_col: str, ref_date: str) -> str:
    """Generate the full price performance CTE query for one suburb or region."""
    return f"""
        WITH ref AS (SELECT DATE '{ref_date}' AS d)
        SELECT
            -- Current periods (count, price_median, sqm_median)
            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '3 months' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '3 months' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '3 months' AND contract_date <= (SELECT d FROM ref) AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '12 months' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '12 months' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '12 months' AND contract_date <= (SELECT d FROM ref) AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '3 years' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '3 years' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '3 years' AND contract_date <= (SELECT d FROM ref) AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '5 years' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '5 years' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '5 years' AND contract_date <= (SELECT d FROM ref) AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '10 years' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '10 years' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '10 years' AND contract_date <= (SELECT d FROM ref) AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '20 years' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '20 years' AND contract_date <= (SELECT d FROM ref)),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '20 years' AND contract_date <= (SELECT d FROM ref) AND price_per_sqm IS NOT NULL),

            -- Prior periods (price_median, count, sqm_median)
            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '6 months'  AND contract_date <= (SELECT d FROM ref) - INTERVAL '3 months'),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '6 months'  AND contract_date <= (SELECT d FROM ref) - INTERVAL '3 months'),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '6 months' AND contract_date <= (SELECT d FROM ref) - INTERVAL '3 months' AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '24 months' AND contract_date <= (SELECT d FROM ref) - INTERVAL '12 months'),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '24 months' AND contract_date <= (SELECT d FROM ref) - INTERVAL '12 months'),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '24 months' AND contract_date <= (SELECT d FROM ref) - INTERVAL '12 months' AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '6 years'  AND contract_date <= (SELECT d FROM ref) - INTERVAL '3 years'),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '6 years'  AND contract_date <= (SELECT d FROM ref) - INTERVAL '3 years'),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '6 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '3 years' AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '10 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '5 years'),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '10 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '5 years'),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '10 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '5 years' AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '20 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '10 years'),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '20 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '10 years'),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '20 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '10 years' AND price_per_sqm IS NOT NULL),

            COUNT(*) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '40 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '20 years'),
            MEDIAN(sale_price) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '40 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '20 years'),
            MEDIAN(price_per_sqm) FILTER (WHERE contract_date > (SELECT d FROM ref) - INTERVAL '40 years' AND contract_date <= (SELECT d FROM ref) - INTERVAL '20 years' AND price_per_sqm IS NOT NULL)

        FROM mapped_sales
        WHERE {entity_col}
    """


def _pack_perf(r, ref_date: str, use_sqm: bool,
               id_cols: list, id_vals: list) -> list:
    """
    Unpack raw perf query result and build INSERT parameter list.
    r is a 36-element tuple:
      [0..2]   3m  (count, med_price, med_sqm)
      [3..5]   12m
      [6..8]   3y
      [9..11]  5y
      [12..14] 10y
      [15..17] 20y
      [18..20] prior 3m
      [21..23] prior 12m
      [24..26] prior 3y
      [27..29] prior 5y
      [30..32] prior 10y
      [33..35] prior 20y
    """
    def cur(i):  # (count, med, sqm) for period i*3
        return r[i*3], r[i*3+1], r[i*3+2]

    def pri(i):  # prior starts at index 18
        base = 18 + i*3
        return r[base], r[base+1], r[base+2]

    def apply(cnt, med, sqm):
        m = _thresh(med, cnt, MIN_PERF_SALES)
        s = _thresh(sqm, cnt, MIN_PERF_SALES)
        return cnt if cnt and cnt >= MIN_PERF_SALES else None, m, s

    def changes(c_cnt, c_med, c_sqm, p_cnt, p_med, p_sqm):
        _, cm, cs = apply(c_cnt, c_med, c_sqm)
        _, pm, ps = apply(p_cnt, p_med, p_sqm)
        base_c = cs if use_sqm else cm
        base_p = ps if use_sqm else pm
        return _nominal(base_c, base_p), _pct(base_c, base_p)

    periods = []
    for i in range(6):
        c_cnt, c_med, c_sqm = cur(i)
        p_cnt, p_med, p_sqm = pri(i)
        c_cnt_t, c_med_t, _ = apply(c_cnt, c_med, c_sqm)
        nom, pct = changes(c_cnt, c_med, c_sqm, p_cnt, p_med, p_sqm)
        periods.extend([c_cnt_t, c_med_t])

    nom_pct = []
    for i in range(6):
        c_cnt, c_med, c_sqm = cur(i)
        p_cnt, p_med, p_sqm = pri(i)
        nom, pct = changes(c_cnt, c_med, c_sqm, p_cnt, p_med, p_sqm)
        nom_pct.extend([nom, pct])

    return id_vals + periods + nom_pct + [ref_date]


def build_price_performance(conn, as_of_date: Optional[str] = None) -> int:
    if not as_of_date:
        row = conn.execute("SELECT MAX(contract_date) FROM mapped_sales").fetchone()
        if not row or not row[0]:
            logger.warning("No data — skipping price_performance")
            return 0
        as_of_date = str(row[0])

    logger.info("Building price_performance as of %s", as_of_date)
    conn.execute("DROP TABLE IF EXISTS price_performance")
    conn.execute("""
        CREATE TABLE price_performance (
            suburb_id    INTEGER NOT NULL,
            region       TEXT NOT NULL,
            suburb       TEXT NOT NULL,
            postcode     INTEGER NOT NULL,
            property_type TEXT NOT NULL,
            sales_3m     INTEGER,  median_3m  DOUBLE,
            sales_12m    INTEGER,  median_12m DOUBLE,
            sales_3y     INTEGER,  median_3y  DOUBLE,
            sales_5y     INTEGER,  median_5y  DOUBLE,
            sales_10y    INTEGER,  median_10y DOUBLE,
            sales_20y    INTEGER,  median_20y DOUBLE,
            nominal_3m   DOUBLE,   pct_3m  DOUBLE,
            nominal_12m  DOUBLE,   pct_12m DOUBLE,
            nominal_3y   DOUBLE,   pct_3y  DOUBLE,
            nominal_5y   DOUBLE,   pct_5y  DOUBLE,
            nominal_10y  DOUBLE,   pct_10y DOUBLE,
            nominal_20y  DOUBLE,   pct_20y DOUBLE,
            as_of_date   DATE NOT NULL,
            PRIMARY KEY (suburb_id, property_type)
        )
    """)

    suburb_rows = conn.execute(
        "SELECT suburb_id, region, suburb, postcode FROM suburb_ref ORDER BY suburb_id"
    ).fetchall()

    placeholders = ",".join(["?"] * 30)  # 5 id + 12 period + 12 nom/pct + 1 date

    inserted = 0
    for suburb_id, region, suburb, postcode in suburb_rows:
        for pt in ALL_TYPES:
            type_filter = "" if pt == "ALL" else f"AND property_type = '{pt}'"
            entity_clause = f"suburb_id = {suburb_id} {type_filter}"

            r = conn.execute(
                _perf_sql(entity_clause, as_of_date)
            ).fetchone()

            use_sqm = (pt == "VACANT LAND")
            params  = _pack_perf(r, as_of_date, use_sqm,
                                  ['suburb_id','region','suburb','postcode','property_type'],
                                  [suburb_id, region, suburb, postcode, pt])

            conn.execute(
                f"INSERT INTO price_performance VALUES ({placeholders})", params
            )
            inserted += 1

    conn.commit()
    logger.info("Built price_performance: %d rows", inserted)
    return inserted


# ─────────────────────────────────────────────────────────────────
# Stage 8: Price performance — region level
# ─────────────────────────────────────────────────────────────────

def build_regional_price_performance(conn,
                                      as_of_date: Optional[str] = None) -> int:
    if not as_of_date:
        row = conn.execute("SELECT MAX(contract_date) FROM mapped_sales").fetchone()
        if not row or not row[0]:
            return 0
        as_of_date = str(row[0])

    conn.execute("DROP TABLE IF EXISTS regional_price_performance")
    conn.execute("""
        CREATE TABLE regional_price_performance (
            region        TEXT NOT NULL,
            property_type TEXT NOT NULL,
            sales_3m      INTEGER,  median_3m  DOUBLE,
            sales_12m     INTEGER,  median_12m DOUBLE,
            sales_3y      INTEGER,  median_3y  DOUBLE,
            sales_5y      INTEGER,  median_5y  DOUBLE,
            sales_10y     INTEGER,  median_10y DOUBLE,
            sales_20y     INTEGER,  median_20y DOUBLE,
            nominal_3m    DOUBLE,   pct_3m  DOUBLE,
            nominal_12m   DOUBLE,   pct_12m DOUBLE,
            nominal_3y    DOUBLE,   pct_3y  DOUBLE,
            nominal_5y    DOUBLE,   pct_5y  DOUBLE,
            nominal_10y   DOUBLE,   pct_10y DOUBLE,
            nominal_20y   DOUBLE,   pct_20y DOUBLE,
            as_of_date    DATE NOT NULL,
            PRIMARY KEY (region, property_type)
        )
    """)

    regions = [r[0] for r in conn.execute(
        "SELECT DISTINCT region FROM suburb_ref ORDER BY region"
    ).fetchall()]

    placeholders = ",".join(["?"] * 27)  # 2 id + 12 period + 12 nom/pct + 1 date

    inserted = 0
    for region in regions:
        for pt in ALL_TYPES:
            type_filter   = "" if pt == "ALL" else f"AND property_type = '{pt}'"
            entity_clause = f"region = '{region}' {type_filter}"

            r = conn.execute(
                _perf_sql(entity_clause, as_of_date)
            ).fetchone()

            use_sqm = (pt == "VACANT LAND")
            params  = _pack_perf(r, as_of_date, use_sqm,
                                  ['region', 'property_type'],
                                  [region, pt])

            conn.execute(
                f"INSERT INTO regional_price_performance VALUES ({placeholders})",
                params
            )
            inserted += 1

    conn.commit()
    logger.info("Built regional_price_performance: %d rows", inserted)
    return inserted


# ─────────────────────────────────────────────────────────────────
# Stage 9: Available types + vacant land zones
# ─────────────────────────────────────────────────────────────────

def build_available_types(conn) -> int:
    """
    Determines which property types have any data per suburb and per region.
    Assessed fresh every pipeline run — future ingestion may add new types.
    """
    conn.execute("DELETE FROM available_types")
    conn.execute("DELETE FROM vacant_land_zones")

    suburb_rows = conn.execute(
        "SELECT suburb_id FROM suburb_ref"
    ).fetchall()

    inserted = 0
    for (suburb_id,) in suburb_rows:
        for pt in ALL_TYPES:
            if pt == "All":
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM mapped_sales WHERE suburb_id = ?",
                    [suburb_id]
                ).fetchone()[0]
            else:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM mapped_sales WHERE suburb_id = ? AND property_type = ?",
                    [suburb_id, pt]
                ).fetchone()[0]
            conn.execute(
                "INSERT INTO available_types VALUES ('suburb', ?, ?, ?, ?)",
                [str(suburb_id), pt, cnt > 0, cnt]
            )
            inserted += 1

    regions = [r[0] for r in conn.execute(
        "SELECT DISTINCT region FROM suburb_ref"
    ).fetchall()]

    for region in regions:
        for pt in ALL_TYPES:
            if pt == "All":
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM mapped_sales WHERE region = ?",
                    [region]
                ).fetchone()[0]
            else:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM mapped_sales WHERE region = ? AND property_type = ?",
                    [region, pt]
                ).fetchone()[0]
            conn.execute(
                "INSERT INTO available_types VALUES ('region', ?, ?, ?, ?)",
                [region, pt, cnt > 0, cnt]
            )
            inserted += 1

    # Vacant land zones per suburb
    conn.execute("""
        INSERT INTO vacant_land_zones
        SELECT suburb_id, region, suburb,
               COALESCE(zone_code, 'UNKNOWN') AS zone_code,
               COUNT(*) AS sales_count
        FROM mapped_sales
        WHERE property_type = 'VACANT LAND'
          AND zone_code IS NOT NULL AND zone_code != ''
        GROUP BY suburb_id, region, suburb, zone_code
        ORDER BY suburb_id, sales_count DESC
    """)

    conn.commit()
    logger.info("Built available_types: %d rows", inserted)
    return inserted


# ─────────────────────────────────────────────────────────────────
# Master runner
# ─────────────────────────────────────────────────────────────────

def run_analytics(db_path: str, as_of_date: Optional[str] = None) -> dict:
    """
    Run all analytics stages. Call after each ingestion run.
    Returns summary dict with row counts and elapsed time.
    """
    import time
    start = time.time()

    conn = duckdb.connect(db_path)
    conn.execute(ANALYTICS_SCHEMA_SQL)
    conn.commit()

    logger.info("Analytics v2.0 starting...")

    if not as_of_date:
        row = conn.execute(
            "SELECT MAX(contract_date) FROM raw_sales WHERE contract_date <= CURRENT_DATE"
        ).fetchone()
        as_of_date = str(row[0]) if row and row[0] else None

    if not as_of_date:
        logger.error("No data in raw_sales. Run ingestion first.")
        conn.close()
        return {"error": "no_data"}

    logger.info("Reference date: %s", as_of_date)

    summary = {
        'suburb_ref':         load_suburb_reference(conn),
        'mapped_sales':       build_mapped_sales(conn),
        'annual_metrics':     build_annual_metrics(conn),
        'regional_annual':    build_annual_regional_metrics(conn),
        'rolling_metrics':    build_rolling_metrics(conn, as_of_date),
        'regional_rolling':   build_regional_rolling_metrics(conn, as_of_date),
        'price_performance':  build_price_performance(conn, as_of_date),
        'regional_perf':      build_regional_price_performance(conn, as_of_date),
        'available_types':    build_available_types(conn),
        'reference_date':     as_of_date,
        'elapsed_seconds':    None,
    }

    conn.close()
    summary['elapsed_seconds'] = round(time.time() - start, 1)
    logger.info("Analytics v2.0 complete in %.1fs", summary['elapsed_seconds'])
    return summary


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="PMR Analytics v2.0")
    parser.add_argument('--db',        required=True)
    parser.add_argument('--as-of',     default=None,
                        help='Reference date YYYY-MM-DD (default: max contract_date)')
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format='%(asctime)s %(levelname)s %(message)s',
                        datefmt='%H:%M:%S')

    result = run_analytics(args.db, as_of_date=args.as_of)
    print("\nAnalytics v2.0 summary:")
    for k, v in result.items():
        label = k.replace('_', ' ').title()
        val   = f"{v:,}" if isinstance(v, int) else str(v)
        print(f"  {label:25s}: {val}")
    print()
