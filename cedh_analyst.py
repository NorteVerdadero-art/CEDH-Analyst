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
import html as html_mod
import urllib.request
import urllib.error
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
    "look at the top",
    "tutor",
]

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
    return line.strip()


def load_decklist(path: str) -> list:
    """Load deck from .json, .txt, or .csv.

    Supported formats:
      .json   — deck_cards.json array (internal format with cer/tier/printId)
      .txt    — one card per line, MTGO/MTGA/plain text (quantities optional)
      .csv    — CSV with at minimum a 'name' column; also handles Moxfield/
                Archidekt exports (columns: Count, Name, Expansion, etc.)
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".json":
        with open(path) as f:
            cards = json.load(f)
        return _filter_cards(cards)

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
        return _filter_cards(cards)

    # .txt — MTGO / MTGA / plain
    cards = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            name = _parse_card_name(line)
            if name and name.lower() not in INVALID_CARDS:
                cards.append({"name": name})
    return _filter_cards(cards)


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
    fast_mana = sum(1 for c in cards if c.get("cmc", 99) == 0 and "Artifact" in c.get("type_line", ""))
    tutors = sum(1 for c in cards if c.get("has_tutor_text") or c.get("type_line", "") == "")
    # Simple heuristic: more fast mana + tutors = faster win
    base_turn = 7
    base_turn -= min(3, fast_mana // 2)
    base_turn -= min(2, tutors // 3)
    return max(2, base_turn)


def calculate_bps(cards: list, combos_data: dict, bracket: int) -> dict:
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

    # 2. CER Average
    cer_values = [c.get("cer", 0) for c in cards if c.get("cer", 0) > 0]
    avg_cer = sum(cer_values) / len(cer_values) if cer_values else 0
    b4_cer, b5_cer = 6.5, 8.0
    if target == 5:
        cer_score = min(1.0, max(0.0, (avg_cer - b4_cer) / (b5_cer - b4_cer)))
    else:
        cer_score = min(1.0, max(0.0, avg_cer / b4_cer))

    # 3. Win Speed (lower turns = higher score)
    avg_turn = estimate_win_speed(cards)
    b4_turn, b5_turn = 6, 4
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


# ── Report builder ─────────────────────────────────────────────────────────────

def build_report(cards: list, combos_data: dict, bps: dict, bracket: int, commander: str) -> dict:
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
            "prices": prices,
            "oracle_text": card.get("oracle_text", ""),
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


def _build_prices_section(cards: list) -> str:
    """Build the full prices table HTML. Only shows cards with at least one price."""
    priced = [c for c in cards if any(c.get("prices", {}).values())]
    priced.sort(key=lambda c: float(c.get("prices", {}).get("tcgplayer") or 0), reverse=True)
    rows = ""
    for card in priced:
        name = html_mod.escape(card.get("name", ""))
        prices = card.get("prices", {})
        tcg = _format_price(prices.get("tcgplayer"))
        ck = _format_price(prices.get("cardkingdom"))
        cm = _format_price(prices.get("cardmarket"))
        mxn = _format_price(prices.get("tcgland_mxn"))
        gc_badge = ' <span style="color:#1565C0;font-size:0.7rem;font-weight:700">GC</span>' if card.get("gc") else ""
        rows += f"""<tr>
          <td class="card-name">{name}{gc_badge}</td>
          <td>{tcg}</td>
          <td>{ck}</td>
          <td>{cm}</td>
          <td>{mxn}</td>
        </tr>"""

    return f"""
<div class="section-card" id="prices-section">
  <h3>Precios en Vivo — MTGStocks ({len(priced)} cartas)</h3>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>Carta</th>
      <th>TCGPlayer</th><th>Card Kingdom</th>
      <th>Cardmarket</th><th>TCG.land MXN</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
