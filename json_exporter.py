"""
JSON Report Exporter
=====================
Version: v2.0

Changes from v1.0:
  - All property types exported (HOUSE / UNIT / VACANT LAND / OTHER / All)
  - Available property types derived dynamically per suburb from the data
    → UI enables/disables buttons based on what actually exists
  - Tiles now use rolling_median table (adaptive window) not last annual row
  - Vacant Land: tiles show median_sqm / avg_sqm; perf table shows $/sqm change
  - Price performance exported as nominal ($) change + pct (not median values)
    consistent with approved preview format
  - 3m performance retained as rolling quarter-on-quarter (labelled clearly)
  - YTD data point included as separate chart field (dotted-line marker)
  - Narrative rebuilt as bullet points (max 450 chars, rule-based)
  - Column names updated throughout to match analytics.py v2.0 schema

Output structure (unchanged from v1.0):
  reports/
    index.json
    eastern-suburbs/
      region.json
      bondi-beach.json
    central-coast/
      region.json
      terrigal.json
    inner-west/
      region.json
      newtown.json

JSON encoding rules:
  - Monetary values: integers (dollars, no cents)
  - Percentages: float rounded to 2 decimal places
  - $/sqm values: float rounded to 2 decimal places
  - NULL -> JSON null -> display as N/A in UI
  - Nominal change: signed integer or float (UI adds +/- prefix from sign)
"""

import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Slug helper
# ─────────────────────────────────────────────

def to_slug(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"['\.]", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# ─────────────────────────────────────────────
# JSON serialiser (handles date objects)
# ─────────────────────────────────────────────

class _DateEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return str(obj)
        return super().default(obj)


def _write_json(filepath: str, data: dict) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, cls=_DateEncoder, ensure_ascii=False)


# ─────────────────────────────────────────────
# Narrative generator (bullet-point, 450-char cap)
# ─────────────────────────────────────────────

