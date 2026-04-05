#!/usr/bin/env python3
"""
resolve_mtgstocks_ids.py — Resolve MTGStocks print IDs by card name

MTGStocks' search endpoint requires auth/CORS, so this script uses a
two-layer strategy:
  1. Cache-first: a persistent name→printId mapping seeded from deck_cards.json
  2. Scryfall-bridge: validates unknown cards and enriches with scryfall data;
     printId is marked null until manually resolved (see --add-id)

Usage:
    python3 resolve_mtgstocks_ids.py --deck decklist.txt --output deck_with_ids.json
    python3 resolve_mtgstocks_ids.py --deck decklist.txt --output deck_with_ids.json --seed data/deck_cards.json
    python3 resolve_mtgstocks_ids.py --add-id "Rhystic Study:12345"
    python3 resolve_mtgstocks_ids.py --show-cache

Input decklist formats:
    1 Sol Ring
    Sol Ring
    1x Sol Ring
    1 Sol Ring (CMR) 472
"""

import sys
import json
import time
import re
import argparse
import os
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CACHE_FILE = os.path.join(PROJECT_ROOT, "data", "mtgstocks_id_cache.json")
DEFAULT_SEED = os.path.join(PROJECT_ROOT, "data", "deck_cards.json")

SCRYFALL_BASE = "https://api.scryfall.com"
MTGSTOCKS_BASE = "https://api.mtgstocks.com"
USER_AGENT = "CEDHDeckAnalyst/1.0"
SCRYFALL_DELAY = 0.11   # 110ms — Scryfall asks for 100ms between requests
MTGSTOCKS_DELAY = 0.18  # 180ms — safe rate per HANDOFF

# Cards to silently skip (custom/joke cards from the Magda test deck)
INVALID_CARDS = {
    "fire nation turret",
    "knuckles the echidna",
    "rms titanic",
    "vivi's thunder magic",
    "ya viene el coco",
}


# ── Cache management ───────────────────────────────────────────────────────────

