#!/usr/bin/env python3
"""
fix_print_ids.py — Find correct MTGStocks printIds for 404 cards.

Strategy (v2): Use MTGStocks card_sets API to find prints by set, matching by
card name. Far more efficient than scanning a blind range.

  1. GET /card_sets          → list of {id, name, slug, ...}
  2. GET /card_sets/{setId}  → list of prints in that set, each with {id, name}
  3. Match by card name → get correct printId

Falls back to range-scan only if card_sets is unavailable.
"""

import json
import time
import sys
import urllib.request
import urllib.error

DECK_FILE = "data/deck_cards.json"
DELAY = 0.4   # 400ms between requests — conservative to avoid rate limiting

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.mtgstocks.com/",
}

BROKEN_IDS = {
    20310, 21705, 25504, 25507, 64220, 64253, 76195, 85793,
    90516, 90561, 94883, 94912, 94913, 94917, 105254,
    119732, 119765, 123456,
}

# MTGStocks set slugs to search for the broken cards, ordered by priority.
# Each entry: (card_name, [set_slugs_to_try])
# Set slugs are derived from the MTGStocks card_sets endpoint.
# Slugs verified from https://api.mtgstocks.com/card_sets on 2026-04-05
CARD_SETS_TO_TRY = {
    "Liquimetal Coating":   ["7-scars-of-mirrodin", "31-mirrodin", "62-magic-2012-m12"],
    "Ichor Wellspring":     ["6-mirrodin-besieged"],
    "Adaptive Automaton":   ["62-magic-2012-m12"],
    "Buried Ruin":          ["62-magic-2012-m12"],
    "Ash Barrens":          ["266-commander-2016"],
    "Metallic Mimic":       ["267-aether-revolt"],
    "Mystic Forge":         ["core-set-2020", "m20"],   # slug TBD — fallback scan covers this
    "Deflecting Swat":      ["350-commander-2020"],
    "Bloodline Pretender":  ["360-kaldheim"],
    "Maskwood Nexus":       ["360-kaldheim"],
    "Liquimetal Torque":    ["363-modern-horizons-2"],
    "Strike It Rich":       ["363-modern-horizons-2"],
    "Xorn":                 ["362-adventures-in-the-forgotten-realms"],
    "Wandering Archaic":    ["363-modern-horizons-2"],
    "Reckless Handling":    ["813-the-brothers-war"],
    "Into the Fire":        ["1504-commander-the-lord-of-the-rings-tales-of-middle-earth"],
    "The One Ring":         ["1471-the-lord-of-the-rings-tales-of-middle-earth"],
    "Magda, the Hoardmaster": ["1901-outlaws-of-thunder-junction"],
}


def fetch_json(url, retries=3):
    """Fetch JSON from URL with exponential backoff on 429."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"    429 rate-limited, waiting {wait}s…", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"    HTTP {e.code} for {url}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"    ERR: {e}", file=sys.stderr)
            return None
    return None


def get_all_card_sets():
    """Fetch the full list of MTGStocks card sets."""
    print("Fetching card_sets list…", file=sys.stderr)
    data = fetch_json("https://api.mtgstocks.com/card_sets")
    if not data:
        return {}
    # Build slug → set_id map
    slug_map = {}
    for s in (data if isinstance(data, list) else []):
        slug = s.get("slug") or s.get("name", "").lower().replace(" ", "-")
        sid = s.get("id")
        if slug and sid:
            slug_map[slug] = sid
    print(f"  → {len(slug_map)} sets indexed", file=sys.stderr)
    return slug_map


def get_prints_in_set(set_id):
    """Fetch all prints for a given MTGStocks set ID."""
    data = fetch_json(f"https://api.mtgstocks.com/card_sets/{set_id}")
    time.sleep(DELAY)
    if not data:
        return []
    # Response may be {"prints": [...]} or a list directly
    if isinstance(data, list):
        return data
    return data.get("prints", data.get("cards", []))


def find_by_card_sets(name, slug_map):
    """Try to find the correct printId using card_sets API."""
    slugs = CARD_SETS_TO_TRY.get(name, [])
    for slug in slugs:
        set_id = slug_map.get(slug)
        if not set_id:
            # Slugs in map are "{id}-{name}" — try exact prefix or substring match
            matches = [k for k in slug_map if k == slug or k.endswith(f"-{slug}") or slug in k]
            if matches:
                set_id = slug_map[matches[0]]
                print(f"  Slug match: '{slug}' → '{matches[0]}' (id={set_id})", file=sys.stderr)
        if not set_id:
            print(f"  Set not found in slug_map: {slug}", file=sys.stderr)
            continue

        print(f"  Searching set '{slug}' (id={set_id})…", file=sys.stderr)
        prints = get_prints_in_set(set_id)
        if not prints:
            print(f"    No prints returned", file=sys.stderr)
            continue

        for p in prints:
            p_name = (p.get("name") or "").strip()
            if p_name.lower() == name.lower():
                pid = p.get("id") or p.get("print_id")
                if pid:
                    legal = (p.get("card") or {}).get("legal", {}).get("commander", "?")
                    print(f"  ✓ FOUND in '{slug}': {name} → printId={pid}  (commander_legal={legal})", file=sys.stderr)
                    return pid

        print(f"    '{name}' not found in set '{slug}' ({len(prints)} prints)", file=sys.stderr)

    return None



# ── Main ───────────────────────────────────────────────────────────────────────
with open(DECK_FILE) as f:
    cards = json.load(f)

failed = {c["name"]: c["printId"] for c in cards if c.get("printId") in BROKEN_IDS}
print(f"Fixing {len(failed)} broken print IDs…\n", file=sys.stderr)

# Try card_sets approach first
slug_map = get_all_card_sets()
use_card_sets = bool(slug_map)

fixes = {}
for name, old_id in sorted(failed.items(), key=lambda x: x[1]):
    print(f"\n[{name}] old={old_id}", file=sys.stderr)
    new_id = None

    if use_card_sets:
        new_id = find_by_card_sets(name, slug_map)

    # No fallback scan — it triggers rate limiting on cards not tracked by MTGStocks.
    # Cards not found via card_sets are likely not tracked (cheap commons/uncommons).

    if new_id:
        fixes[name] = new_id
    else:
        print(f"  ✗ NOT FOUND", file=sys.stderr)

# ── Apply fixes ───────────────────────────────────────────────────────────────
if fixes:
    print(f"\nApplying {len(fixes)} fixes to {DECK_FILE}…", file=sys.stderr)
    patched = 0
    for card in cards:
        if card["name"] in fixes:
            old = card["printId"]
            card["printId"] = fixes[card["name"]]
            print(f"  {card['name']}: {old} → {card['printId']}", file=sys.stderr)
            patched += 1
    with open(DECK_FILE, "w") as f:
        json.dump(cards, f, indent=2, ensure_ascii=False)
    print(f"\nPatched {patched} cards in {DECK_FILE}", file=sys.stderr)

    cache_file = "data/mtgstocks_id_cache.json"
    try:
        with open(cache_file) as f:
            cache = json.load(f)
        for name, pid in fixes.items():
            cache[name] = pid
        with open(cache_file, "w") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        print(f"Updated {cache_file}", file=sys.stderr)
    except FileNotFoundError:
        pass

not_found = [n for n in failed if n not in fixes]
if not_found:
    print(f"\n✗ Still unresolved ({len(not_found)}):", file=sys.stderr)
    for n in not_found:
        print(f"  {n}", file=sys.stderr)
    print("\nThese cards may not be tracked by MTGStocks or need manual ID lookup.", file=sys.stderr)