def _generate_narrative(suburb: str,
                         annual_rows: list,
                         perf: Optional[dict],
                         property_type: str = "All") -> list:
    """
    Generate 2-3 analytical bullet points for a suburb x type.

    Rules (per approved preview spec):
    - Bullet-point format (list of strings, each a bullet)
    - Total characters across all bullets: max 450
    - Volume signal overrides price signal if conflicting (BRD s22)
    - No claims when data is suppressed (NULL)
    - Data-backed only, no opinion
    - For VACANT LAND: reference $/sqm not median price

    Returns list of bullet strings (without bullet symbol, UI adds it).
    """
    is_land = property_type == "VACANT LAND"

    if not annual_rows:
        return [
            f"{suburb} has insufficient transaction history to generate "
            f"a market summary. Data will appear as more sales are recorded."
        ]

    rows = sorted(annual_rows, key=lambda r: r["year"])
    last_row   = rows[-1]
    last_year  = last_row["year"]
    last_sales = last_row.get("sales_count", 0)
    last_price = last_row.get("median_price")
    last_sqm   = last_row.get("median_sqm")

    bullets = []

    # Bullet 1: Volume + headline metric
    if is_land and last_sqm:
        bullets.append(
            f"{last_sales} settlements recorded in {last_year} "
            f"with median land value of ${last_sqm:,.0f}/sqm"
        )
    elif last_price:
        bullets.append(
            f"{last_sales} settlements recorded in {last_year} "
            f"with a median price of ${last_price:,.0f}"
        )
    else:
        bullets.append(f"{last_sales} settlements recorded in {last_year}")

    # Bullet 2: Year-on-year movement (volume priority per BRD s22)
    if len(rows) >= 2:
        prior_row   = rows[-2]
        prior_sales = prior_row.get("sales_count", 0)
        prior_year  = prior_row["year"]
        prior_price = prior_row.get("median_price")
        prior_sqm   = prior_row.get("median_sqm")

        vol_change = last_sales - prior_sales
        vol_pct = (vol_change / prior_sales * 100) if prior_sales > 0 else None

        if vol_pct is not None and abs(vol_pct) >= 20:
            direction = "surged" if vol_pct > 0 else "slowed"
            bullets.append(
                f"Transaction volume {direction} {abs(vol_pct):.0f}% "
                f"vs {prior_year} ({prior_sales} to {last_sales} sales)"
            )
        elif is_land and last_sqm and prior_sqm and prior_sqm > 0:
            sqm_chg = (last_sqm - prior_sqm) / prior_sqm * 100
            direction = "up" if sqm_chg > 0 else "down"
            bullets.append(
                f"Land value per sqm moved {direction} "
                f"{abs(sqm_chg):.1f}% vs {prior_year}"
            )
        elif last_price and prior_price and prior_price > 0:
            price_chg = (last_price - prior_price) / prior_price * 100
            direction = "up" if price_chg > 0 else "down"
            bullets.append(
                f"Median price {direction} {abs(price_chg):.1f}% vs {prior_year}"
            )

    # Bullet 3: Long-term or data note
    if perf:
        entry_10y = perf.get("10y", {})
        nom_10y   = entry_10y.get("nominal")
        pct_10y   = entry_10y.get("pct")
        if nom_10y is not None and pct_10y is not None:
            direction = "growth" if nom_10y > 0 else "decline"
            prefix    = "+" if nom_10y > 0 else ""
            pct_pfx   = "+" if pct_10y > 0 else ""
            if is_land:
                bullets.append(
                    f"10-year land value {direction}: "
                    f"{prefix}${nom_10y:,.2f}/sqm ({pct_pfx}{pct_10y:.1f}%)"
                )
            else:
                bullets.append(
                    f"10-year {direction}: "
                    f"{prefix}${nom_10y:,.0f} ({pct_pfx}{pct_10y:.1f}%) "
                    f"since {last_year - 10}"
                )
        elif len(rows) < 5:
            bullets.append(
                f"Limited history: {len(rows)} year(s) of qualifying data "
                f"(>=100 sales/year). Trend indicators will strengthen over time."
            )
    else:
        if len(rows) < 5:
            bullets.append(
                f"Limited history: {len(rows)} year(s) of qualifying data. "
                f"Trend indicators will strengthen over time."
            )

    # Enforce 450-char total cap
    total = sum(len(b) for b in bullets)
    while total > 450 and len(bullets) > 1:
        bullets = bullets[:-1]
        total = sum(len(b) for b in bullets)
    if total > 450 and bullets:
        bullets[0] = bullets[0][:447] + "..."

    return bullets


# ─────────────────────────────────────────────
# Data extractors (v2.0 column names)
# ─────────────────────────────────────────────

def _fetch_annual_by_type(conn, suburb_id: int) -> dict:
    rows = conn.execute("""
        SELECT property_type, contract_year, sales_count,
               median_price, avg_price,
               median_price_sqm AS median_sqm,
               avg_price_sqm    AS avg_sqm
        FROM annual_metrics
        WHERE suburb_id = ?
        ORDER BY property_type, contract_year
    """, [suburb_id]).fetchall()

    by_type = {}
    for r in rows:
        pt = r[0]
        by_type.setdefault(pt, []).append({
            "year":         r[1],
            "sales_count":  r[2],
            "median_price": round(r[3]) if r[3] else None,
            "avg_price":    round(r[4]) if r[4] else None,
            "median_sqm":   round(r[5], 2) if r[5] else None,
            "avg_sqm":      round(r[6], 2) if r[6] else None,
        })
    return by_type


