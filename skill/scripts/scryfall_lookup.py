#!/usr/bin/env python3
"""
scryfall_lookup.py — Batch card data lookup from Scryfall API

Usage:
    python3 scryfall_lookup.py "Sol Ring" "Mana Crypt" "Rhystic Study"
    python3 scryfall_lookup.py --file decklist.txt
    python3 scryfall_lookup.py --file decklist.txt --output cards_data.json

Input formats for --file:
    - One card name per line
    - MTGO format: "1 Sol Ring"
    - MTGA format: "1 Sol Ring (CMR) 472"

Output: JSON array of card objects with CER-relevant fields.
"""

import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import re
import argparse


SCRYFALL_BASE = "https://api.scryfall.com"
USER_AGENT = "CEDHDeckAnalyst/1.0"
DELAY_MS = 110  # Scryfall asks for 100ms between requests


def parse_card_line(line: str) -> str:
    """Extract card name from various formats."""
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return ""
    # Remove quantity prefix: "1 Sol Ring" -> "Sol Ring"
    line = re.sub(r"^\d+x?\s+", "", line)
    # Remove set/collector info: "Sol Ring (CMR) 472" -> "Sol Ring"
    line = re.sub(r"\s+\([A-Z0-9]+\)\s*\d*$", "", line)
    # Remove tags: "Sol Ring #Ramp" -> "Sol Ring"
    line = re.sub(r"\s+#\S+", "", line)
    return line.strip()


def fetch_card(name: str) -> dict:
    """Fetch a single card from Scryfall by exact name."""
    url = f"{SCRYFALL_BASE}/cards/named?exact={urllib.parse.quote(name)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json"
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Try fuzzy search
            url_fuzzy = f"{SCRYFALL_BASE}/cards/named?fuzzy={urllib.parse.quote(name)}"
            req_fuzzy = urllib.request.Request(url_fuzzy, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json"
            })
            try:
                with urllib.request.urlopen(req_fuzzy) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except:
                return {"error": f"Card not found: {name}"}
        return {"error": f"HTTP {e.code} for {name}"}


def fetch_collection(names: list) -> list:
    """Fetch multiple cards using /cards/collection (max 75 per request)."""
    results = []
    not_found = []

    for i in range(0, len(names), 75):
        batch = names[i:i+75]
        identifiers = [{"name": n} for n in batch]
        payload = json.dumps({"identifiers": identifiers}).encode("utf-8")

        req = urllib.request.Request(
            f"{SCRYFALL_BASE}/cards/collection",
            data=payload,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results.extend(data.get("data", []))
                not_found.extend(data.get("not_found", []))
        except urllib.error.HTTPError as e:
            print(f"Error fetching batch {i//75 + 1}: HTTP {e.code}", file=sys.stderr)

        if i + 75 < len(names):
            time.sleep(DELAY_MS / 1000)

    return results, not_found


def extract_cer_fields(card: dict) -> dict:
    """Extract fields relevant for CER calculation."""
    if "error" in card:
        return card

    prices = card.get("prices", {})

    return {
        "name": card.get("name", "Unknown"),
        "mana_cost": card.get("mana_cost", ""),
        "cmc": card.get("cmc", 0),
        "type_line": card.get("type_line", ""),
        "oracle_text": card.get("oracle_text", ""),
        "colors": card.get("colors", []),
        "color_identity": card.get("color_identity", []),
        "keywords": card.get("keywords", []),
        "rarity": card.get("rarity", ""),
        "edhrec_rank": card.get("edhrec_rank", None),
        "legalities": {
            "commander": card.get("legalities", {}).get("commander", "unknown")
        },
        "price_usd": prices.get("usd"),
        "price_usd_foil": prices.get("usd_foil"),
        "scryfall_uri": card.get("scryfall_uri", ""),
        "image_uri": (card.get("image_uris") or {}).get("normal", ""),
        # Derived fields for CER
        "is_land": "Land" in card.get("type_line", ""),
        "is_creature": "Creature" in card.get("type_line", ""),
        "is_instant_sorcery": any(t in card.get("type_line", "") for t in ["Instant", "Sorcery"]),
        "is_artifact": "Artifact" in card.get("type_line", ""),
        "is_enchantment": "Enchantment" in card.get("type_line", ""),
        "has_tutor_text": any(w in (card.get("oracle_text", "").lower()) for w in ["search your library", "tutor"]),
        "has_draw_text": any(w in (card.get("oracle_text", "").lower()) for w in ["draw a card", "draw cards", "draw two", "draw three"]),
        "has_mana_production": any(w in (card.get("oracle_text", "").lower()) for w in ["add {", "add one mana", "add mana"]),
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch card data from Scryfall")
    parser.add_argument("cards", nargs="*", help="Card names to look up")
    parser.add_argument("--file", "-f", help="File with card names (one per line)")
    parser.add_argument("--output", "-o", help="Output JSON file")
    parser.add_argument("--raw", action="store_true", help="Output raw Scryfall data")
    args = parser.parse_args()

    card_names = list(args.cards) if args.cards else []

    if args.file:
        with open(args.file, "r") as f:
            for line in f:
                name = parse_card_line(line)
                if name:
                    card_names.append(name)

    if not card_names:
        print("No card names provided. Use positional args or --file.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching {len(card_names)} cards from Scryfall...", file=sys.stderr)

    if len(card_names) <= 5:
        # Use individual lookups for small batches
        results = []
        for name in card_names:
            card = fetch_card(name)
            if args.raw:
                results.append(card)
            else:
                results.append(extract_cer_fields(card))
            time.sleep(DELAY_MS / 1000)
    else:
        # Use collection endpoint for larger batches
        raw_results, not_found = fetch_collection(card_names)
        if args.raw:
            results = raw_results
        else:
            results = [extract_cer_fields(c) for c in raw_results]

        if not_found:
            print(f"Warning: {len(not_found)} cards not found:", file=sys.stderr)
            for nf in not_found:
                print(f"  - {nf}", file=sys.stderr)

    output = json.dumps(results, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Saved to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
