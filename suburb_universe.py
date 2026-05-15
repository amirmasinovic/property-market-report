"""
Approved Suburb Universe
=========================
Version: v1.0

Defines the governed geographic scope for reporting.
Source: BRD v1.1 Section 29 — Approved Suburb Universe (Embedded Scope Control)

This is the single source of truth for which suburbs are in scope.
Any change to this list requires a controlled document revision (per BRD).

Each suburb entry contains:
  - region:        'Eastern Suburbs' | 'Central Coast' | 'Inner West'
  - suburb:        Canonical name (title case, as per BRD)
  - suburb_upper:  Uppercase version for matching against DAT file data
  - postcode:      Integer postcode
  - postcode_str:  Zero-padded string postcode for matching

Matching strategy:
  Primary:   suburb_upper + postcode_str (both must match)
  Fallback:  postcode_str only (used when suburb name has minor variation)

The postcode-only fallback is conservative — only applies when a postcode
belongs to exactly ONE approved suburb. Where a postcode is shared by
multiple approved suburbs, the full name+postcode match is required.
"""

from typing import Optional

# ─────────────────────────────────────────────
# Canonical suburb universe
# ─────────────────────────────────────────────

SUBURB_UNIVERSE = [
    # ── Eastern Suburbs (40) ──────────────────
    {"region": "Eastern Suburbs", "suburb": "Banksmeadow",    "postcode": 2019},
    {"region": "Eastern Suburbs", "suburb": "Beaconsfield",   "postcode": 2015},
    {"region": "Eastern Suburbs", "suburb": "Bellevue Hill",  "postcode": 2023},
    {"region": "Eastern Suburbs", "suburb": "Bondi",          "postcode": 2026},
    {"region": "Eastern Suburbs", "suburb": "Bondi Beach",    "postcode": 2026},
    {"region": "Eastern Suburbs", "suburb": "Bondi Junction", "postcode": 2022},
    {"region": "Eastern Suburbs", "suburb": "Botany",         "postcode": 2019},
    {"region": "Eastern Suburbs", "suburb": "Bronte",         "postcode": 2024},
    {"region": "Eastern Suburbs", "suburb": "Centennial Park","postcode": 2021},
    {"region": "Eastern Suburbs", "suburb": "Chifley",        "postcode": 2036},
    {"region": "Eastern Suburbs", "suburb": "Clovelly",       "postcode": 2031},
    {"region": "Eastern Suburbs", "suburb": "Coogee",         "postcode": 2034},
    {"region": "Eastern Suburbs", "suburb": "Darling Point",  "postcode": 2027},
    {"region": "Eastern Suburbs", "suburb": "Darlinghurst",   "postcode": 2010},
    {"region": "Eastern Suburbs", "suburb": "Dover Heights",  "postcode": 2030},
    {"region": "Eastern Suburbs", "suburb": "Eastlakes",      "postcode": 2018},
    {"region": "Eastern Suburbs", "suburb": "Edgecliff",      "postcode": 2027},
    {"region": "Eastern Suburbs", "suburb": "Elizabeth Bay",  "postcode": 2011},
    {"region": "Eastern Suburbs", "suburb": "Kensington",     "postcode": 2033},
    {"region": "Eastern Suburbs", "suburb": "Kingsford",      "postcode": 2032},
    {"region": "Eastern Suburbs", "suburb": "La Perouse",     "postcode": 2036},
    {"region": "Eastern Suburbs", "suburb": "Little Bay",     "postcode": 2036},
    {"region": "Eastern Suburbs", "suburb": "Malabar",        "postcode": 2036},
    {"region": "Eastern Suburbs", "suburb": "Maroubra",       "postcode": 2035},
    {"region": "Eastern Suburbs", "suburb": "Mascot",         "postcode": 2020},
    {"region": "Eastern Suburbs", "suburb": "Matraville",     "postcode": 2036},
    {"region": "Eastern Suburbs", "suburb": "Moore Park",     "postcode": 2021},
    {"region": "Eastern Suburbs", "suburb": "Paddington",     "postcode": 2021},
    {"region": "Eastern Suburbs", "suburb": "Pagewood",       "postcode": 2035},
    {"region": "Eastern Suburbs", "suburb": "Potts Point",    "postcode": 2011},
    {"region": "Eastern Suburbs", "suburb": "Queens Park",    "postcode": 2022},
    {"region": "Eastern Suburbs", "suburb": "Randwick",       "postcode": 2031},
    {"region": "Eastern Suburbs", "suburb": "Rose Bay",       "postcode": 2029},
    {"region": "Eastern Suburbs", "suburb": "Rosebery",       "postcode": 2018},
    {"region": "Eastern Suburbs", "suburb": "Rushcutters Bay","postcode": 2011},
    {"region": "Eastern Suburbs", "suburb": "Surry Hills",    "postcode": 2010},
    {"region": "Eastern Suburbs", "suburb": "Tamarama",       "postcode": 2026},
    {"region": "Eastern Suburbs", "suburb": "Vaucluse",       "postcode": 2030},
    {"region": "Eastern Suburbs", "suburb": "Watsons Bay",    "postcode": 2030},
    {"region": "Eastern Suburbs", "suburb": "Woollahra",      "postcode": 2025},

    # ── Central Coast (26) ───────────────────
    {"region": "Central Coast", "suburb": "Avoca Beach",      "postcode": 2251},
    {"region": "Central Coast", "suburb": "Bateau Bay",       "postcode": 2261},
    {"region": "Central Coast", "suburb": "Copacabana",       "postcode": 2251},
    {"region": "Central Coast", "suburb": "Erina",            "postcode": 2250},
    {"region": "Central Coast", "suburb": "Ettalong Beach",   "postcode": 2257},
    {"region": "Central Coast", "suburb": "Gosford",          "postcode": 2250},
    {"region": "Central Coast", "suburb": "Green Point",      "postcode": 2251},
    {"region": "Central Coast", "suburb": "Kariong",          "postcode": 2250},
    {"region": "Central Coast", "suburb": "Killcare",         "postcode": 2257},
    {"region": "Central Coast", "suburb": "Kincumber",        "postcode": 2251},
    {"region": "Central Coast", "suburb": "Lake Haven",       "postcode": 2263},
    {"region": "Central Coast", "suburb": "Lisarow",          "postcode": 2250},
    {"region": "Central Coast", "suburb": "MacMasters Beach", "postcode": 2251},
    {"region": "Central Coast", "suburb": "Narara",           "postcode": 2250},
    {"region": "Central Coast", "suburb": "Norah Head",       "postcode": 2263},
    {"region": "Central Coast", "suburb": "Ourimbah",         "postcode": 2258},
    {"region": "Central Coast", "suburb": "Pearl Beach",      "postcode": 2256},
    {"region": "Central Coast", "suburb": "Shelly Beach",     "postcode": 2261},
    {"region": "Central Coast", "suburb": "Terrigal",         "postcode": 2260},
    {"region": "Central Coast", "suburb": "The Entrance",     "postcode": 2261},
    {"region": "Central Coast", "suburb": "Toukley",          "postcode": 2263},
    {"region": "Central Coast", "suburb": "Tumbi Umbi",       "postcode": 2261},
    {"region": "Central Coast", "suburb": "Tuggerah",         "postcode": 2259},
    {"region": "Central Coast", "suburb": "Umina Beach",      "postcode": 2257},
    {"region": "Central Coast", "suburb": "Woy Woy",          "postcode": 2256},
    {"region": "Central Coast", "suburb": "Wyong",            "postcode": 2259},

    # ── Inner West (26) ──────────────────────
    {"region": "Inner West", "suburb": "Annandale",   "postcode": 2038},
    {"region": "Inner West", "suburb": "Ashfield",    "postcode": 2131},
    {"region": "Inner West", "suburb": "Balmain",     "postcode": 2041},
    {"region": "Inner West", "suburb": "Camperdown",  "postcode": 2050},
    {"region": "Inner West", "suburb": "Chiswick",    "postcode": 2046},
    {"region": "Inner West", "suburb": "Croydon",     "postcode": 2132},
    {"region": "Inner West", "suburb": "Croydon Park","postcode": 2133},
    {"region": "Inner West", "suburb": "Drummoyne",   "postcode": 2047},
    {"region": "Inner West", "suburb": "Dulwich Hill","postcode": 2203},
    {"region": "Inner West", "suburb": "Enmore",      "postcode": 2042},
    {"region": "Inner West", "suburb": "Erskineville", "postcode": 2043},
    {"region": "Inner West", "suburb": "Five Dock",   "postcode": 2046},
    {"region": "Inner West", "suburb": "Glebe",       "postcode": 2037},
    {"region": "Inner West", "suburb": "Haberfield",  "postcode": 2045},
    {"region": "Inner West", "suburb": "Leichhardt",  "postcode": 2040},
    {"region": "Inner West", "suburb": "Lewisham",    "postcode": 2049},
    {"region": "Inner West", "suburb": "Lilyfield",   "postcode": 2040},
    {"region": "Inner West", "suburb": "Marrickville", "postcode": 2204},
    {"region": "Inner West", "suburb": "Newtown",     "postcode": 2042},
    {"region": "Inner West", "suburb": "Petersham",   "postcode": 2049},
    {"region": "Inner West", "suburb": "Rodd Point",  "postcode": 2046},
    {"region": "Inner West", "suburb": "Rozelle",     "postcode": 2039},
    {"region": "Inner West", "suburb": "Russell Lea", "postcode": 2046},
    {"region": "Inner West", "suburb": "St Peters",   "postcode": 2044},
    {"region": "Inner West", "suburb": "Stanmore",    "postcode": 2048},
    {"region": "Inner West", "suburb": "Summer Hill", "postcode": 2130},
]

