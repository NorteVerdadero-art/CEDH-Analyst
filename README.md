# cEDH Deck Analyst

Quantitative analysis tool for MTG Commander decks. Scores cards via **CER (Card Effectiveness Rating)**, fetches live prices from MTGStocks, detects combos via Commander Spellbook, and calculates **BPS (Bracket Proximity Score)**.

## Requirements

- Python 3.9+
- No external packages — stdlib only

## Quick Start

```bash
# Analyze a deck from a plain-text decklist
python3 cedh_analyst.py analyze \
  --decklist decklist.txt \
  --commander "Magda, Brazen Outlaw" \
  --bracket 4 \
  --output ./output/

# Open the generated dashboard
open output/dashboard.html
```

## Accepted Input Formats

### Plain text (`.txt`) — MTGO / MTGA / Moxfield export
```
1 Sol Ring
1x Chrome Mox
1 Ancient Tomb (2ED) 290
Sensei's Divining Top
```

### CSV (`.csv`) — any CSV with a `name` column
```csv
Count,Name,Expansion,Collector Number
1,Sol Ring,CMR,472
1,Chrome Mox,MRD,152
```
Supports Moxfield CSV exports (File → Export → CSV) and Archidekt exports directly.

### JSON (`.json`) — internal format
Pre-scored deck with `name`, `printId`, `cer`, `tier`, `gc`, `cmc`, `type_line` fields.

## CLI Reference

```
python3 cedh_analyst.py analyze
  --decklist  PATH   .txt / .csv / .json decklist (required)
  --commander NAME   Commander name, e.g. "Magda, Brazen Outlaw" (required)
  --bracket   1-5    Power bracket (default: 4)
  --output    DIR    Output directory (default: ./output/)
  --no-prices        Skip MTGStocks price fetch
  --no-combos        Skip Commander Spellbook combo fetch
```

## Output

| File | Contents |
|---|---|
| `output/dashboard.html` | Self-contained interactive HTML dashboard |
| `output/report.json` | Full CER analysis + BPS + combo summary |
| `output/prices.json` | Live prices per card (TCGPlayer, Card Kingdom, Cardmarket, MXN) |
| `output/combos.json` | Commander Spellbook combo data |

## CER Formula

```
CER = (WIR × 0.35) + (PIR × 0.25) + (TSC × 0.20) + (SYN × 0.10) + (FLX × 0.10)
```

| Component | Weight | Description |
|---|---|---|
| WIR — Win Inclusion Rate | 35% | Top-4 appearances / total tournament appearances |
| PIR — Popularity-adjusted Inclusion | 25% | Inclusion rate across bracket decks |
| TSC — Turn Speed Contribution | 20% | CMC + card type vs. average win turn |
| SYN — Synergy Score | 10% | Co-occurrence with other winning cards |
| FLX — Flexibility Score | 10% | Diversity across archetypes |

## Bracket Proximity Score (BPS)

Measures progress toward the next bracket across 5 dimensions: GC Density (30%), CER Average (25%), Win Speed (20%), Tutor Density (15%), Combo Presence (10%).

## Data Sources

| Source | Usage |
|---|---|
| [MTGStocks](https://www.mtgstocks.com) | Live prices (TCGPlayer, Card Kingdom, Cardmarket, TCG.land MXN) |
| [Commander Spellbook](https://commanderspellbook.com) | Combo detection |
| [Scryfall](https://scryfall.com) | Card metadata |
| [WotC Commander banlist](https://mtgcommander.net/index.php/banned-list/) | Ban validation (hardcoded, not MTGStocks) |

## Project Structure

```
cedh_project/
├── cedh_analyst.py          # Main CLI entrypoint
├── fix_print_ids.py         # Utility: resolve MTGStocks print IDs via card_sets API
├── data/
│   └── deck_cards.json      # Example deck (Magda, Brazen Outlaw — B4)
├── skill/scripts/
│   ├── fetch_prices.py      # MTGStocks price fetcher
│   ├── fetch_combos.py      # Commander Spellbook combo fetcher
│   ├── generate_dashboard.py# HTML dashboard generator
│   ├── cer_calculator.py    # CER scoring engine
│   └── resolve_mtgstocks_ids.py  # Print ID resolver
└── output/                  # Generated artifacts (gitignored)
```
