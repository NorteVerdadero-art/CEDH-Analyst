#!/usr/bin/env python3
"""
cer_calculator.py — Card Effectiveness Rating calculator for Commander decks

This script takes card data (from scryfall_lookup.py output) and tournament
context to compute CER scores per bracket.

Usage:
    python3 cer_calculator.py --cards cards_data.json --bracket 5
    python3 cer_calculator.py --cards cards_data.json --bracket 3 --output report.json

The CER formula:
    CER = (WIR × 0.35) + (PIR × 0.25) + (TSC × 0.20) + (SYN × 0.10) + (FLX × 0.10)

When tournament data is unavailable (common for Brackets 1-4), proxy calculations
are used based on card properties and EDHREC rank.
"""

import json
import sys
import argparse
import math

# Game Changers that restrict bracket placement
GAME_CHANGERS = {
    # Fast Mana
    "Mana Crypt", "Mana Vault", "Grim Monolith", "Mox Diamond",
    "Chrome Mox", "Gaea's Cradle", "Serra's Sanctum", "Mishra's Workshop",
    # Tutors
    "Demonic Tutor", "Vampiric Tutor", "Imperial Seal", "Mystical Tutor",
    "Enlightened Tutor", "Worldly Tutor", "Gamble",
    # Card Advantage
    "Rhystic Study", "Necropotence", "Smothering Tithe", "Mystic Remora",
    "Sylvan Library", "The One Ring", "Ad Nauseam",
    # Wincons
    "Thassa's Oracle", "Underworld Breach", "Doomsday",
    # Interaction
    "Cyclonic Rift", "Force of Will", "Fierce Guardianship", "Mana Drain",
    # Stax
    "Drannith Magistrate", "Opposition Agent", "Tergrid, God of Fright",
    "Narset, Parter of Veils", "Notion Thief",
}

# Bracket descriptions for Turn Speed Contribution baseline
BRACKET_AVG_TURNS = {1: 12, 2: 9, 3: 7, 4: 5, 5: 3}

# EDHREC rank thresholds (lower = more popular)
EDHREC_TIER_THRESHOLDS = {
    "S": 50,      # Top 50 = S-tier popularity
    "A": 200,     # Top 200 = A-tier
    "B": 1000,    # Top 1000 = B-tier
    "C": 5000,    # Top 5000 = C-tier
    "D": 15000,   # Top 15000 = D-tier
}


def estimate_pir(card: dict, bracket: int) -> float:
    """
    Estimate Popularity-adjusted Inclusion Rate (PIR) using EDHREC rank as proxy.
    Scale: 0.0 to 10.0
    """
    rank = card.get("edhrec_rank")
    if rank is None:
        # No rank = very niche card
        return 2.0 if bracket >= 4 else 1.0

    # Transform rank to 0-10 scale (logarithmic)
    # Rank 1 -> ~10.0, Rank 100 -> ~6.0, Rank 1000 -> ~4.0, Rank 10000 -> ~2.0
    if rank <= 0:
        return 10.0
    score = max(0, 10.0 - 2.0 * math.log10(rank))

    # Bracket adjustment: popular cards in cEDH may be less relevant in casual
    if bracket <= 2 and card.get("name") in GAME_CHANGERS:
        return 0.0  # Game Changers not allowed in B1-B2

    return min(10.0, max(0.0, score))


def estimate_tsc(card: dict, bracket: int) -> float:
    """
    Estimate Turn Speed Contribution (TSC).
    Higher score = card contributes to faster wins.
    Scale: 0.0 to 10.0
    """
    cmc = card.get("cmc", 0)
    avg_turns = BRACKET_AVG_TURNS.get(bracket, 7)
    score = 5.0  # baseline

    # Low CMC cards contribute to speed
    if cmc == 0:
        score += 3.0
    elif cmc <= 1:
        score += 2.5
    elif cmc <= 2:
        score += 1.5
    elif cmc <= 3:
        score += 0.5
    elif cmc >= 6:
        score -= 2.0
    elif cmc >= 4:
        score -= 0.5

    # Mana production accelerates
    if card.get("has_mana_production"):
        score += 1.5

    # Tutors accelerate (find combo pieces)
    if card.get("has_tutor_text"):
        score += 2.0 if bracket >= 4 else 1.0

    # Draw = more consistent, indirectly faster
    if card.get("has_draw_text"):
        score += 1.0

    # Lands (basic function, moderate contribution)
    if card.get("is_land"):
        score = 5.0  # normalize lands

    # Adjust by bracket: speed matters more in higher brackets
    bracket_multiplier = {1: 0.6, 2: 0.7, 3: 0.85, 4: 1.0, 5: 1.2}
    score *= bracket_multiplier.get(bracket, 1.0)

    return min(10.0, max(0.0, score))