# ─────────────────────────────────────────────
# Derived lookups (built once at import time)
# ─────────────────────────────────────────────

# Primary lookup: (SUBURB_UPPER, postcode_str) → suburb entry
_PRIMARY_LOOKUP: dict[tuple[str, str], dict] = {}

# Postcode-only lookup: postcode_str → suburb entry
# Only populated for postcodes that map to exactly ONE approved suburb
_POSTCODE_ONLY_LOOKUP: dict[str, dict] = {}

# Known name variations in the DAT files → canonical suburb_upper
# Add entries here when new variations are discovered in the data
_NAME_VARIANTS: dict[str, str] = {
    "WATSONS BAY":     "WATSONS BAY",    # DAT uses WATSONS (no apostrophe) - same
    "ST. PETERS":      "ST PETERS",
    "MACMASTERS BEACH":"MACMASTERS BEACH",
    "MAC MASTERS BEACH":"MACMASTERS BEACH",
}

def _build_lookups():
    from collections import defaultdict
    postcode_to_entries = defaultdict(list)

    for entry in SUBURB_UNIVERSE:
        suburb_upper = entry["suburb"].upper()
        postcode_str = str(entry["postcode"])
        enriched = {**entry, "suburb_upper": suburb_upper, "postcode_str": postcode_str}

        _PRIMARY_LOOKUP[(suburb_upper, postcode_str)] = enriched
        postcode_to_entries[postcode_str].append(enriched)

    # Postcode-only fallback: only where postcode is unambiguous
    for postcode_str, entries in postcode_to_entries.items():
        if len(entries) == 1:
            _POSTCODE_ONLY_LOOKUP[postcode_str] = entries[0]
        # Where multiple suburbs share a postcode (e.g. 2026 = Bondi + Bondi Beach + Tamarama),
        # we don't add a postcode-only entry — name match is required.

