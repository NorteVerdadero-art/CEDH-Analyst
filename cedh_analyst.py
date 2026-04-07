#!/usr/bin/env python3
"""
cedh_analyst.py — cEDH Deck Analyst CLI

Usage:
    python3 cedh_analyst.py analyze \\
        --decklist data/deck_cards.json \\
        --commander "Magda, Brazen Outlaw" \\
        --bracket 4 \\
        --output ./output/

    python3 cedh_analyst.py analyze \\
        --decklist magda.txt \\
        --commander "Magda, Brazen Outlaw" \\
        --bracket 4 \\
        --output ./output/ \\
        --no-prices

Input:
    --decklist accepts:
      - deck_cards.json  (array with name, printId, cer, tier, gc, cmc, type_line)
      - decklist.txt     (one card per line, MTGO/MTGA formats)

Output ./output/:
    report.json     — CER analysis + prices + BPS
    combos.json     — Commander Spellbook combos
    dashboard.html  — Full interactive dashboard (self-contained)
"""

import sys
import os
import json
import time
import re
import csv
import argparse
import math
import hashlib
import html as html_mod
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from typing import Optional

# ── Add skill/scripts to path ──────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "skill", "scripts"))

import fetch_combos as fc_mod
import fetch_prices as fp_mod
from generate_dashboard import generate_html as _base_generate_html

# ── Constants ──────────────────────────────────────────────────────────────────
BRACKET_NAMES = {1: "Exhibition", 2: "Core", 3: "Upgraded", 4: "Optimized", 5: "cEDH"}

# BPS thresholds per the HANDOFF
BPS_THRESHOLDS = {
    "gc_density":    {"b4_min": 4, "b5_min": 9,  "weight": 0.30},
    "cer_avg":       {"b4_min": 6.5, "b5_min": 8.0, "weight": 0.25},
    "win_speed":     {"b4_min": 6,   "b5_min": 4,   "weight": 0.20},  # turns (lower = faster)
    "tutor_density": {"b4_min": 6,   "b5_min": 10,  "weight": 0.15},
    "combo_presence":{"b4_min": 2,   "b5_min": 3,   "weight": 0.10},
}

TUTOR_PATTERNS = [
    "search your library",
    "searches your library",
]

FAST_MANA_NAMES = {
    "black lotus", "mox emerald", "mox jet", "mox pearl", "mox ruby", "mox sapphire",
    "mox opal", "chrome mox", "mox amber", "mox diamond", "lotus petal", "lotus bloom",
    "sol ring", "mana vault", "mana crypt", "grim monolith",
}

INVALID_CARDS = {
    "fire nation turret", "knuckles the echidna",
    "rms titanic", "vivi's thunder magic", "ya viene el coco",
}

# Official WotC Commander banlist (updated Sep 2024 + historical)
# Source: https://mtgcommander.net/index.php/banned-list/
BANNED_CARDS = {
    # Sep 2024 additions
    "dockside extortionist", "mana crypt", "jeweled lotus", "nadu, winged wisdom",
    # Historical bans
    "ancestral recall", "balance", "biorhythm", "black lotus",
    "braids, cabal minion", "channel", "chaos orb", "coalition victory",
    "emrakul, the aeons torn", "erayo, soratami ascendant", "falling star",
    "fastbond", "flash", "gifts ungiven", "golos, tireless pilgrim",
    "griselbrand", "hullbreacher", "iona, shield of emeria", "karakas",
    "leovold, emissary of trest", "library of alexandria", "limited resources",
    "lutri, the spellchaser", "mox emerald", "mox jet", "mox pearl",
    "mox ruby", "mox sapphire", "panoptic mirror", "paradox engine",
    "primeval titan", "prophet of kruphix", "recurring nightmare",
    "rofellos, llanowar emissary", "sundering titan", "sway of the stars",
    "sylvan primordial", "time vault", "time walk", "tinker",
    "tolarian academy", "trade secrets", "upheaval", "worldfire",
    "yawgmoth's bargain",
}


# ── Decklist loading ───────────────────────────────────────────────────────────

def _filter_cards(cards: list) -> list:
    """Apply invalid/banned filtering to a card list."""
    valid = []
    for c in cards:
        name_low = c.get("name", "").lower()
        if name_low in INVALID_CARDS:
            print(f"  [skip] invalid card: {c['name']}", file=sys.stderr)
            continue
        if name_low in BANNED_CARDS:
            print(f"  [ban] {c['name']} — BANNED (WotC Commander banlist)", file=sys.stderr)
            c["banned"] = True
        valid.append(c)
    return valid


def _parse_card_name(line: str) -> str:
    """Strip quantity/set/tag prefixes from a decklist line."""
    line = re.sub(r"^\d+x?\s+", "", line)           # "1x Sol Ring" → "Sol Ring"
    line = re.sub(r"\s+\([A-Z0-9]+\)\s*\d*$", "", line)  # remove (SET) 123
    line = re.sub(r"\s+#\S+.*$", "", line)           # remove #tags
    line = re.sub(r"\s*\*CMDR\*\s*", "", line)       # remove Moxfield *CMDR* tag
    return line.strip()