def load_cache() -> dict:
    """Load the name→printId cache from disk."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def seed_cache_from_deck_json(path: str, cache: dict) -> int:
    """Seed cache from a deck_cards.json file. Returns count of new entries."""
    if not os.path.exists(path):
        print(f"[warn] Seed file not found: {path}", file=sys.stderr)
        return 0
    with open(path) as f:
        cards = json.load(f)
    added = 0
    for card in cards:
        name = card.get("name", "").strip()
        pid = card.get("printId")
        if name and pid and name not in cache:
            cache[name] = pid
            added += 1
    return added


# ── Decklist parsing ───────────────────────────────────────────────────────────

def parse_decklist(path: str) -> list:
    """Parse a decklist file into a list of card names."""
    names = []
    with open(path) as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            # Skip section headers like "Commander", "Lands", etc.
            if line.startswith("//") or re.match(r"^[A-Z][a-zA-Z ]+:$", line):
                continue
            # Remove quantity prefix: "1 Sol Ring", "1x Sol Ring"
            line = re.sub(r"^\d+x?\s+", "", line)
            # Remove set/collector suffix: "Sol Ring (CMR) 472"
            line = re.sub(r"\s+\([A-Z0-9]+\)\s*\d*$", "", line)
            # Remove tags: "Sol Ring #Ramp"
            line = re.sub(r"\s+#\S+.*$", "", line)
            name = line.strip()
            if name:
                names.append(name)
    return names


# ── Scryfall lookup ────────────────────────────────────────────────────────────

def scryfall_collection(names: list) -> tuple:
    """Batch-fetch card data from Scryfall. Returns (found, not_found)."""
    all_found = []
    all_missing = []

    for i in range(0, len(names), 75):
        batch = names[i:i + 75]
        payload = json.dumps({"identifiers": [{"name": n} for n in batch]}).encode()
        req = urllib.request.Request(
            f"{SCRYFALL_BASE}/cards/collection",
            data=payload,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                all_found.extend(data.get("data", []))
                all_missing.extend(data.get("not_found", []))
        except urllib.error.HTTPError as e:
            print(f"[scryfall] HTTP {e.code} on batch {i // 75 + 1}", file=sys.stderr)
        except Exception as e:
            print(f"[scryfall] Error on batch {i // 75 + 1}: {e}", file=sys.stderr)

        if i + 75 < len(names):
            time.sleep(SCRYFALL_DELAY)

    return all_found, all_missing


def scryfall_single(name: str) -> Optional[dict]:
    """Fetch a single card from Scryfall (exact, then fuzzy)."""
    for mode in ("exact", "fuzzy"):
        url = f"{SCRYFALL_BASE}/cards/named?{mode}={urllib.parse.quote(name)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            print(f"[scryfall] HTTP {e.code} for '{name}'", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[scryfall] Error for '{name}': {e}", file=sys.stderr)
            return None
    return None


# ── MTGStocks live lookup ──────────────────────────────────────────────────────

_MTGSTOCKS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.mtgstocks.com/",
}


def fetch_mtgstocks_print(print_id: int) -> Optional[dict]:
    """Fetch print data from MTGStocks by numeric ID."""
    url = f"{MTGSTOCKS_BASE}/prints/{print_id}"
    req = urllib.request.Request(url, headers=_MTGSTOCKS_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[mtgstocks] HTTP {e.code} for printId={print_id}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[mtgstocks] Error for printId={print_id}: {e}", file=sys.stderr)
        return None


def extract_mtgstocks_data(raw: dict) -> dict:
    """Extract price + legality fields from a MTGStocks prints response."""
    tcg = raw.get("tcgplayer") or {}
    ck = raw.get("cardkingdom") or {}
    scg = raw.get("starcitygames") or {}
    cm = raw.get("cardmarket") or {}
    tcgland = raw.get("tcgland") or {}

    card_legal = (raw.get("card") or {}).get("legal") or {}
    commander_legal = card_legal.get("commander", "unknown")

    return {
        "mtgstocks_name": raw.get("name"),
        "mtgstocks_slug": raw.get("slug"),
        "mtgstocks_scryfall_id": raw.get("scryfallId"),
        "commander_legal": commander_legal,  # "legal" | "gc" | "banned"
        "is_gc": commander_legal == "gc",
        "is_banned": commander_legal == "banned",
        "prices": {
            "tcgplayer": (tcg.get("latestPrice") or {}).get("market"),
            "tcgplayer_avg": (tcg.get("latestPrice") or {}).get("avg"),
            "cardkingdom": (ck.get("latestPrice") or {}).get("avg"),
            "starcitygames": (scg.get("latestPrice") or {}).get("avg"),
            "cardmarket": (cm.get("latestPrice") or {}).get("avg"),
            "tcgland_mxn": tcgland.get("mxn"),
        },
    }


# ── Main resolution logic ──────────────────────────────────────────────────────

def resolve_deck(
    card_names: list,
    cache: dict,
    fetch_prices: bool = True,
    verbose: bool = False,
) -> tuple:
    """
    Resolve card names to full records with printId + prices.

    Returns (resolved_cards, unresolved_names).
    """
    # Deduplicate, filter invalids
    unique_names = []
    seen = set()
    skipped = []
    for name in card_names:
        low = name.lower()
        if low in INVALID_CARDS:
            skipped.append(name)
            continue
        if low not in seen:
            seen.add(low)
            unique_names.append(name)

    if skipped:
        print(f"[info] Skipped {len(skipped)} invalid/custom cards: {skipped}", file=sys.stderr)

    # Split: known vs unknown
    known, unknown = [], []
    for name in unique_names:
        # Case-insensitive cache lookup
        matched_key = next((k for k in cache if k.lower() == name.lower()), None)
        if matched_key:
            known.append((name, cache[matched_key]))
        else:
            unknown.append(name)

    if unknown:
        print(f"[info] {len(unknown)} cards not in cache — fetching from Scryfall…", file=sys.stderr)

    # Scryfall batch lookup for unknown cards
    scryfall_map = {}
    if unknown:
        found, not_found = scryfall_collection(unknown)
        for card in found:
            scryfall_map[card["name"].lower()] = card
        for nf in not_found:
            nf_name = nf.get("name", str(nf))
            print(f"[warn] Scryfall: not found → '{nf_name}'", file=sys.stderr)

    # Build resolved records
    resolved = []
    unresolved_names = []

    # Process known cards (in cache)
    for name, print_id in known:
        record = {"name": name, "printId": print_id}
        if fetch_prices and print_id:
            raw = fetch_mtgstocks_print(print_id)
            if raw:
                record.update(extract_mtgstocks_data(raw))
            time.sleep(MTGSTOCKS_DELAY)
        resolved.append(record)
        if verbose:
            print(f"  ✓ {name} (printId={print_id})", file=sys.stderr)

    # Process unknown cards
    for name in unknown:
        sf = scryfall_map.get(name.lower())
        record = {
            "name": sf["name"] if sf else name,
            "printId": None,
            "printId_unresolved": True,
        }
        if sf:
            record["scryfall_id"] = sf.get("id")
            record["cmc"] = sf.get("cmc", 0)
            record["type_line"] = sf.get("type_line", "")
            record["mana_cost"] = sf.get("mana_cost", "")
            record["rarity"] = sf.get("rarity", "")
            record["edhrec_rank"] = sf.get("edhrec_rank")
            record["legalities"] = {"commander": sf.get("legalities", {}).get("commander", "unknown")}
            record["price_usd_scryfall"] = (sf.get("prices") or {}).get("usd")
        else:
            record["scryfall_not_found"] = True

        resolved.append(record)
        unresolved_names.append(name)
        if verbose:
            print(f"  ? {name} (printId=UNRESOLVED)", file=sys.stderr)

    return resolved, unresolved_names


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Resolve MTGStocks print IDs by card name"
    )
    parser.add_argument("--deck", "-d", help="Decklist .txt file")
    parser.add_argument("--output", "-o", help="Output JSON file (default: stdout)")
    parser.add_argument(
        "--seed", "-s",
        default=DEFAULT_SEED,
        help=f"deck_cards.json to seed cache (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--no-prices",
        action="store_true",
        help="Skip live MTGStocks price fetch (faster, cache-only)",
    )
    parser.add_argument(
        "--add-id",
        metavar="NAME:ID",
        help='Manually add a print ID to cache, e.g. "Rhystic Study:12345"',
    )
    parser.add_argument(
        "--show-cache",
        action="store_true",
        help="Print the current cache and exit",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    cache = load_cache()

    # Seed from deck_cards.json if cache is empty or seed was specified
    if args.seed and os.path.exists(args.seed):
        added = seed_cache_from_deck_json(args.seed, cache)
        if added:
            save_cache(cache)
            print(f"[cache] Seeded {added} entries from {args.seed}", file=sys.stderr)

    # --show-cache
    if args.show_cache:
        print(f"Cache: {len(cache)} entries  ({CACHE_FILE})")
        for name, pid in sorted(cache.items()):
            print(f"  {pid:>8}  {name}")
        return

    # --add-id "Card Name:12345"
    if args.add_id:
        try:
            name_part, id_part = args.add_id.rsplit(":", 1)
            cache[name_part.strip()] = int(id_part.strip())
            save_cache(cache)
            print(f"[cache] Added: '{name_part.strip()}' → {int(id_part.strip())}")
        except ValueError:
            print(f"[error] --add-id format must be 'Card Name:12345'", file=sys.stderr)
            sys.exit(1)
        return

    if not args.deck:
        parser.print_help()
        sys.exit(1)

    # Parse decklist
    card_names = parse_decklist(args.deck)
    if not card_names:
        print("[error] No cards parsed from decklist", file=sys.stderr)
        sys.exit(1)

    print(f"[info] Parsed {len(card_names)} card lines from {args.deck}", file=sys.stderr)

    # Resolve
    resolved, unresolved = resolve_deck(
        card_names,
        cache,
        fetch_prices=not args.no_prices,
        verbose=args.verbose,
    )

    # Update cache with any newly confirmed names (from Scryfall canonical names)
    save_cache(cache)

    # Report
    total = len(resolved)
    n_resolved = sum(1 for c in resolved if c.get("printId") is not None)
    n_unresolved = len(unresolved)
    print(f"[done] {n_resolved}/{total} resolved  |  {n_unresolved} unresolved", file=sys.stderr)

    if unresolved:
        print("[hint] Add missing IDs with:", file=sys.stderr)
        for name in unresolved[:5]:
            print(f'  python3 {os.path.basename(__file__)} --add-id "{name}:<printId>"', file=sys.stderr)
        if len(unresolved) > 5:
            print(f"  … and {len(unresolved) - 5} more", file=sys.stderr)

    # Output
    output = json.dumps(resolved, indent=2, ensure_ascii=False)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output)
        print(f"[saved] {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