def _fetch_ytd(conn, suburb_id: int) -> dict:
    current_year = datetime.utcnow().year

    rows = conn.execute("""
        SELECT property_type,
               COUNT(*) AS cnt,
               MEDIAN(sale_price) AS med,
               AVG(sale_price) AS avg_p,
               MEDIAN(price_per_sqm) FILTER (WHERE price_per_sqm IS NOT NULL) AS med_sqm,
               AVG(price_per_sqm)    FILTER (WHERE price_per_sqm IS NOT NULL) AS avg_sqm
        FROM mapped_sales
        WHERE suburb_id = ? AND contract_year = ?
        GROUP BY property_type
    """, [suburb_id, current_year]).fetchall()

    ytd = {}
    for r in rows:
        ytd[r[0]] = {
            "year":         current_year,
            "sales_count":  r[1],
            "median_price": round(r[2]) if r[2] else None,
            "avg_price":    round(r[3]) if r[3] else None,
            "median_sqm":   round(r[4], 2) if r[4] else None,
            "avg_sqm":      round(r[5], 2) if r[5] else None,
            "is_ytd":       True,
        }

    # All aggregate
    all_row = conn.execute("""
        SELECT COUNT(*), MEDIAN(sale_price), AVG(sale_price),
               MEDIAN(price_per_sqm) FILTER (WHERE price_per_sqm IS NOT NULL),
               AVG(price_per_sqm)    FILTER (WHERE price_per_sqm IS NOT NULL)
        FROM mapped_sales
        WHERE suburb_id = ? AND contract_year = ?
    """, [suburb_id, current_year]).fetchone()

    if all_row and all_row[0]:
        ytd["All"] = {
            "year":         current_year,
            "sales_count":  all_row[0],
            "median_price": round(all_row[1]) if all_row[1] else None,
            "avg_price":    round(all_row[2]) if all_row[2] else None,
            "median_sqm":   round(all_row[3], 2) if all_row[3] else None,
            "avg_sqm":      round(all_row[4], 2) if all_row[4] else None,
            "is_ytd":       True,
        }

    return ytd


def _fetch_rolling_by_type(conn, suburb_id: int) -> dict:
    rows = conn.execute("""
        SELECT property_type, window_weeks, sales_count,
               is_caution, is_suppressed,
               median_price, avg_price,
               median_price_sqm, avg_price_sqm,
               data_as_of
        FROM rolling_metrics
        WHERE suburb_id = ?
    """, [suburb_id]).fetchall()

    result = {}
    for r in rows:
        pt         = r[0]
        wks        = r[1]
        suppressed = r[4]
        result[pt] = {
            "window_weeks":  wks,
            "window_label":  f"{wks}-week" if wks else None,
            "sales_count":   r[2],
            "is_caution":    r[3],
            "is_suppressed": suppressed,
            "median_price":  round(r[5]) if r[5] and not suppressed else None,
            "avg_price":     round(r[6]) if r[6] and not suppressed else None,
            "median_sqm":    round(r[7], 2) if r[7] and not suppressed else None,
            "avg_sqm":       round(r[8], 2) if r[8] and not suppressed else None,
            "as_of":         str(r[9]) if r[9] else None,
        }
    return result


def _fetch_perf_by_type(conn, suburb_id: int) -> dict:
    rows = conn.execute("""
        SELECT property_type,
               sales_3m,  median_3m,
               sales_12m, median_12m,
               sales_3y,  median_3y,
               sales_5y,  median_5y,
               sales_10y, median_10y,
               sales_20y, median_20y,
               nominal_3m,  pct_3m,
               nominal_12m, pct_12m,
               nominal_3y,  pct_3y,
               nominal_5y,  pct_5y,
               nominal_10y, pct_10y,
               nominal_20y, pct_20y,
               as_of_date
        FROM price_performance
        WHERE suburb_id = ?
    """, [suburb_id]).fetchall()

    result = {}
    for r in rows:
        pt      = r[0]
        is_land = pt == "VACANT LAND"

        def period(nominal, pct, note=None):
            d = {
                "nominal": (round(nominal, 2) if is_land else round(nominal))
                           if nominal is not None else None,
                "pct":     round(pct, 2) if pct is not None else None,
            }
            if note:
                d["note"] = note
            return d

        result[pt] = {
            "is_sqm_basis": is_land,
            "3m":  period(r[13], r[14], note="rolling quarter vs same quarter prior year"),
            "12m": period(r[15], r[16]),
            "3y":  period(r[17], r[18]),
            "5y":  period(r[19], r[20]),
            "10y": period(r[21], r[22]),
            "20y": period(r[23], r[24]),
            "as_of": str(r[25]) if r[25] else None,
        }
    return result


def _fetch_available_types(conn, suburb_id: int) -> list:
    rows = conn.execute("""
        SELECT DISTINCT property_type
        FROM annual_metrics
        WHERE suburb_id = ? AND property_type != 'All'
    """, [suburb_id]).fetchall()

    found = {r[0] for r in rows}
    ordered = ["HOUSE", "UNIT", "VACANT LAND", "OTHER"]
    return ["All"] + [t for t in ordered if t in found]