def load_decklist(path: str) -> tuple:
    """Load deck from .json, .txt, or .csv.

    Supported formats:
      .json   — deck_cards.json array (internal format with cer/tier/printId)
      .txt    — one card per line, MTGO/MTGA/plain text (quantities optional)
                Moxfield exports with *CMDR* tag auto-detect the commander
      .csv    — CSV with at minimum a 'name' column; also handles Moxfield/
                Archidekt exports (columns: Count, Name, Expansion, etc.)

    Returns (cards, detected_commander_or_None).
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".json":
        with open(path) as f:
            cards = json.load(f)
        return _filter_cards(cards), None

    if ext == ".csv":
        cards = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = [h.lower().strip() for h in (reader.fieldnames or [])]
            # Detect name column: 'name', 'card name', 'card', 'Name'
            name_col = next(
                (h for h in reader.fieldnames or []
                 if h.lower().strip() in ("name", "card name", "card")),
                None
            )
            if not name_col:
                print("[warn] CSV has no 'name' column — trying first column", file=sys.stderr)
                name_col = (reader.fieldnames or ["name"])[0]
            for row in reader:
                name = row.get(name_col, "").strip()
                if not name or name.lower() in INVALID_CARDS:
                    continue
                cards.append({"name": name})
        return _filter_cards(cards), None

    # .txt — MTGO / MTGA / plain (Moxfield exports with *CMDR* tag)
    cards = []
    detected_commander = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            if "*CMDR*" in line:
                raw_name = re.sub(r"^\d+x?\s+", "", line)
                raw_name = re.sub(r"\s*\*CMDR\*.*$", "", raw_name)
                raw_name = re.sub(r"\s+\([A-Z0-9]+\)\s*\d*$", "", raw_name).strip()
                if detected_commander:
                    detected_commander = f"{detected_commander} // {raw_name}"
                else:
                    detected_commander = raw_name
            name = _parse_card_name(line)
            if name and name.lower() not in INVALID_CARDS:
                cards.append({"name": name})
    return _filter_cards(cards), detected_commander


# ── Price enrichment ───────────────────────────────────────────────────────────

def enrich_with_prices(cards: list, verbose: bool = False) -> dict:
    """Fetch MTGStocks prices for all cards that have a printId. Returns name→prices map."""
    print(f"\n[prices] Fetching from MTGStocks…", file=sys.stderr)
    prices_map = fp_mod.fetch_all(cards)
    return prices_map


def apply_prices_to_cards(cards: list, prices_map: dict) -> list:
    """Merge price data into card dicts. Ban status comes exclusively from BANNED_CARDS."""
    for card in cards:
        name = card.get("name", "")
        pdata = prices_map.get(name, {})
        if pdata:
            card["prices"] = pdata.get("prices", {})
            # Do NOT use MTGStocks commander_legal for ban/gc determination —
            # their data can be stale or incorrect. We rely on BANNED_CARDS only.
            tcg = (pdata.get("prices") or {}).get("tcgplayer")
            if tcg:
                card["price_usd"] = float(tcg)
    return cards


# ── BPS Calculation ────────────────────────────────────────────────────────────

def estimate_win_speed(cards: list) -> int:
    """Estimate average win turn from deck composition. Returns turn number."""
    fast_mana = sum(1 for c in cards
                    if c.get("name", "").lower() in FAST_MANA_NAMES
                    or (c.get("cmc", 99) == 0 and "artifact" in (c.get("type_line") or "").lower()))
    tutors = sum(1 for c in cards
                 if any(p in (c.get("oracle_text") or "").lower() for p in TUTOR_PATTERNS))
    base_turn = 7
    base_turn -= min(3, fast_mana // 2)
    base_turn -= min(2, tutors // 3)
    return max(2, base_turn)


def calculate_bps(cards: list, combos_data: dict, bracket: int, win_curve: list = None) -> dict:
    """
    Calculate Bracket Proximity Score toward next bracket.

    BPS dimensions (toward B5):
    - GC Density:    30% — # of Game Changers in deck
    - CER Average:   25% — average CER score
    - Win Speed:     20% — estimated average win turn
    - Tutor Density: 15% — # of tutors in deck
    - Combo Presence:10% — # of confirmed combos
    """
    target = bracket + 1  # e.g. B4 → scoring toward B5

    # 1. GC Density
    gc_count = sum(1 for c in cards if c.get("gc") or c.get("is_gc_live"))
    b4_gc, b5_gc = 4, 9
    if target == 5:
        gc_score = min(1.0, (gc_count - b4_gc) / (b5_gc - b4_gc)) if gc_count >= b4_gc else 0.0
    else:
        gc_score = min(1.0, gc_count / b4_gc)
    gc_score = max(0.0, gc_score)

    # 2. CER Average — thresholds calibrated to synthetic CER range (no WIR/PIR raw data)
    cer_values = [c.get("cer", 0) for c in cards if c.get("cer", 0) > 0]
    avg_cer = sum(cer_values) / len(cer_values) if cer_values else 0
    b4_cer, b5_cer = 4.5, 6.5
    if target == 5:
        cer_score = min(1.0, max(0.0, (avg_cer - b4_cer) / (b5_cer - b4_cer)))
    else:
        cer_score = min(1.0, max(0.0, avg_cer / b4_cer))

    # 3. Win Speed — use win_curve T50 (first turn P(win)>=50%) when available
    b4_turn, b5_turn = 6, 4
    if win_curve:
        avg_turn = next((i + 1 for i, p in enumerate(win_curve) if p >= 50), 8)
    else:
        avg_turn = estimate_win_speed(cards)
    if target == 5:
        speed_score = min(1.0, max(0.0, (b4_turn - avg_turn) / (b4_turn - b5_turn)))
    else:
        speed_score = min(1.0, max(0.0, (10 - avg_turn) / (10 - b4_turn)))

    # 4. Tutor Density
    tutor_count = sum(1 for c in cards if
        any(p in (c.get("oracle_text") or "").lower() for p in TUTOR_PATTERNS)
        or c.get("has_tutor_text")
    )
    b4_tutors, b5_tutors = 6, 10
    if target == 5:
        tutor_score = min(1.0, max(0.0, (tutor_count - b4_tutors) / (b5_tutors - b4_tutors)))
    else:
        tutor_score = min(1.0, tutor_count / b4_tutors)

    # 5. Combo Presence
    combo_count = len((combos_data or {}).get("included", []))
    b4_combos, b5_combos = 2, 3
    if target == 5:
        combo_score = min(1.0, max(0.0, (combo_count - b4_combos) / (b5_combos - b4_combos)))
    else:
        combo_score = min(1.0, combo_count / b4_combos)

    # Weighted BPS
    bps = (
        gc_score * 0.30 +
        cer_score * 0.25 +
        speed_score * 0.20 +
        tutor_score * 0.15 +
        combo_score * 0.10
    )

    return {
        "bracket": bracket,
        "target_bracket": target,
        "bps_pct": round(bps * 100, 1),
        "dimensions": {
            "gc_density": {
                "value": gc_count,
                "score_pct": round(gc_score * 100, 1),
                "b4_min": b4_gc, "b5_min": b5_gc, "weight": 0.30,
                "contribution": round(gc_score * 0.30 * 100, 1),
            },
            "cer_avg": {
                "value": round(avg_cer, 2),
                "score_pct": round(cer_score * 100, 1),
                "b4_min": b4_cer, "b5_min": b5_cer, "weight": 0.25,
                "contribution": round(cer_score * 0.25 * 100, 1),
            },
            "win_speed": {
                "value": f"T{avg_turn}",
                "score_pct": round(speed_score * 100, 1),
                "b4_min": f"T{b4_turn}", "b5_min": f"T{b5_turn}", "weight": 0.20,
                "contribution": round(speed_score * 0.20 * 100, 1),
            },
            "tutor_density": {
                "value": tutor_count,
                "score_pct": round(tutor_score * 100, 1),
                "b4_min": b4_tutors, "b5_min": b5_tutors, "weight": 0.15,
                "contribution": round(tutor_score * 0.15 * 100, 1),
            },
            "combo_presence": {
                "value": combo_count,
                "score_pct": round(combo_score * 100, 1),
                "b4_min": b4_combos, "b5_min": b5_combos, "weight": 0.10,
                "contribution": round(combo_score * 0.10 * 100, 1),
            },
        },
    }


# ── Win Contribution Index ─────────────────────────────────────────────────────

def calculate_wci(cards: list, combos_data: dict, edhrec_synergy: dict = None) -> list:
    """
    Win Contribution Index (0-100%) per card.

    Components:
      50% — combo score: ALL included combos, log-weighted by popularity
      30% — EDHREC synergy score (normalized [-1,1] → [0,1]; 0.5 if not on EDHREC)
      20% — enabler role: tutors, fast mana, GC, CER
    """
    edhrec_synergy = edhrec_synergy or {}
    included = (combos_data or {}).get("included", [])

    # Build log-weighted combo presence map
    total_weight = sum(math.log1p(c.get("popularity") or 0) for c in included)
    combo_weight_map: dict = {}
    for combo in included:
        w = math.log1p(combo.get("popularity") or 0)
        for c in combo.get("cards", []):
            n = c["name"].lower()
            combo_weight_map[n] = combo_weight_map.get(n, 0.0) + w

    enriched = []
    for card in cards:
        name_low = card.get("name", "").lower()
        oracle = (card.get("oracle_text") or "").lower()
        cmc    = card.get("cmc", 0)
        tline  = (card.get("type_line") or "").lower()
        cer    = card.get("cer", 0)

        # Component 1: combo presence (50%)
        combo_score = (min(1.0, combo_weight_map.get(name_low, 0.0) / total_weight)
                       if total_weight > 0 else 0.0)

        # Component 2: EDHREC synergy (30%) — [-1,1] → [0,1]; 0.5 neutral if absent
        raw_syn = edhrec_synergy.get(name_low)
        syn_score = (float(raw_syn) + 1.0) / 2.0 if raw_syn is not None else 0.5

        # Component 3: enabler role (20%)
        enabler = 0.0
        if any(p in oracle for p in TUTOR_PATTERNS):
            enabler = max(enabler, 0.80)
        if name_low in FAST_MANA_NAMES or (cmc == 0 and "artifact" in tline):
            enabler = max(enabler, 0.65)
        if card.get("gc"):
            enabler = max(enabler, 0.55)
        if cmc <= 2 and "artifact" in tline and ("add" in oracle or "mana" in oracle):
            enabler = max(enabler, 0.45)
        if cer > 0:
            enabler = max(enabler, cer / 10.0)

        wci = round(min(100.0, (combo_score * 0.50 + syn_score * 0.30 + enabler * 0.20) * 100), 1)
        enriched.append({**card, "wci": wci})

    return enriched


# ── Win probability curve ───────────────────────────────────────────────────────

def _p_at_least_one(N: int, K: int, n: int) -> float:
    """Hypergeometric P(X >= 1): at least 1 of K cards seen in n draws from N deck."""
    if K <= 0 or n <= 0 or N <= 0:
        return 0.0
    if K >= N or n >= N:
        return 1.0
    draw = min(n, N)
    try:
        p_zero = math.comb(N - K, draw) / math.comb(N, draw)
        return round(max(0.0, min(1.0, 1.0 - p_zero)), 4)
    except (ZeroDivisionError, ValueError):
        return 0.0


def calculate_win_curve(cards: list, combos_data: dict, deck_size: int = 99) -> list:
    """
    P(win by turn T) for T=1..10.

    Model:
      mana(T)    = T land drops + expected mana rocks/fast mana drawn
      p_combo(T) = P(at least 1 combo piece in hand/drawn)
      p_tutor(T) = P(at least 1 tutor drawn)
      p_access   = p_combo + (1-p_combo)*p_tutor*0.75
      p_win(T)   = p_access × mana_factor(T)
    """
    included = (combos_data or {}).get("included", [])
    top3 = sorted(included, key=lambda c: c.get("popularity", 0), reverse=True)[:3]

    combo_names = set()
    for combo in top3:
        for c in combo.get("cards", []):
            combo_names.add(c["name"].lower())

    fast_mana = [c for c in cards
                 if c.get("name","").lower() in FAST_MANA_NAMES
                 or (c.get("cmc", 1) == 0 and "artifact" in (c.get("type_line") or "").lower())]

    mana_rocks = [c for c in cards
                  if "artifact" in (c.get("type_line") or "").lower()
                  and 1 <= c.get("cmc", 99) <= 2
                  and ("add" in (c.get("oracle_text") or "").lower()
                       or "mana" in (c.get("oracle_text") or "").lower())]

    tutors = [c for c in cards
              if any(p in (c.get("oracle_text") or "").lower() for p in TUTOR_PATTERNS)]

    combo_pieces = [c for c in cards if c.get("name","").lower() in combo_names]

    n_fast   = len(fast_mana)
    n_rocks  = len(mana_rocks)
    n_tutors = len(tutors)
    n_combo  = len(combo_pieces)

    avg_combo_cmc = (sum(c.get("cmc", 2) for c in combo_pieces) / max(len(combo_pieces), 1)
                     if combo_pieces else 3.0)
    mana_needed = max(2.0, avg_combo_cmc + 1)

    curve = []
    for turn in range(1, 11):
        n_seen = min(7 + turn, deck_size)

        exp_fast  = n_fast  * n_seen / deck_size
        exp_rocks = n_rocks * n_seen / deck_size
        mana = turn + exp_rocks + exp_fast * 1.5
        mana_factor = min(1.0, mana / mana_needed)

        p_combo  = _p_at_least_one(deck_size, n_combo,  n_seen)
        p_tutor  = _p_at_least_one(deck_size, n_tutors, n_seen)
        p_access = p_combo + (1 - p_combo) * p_tutor * 0.75
        p_win    = round(min(1.0, p_access * mana_factor) * 100, 1)
        curve.append(p_win)

    return curve


# ── Report builder ─────────────────────────────────────────────────────────────

def build_report(cards: list, combos_data: dict, bps: dict, bracket: int, commander: str,
                 win_curve: list = None) -> dict:
    """Build the full report.json structure (compatible with generate_dashboard.py)."""
    results = []
    for card in cards:
        name = card.get("name", "Unknown")
        cer = card.get("cer", 0.0)
        tier = card.get("tier", "C-Tier")
        gc = card.get("gc", False)

        # Pick best available price
        prices = card.get("prices", {})
        price_usd = card.get("price_usd") or prices.get("tcgplayer")
        if price_usd:
            try:
                price_usd = float(price_usd)
            except (ValueError, TypeError):
                price_usd = None

        results.append({
            "name": name,
            "bracket": bracket,
            "cer": cer,
            "tier": tier,
            "price_usd": price_usd,
            "cmc": card.get("cmc", 0),
            "type_line": card.get("type_line", ""),
            "color_identity": card.get("color_identity", []),
            "gc": gc,
            "banned": card.get("banned", False),
            "wci": card.get("wci", 0.0),
            "prices": prices,
            "oracle_text": card.get("oracle_text", ""),
            "image_uri": card.get("image_uri", ""),
            "note": card.get("note", ""),
            "components": card.get("components", {
                "wir": 0, "pir": 0, "tsc": 0, "syn": 0, "flx": 0
            }),
        })

    results.sort(key=lambda x: x["cer"], reverse=True)

    cer_values = [r["cer"] for r in results if r["cer"] > 0]
    tier_counts = {}
    for r in results:
        t = r["tier"]
        tier_counts[t] = tier_counts.get(t, 0) + 1

    total_price = sum(float(r.get("price_usd") or 0) for r in results)
    gc_count = sum(1 for r in results if r.get("gc"))

    return {
        "bracket": bracket,
        "commander": commander,
        "generated_at": datetime.now().isoformat(),
        "total_cards": len(results),
        "avg_cer": round(sum(cer_values) / len(cer_values), 2) if cer_values else 0,
        "median_cer": round(sorted(cer_values)[len(cer_values) // 2], 2) if cer_values else 0,
        "tier_distribution": tier_counts,
        "estimated_price_usd": round(total_price, 2),
        "game_changers_count": gc_count,
        "bps": bps,
        "combos_summary": {
            "included": len((combos_data or {}).get("included", [])),
            "almost_included": (combos_data or {}).get("summary", {}).get("almost_included_count", 0),
        },
        "win_curve": win_curve or [],
        "top_10": results[:10],
        "bottom_10": results[-10:] if len(results) >= 10 else results,
        "all_cards": results,
    }


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

def _format_price(val) -> str:
    if val is None:
        return "—"
    try:
        return f"${float(val):,.2f}"
    except (ValueError, TypeError):
        return str(val)


def _buy_links(name: str) -> str:
    """Generate store buy links for a card name."""
    import urllib.parse
    q = urllib.parse.quote(name)
    stores = [
        ("TCGPlayer",      f"https://www.tcgplayer.com/search/magic/product?productLineName=magic&q={q}",       "#E3671A"),
        ("Card Kingdom",   f"https://www.cardkingdom.com/catalog/search?search=header&filter[name]={q}",        "#1565C0"),
        ("Star City Games",f"https://starcitygames.com/search/?search_query={q}&search_type=card",              "#C62828"),
        ("TCG.land MXN",   f"https://www.tcg.land/search?q={q}",                                               "#2E7D32"),
    ]
    links = ""
    for label, url, color in stores:
        links += (f'<a href="{url}" target="_blank" rel="noopener" '
                  f'style="display:inline-block;margin:2px 4px;padding:3px 10px;'
                  f'border-radius:4px;font-size:0.75rem;font-weight:600;'
                  f'color:{color};border:1px solid {color};'
                  f'text-decoration:none;white-space:nowrap;">'
                  f'{label}</a>')
    return links


def _build_full_card_table(cards: list) -> str:
    """
    Build a full sortable/filterable card table with prices and buy links.
    All 83+ cards are rendered; JS handles sort and filter client-side.
    """
    import urllib.parse

    has_prices = any(c.get("prices", {}).get("tcgplayer") for c in cards)

    rows_data = []
    for card in sorted(cards, key=lambda c: c.get("cer", 0), reverse=True):
        name = card.get("name", "")
        cer = card.get("cer", 0.0)
        tier = card.get("tier", "C-Tier")
        cmc = card.get("cmc", 0)
        tline = card.get("type_line", "")
        gc = card.get("gc", False)
        banned = card.get("banned", False)
        wci = card.get("wci", 0.0)
        prices = card.get("prices", {})

        tcg = prices.get("tcgplayer")
        ck  = prices.get("cardkingdom")
        mxn = prices.get("tcgland_mxn")

        tier_class = tier.lower().replace("-", "").replace(" ", "")

        gc_badge = (' <span style="background:#E3F2FD;color:#1565C0;font-size:0.65rem;'
                    'font-weight:700;padding:1px 5px;border-radius:3px">GC</span>'
                    if gc else "")
        banned_badge = (' <span style="background:#FFEBEE;color:#C62828;font-size:0.65rem;'
                        'font-weight:700;padding:1px 5px;border-radius:3px">BAN</span>'
                        if banned else "")

        # Main card type for filter
        card_type = "Land"
        for t in ["Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker"]:
            if t in tline:
                card_type = t
                break
        else:
            if "Land" not in tline:
                card_type = "Other"

        q = urllib.parse.quote(name)
        buy_links = (
            f'<a href="https://www.tcgplayer.com/search/magic/product?productLineName=magic&q={q}" '
            f'target="_blank" rel="noopener" class="buy-link tcg">TCG</a>'
            f'<a href="https://www.cardkingdom.com/catalog/search?search=header&filter[name]={q}" '
            f'target="_blank" rel="noopener" class="buy-link ck">CK</a>'
            f'<a href="https://www.tcg.land/search?q={q}" '
            f'target="_blank" rel="noopener" class="buy-link mxn">MXN</a>'
        )

        tcg_val  = f"${float(tcg):,.2f}" if tcg else "—"
        ck_val   = f"${float(ck):,.2f}"  if ck  else "—"
        mxn_val  = f"${int(mxn):,} MXN"  if mxn else "—"

        rows_data.append((
            cer, tier_class, tier, card_type, gc, banned,
            f"""<tr data-cer="{cer}" data-name="{html_mod.escape(name).lower()}" """
            f"""data-tier="{tier_class}" data-type="{card_type.lower()}" """
            f"""data-gc="{'1' if gc else '0'}">
  <td class="card-name"{' data-img="' + html_mod.escape(card.get("image_uri","")) + '"' if card.get("image_uri") else ""}>{html_mod.escape(name)}{gc_badge}{banned_badge}</td>
  <td style="text-align:center"><span class="cer-badge {tier_class}">{cer:.2f}</span></td>
  <td style="text-align:center"><span class="tier-tag {tier_class}">{tier}</span></td>
  <td style="text-align:center;color:#718096;font-size:0.82rem">{cmc}</td>
  <td style="font-size:0.8rem;color:#4a5568">{card_type}</td>
  <td style="text-align:right;white-space:nowrap">{buy_links}</td>