def estimate_syn(card: dict, bracket: int) -> float:
    """
    Estimate Synergy Score (SYN) based on card properties.
    Without actual co-occurrence data, this is a rough proxy.
    Scale: 0.0 to 10.0
    """
    score = 5.0
    oracle = (card.get("oracle_text") or "").lower()

    # Combo indicators
    combo_keywords = [
        "you win the game", "infinite", "untap", "copy",
        "exile your library", "mill", "each opponent loses"
    ]
    for kw in combo_keywords:
        if kw in oracle:
            score += 1.5

    # Protection for combo (counterspells, hexproof)
    protection_keywords = ["counter target", "hexproof", "can't be countered", "indestructible"]
    for kw in protection_keywords:
        if kw in oracle:
            score += 0.5

    # Game Changers inherently have high synergy in their brackets
    if card.get("name") in GAME_CHANGERS and bracket >= 3:
        score += 2.0

    return min(10.0, max(0.0, score))


def estimate_flx(card: dict, bracket: int) -> float:
    """
    Estimate Flexibility Score (FLX) based on color identity and card type.
    Scale: 0.0 to 10.0
    """
    score = 5.0
    colors = card.get("color_identity", [])

    # Colorless cards are universally playable
    if len(colors) == 0:
        score += 3.0
    elif len(colors) == 1:
        score += 1.5
    elif len(colors) == 2:
        score += 0.5
    elif len(colors) >= 4:
        score -= 1.5

    # Modal/versatile cards
    oracle = (card.get("oracle_text") or "").lower()
    if "choose one" in oracle or "choose two" in oracle:
        score += 1.0
    if "cycling" in oracle or "channel" in oracle:
        score += 0.5

    # Artifacts and colorless tend to be more flexible
    if card.get("is_artifact") and len(colors) == 0:
        score += 1.0

    return min(10.0, max(0.0, score))