def _fetch_regional_annual_by_type(conn, region: str) -> dict:
    rows = conn.execute("""
        SELECT am.property_type, am.contract_year,
               SUM(am.sales_count)        AS sales_count,
               MEDIAN(am.median_price)    AS median_price,
               AVG(am.avg_price)          AS avg_price,
               MEDIAN(am.median_price_sqm) AS median_sqm,
               AVG(am.avg_price_sqm)       AS avg_sqm,
               COUNT(DISTINCT am.suburb_id) AS suburb_count
        FROM annual_metrics am
        JOIN suburb_ref sr ON am.suburb_id = sr.suburb_id
        WHERE sr.region = ?
        GROUP BY am.property_type, am.contract_year
        ORDER BY am.property_type, am.contract_year
    """, [region]).fetchall()
    by_type = {}
    for r in rows:
        pt = r[0]
        by_type.setdefault(pt, []).append({
            "year":         r[1],
            "sales_count":  r[2],
            "median_price": round(r[3]) if r[3] else None,
            "avg_price":    round(r[4]) if r[4] else None,
            "median_sqm":   round(r[5], 2) if r[5] else None,
            "avg_sqm":      round(r[6], 2) if r[6] else None,
            "suburb_count": r[7],
        })
    return by_type


def _fetch_regional_rolling_by_type(conn, region: str) -> dict:
    rows = conn.execute("""
        SELECT property_type, window_weeks, sales_count,
               is_caution, is_suppressed,
               median_price, avg_price,
               median_price_sqm, avg_price_sqm,
               data_as_of
        FROM regional_rolling_metrics
        WHERE region = ?
    """, [region]).fetchall()

    result = {}
    for r in rows:
        pt         = r[0]
        wks        = r[1]
        suppressed = r[4]
        result[pt] = {
            "window_weeks":  wks,
            "window_label":  f"{wks}-week" if wks else None,
            "sales_count":   r[2],
            "is_caution":    r[3],
            "is_suppressed": suppressed,
            "median_price":  round(r[5]) if r[5] and not suppressed else None,
            "avg_price":     round(r[6]) if r[6] and not suppressed else None,
            "median_sqm":    round(r[7], 2) if r[7] and not suppressed else None,
            "avg_sqm":       round(r[8], 2) if r[8] and not suppressed else None,
            "as_of":         str(r[9]) if r[9] else None,
        }
    return result


def _fetch_regional_perf_by_type(conn, region: str) -> dict:
    rows = conn.execute("""
        SELECT property_type,
               sales_3m,  median_3m,
               sales_12m, median_12m,
               sales_3y,  median_3y,
               sales_5y,  median_5y,
               sales_10y, median_10y,
               sales_20y, median_20y,
               nominal_3m,  pct_3m,
               nominal_12m, pct_12m,
               nominal_3y,  pct_3y,
               nominal_5y,  pct_5y,
               nominal_10y, pct_10y,
               nominal_20y, pct_20y,
               as_of_date
        FROM regional_price_performance
        WHERE region = ?
    """, [region]).fetchall()

    result = {}
    for r in rows:
        pt      = r[0]
        is_land = pt == "VACANT LAND"

        def period(nominal, pct, note=None):
            d = {
                "nominal": (round(nominal, 2) if is_land else round(nominal))
                           if nominal is not None else None,
                "pct":     round(pct, 2) if pct is not None else None,
            }
            if note:
                d["note"] = note
            return d

        result[pt] = {
            "is_sqm_basis": is_land,
            "3m":  period(r[13], r[14], note="rolling quarter vs same quarter prior year"),
            "12m": period(r[15], r[16]),
            "3y":  period(r[17], r[18]),
            "5y":  period(r[19], r[20]),
            "10y": period(r[21], r[22]),
            "20y": period(r[23], r[24]),
            "as_of": str(r[25]) if r[25] else None,
        }
    return result