</tr>"""
        ))

    rows_html = "\n".join(r[-1] for r in rows_data)

    price_note = ''

    return f"""
<div class="section-card" id="all-cards-section">
  <h3>Todas las Cartas — {len(cards)} cartas {price_note}</h3>

  <!-- Filters + search -->
  <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px;align-items:center">
    <input id="cardSearch" type="text" placeholder="Buscar carta…"
      oninput="filterCards()"
      style="padding:6px 12px;border:1px solid #dde2ea;border-radius:6px;font-size:0.85rem;
             width:200px;outline:none;color:#1a202c">

    <select id="tierFilter" onchange="filterCards()"
      style="padding:6px 10px;border:1px solid #dde2ea;border-radius:6px;
             font-size:0.85rem;color:#4a5568;background:#fff">
      <option value="">Todos los tiers</option>
      <option value="stier">S-Tier</option>
      <option value="atier">A-Tier</option>
      <option value="btier">B-Tier</option>
      <option value="ctier">C-Tier</option>
      <option value="dtier">D-Tier</option>
      <option value="ftier">F-Tier</option>
      <option value="banned">Banned</option>
    </select>

    <select id="typeFilter" onchange="filterCards()"
      style="padding:6px 10px;border:1px solid #dde2ea;border-radius:6px;
             font-size:0.85rem;color:#4a5568;background:#fff">
      <option value="">Todos los tipos</option>
      <option value="creature">Creature</option>
      <option value="instant">Instant</option>
      <option value="sorcery">Sorcery</option>
      <option value="artifact">Artifact</option>
      <option value="enchantment">Enchantment</option>
      <option value="land">Land</option>
    </select>

    <label style="font-size:0.82rem;color:#4a5568;display:flex;align-items:center;gap:4px;cursor:pointer">
      <input type="checkbox" id="gcFilter" onchange="filterCards()"> Solo GC
    </label>

    <span id="cardCount" style="color:#718096;font-size:0.8rem;margin-left:4px"></span>
    <button onclick="resetFilters()"
      style="margin-left:auto;padding:5px 14px;border:1px solid #dde2ea;border-radius:6px;
             background:#fff;font-size:0.8rem;color:#4a5568;cursor:pointer">
      Limpiar filtros
    </button>
  </div>

  <div style="overflow-x:auto">
  <table id="allCardsTable">
    <thead>
      <tr>
        <th style="cursor:pointer" onclick="sortTable('name')">Carta ⇅</th>
        <th style="cursor:pointer;text-align:center" onclick="sortTable('cer')">CER ⇅</th>
        <th style="text-align:center">Tier</th>
        <th style="cursor:pointer;text-align:center" onclick="sortTable('cmc')">CMC ⇅</th>
        <th>Tipo</th>
        <th style="text-align:right">Comprar</th>
      </tr>
    </thead>
    <tbody id="allCardsBody">{rows_html}</tbody>
  </table>
  </div>
