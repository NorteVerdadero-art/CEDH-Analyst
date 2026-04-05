#!/usr/bin/env python3
"""
fetch_combos.py — Fetch combo data from Commander Spellbook (server-side, no CORS)

Uses POST /find-my-combos to get all combos possible with a given deck,
plus "almost included" combos (missing only 1 card).

API schema confirmed at: https://backend.commanderspellbook.com/schema/

Usage:
    python3 fetch_combos.py --cards decklist.txt --commander "Magda, Brazen Outlaw" --output combos.json
    python3 fetch_combos.py --cards deck_with_ids.json --commander "Magda, Brazen Outlaw" --output combos.json
    python3 fetch_combos.py --cards decklist.txt --commander "Magda, Brazen Outlaw" --almost --output combos.json

Input:
    --cards accepts either:
      - A .txt decklist (one card per line, MTGO/MTGA formats supported)
      - A .json file (array of objects with "name" field, e.g. deck_with_ids.json)
"""

import sys
import json
import re
import time
import argparse
import os
import urllib.request
import urllib.error

# ── Constants ──────────────────────────────────────────────────────────────────
SPELLBOOK_BASE = "https://backend.commanderspellbook.com"
USER_AGENT = "CEDHDeckAnalyst/1.0"

# bracketTag values from Commander Spellbook (confirmed from /schema/)
# R=Ruthless, S=Spicy, P=Powerful, O=Oddball, C=Core, E=Exhibition, B=Banned
BRACKET_TAG_MAP = {
    "R": "Ruthless (B4)",
    "S": "Spicy (B3-4)",
    "P": "Powerful (B3)",
    "O": "Oddball",
    "C": "Core (B2)",
    "E": "Exhibition (B1-2)",
    "B": "Banned",
}

# Zone location codes → human-readable
ZONE_MAP = {
    "B": "Battlefield",
    "H": "Hand",
    "G": "Graveyard",
    "E": "Exile",
    "L": "Library",
    "C": "Command Zone",
}

# Cards to skip (custom/joke cards)
INVALID_CARDS = {
    "fire nation turret",
    "knuckles the echidna",
    "rms titanic",
    "vivi's thunder magic",
    "ya viene el coco",
}


# ── Input parsing ──────────────────────────────────────────────────────────────

def parse_decklist_txt(path: str) -> list[str]:
    """Parse .txt decklist into list of card names."""
    names = []
    with open(path) as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            line = re.sub(r"^\d+x?\s+", "", line)
            line = re.sub(r"\s+\([A-Z0-9]+\)\s*\d*$", "", line)
            line = re.sub(r"\s+#\S+.*$", "", line)
            name = line.strip()
            if name and name.lower() not in INVALID_CARDS:
                names.append(name)
    return names


def parse_deck_json(path: str) -> list[str]:
    """Parse deck_with_ids.json into list of card names."""
    with open(path) as f:
        cards = json.load(f)
    return [
        c["name"] for c in cards
        if "name" in c and c["name"].lower() not in INVALID_CARDS
    ]


def load_card_names(path: str) -> list[str]:
    """Auto-detect format and return card names."""
    if path.endswith(".json"):
        return parse_deck_json(path)
    return parse_decklist_txt(path)


# ── Commander Spellbook API ────────────────────────────────────────────────────

def call_find_my_combos(
    main_cards: list[str],
    commanders: list[str],
    limit: int = 200,
) -> dict:
    """
    POST /find-my-combos

    Request body (DeckRequest schema):
      {
        "main": [{"card": "Sol Ring"}, ...],
        "commanders": [{"card": "Magda, Brazen Outlaw"}]
      }

    Returns the raw API response dict.
    """
    payload = json.dumps({
        "main": [{"card": name} for name in main_cards],
        "commanders": [{"card": name} for name in commanders],
    }).encode("utf-8")

    url = f"{SPELLBOOK_BASE}/find-my-combos?limit={limit}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[spellbook] HTTP {e.code}: {body}", file=sys.stderr)
        raise
    except Exception as e:
        print(f"[spellbook] Error: {e}", file=sys.stderr)
        raise


# ── Response normalization ─────────────────────────────────────────────────────

def normalize_variant(v: dict) -> dict:
    """Normalize a Variant object into a clean combo record."""
    uses = v.get("uses", [])
    produces = v.get("produces", [])
    requires = v.get("requires", [])

    # Card names with zone info
    cards_used = []
    for u in uses:
        card = u.get("card", {})
        zones = [ZONE_MAP.get(z, z) for z in (u.get("zoneLocations") or [])]
        cards_used.append({
            "name": card.get("name", "?"),
            "zones": zones,
            "battlefield_state": u.get("battlefieldCardState", "") or None,
            "graveyard_state": u.get("graveyardCardState", "") or None,
            "exile_state": u.get("exileCardState", "") or None,
            "library_state": u.get("libraryCardState", "") or None,
        })

    # Effects produced
    effects = []
    for p in produces:
        feature = p.get("feature", {})
        effects.append(feature.get("name", "?"))

    # Requirements (non-card prerequisites)
    reqs = []
    for r in requires:
        tmpl = r.get("template", {})
        reqs.append(tmpl.get("name", "?"))

    bracket_tag = v.get("bracketTag", "?")

    return {
        "id": v.get("id"),
        "combo_ids": [c["id"] for c in (v.get("of") or v.get("includes") or [])],
        "cards": cards_used,
        "card_names": [c["name"] for c in cards_used],
        "effects": effects,
        "requirements": reqs,
        "description": v.get("description", ""),
        "mana_needed": v.get("manaNeeded", "") or None,
        "bracket_tag": bracket_tag,
        "bracket_label": BRACKET_TAG_MAP.get(bracket_tag, "Unknown"),
        "popularity": v.get("popularity"),
        "status": v.get("status", "OK"),
        "identity": v.get("identity", ""),
        "legal_commander": (v.get("legalities") or {}).get("commander", True),
        "prices": v.get("prices") or {},
        "spoiler": v.get("spoiler", False),
        "notes": v.get("notes", "") or None,
    }