def _fetch_regional_available_types(conn, region: str) -> list:
    rows = conn.execute("""
        SELECT DISTINCT property_type
        FROM annual_regional_metrics
        WHERE region = ? AND property_type != 'All'
    """, [region]).fetchall()

    found = {r[0] for r in rows}
    ordered = ["HOUSE", "UNIT", "VACANT LAND", "OTHER"]
    return ["All"] + [t for t in ordered if t in found]


# ─────────────────────────────────────────────
# Tile builder
# ─────────────────────────────────────────────

def _build_tiles(rolling: Optional[dict], property_type: str) -> dict:
    is_land = property_type == "VACANT LAND"
    window  = rolling.get("window_label") if rolling else None
    count   = rolling.get("sales_count", 0) if rolling else 0

    # Null out metric values if threshold not met (no window)
    median_val = avg_val = None
    if rolling and window:
        median_val = rolling.get("median_sqm") if is_land else rolling.get("median_price")
        avg_val    = rolling.get("avg_sqm")    if is_land else rolling.get("avg_price")

    metric_label = "$/sqm" if is_land else "price"

    return {
        "sales_count": {
            "value":  count,
            "label":  f"Sales ({window})" if window else "Sales (insufficient data)",
            "window": window,
        },
        "median": {
            "value":  median_val,
            "label":  f"Median {metric_label} ({window})" if window
                      else f"Median {metric_label}",
            "window": window,
            "is_sqm": is_land,
        },
        "avg": {
            "value":  avg_val,
            "label":  f"Avg {metric_label} ({window})" if window
                      else f"Avg {metric_label}",
            "window": window,
            "is_sqm": is_land,
        },
    }


# ─────────────────────────────────────────────
# Headline card (suburb grid in index/region)
# ─────────────────────────────────────────────

def _headline_card(entry: dict,
                   annual_by_type: dict,
                   rolling_by_type: dict,
                   perf_by_type: dict,
                   available_types: list) -> dict:
    annual_all  = annual_by_type.get("All", [])
    rolling_all = rolling_by_type.get("All", {})
    perf_all    = perf_by_type.get("All", {})

    trend_12m     = perf_all.get("12m", {}) if perf_all else {}
    trend_nominal = trend_12m.get("nominal")
    trend_pct     = trend_12m.get("pct")
    trend_dir     = None
    if trend_pct is not None:
        trend_dir = "up" if trend_pct > 0 else ("down" if trend_pct < 0 else "flat")

    return {
        "suburb":          entry["suburb"],
        "postcode":        entry["postcode"],
        "slug":            to_slug(entry["suburb"]),
        "available_types": available_types,
        "sales_count":     rolling_all.get("sales_count"),
        "window_label":    rolling_all.get("window_label"),
        "median_price":    rolling_all.get("median_price"),
        "avg_price":       rolling_all.get("avg_price"),
        "trend": {
            "direction": trend_dir,
            "nominal":   trend_nominal,
            "pct":       trend_pct,
            "period":    "12m",
        },
        "data_coverage": {
            "first_year": annual_all[0]["year"] if annual_all else None,
            "last_year":  annual_all[-1]["year"] if annual_all else None,
            "years":      len(annual_all),
        },
    }


# ─────────────────────────────────────────────
# Chart helpers
# ─────────────────────────────────────────────

def _chart_meta(property_type: str) -> dict:
    is_land = property_type == "VACANT LAND"
    return {
        "x_label":       "Year",
        "y_left_label":  "Median $/sqm" if is_land else "Median price ($)",
        "y_right_label": "Sales volume",
        "primary_field": "median_sqm" if is_land else "median_price",
    }


def _chart_note(annual_rows: list) -> Optional[str]:
    n = len(annual_rows)
    if n == 0:
        return "Insufficient history (no qualifying years with >=100 sales)"
    if n < 5:
        return f"{n} year{'s' if n > 1 else ''} of qualifying data (>=100 sales/year)"
    return None


# ─────────────────────────────────────────────
# Main export function
# ─────────────────────────────────────────────

