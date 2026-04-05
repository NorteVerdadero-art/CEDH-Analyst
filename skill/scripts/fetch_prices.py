#!/usr/bin/env python3
"""
fetch_prices.py — Fetch live prices from MTGStocks for a deck

Usage:
    python3 fetch_prices.py --cards deck_with_ids.json --output prices.json
    python3 fetch_prices.py --cards deck_with_ids.json --output prices.json --no-mxn

Reads a JSON array with "name" and "printId" fields.
Outputs a dict: { "Card Name": { prices dict, commander_legal, ... } }

Rate limit: 180ms between requests (confirmed safe per HANDOFF).
"""

import sys
import json
import time
import argparse
import os
import urllib.request
import urllib.error
from typing import Optional

MTGSTOCKS_BASE = "https://api.mtgstocks.com"
DELAY = 0.18  # 180ms between requests

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.mtgstocks.com/",
}


def fetch_print(print_id: int) -> Optional[dict]:
    url = f"{MTGSTOCKS_BASE}/prints/{print_id}"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  [HTTP {e.code}] printId={print_id}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [ERR] printId={print_id}: {e}", file=sys.stderr)
        return None


def extract_prices(raw: dict) -> dict:
    tcg = raw.get("tcgplayer") or {}
    ck = raw.get("cardkingdom") or {}
    scg = raw.get("starcitygames") or {}
    cm = raw.get("cardmarket") or {}
    tcgland = raw.get("tcgland") or {}
    card_legal = (raw.get("card") or {}).get("legal") or {}

    return {
        "printId": raw.get("id"),
        "slug": raw.get("slug"),
        "scryfall_id": raw.get("scryfallId"),
        "commander_legal": card_legal.get("commander", "unknown"),
        "is_gc": card_legal.get("commander") == "gc",
        "is_banned": card_legal.get("commander") == "banned",
        "prices": {
            "tcgplayer": (tcg.get("latestPrice") or {}).get("market"),
            "tcgplayer_avg": (tcg.get("latestPrice") or {}).get("avg"),
            "cardkingdom": (ck.get("latestPrice") or {}).get("avg"),
            "starcitygames": (scg.get("latestPrice") or {}).get("avg"),
            "cardmarket": (cm.get("latestPrice") or {}).get("avg"),
            "tcgland_mxn": tcgland.get("mxn"),
        },
    }


def fetch_all(cards: list) -> dict:
    """Fetch prices for all cards with a known printId. Returns name→price dict."""
    results = {}
    total = sum(1 for c in cards if c.get("printId"))
    done = 0

    for card in cards:
        name = card.get("name", "Unknown")
        pid = card.get("printId")

        if not pid:
            results[name] = {"printId": None, "prices": {}, "note": "printId_unresolved"}
            continue

        done += 1
        print(f"  [{done}/{total}] {name} (#{pid})…", file=sys.stderr, end="")
        raw = fetch_print(pid)
        if raw:
            results[name] = extract_prices(raw)
            tcg = results[name]["prices"].get("tcgplayer") or "—"
            print(f" ${tcg}", file=sys.stderr)
        else:
            results[name] = {"printId": pid, "prices": {}, "note": "fetch_failed"}
            print(" FAILED", file=sys.stderr)

        time.sleep(DELAY)

    return results


def main():
    parser = argparse.ArgumentParser(description="Fetch MTGStocks prices for a deck")
    parser.add_argument("--cards", "-c", required=True,
                        help="deck_with_ids.json (array with name + printId)")
    parser.add_argument("--output", "-o", help="Output JSON file (default: stdout)")
    args = parser.parse_args()

    with open(args.cards) as f:
        cards = json.load(f)

    print(f"[info] Fetching prices for {len(cards)} cards…", file=sys.stderr)
    prices = fetch_all(cards)

    # Summary
    resolved = sum(1 for v in prices.values() if v.get("prices", {}).get("tcgplayer"))
    total_usd = sum(
        float(v["prices"].get("tcgplayer") or 0) for v in prices.values()
    )
    print(f"[done] {resolved} prices fetched  |  Total TCGPlayer: ${total_usd:,.2f}", file=sys.stderr)

    output = json.dumps(prices, indent=2, ensure_ascii=False)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output)
        print(f"[saved] {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