def process_response(raw: dict, include_almost: bool = False) -> dict:
    """
    Process the raw find-my-combos response into a structured output.

    The API returns:
      {
        "count": null,
        "next": null,
        "previous": null,
        "results": {  ← single FindMyCombosResponse, not a list
          "identity": "R",
          "included": [...],
          "almostIncluded": [...],
          "almostIncludedByAddingColors": [...],
          ...
        }
      }
    """
    results = raw.get("results", {})

    # Handle both paginated list (future API) and single object (current)
    if isinstance(results, list):
        r = results[0] if results else {}
    else:
        r = results

    included_raw = r.get("included", [])
    almost_raw = r.get("almostIncluded", [])
    almost_colors_raw = r.get("almostIncludedByAddingColors", [])

    included = [normalize_variant(v) for v in included_raw]
    almost = [normalize_variant(v) for v in almost_raw] if include_almost else []
    almost_colors = [normalize_variant(v) for v in almost_colors_raw] if include_almost else []

    # Summary
    summary = {
        "color_identity": r.get("identity", ""),
        "included_count": len(included),
        "almost_included_count": len(almost_raw),
        "almost_included_by_color_count": len(almost_colors_raw),
    }

    # Group included combos by bracketTag
    by_bracket = {}
    for combo in included:
        tag = combo["bracket_tag"]
        by_bracket.setdefault(tag, []).append(combo)

    return {
        "summary": summary,
        "included": included,
        "almost_included": almost if include_almost else None,
        "almost_included_by_adding_colors": almost_colors if include_almost else None,
        "included_by_bracket": {
            tag: {
                "label": BRACKET_TAG_MAP.get(tag, "Unknown"),
                "count": len(combos),
                "combos": combos,
            }
            for tag, combos in sorted(by_bracket.items())
        },
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch Commander Spellbook combos for a deck (server-side)"
    )
    parser.add_argument(
        "--cards", "-c", required=True,
        help="Decklist file (.txt) or deck_with_ids.json",
    )
    parser.add_argument(
        "--commander", "-C", required=True,
        help='Commander name, e.g. "Magda, Brazen Outlaw"',
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON file (default: stdout)",
    )
    parser.add_argument(
        "--almost", "-a",
        action="store_true",
        help="Also include 'almost included' combos (missing 1 card)",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int, default=200,
        help="Max results per page (default: 200)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    # Load card names
    card_names = load_card_names(args.cards)
    if not card_names:
        print("[error] No cards loaded from input", file=sys.stderr)
        sys.exit(1)

    # Parse commander(s) — support comma-separated for partner commanders
    commanders = [c.strip() for c in args.commander.split(" // ")]

    # Remove commander from main if present (Spellbook wants them separate)
    commander_set = {c.lower() for c in commanders}
    main_cards = [n for n in card_names if n.lower() not in commander_set]

    print(
        f"[info] Deck: {len(main_cards)} main cards + {len(commanders)} commander(s)",
        file=sys.stderr,
    )
    print(f"[info] Commander(s): {', '.join(commanders)}", file=sys.stderr)
    print(f"[info] Querying Commander Spellbook…", file=sys.stderr)

    # Fetch
    raw = call_find_my_combos(main_cards, commanders, limit=args.limit)

    # Process
    output_data = process_response(raw, include_almost=args.almost)

    summary = output_data["summary"]
    print(
        f"[done] {summary['included_count']} combos included  |  "
        f"{summary['almost_included_count']} almost included  |  "
        f"Identity: {summary['color_identity']}",
        file=sys.stderr,
    )

    if args.verbose and output_data["included"]:
        print("\n── Included combos ──", file=sys.stderr)
        for combo in output_data["included"]:
            cards_str = " + ".join(combo["card_names"])
            print(
                f"  [{combo['bracket_tag']}] {cards_str}\n"
                f"      → {', '.join(combo['effects'][:3])}",
                file=sys.stderr,
            )

    # Output
    result_json = json.dumps(output_data, indent=2, ensure_ascii=False)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            f.write(result_json)
        print(f"[saved] {args.output}", file=sys.stderr)
    else:
        print(result_json)


if __name__ == "__main__":
    main()