def calculate_cer(card: dict, bracket: int, wir: float = None) -> dict:
    """
    Calculate the Card Effectiveness Rating for a card at a given bracket.

    Args:
        card: Card data from scryfall_lookup.py
        bracket: Target bracket (1-5)
        wir: Win Inclusion Rate from tournament data (None = estimate)

    Returns:
        dict with CER score and component breakdown
    """
    name = card.get("name", "Unknown")

    # Check Game Changer restriction
    if name in GAME_CHANGERS and bracket <= 2:
        return {
            "name": name,
            "bracket": bracket,
            "cer": 0.0,
            "tier": "N/A",
            "note": "Game Changer — no permitido en Bracket 1-2",
            "components": {"wir": 0, "pir": 0, "tsc": 0, "syn": 0, "flx": 0}
        }

    # Check legality
    if card.get("legalities", {}).get("commander") != "legal":
        return {
            "name": name,
            "bracket": bracket,
            "cer": 0.0,
            "tier": "BANNED",
            "note": "Carta no legal en Commander",
            "components": {"wir": 0, "pir": 0, "tsc": 0, "syn": 0, "flx": 0}
        }

    # Calculate or estimate components
    if wir is not None:
        wir_score = wir * 10.0  # Normalize to 0-10
    else:
        # Estimate WIR from EDHREC rank (proxy)
        rank = card.get("edhrec_rank")
        if rank and rank > 0:
            wir_score = max(0, 8.0 - 1.5 * math.log10(rank))
        else:
            wir_score = 5.0

        # Boost for Game Changers in high brackets (these define the meta)
        if name in GAME_CHANGERS and bracket >= 4:
            wir_score = max(wir_score, 8.5)

        # Boost for cards with "you win the game" text in high brackets
        oracle = (card.get("oracle_text") or "").lower()
        if "you win the game" in oracle and bracket >= 4:
            wir_score = max(wir_score, 8.0)

    pir_score = estimate_pir(card, bracket)
    tsc_score = estimate_tsc(card, bracket)
    syn_score = estimate_syn(card, bracket)
    flx_score = estimate_flx(card, bracket)

    # Weighted CER
    cer = (
        wir_score * 0.35 +
        pir_score * 0.25 +
        tsc_score * 0.20 +
        syn_score * 0.10 +
        flx_score * 0.10
    )

    # Determine tier
    if cer >= 9.0:
        tier = "S-Tier"
    elif cer >= 7.5:
        tier = "A-Tier"
    elif cer >= 6.0:
        tier = "B-Tier"
    elif cer >= 4.5:
        tier = "C-Tier"
    elif cer >= 3.0:
        tier = "D-Tier"
    else:
        tier = "F-Tier"

    return {
        "name": name,
        "bracket": bracket,
        "cer": round(cer, 2),
        "tier": tier,
        "price_usd": card.get("price_usd"),
        "cmc": card.get("cmc"),
        "type_line": card.get("type_line"),
        "color_identity": card.get("color_identity"),
        "components": {
            "wir": round(wir_score, 2),
            "pir": round(pir_score, 2),
            "tsc": round(tsc_score, 2),
            "syn": round(syn_score, 2),
            "flx": round(flx_score, 2)
        }
    }


def analyze_deck(cards: list, bracket: int) -> dict:
    """Analyze a full deck and generate summary statistics."""
    results = []
    for card in cards:
        if "error" in card:
            continue
        cer_data = calculate_cer(card, bracket)
        results.append(cer_data)

    results.sort(key=lambda x: x["cer"], reverse=True)

    # Summary stats
    cer_values = [r["cer"] for r in results if r["cer"] > 0]
    tier_counts = {}
    for r in results:
        t = r["tier"]
        tier_counts[t] = tier_counts.get(t, 0) + 1

    total_price = sum(
        float(r.get("price_usd") or 0) for r in results
    )

    summary = {
        "bracket": bracket,
        "total_cards": len(results),
        "avg_cer": round(sum(cer_values) / len(cer_values), 2) if cer_values else 0,
        "median_cer": round(sorted(cer_values)[len(cer_values) // 2], 2) if cer_values else 0,
        "tier_distribution": tier_counts,
        "estimated_price_usd": round(total_price, 2),
        "game_changers_count": sum(1 for r in results if r.get("name") in GAME_CHANGERS),
        "top_10": results[:10],
        "bottom_10": results[-10:] if len(results) >= 10 else results,
        "all_cards": results,
    }

    return summary


def main():
    parser = argparse.ArgumentParser(description="Calculate CER for Commander cards")
    parser.add_argument("--cards", "-c", required=True, help="JSON file with card data")
    parser.add_argument("--bracket", "-b", type=int, required=True, choices=[1,2,3,4,5],
                        help="Target bracket (1-5)")
    parser.add_argument("--output", "-o", help="Output JSON file")
    args = parser.parse_args()

    with open(args.cards) as f:
        cards = json.load(f)

    summary = analyze_deck(cards, args.bracket)
    output = json.dumps(summary, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Report saved to {args.output}", file=sys.stderr)
    else:
        print(output)

    # Print quick summary to stderr
    print(f"\n=== Deck Analysis Summary (Bracket {args.bracket}) ===", file=sys.stderr)
    print(f"Cards analyzed: {summary['total_cards']}", file=sys.stderr)
    print(f"Average CER: {summary['avg_cer']}", file=sys.stderr)
    print(f"Tier distribution: {summary['tier_distribution']}", file=sys.stderr)
    print(f"Estimated price: ${summary['estimated_price_usd']}", file=sys.stderr)


if __name__ == "__main__":
    main()