</div>

<script>
// Sort and filter state
let _sortKey = 'cer', _sortDir = -1;

function sortTable(key) {{
  if (_sortKey === key) _sortDir *= -1;
  else {{ _sortKey = key; _sortDir = -1; }}
  applySort();
}}

function applySort() {{
  const tbody = document.getElementById('allCardsBody');
  const rows = Array.from(tbody.querySelectorAll('tr:not([style*="display:none"])'));
  rows.sort((a, b) => {{
    let av, bv;
    if (_sortKey === 'cer')   {{ av = parseFloat(a.dataset.cer);  bv = parseFloat(b.dataset.cer); }}
    else if (_sortKey === 'name')  {{ av = a.dataset.name;  bv = b.dataset.name;
                                      return _sortDir * av.localeCompare(bv); }}
    else if (_sortKey === 'cmc')   {{ av = parseFloat(a.cells[3].textContent); bv = parseFloat(b.cells[3].textContent); }}
    else return 0;
    return _sortDir * (av - bv);
  }});
  rows.forEach(r => tbody.appendChild(r));
}}

function filterCards() {{
  const search = document.getElementById('cardSearch').value.toLowerCase();
  const tier   = document.getElementById('tierFilter').value;
  const type   = document.getElementById('typeFilter').value;
  const gcOnly = document.getElementById('gcFilter').checked;
  const rows   = document.querySelectorAll('#allCardsBody tr');
  let visible  = 0;
  rows.forEach(r => {{
    const nameMatch  = !search || r.dataset.name.includes(search);
    const tierMatch  = !tier   || r.dataset.tier === tier;
    const typeMatch  = !type   || r.dataset.type === type;
    const gcMatch    = !gcOnly || r.dataset.gc === '1';
    const show = nameMatch && tierMatch && typeMatch && gcMatch;
    r.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  document.getElementById('cardCount').textContent = `${{visible}} cartas`;
}}

function resetFilters() {{
  document.getElementById('cardSearch').value = '';
  document.getElementById('tierFilter').value = '';
  document.getElementById('typeFilter').value = '';
  document.getElementById('gcFilter').checked = false;
  filterCards();
}}

// Init count
filterCards();
</script>
<style>
.buy-link {{
  display:inline-block;margin:1px 3px;padding:2px 8px;
  border-radius:4px;font-size:0.72rem;font-weight:600;
  text-decoration:none;white-space:nowrap;border:1px solid;
}}
.buy-link.tcg {{ color:#E3671A;border-color:#E3671A; }}
.buy-link.ck  {{ color:#1565C0;border-color:#1565C0; }}
.buy-link.mxn {{ color:#2E7D32;border-color:#2E7D32; }}
.buy-link:hover {{ opacity:0.75; }}
</style>"""


def _build_combos_section(combos_data: dict) -> str:
    """Build the combos HTML section."""
    if not combos_data:
        return ""

    included = combos_data.get("included", [])
    almost = combos_data.get("almost_included") or []
    summary = combos_data.get("summary", {})

    combo_rows = ""
    for combo in included:
        _spans = []
        for c in combo.get("cards", []):
            _cn = c["name"]
            _img = f"https://api.scryfall.com/cards/named?exact={urllib.parse.quote(_cn)}&format=image&version=normal"
            _spans.append(
                f'<span class="card-name" data-img="{html_mod.escape(_img)}">{html_mod.escape(_cn)}</span>'
            )
        card_names = " + ".join(_spans)
        effects = ", ".join(html_mod.escape(e) for e in combo.get("effects", [])[:3])
        desc = html_mod.escape(combo.get("description", "")[:200])
        tag = combo.get("bracket_tag", "?")
        label = combo.get("bracket_label", "")
        tag_colors = {
            "R": "#C62828", "S": "#E65100", "P": "#E65100",
            "O": "#90A4AE", "C": "#1565C0", "E": "#2E7D32", "B": "#B71C1C"
        }
        color = tag_colors.get(tag, "#90A4AE")
        pop = combo.get("popularity", 0) or 0
        combo_rows += f"""<tr>
          <td><span style="color:{color};font-weight:700;font-family:'Inter',sans-serif">[{tag}]</span>
              <span style="color:#718096;font-size:0.75rem"> {html_mod.escape(label)}</span></td>
          <td style="font-weight:600">{card_names}</td>
          <td style="color:#718096;font-size:0.85rem">{effects}</td>
          <td style="font-family:'Inter',monospace;font-size:0.8rem">{pop:,}</td>
        </tr>
        <tr><td colspan="4" style="color:#718096;font-size:0.78rem;padding:2px 12px 10px;border-bottom:1px solid #dde2ea">
          {desc}{'…' if len(combo.get('description','')) > 200 else ''}
        </td></tr>"""

    # Almost included (top 5)
    almost_rows = ""
    for combo in almost[:5]:
        _aspans = []
        for c in combo.get("cards", []):
            _cn = c["name"]
            _img = f"https://api.scryfall.com/cards/named?exact={urllib.parse.quote(_cn)}&format=image&version=normal"
            _aspans.append(
                f'<span class="card-name" data-img="{html_mod.escape(_img)}">{html_mod.escape(_cn)}</span>'
            )
        card_names = " + ".join(_aspans)
        effects = ", ".join(html_mod.escape(e) for e in combo.get("effects", [])[:2])
        almost_rows += f"""<tr>
          <td style="font-weight:600;color:#1565C0">{card_names}</td>
          <td style="color:#718096;font-size:0.85rem">{effects}</td>
        </tr>"""

    almost_section = ""
    if almost:
        almost_section = f"""
<div class="section-card" style="margin-top:16px">
  <h3>Casi-Combos — Falta 1 Carta ({len(almost)} total)</h3>
  <div style="overflow-x:auto">
  <table>
    <thead><tr><th>Cartas del Combo</th><th>Efectos</th></tr></thead>
    <tbody>{almost_rows}</tbody>
  </table>
  </div>
  {f'<p style="color:#718096;font-size:0.8rem;margin-top:8px">…y {len(almost)-5} combos más</p>' if len(almost) > 5 else ''}
</div>"""

    return f"""
<div class="section-card" id="combos-section">
  <h3>Combos Confirmados — Commander Spellbook
    <span style="color:#2E7D32;font-size:0.85rem;margin-left:12px">{summary.get('included_count',0)} incluidos</span>
    <span style="color:#1565C0;font-size:0.85rem;margin-left:8px">{summary.get('almost_included_count',0)} casi-incluidos</span>
  </h3>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>Bracket Tag</th><th>Cartas</th><th>Efectos</th><th>Popularidad</th>
    </tr></thead>
    <tbody>{combo_rows}</tbody>
  </table>
  </div>
</div>
{almost_section}"""


def _build_bps_section(bps: dict) -> str:
    """Build the BPS gauge SVG section."""
    if not bps:
        return ""

    pct = bps.get("bps_pct", 0)
    target = bps.get("target_bracket", 5)
    dims = bps.get("dimensions", {})

    # Gauge SVG
    angle = pct / 100 * 180  # 0..180 degrees
    r = 80
    cx, cy = 100, 100
    # Convert angle to SVG arc
    import math
    rad = math.radians(180 - angle)
    x = cx + r * math.cos(rad)
    y = cy - r * math.sin(rad)
    large_arc = 1 if angle > 180 else 0

    # Dimension bars
    dim_labels = {
        "gc_density": "GC Density",
        "cer_avg": "CER Promedio",
        "win_speed": "Win Speed",
        "tutor_density": "Tutor Density",
        "combo_presence": "Combo Presence",
    }
    dim_colors = {
        "gc_density": "#1565C0",
        "cer_avg": "#1976D2",
        "win_speed": "#455A64",
        "tutor_density": "#78909C",
        "combo_presence": "#90A4AE",
    }

    bars = ""
    for key, label in dim_labels.items():
        dim = dims.get(key, {})
        dim_pct = dim.get("score_pct", 0)
        color = dim_colors.get(key, "#9E9E9E")
        weight = int(dim.get("weight", 0) * 100)
        val = dim.get("value", "?")
        b5_val = dim.get("b5_min", "?")
        bars += f"""
<div style="margin-bottom:12px">
  <div style="display:flex;justify-content:space-between;font-size:0.8rem;margin-bottom:4px">
    <span style="color:#1a202c">{label} <span style="color:#718096">({weight}%)</span></span>
    <span style="color:{color};font-family:'Inter',sans-serif">{val} / {b5_val} → {dim_pct:.0f}%</span>
  </div>
  <div style="background:#f0f4f8;border-radius:4px;height:8px;overflow:hidden">
    <div style="width:{min(100,dim_pct):.1f}%;height:100%;background:{color};border-radius:4px;transition:width 0.5s"></div>
  </div>
</div>"""

    return f"""
<div class="section-card" id="bps-section">
  <h3>BPS — Bracket Proximity Score hacia B{target}</h3>
  <div style="display:grid;grid-template-columns:200px 1fr;gap:32px;align-items:start">
    <div style="text-align:center">
      <svg viewBox="0 0 200 120" style="width:180px">
        <!-- Background arc -->
        <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="#dde2ea" stroke-width="16" stroke-linecap="round"/>
        <!-- Progress arc -->
        <path d="M 20 100 A 80 80 0 {large_arc} 1 {x:.2f} {y:.2f}"
              fill="none" stroke="#1565C0" stroke-width="16" stroke-linecap="round"/>
        <!-- Value -->
        <text x="100" y="95" text-anchor="middle" fill="#1a202c"
              font-family="'Inter',sans-serif" font-size="22" font-weight="700">{pct:.0f}%</text>
        <text x="100" y="112" text-anchor="middle" fill="#718096" font-size="11">hacia B{target}</text>
      </svg>
    </div>
    <div>{bars}</div>
  </div>
</div>"""


def generate_enhanced_dashboard(report: dict, combos_data: Optional[dict], commander: str) -> str:
    """Generate a complete dashboard HTML with CER charts + prices + combos + BPS."""

    # Base CER dashboard from generate_dashboard.py
    base_html = _base_generate_html(report, commander=commander)

    # Build extra sections
    all_cards = report.get("all_cards", [])
    bps = report.get("bps")

    full_table_section = _build_full_card_table(all_cards)
    combos_section = _build_combos_section(combos_data) if combos_data else ""
    bps_section = _build_bps_section(bps) if bps else ""

    # Extra CSS for new sections
    extra_css = """
<style>
.section-card {
  background: var(--bg-card, #ffffff);
  border: 1px solid var(--border, #dde2ea);
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
}
.section-card h3 {
  font-size: 0.85rem;
  font-weight: 600;
  margin-bottom: 16px;
  color: var(--text-secondary, #4a5568);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
</style>"""

    # Inject before </body>
    injection = f"""
{extra_css}
<div class="container">
{bps_section}
{combos_section}
{full_table_section}
</div>"""

    return base_html.replace("</body>", injection + "\n</body>")


# ── Cache enrichment ───────────────────────────────────────────────────────────

_SCRYFALL_DELAY = 0.11   # 110ms — Scryfall rate limit

def _scryfall_collection(names: list) -> dict:
    """Batch-fetch card metadata from Scryfall. Returns name→data map."""
    result = {}
    for i in range(0, len(names), 75):
        batch = names[i:i + 75]
        payload = json.dumps({"identifiers": [{"name": n} for n in batch]}).encode()
        req = urllib.request.Request(
            "https://api.scryfall.com/cards/collection",
            data=payload,
            headers={"User-Agent": "CEDHDeckAnalyst/1.0",
                     "Content-Type": "application/json",
                     "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                for card in data.get("data", []):
                    result[card["name"].lower()] = card
            for nf in data.get("not_found", []):
                print(f"  [scryfall] not found: {nf.get('name', nf)}", file=sys.stderr)
        except Exception as e:
            print(f"  [scryfall] batch error: {e}", file=sys.stderr)
        if i + 75 < len(names):
            time.sleep(_SCRYFALL_DELAY)
    return result


def fetch_edhrec_synergy(commander_name: str) -> dict:
    """Fetch EDHREC synergy scores for a commander. Returns {card_name_lower: synergy_float}.

    Uses json.edhrec.com (unofficial but same data as the website).
    Synergy values are in [-1.0, 1.0]. Returns {} on any failure.
    """
    try:
        # Build slug: first partner only, lowercase, spaces→dashes, strip non-alphanumeric
        base_name = commander_name.split(" // ")[0].strip()
        slug = re.sub(r"[^a-z0-9\-]", "", base_name.lower().replace(" ", "-").replace(",", ""))
        url = f"https://json.edhrec.com/pages/commanders/{slug}.json"
        time.sleep(0.2)
        req = urllib.request.Request(url, headers={"User-Agent": "CEDHDeckAnalyst/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        synergy_map = {}
        cardlists = data["container"]["json_dict"]["cardlists"]
        for cardlist in cardlists:
            for entry in cardlist.get("cardviews", []):
                name = entry.get("name")
                syn = entry.get("synergy")
                if name and syn is not None:
                    synergy_map[name.lower()] = float(syn)
        print(f"  [edhrec] {len(synergy_map)} synergy scores para {commander_name}", file=sys.stderr)
        return synergy_map
    except Exception as e:
        print(f"  [edhrec] warn: {e}", file=sys.stderr)
        return {}


def _enrich_from_cache(cards: list) -> list:
    """
    Enrich a card list (from .txt/.csv) with printId, cer, tier, gc, cmc, type_line.

    Priority:
      1. data/deck_cards.json (has full CER + printId)
      2. data/mtgstocks_id_cache.json (printId only)
      3. Scryfall (cmc + type_line for unknown cards)
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Load existing deck_cards.json as a name → record map
    deck_json_path = os.path.join(script_dir, "data", "deck_cards.json")
    deck_cache: dict = {}
    if os.path.exists(deck_json_path):
        with open(deck_json_path) as f:
            for c in json.load(f):
                deck_cache[c["name"].lower()] = c

    # Load MTGStocks ID cache (name → printId)
    id_cache_path = os.path.join(script_dir, "data", "mtgstocks_id_cache.json")
    id_cache: dict = {}
    if os.path.exists(id_cache_path):
        with open(id_cache_path) as f:
            for name, pid in json.load(f).items():
                id_cache[name.lower()] = pid

    unknown_names = []
    for card in cards:
        name_low = card["name"].lower()
        if name_low in deck_cache:
            src = deck_cache[name_low]
            card.setdefault("printId", src.get("printId"))
            card.setdefault("cer", src.get("cer", 0.0))
            card.setdefault("tier", src.get("tier", "C-Tier"))
            card.setdefault("gc", src.get("gc", False))
            card.setdefault("cmc", src.get("cmc", 0))
            card.setdefault("type_line", src.get("type_line", ""))
        elif name_low in id_cache:
            card.setdefault("printId", id_cache[name_low])
        else:
            unknown_names.append(card["name"])

    # Fetch unknown cards from Scryfall
    if unknown_names:
        print(f"  [enrich] {len(unknown_names)} cartas nuevas — consultando Scryfall…",
              file=sys.stderr)
        sf_map = _scryfall_collection(unknown_names)
        for card in cards:
            sf = sf_map.get(card["name"].lower())
            if sf:
                card.setdefault("cmc", int(sf.get("cmc", 0)))
                card.setdefault("type_line", sf.get("type_line", ""))
                card.setdefault("oracle_text", sf.get("oracle_text", ""))
                card.setdefault("color_identity", sf.get("color_identity", []))
                card.setdefault("image_uri",
                    (sf.get("image_uris") or
                     (sf.get("card_faces") or [{}])[0].get("image_uris") or {}).get("normal", ""))
                # Use Scryfall price as fallback
                sf_price = (sf.get("prices") or {}).get("usd")
                if sf_price:
                    card.setdefault("price_usd", float(sf_price))

    # Enrich oracle_text for cached cards that lack it (needed for tutor/mana detection)
    needs_oracle = [c["name"] for c in cards if not c.get("oracle_text")]
    if needs_oracle:
        sf_oracle = _scryfall_collection(needs_oracle)
        for card in cards:
            if not card.get("oracle_text"):
                sf = sf_oracle.get(card["name"].lower())
                if sf:
                    card["oracle_text"] = sf.get("oracle_text", "")
                    card.setdefault("color_identity", sf.get("color_identity", []))
                    card.setdefault("image_uri",
                        (sf.get("image_uris") or
                         (sf.get("card_faces") or [{}])[0].get("image_uris") or {}).get("normal", ""))

    known = sum(1 for c in cards if c.get("printId") or c.get("cer", 0) > 0)
    print(f"  [enrich] {known}/{len(cards)} cartas con datos de caché",
          file=sys.stderr)
    return cards


# ── Moxfield loader ────────────────────────────────────────────────────────────

_MOXFIELD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.moxfield.com",
    "Referer": "https://www.moxfield.com/",
}


def _parse_moxfield_url(url: str) -> Optional[str]:
    """Extract the deck public ID from a Moxfield URL."""
    # https://www.moxfield.com/decks/abc123xyz  →  abc123xyz
    import re
    m = re.search(r"moxfield\.com/decks/([A-Za-z0-9_\-]+)", url)
    return m.group(1) if m else None


def load_from_moxfield(url: str) -> tuple:
    """
    Fetch a deck from Moxfield and return (cards, commander_name).

    Tries Moxfield API v3. If blocked (403/Cloudflare), raises a
    RuntimeError with instructions for the user.

    Each card dict has: name, quantity, cmc, type_line, oracle_text,
    color_identity, price_usd (USD TCGPlayer), price_eur (Cardmarket).
    """
    deck_id = _parse_moxfield_url(url)
    if not deck_id:
        raise ValueError(f"No se pudo extraer el deck ID de la URL: {url}")

    api_url = f"https://api2.moxfield.com/v3/decks/all/{deck_id}"
    req = urllib.request.Request(api_url, headers=_MOXFIELD_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            export_url = f"https://www.moxfield.com/decks/{deck_id}/export/txt"
            raise RuntimeError(
                f"Moxfield bloqueó la request (HTTP {e.code} — Cloudflare).\n\n"
                f"Descarga el deck en texto plano aquí (ábelo en tu browser):\n"
                f"  {export_url}\n\n"
                f"Luego analízalo con precios de Scryfall:\n"
                f"  python3 cedh_analyst.py analyze \\\n"
                f"    --decklist deck.txt \\\n"
                f"    --commander \"Nombre del Comandante\" \\\n"
                f"    --scryfall-prices\n"
            )
        raise

    cards = []

    # Commanders
    commander_names = []
    for entry in (data.get("commanders") or {}).values():
        card = entry.get("card", {})
        commander_names.append(card.get("name", ""))

    # Mainboard (and optionally commanders board)
    boards_to_read = ["mainboard", "commanders"]
    seen = set()
    for board_name in boards_to_read:
        board = (data.get("boards") or {}).get(board_name, {})
        for entry in (board.get("cards") or {}).values():
            card = entry.get("card", {})
            name = card.get("name", "").strip()
            if not name or name in seen:
                continue
            seen.add(name)

            prices = card.get("prices") or {}
            price_usd = prices.get("usd") or prices.get("usdEtched")
            price_eur = prices.get("eur")

            cards.append({
                "name": name,
                "quantity": entry.get("quantity", 1),
                "cmc": int(card.get("cmc", 0)),
                "type_line": card.get("type_line", card.get("type", "")),
                "oracle_text": card.get("oracle_text", card.get("text", "")),
                "color_identity": card.get("color_identity", []),
                "rarity": card.get("rarity", ""),
                "edhrec_rank": card.get("edhrec_rank"),
                "price_usd": float(price_usd) if price_usd else None,
                "price_eur": float(price_eur) if price_eur else None,
                "prices": {
                    "tcgplayer": float(price_usd) if price_usd else None,
                    "cardmarket": float(price_eur) if price_eur else None,
                },
            })

    commander = " // ".join(filter(None, commander_names)) or "Commander"
    print(
        f"  [moxfield] {len(cards)} cartas cargadas  |  Commander: {commander}",
        file=sys.stderr,
    )
    return _filter_cards(cards), commander


# ── Scryfall price enrichment (for all cards) ───────────────────────────────────

def enrich_prices_from_scryfall(cards: list) -> list:
    """
    Fetch prices from Scryfall for cards that lack price_usd.
    Scryfall prices = TCGPlayer market price (updated daily).
    """
    missing = [c["name"] for c in cards if not c.get("price_usd")]
    if not missing:
        return cards

    print(f"  [scryfall] Fetching prices for {len(missing)} cards…", file=sys.stderr)
    sf_map = _scryfall_collection(missing)

    for card in cards:
        if card.get("price_usd"):
            continue
        sf = sf_map.get(card["name"].lower())
        if not sf:
            continue
        p = sf.get("prices") or {}
        usd = p.get("usd")
        eur = p.get("eur")
        if usd:
            card["price_usd"] = float(usd)
            card.setdefault("prices", {})["tcgplayer"] = float(usd)
        if eur:
            card.setdefault("prices", {})["cardmarket"] = float(eur)
        # Also enrich missing metadata
        card.setdefault("cmc", int(sf.get("cmc", 0)))
        card.setdefault("type_line", sf.get("type_line", ""))
        card.setdefault("oracle_text", sf.get("oracle_text", ""))
        card.setdefault("color_identity", sf.get("color_identity", []))
        card.setdefault("edhrec_rank", sf.get("edhrec_rank"))
        card.setdefault("image_uri",
            (sf.get("image_uris") or
             (sf.get("card_faces") or [{}])[0].get("image_uris") or {}).get("normal", ""))

    fetched = sum(1 for c in cards if c.get("price_usd"))
    total = sum(c.get("price_usd") or 0 for c in cards)
    print(
        f"  [scryfall] {fetched}/{len(cards)} con precio  |  "
        f"Total TCGPlayer: ${total:,.2f}",
        file=sys.stderr,
    )
    return cards


# ── analyze command ────────────────────────────────────────────────────────────

def cmd_analyze(args):
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  cEDH Deck Analyst — {args.commander}", file=sys.stderr)
    print(f"  Bracket {args.bracket} ({BRACKET_NAMES.get(args.bracket, '?')})", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    os.makedirs(args.output, exist_ok=True)

    # --refresh: delete cached prices.json and combos.json to force re-fetch
    if getattr(args, 'refresh', False):
        for fname in ("prices.json", "combos.json", "edhrec_synergy.json"):
            p = os.path.join(args.output, fname)
            if os.path.exists(p):
                os.remove(p)
                print(f"  [refresh] Removed {p}", file=sys.stderr)

    # ── 1. Load decklist — Moxfield URL or local file
    moxfield_commander = None
    if args.moxfield:
        print(f"[1/5] Cargando deck desde Moxfield…", file=sys.stderr)
        print(f"  URL: {args.moxfield}", file=sys.stderr)
        try:
            cards, moxfield_commander = load_from_moxfield(args.moxfield)
            # Use Moxfield-detected commander if not explicitly overridden
            if not args.commander or args.commander == "Commander Deck":
                args.commander = moxfield_commander
                print(f"  Commander detectado: {args.commander}", file=sys.stderr)
        except RuntimeError as e:
            print(f"\n[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"[1/5] Cargando decklist: {args.decklist}", file=sys.stderr)
        cards, txt_commander = load_decklist(args.decklist)
        if txt_commander and (not args.commander or args.commander == "Commander Deck"):
            args.commander = txt_commander
            print(f"  Commander detectado: {args.commander}", file=sys.stderr)
        print(f"  → {len(cards)} cartas cargadas", file=sys.stderr)

        # For .txt/.csv input, enrich cards with data from local caches
        ext = os.path.splitext(args.decklist)[1].lower()
        if ext in (".txt", ".csv"):
            cards = _enrich_from_cache(cards)

    print(f"  → {len(cards)} cartas listas", file=sys.stderr)

    # ── 2. Prices — Scryfall (Moxfield/--scryfall-prices) or MTGStocks (cached)
    prices_path = os.path.join(args.output, "prices.json")
    if args.no_prices:
        print(f"\n[2/5] Precios omitidos (--no-prices)", file=sys.stderr)
    elif args.moxfield or getattr(args, 'scryfall_prices', False):
        # When loading from Moxfield or --scryfall-prices: Moxfield already embeds
        # prices from Scryfall; fill in any gaps via Scryfall API.
        print(f"\n[2/5] Precios desde Scryfall (TCGPlayer)…", file=sys.stderr)
        cards = enrich_prices_from_scryfall(cards)
        # Persist for future runs
        prices_map = {c["name"]: {"prices": c.get("prices", {})} for c in cards}
        with open(prices_path, "w") as f:
            json.dump(prices_map, f, indent=2, ensure_ascii=False)
        print(f"  → Saved: {prices_path}", file=sys.stderr)
    elif os.path.exists(prices_path):
        print(f"\n[2/5] Cargando precios desde {prices_path}…", file=sys.stderr)
        with open(prices_path) as f:
            prices_map = json.load(f)
        cards = apply_prices_to_cards(cards, prices_map)
        total_usd = sum(float(c.get("price_usd") or 0) for c in cards)
        print(f"  → Precios cargados  |  Total estimado: ${total_usd:,.2f}", file=sys.stderr)
    else:
        # Check if any card has a printId to fetch
        n_with_id = sum(1 for c in cards if c.get("printId"))
        if n_with_id > 0:
            print(f"\n[2/5] Fetching precios desde MTGStocks ({n_with_id} cartas con printId)…",
                  file=sys.stderr)
            prices_map = fp_mod.fetch_all(cards)
            cards = apply_prices_to_cards(cards, prices_map)
            with open(prices_path, "w") as f:
                json.dump(prices_map, f, indent=2, ensure_ascii=False)
            total_usd = sum(float(c.get("price_usd") or 0) for c in cards)
            print(f"  → ${total_usd:,.2f} total (TCGPlayer)  |  Saved: {prices_path}",
                  file=sys.stderr)
        else:
            print(f"\n[2/5] Sin printIds — precios omitidos. "
                  f"Usa --no-prices para suprimir este mensaje.", file=sys.stderr)

    # ── 3. Fetch combos (or load cached combos.json)
    combos_data = None
    combos_path = os.path.join(args.output, "combos.json")
    deck_hash = hashlib.md5("|".join(sorted(c["name"] for c in cards)).encode()).hexdigest()
    if args.no_combos:
        print(f"\n[3/5] Combos omitidos (--no-combos)", file=sys.stderr)
    elif os.path.exists(combos_path):
        with open(combos_path) as f:
            cached = json.load(f)
        if cached.get("deck_hash") == deck_hash:
            print(f"\n[3/5] Cargando combos desde {combos_path}…", file=sys.stderr)
            combos_data = cached
            summary = combos_data.get("summary", {})
            print(
                f"  → {summary.get('included_count', 0)} incluidos  |  "
                f"{summary.get('almost_included_count', 0)} casi-incluidos",
                file=sys.stderr,
            )
        else:
            print(f"\n[3/5] Caché de combos es de otro deck — re-fetching…", file=sys.stderr)
    if not args.no_combos and combos_data is None:
        print(f"\n[3/5] Combos desde Commander Spellbook…", file=sys.stderr)
        commanders = [c.strip() for c in args.commander.split(" // ")]
        commander_set = {c.lower() for c in commanders}
        main_cards = [n for n in [c["name"] for c in cards] if n.lower() not in commander_set]
        try:
            raw = fc_mod.call_find_my_combos(main_cards, commanders, limit=200)
            combos_data = fc_mod.process_response(raw, include_almost=True)
            combos_data["deck_hash"] = deck_hash
            with open(combos_path, "w") as f:
                json.dump(combos_data, f, indent=2, ensure_ascii=False)
            summary = combos_data.get("summary", {})
            print(
                f"  → {summary.get('included_count', 0)} incluidos  |  "
                f"{summary.get('almost_included_count', 0)} casi-incluidos",
                file=sys.stderr,
            )
            print(f"  → Saved: {combos_path}", file=sys.stderr)
        except Exception as e:
            print(f"  [warn] Combos fallidos: {e}", file=sys.stderr)

    # ── 3.5. EDHREC synergy
    edhrec_path = os.path.join(args.output, "edhrec_synergy.json")
    edhrec_synergy = {}
    if not args.no_combos:
        if os.path.exists(edhrec_path):
            with open(edhrec_path) as _f:
                _cached_edhrec = json.load(_f)
            if _cached_edhrec.get("deck_hash") == deck_hash:
                edhrec_synergy = _cached_edhrec.get("synergy", {})
                print(f"\n[3.5/5] EDHREC synergy desde caché ({len(edhrec_synergy)} cartas)",
                      file=sys.stderr)
        if not edhrec_synergy:
            print(f"\n[3.5/5] Fetching EDHREC synergy para {args.commander}…", file=sys.stderr)
            edhrec_synergy = fetch_edhrec_synergy(args.commander)
            with open(edhrec_path, "w") as _f:
                json.dump({"deck_hash": deck_hash, "synergy": edhrec_synergy}, _f, indent=2)

    # ── 4. BPS + WCI + Win curve
    print(f"\n[4/5] Calculando BPS / WCI / curva de victoria…", file=sys.stderr)
    cards = calculate_wci(cards, combos_data, edhrec_synergy=edhrec_synergy)
    win_curve = calculate_win_curve(cards, combos_data)
    print(f"  → Win curve T1-T10: {win_curve}", file=sys.stderr)
    bps = calculate_bps(cards, combos_data, args.bracket, win_curve=win_curve)
    print(f"  → BPS hacia B{bps['target_bracket']}: {bps['bps_pct']}%", file=sys.stderr)

    # ── 5. Build report + dashboard
    print(f"\n[5/5] Generando reporte y dashboard…", file=sys.stderr)
    report = build_report(cards, combos_data, bps, args.bracket, args.commander,
                          win_curve=win_curve)

    report_path = os.path.join(args.output, "report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"  → Saved: {report_path}", file=sys.stderr)

    dashboard_html = generate_enhanced_dashboard(report, combos_data, args.commander)
    dashboard_path = os.path.join(args.output, "dashboard.html")
    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.write(dashboard_html)
    print(f"  → Saved: {dashboard_path}", file=sys.stderr)

    # ── Summary
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Deck: {args.commander} · B{args.bracket}", file=sys.stderr)
    print(f"  Cartas: {report['total_cards']}  |  CER avg: {report['avg_cer']}", file=sys.stderr)
    total_price = report.get("estimated_price_usd", 0)
    price_str = f"${total_price:,.2f}" if total_price else "sin precios"
    print(f"  Precio total (TCGPlayer): {price_str}", file=sys.stderr)
    print(f"  Game Changers: {report['game_changers_count']}", file=sys.stderr)
    if combos_data:
        print(f"  Combos: {report['combos_summary']['included']}", file=sys.stderr)
    print(f"  BPS → B{bps['target_bracket']}: {bps['bps_pct']}%", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"\n  Dashboard: {dashboard_path}", file=sys.stderr)
    print(f"  Abre con: open {dashboard_path}\n", file=sys.stderr)


# ── CLI entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="cedh_analyst",
        description="cEDH Deck Analyst — pipeline de análisis de decks Commander",
    )
    sub = parser.add_subparsers(dest="command")

    # analyze
    analyze = sub.add_parser("analyze", help="Analiza un deck completo")

    # Input — mutually exclusive: Moxfield URL vs local file
    input_group = analyze.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--moxfield", "-m",
                             metavar="URL",
                             help="URL de un deck público en Moxfield "
                                  "(ej: https://www.moxfield.com/decks/abc123)")
    input_group.add_argument("--decklist", "-d",
                             metavar="PATH",
                             help="deck_cards.json, decklist.txt, o deck.csv")

    analyze.add_argument("--commander", "-c", default="Commander Deck",
                         help='Comandante, ej: "Magda, Brazen Outlaw" '
                              '(se auto-detecta desde Moxfield si no se especifica)')
    analyze.add_argument("--bracket", "-b", type=int, default=4, choices=[1, 2, 3, 4, 5],
                         help="Bracket del deck (1-5, default: 4)")
    analyze.add_argument("--output", "-o", default="./output/",
                         help="Directorio de salida (default: ./output/)")
    analyze.add_argument("--scryfall-prices", action="store_true",
                         help="Usa Scryfall (TCGPlayer) para precios en vez de MTGStocks")
    analyze.add_argument("--no-prices", action="store_true",
                         help="Omite fetch de precios")
    analyze.add_argument("--no-combos", action="store_true",
                         help="Omite fetch de combos de Commander Spellbook")
    analyze.add_argument("--refresh", action="store_true",
                         help="Fuerza re-fetch de precios y combos (ignora caché)")

    args = parser.parse_args()

    if args.command == "analyze":
        cmd_analyze(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