</div>"""


def _build_combos_section(combos_data: dict) -> str:
    """Build the combos HTML section."""
    if not combos_data:
        return ""

    included = combos_data.get("included", [])
    almost = combos_data.get("almost_included") or []
    summary = combos_data.get("summary", {})

    combo_rows = ""
    for combo in included:
        card_names = " + ".join(html_mod.escape(c["name"]) for c in combo.get("cards", []))
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
        card_names = " + ".join(html_mod.escape(c["name"]) for c in combo.get("cards", []))
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

    prices_section = _build_prices_section(all_cards) if any(
        c.get("prices") for c in all_cards
    ) else ""
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
{prices_section}
</div>"""

    return base_html.replace("</body>", injection + "\n</body>")


# ── analyze command ────────────────────────────────────────────────────────────

def cmd_analyze(args):
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  cEDH Deck Analyst — {args.commander}", file=sys.stderr)
    print(f"  Bracket {args.bracket} ({BRACKET_NAMES.get(args.bracket, '?')})", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    os.makedirs(args.output, exist_ok=True)

    # ── 1. Load decklist
    print(f"[1/5] Cargando decklist: {args.decklist}", file=sys.stderr)
    cards = load_decklist(args.decklist)
    print(f"  → {len(cards)} cartas cargadas", file=sys.stderr)

    # ── 2. Fetch prices
    prices_map = {}
    if not args.no_prices:
        print(f"\n[2/5] Precios desde MTGStocks…", file=sys.stderr)
        prices_map = fp_mod.fetch_all(cards)
        cards = apply_prices_to_cards(cards, prices_map)
        # Save prices
        prices_path = os.path.join(args.output, "prices.json")
        with open(prices_path, "w") as f:
            json.dump(prices_map, f, indent=2, ensure_ascii=False)
        print(f"  → Saved: {prices_path}", file=sys.stderr)
    else:
        print(f"\n[2/5] Precios omitidos (--no-prices)", file=sys.stderr)

    # ── 3. Fetch combos
    combos_data = None
    if not args.no_combos:
        print(f"\n[3/5] Combos desde Commander Spellbook…", file=sys.stderr)
        commanders = [c.strip() for c in args.commander.split(" // ")]
        commander_set = {c.lower() for c in commanders}
        main_cards = [n for n in [c["name"] for c in cards] if n.lower() not in commander_set]
        try:
            raw = fc_mod.call_find_my_combos(main_cards, commanders, limit=200)
            combos_data = fc_mod.process_response(raw, include_almost=True)
            combos_path = os.path.join(args.output, "combos.json")
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
    else:
        print(f"\n[3/5] Combos omitidos (--no-combos)", file=sys.stderr)

    # ── 4. BPS
    print(f"\n[4/5] Calculando BPS…", file=sys.stderr)
    bps = calculate_bps(cards, combos_data, args.bracket)
    print(f"  → BPS hacia B{bps['target_bracket']}: {bps['bps_pct']}%", file=sys.stderr)

    # ── 5. Build report + dashboard
    print(f"\n[5/5] Generando reporte y dashboard…", file=sys.stderr)
    report = build_report(cards, combos_data, bps, args.bracket, args.commander)

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
    print(f"  Precio estimado: ${report['estimated_price_usd']:,.2f} USD", file=sys.stderr)
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
    analyze.add_argument("--decklist", "-d", required=True,
                         help="deck_cards.json o decklist.txt")
    analyze.add_argument("--commander", "-c", required=True,
                         help='Nombre del comandante, ej: "Magda, Brazen Outlaw"')
    analyze.add_argument("--bracket", "-b", type=int, default=4, choices=[1, 2, 3, 4, 5],
                         help="Bracket del deck (1-5, default: 4)")
    analyze.add_argument("--output", "-o", default="./output/",
                         help="Directorio de salida (default: ./output/)")
    analyze.add_argument("--no-prices", action="store_true",
                         help="No fetch precios de MTGStocks")
    analyze.add_argument("--no-combos", action="store_true",
                         help="No fetch combos de Commander Spellbook")

    args = parser.parse_args()

    if args.command == "analyze":
        cmd_analyze(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