def export_all(db_path: str, output_dir: str) -> dict:
    """
    Export all analytics tables to JSON for the browser dashboard.

    Each suburb gets one JSON file containing data for all available property
    types. The UI reads available_types to enable/disable toggle buttons.
    """
    import duckdb
    import time
    start = time.time()

    conn = duckdb.connect(db_path, read_only=True)

    suburb_rows = conn.execute("""
        SELECT suburb_id, region, suburb, postcode, postcode_str
        FROM suburb_ref
        ORDER BY region, suburb
    """).fetchall()

    if not suburb_rows:
        logger.error("suburb_ref is empty. Run analytics.py first.")
        conn.close()
        return {"error": "suburb_ref empty"}

    suburbs_by_region: dict = {}
    for row in suburb_rows:
        entry = {
            "suburb_id":    row[0],
            "region":       row[1],
            "suburb":       row[2],
            "postcode":     row[3],
            "postcode_str": row[4],
        }
        suburbs_by_region.setdefault(row[1], []).append(entry)

    regions = list(suburbs_by_region.keys())
    generated_at  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    current_year  = datetime.utcnow().year

    index_data = {
        "generated_at": generated_at,
        "data_source":  "NSW Valuer General confirmed sales",
        "threshold":    ">=100 sales/year per property type required",
        "regions":      [],
    }

    suburb_files_written = 0
    warnings: list = []

    for region in regions:
        region_slug    = to_slug(region)
        region_suburbs = suburbs_by_region[region]

        reg_annual_by_type  = _fetch_regional_annual_by_type(conn, region)
        reg_rolling_by_type = _fetch_regional_rolling_by_type(conn, region)
        reg_avail_types     = _fetch_regional_available_types(conn, region)

        suburb_cards: list = []

        for entry in region_suburbs:
            sid = entry["suburb_id"]

            annual_by_type  = _fetch_annual_by_type(conn, sid)
            rolling_by_type = _fetch_rolling_by_type(conn, sid)
            perf_by_type    = _fetch_perf_by_type(conn, sid)
            available_types = _fetch_available_types(conn, sid)
            ytd_by_type     = _fetch_ytd(conn, sid)

            card = _headline_card(
                entry, annual_by_type, rolling_by_type,
                perf_by_type, available_types
            )
            suburb_cards.append(card)

            # Build byType blocks
            by_type_data: dict = {}
            for pt in available_types:
                annual   = annual_by_type.get(pt, [])
                rolling  = rolling_by_type.get(pt)
                perf     = perf_by_type.get(pt)
                ytd      = ytd_by_type.get(pt)

                tiles             = _build_tiles(rolling, pt)
                narrative_bullets = _generate_narrative(
                    entry["suburb"], annual, perf, pt
                )

                by_type_data[pt] = {
                    "tiles":    tiles,
                    "chart": {
                        **_chart_meta(pt),
                        "data":   annual,
                        "ytd":    ytd,
                        "note":   _chart_note(annual),
                    },
                    "price_performance": perf,
                    "narrative":         narrative_bullets,
                    "data_coverage": {
                        "first_year": annual[0]["year"] if annual else None,
                        "last_year":  annual[-1]["year"] if annual else None,
                        "years":      len(annual),
                    },
                }

            if not by_type_data or all(
                v["data_coverage"]["years"] == 0
                for v in by_type_data.values()
            ):
                warnings.append(
                    f"{region} / {entry['suburb']}: no qualifying annual data"
                )

            suburb_data = {
                "suburb":          entry["suburb"],
                "region":          entry["region"],
                "postcode":        entry["postcode"],
                "slug":            to_slug(entry["suburb"]),
                "region_slug":     region_slug,
                "generated_at":    generated_at,
                "available_types": available_types,
                "by_type":         by_type_data,
            }

            suburb_filepath = os.path.join(
                output_dir, region_slug, f"{to_slug(entry['suburb'])}.json"
            )
            _write_json(suburb_filepath, suburb_data)
            suburb_files_written += 1

        # Regional by_type blocks
        reg_by_type: dict = {}
        reg_perf_by_type = _fetch_regional_perf_by_type(conn, region)

        for pt in reg_avail_types:
            annual_r  = reg_annual_by_type.get(pt, [])
            rolling_r = reg_rolling_by_type.get(pt)
            perf_r    = reg_perf_by_type.get(pt)

            # Regional YTD
            type_filter = "" if pt == "All" else f"AND property_type = '{pt}'"
            ytd_r_row = conn.execute(f"""
                SELECT COUNT(*), MEDIAN(sale_price), AVG(sale_price),
                       MEDIAN(price_per_sqm) FILTER (WHERE price_per_sqm IS NOT NULL),
                       AVG(price_per_sqm)    FILTER (WHERE price_per_sqm IS NOT NULL)
                FROM mapped_sales
                WHERE region = ? AND contract_year = {current_year}
                {type_filter}
            """, [region]).fetchone()

            ytd_r = None
            if ytd_r_row and ytd_r_row[0]:
                ytd_r = {
                    "year":         current_year,
                    "sales_count":  ytd_r_row[0],
                    "median_price": round(ytd_r_row[1]) if ytd_r_row[1] else None,
                    "avg_price":    round(ytd_r_row[2]) if ytd_r_row[2] else None,
                    "median_sqm":   round(ytd_r_row[3], 2) if ytd_r_row[3] else None,
                    "avg_sqm":      round(ytd_r_row[4], 2) if ytd_r_row[4] else None,
                    "is_ytd":       True,
                }

            reg_by_type[pt] = {
                "tiles": _build_tiles(rolling_r, pt),
                "chart": {
                    **_chart_meta(pt),
                    "data": annual_r,
                    "ytd":  ytd_r,
                    "note": _chart_note(annual_r),
                },
                "price_performance": perf_r,
                "narrative": _generate_narrative(region, annual_r, perf_r, pt),
            }

        region_data = {
            "region":          region,
            "slug":            region_slug,
            "generated_at":    generated_at,
            "data_to":         reg_rolling_by_type.get("All", {}).get("as_of"),
            "available_types": reg_avail_types,
            "by_type":         reg_by_type,
            "suburbs":         suburb_cards,
        }

        region_filepath = os.path.join(output_dir, region_slug, "region.json")
        _write_json(region_filepath, region_data)

        reg_rolling_all = reg_rolling_by_type.get("All", {})
        index_data["regions"].append({
            "region":          region,
            "slug":            region_slug,
            "suburb_count":    len(region_suburbs),
            "available_types": reg_avail_types,
            "headline": {
                "sales_count":  reg_rolling_all.get("sales_count"),
                "median_price": reg_rolling_all.get("median_price"),
                "avg_price":    reg_rolling_all.get("avg_price"),
                "window_label": reg_rolling_all.get("window_label"),
                "as_of":        reg_rolling_all.get("as_of"),
            },
            "suburbs": suburb_cards,
        })

    index_filepath = os.path.join(output_dir, "index.json")
    _write_json(index_filepath, index_data)

    conn.close()
    elapsed     = time.time() - start
    total_files = 1 + len(regions) + suburb_files_written

    logger.info(
        "Export v2.0 complete: %d files in %.1fs (1 index + %d region + %d suburb)",
        total_files, elapsed, len(regions), suburb_files_written
    )

    return {
        "index_file":      index_filepath,
        "region_files":    len(regions),
        "suburb_files":    suburb_files_written,
        "total_files":     total_files,
        "elapsed_seconds": elapsed,
        "warnings":        warnings,
    }


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export PMR analytics to JSON v2.0")
    parser.add_argument("--db",     required=True, help="Path to DuckDB database")
    parser.add_argument("--output", required=True, help="Output directory for JSON files")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    summary = export_all(args.db, args.output)

    print(f"\nExport v2.0 summary:")
    print(f"  Index file    : {summary.get('index_file')}")
    print(f"  Region files  : {summary.get('region_files')}")
    print(f"  Suburb files  : {summary.get('suburb_files')}")
    print(f"  Total files   : {summary.get('total_files')}")
    print(f"  Time elapsed  : {summary.get('elapsed_seconds', 0):.1f}s")
    if summary.get("warnings"):
        print(f"  Warnings ({len(summary['warnings'])}):")
        for w in summary["warnings"][:20]:
            print(f"    - {w}")
        if len(summary["warnings"]) > 20:
            print(f"    ... and {len(summary['warnings']) - 20} more")
    print()