_build_lookups()


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def lookup_suburb(suburb_raw: str, postcode_raw: str) -> Optional[dict]:
    """
    Look up a raw suburb name + postcode against the approved universe.

    Returns a suburb entry dict if matched, or None if not in scope.

    Matching order:
      1. Exact: suburb_upper + postcode
      2. Known variant: normalise suburb name, then retry exact match
      3. Fallback: postcode only (only for unambiguous postcodes)

    The returned dict has keys:
      region, suburb, suburb_upper, postcode, postcode_str
    """
    suburb_upper = suburb_raw.strip().upper()
    postcode_str = str(postcode_raw).strip().zfill(4)

    # 1. Exact match
    key = (suburb_upper, postcode_str)
    if key in _PRIMARY_LOOKUP:
        return _PRIMARY_LOOKUP[key]

    # 2. Known name variant
    normalised = _NAME_VARIANTS.get(suburb_upper)
    if normalised:
        key2 = (normalised, postcode_str)
        if key2 in _PRIMARY_LOOKUP:
            return _PRIMARY_LOOKUP[key2]

    # 3. Postcode-only fallback (unambiguous postcodes only)
    if postcode_str in _POSTCODE_ONLY_LOOKUP:
        return _POSTCODE_ONLY_LOOKUP[postcode_str]

    return None


def is_in_scope(suburb_raw: str, postcode_raw: str) -> bool:
    """Return True if the suburb+postcode is within the approved universe."""
    return lookup_suburb(suburb_raw, postcode_raw) is not None


def get_all_suburbs() -> list[dict]:
    """Return the complete approved suburb universe as a list of dicts."""
    return [
        {**s, "suburb_upper": s["suburb"].upper(), "postcode_str": str(s["postcode"])}
        for s in SUBURB_UNIVERSE
    ]


def get_suburbs_by_region(region: str) -> list[dict]:
    """Return all approved suburbs for a given region name."""
    return [s for s in get_all_suburbs() if s["region"] == region]


def get_regions() -> list[str]:
    """Return the list of distinct region names."""
    seen = []
    for s in SUBURB_UNIVERSE:
        if s["region"] not in seen:
            seen.append(s["region"])
    return seen


def get_ambiguous_postcodes() -> list[str]:
    """
    Return postcodes shared by more than one approved suburb.
    These require name matching — postcode alone is not sufficient.
    """
    from collections import Counter
    counts = Counter(str(s["postcode"]) for s in SUBURB_UNIVERSE)
    return [pc for pc, n in counts.items() if n > 1]


if __name__ == "__main__":
    # Quick self-test
    print(f"Total suburbs: {len(SUBURB_UNIVERSE)}")
    for region in get_regions():
        subs = get_suburbs_by_region(region)
        print(f"  {region}: {len(subs)} suburbs")

    print(f"\nAmbiguous postcodes (require name match):")
    for pc in sorted(get_ambiguous_postcodes()):
        matches = [s["suburb"] for s in get_all_suburbs() if s["postcode_str"] == pc]
        print(f"  {pc}: {', '.join(matches)}")

    print(f"\nSample lookups:")
    tests = [
        ("BONDI BEACH",   "2026"),   # exact match
        ("BONDI",         "2026"),   # different suburb, same postcode
        ("TAMARAMA",      "2026"),   # third suburb, same postcode
        ("ST PETERS",     "2044"),   # exact
        ("ST. PETERS",    "2044"),   # variant
        ("CAMERON PARK",  "2285"),   # NOT in scope
        ("MACMASTERS BEACH", "2251"),# exact
        ("PADDINGTON",    "2021"),   # exact
        ("KENSINGTON",    "2033"),   # exact (not to be confused with Kensington 2203 which doesn't exist)
    ]
    for suburb, postcode in tests:
        result = lookup_suburb(suburb, postcode)
        if result:
            print(f"  {suburb:<22} {postcode} → {result['region']} / {result['suburb']}")
        else:
            print(f"  {suburb:<22} {postcode} → NOT IN SCOPE")
