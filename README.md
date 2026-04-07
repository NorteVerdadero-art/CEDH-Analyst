# cEDH Deck Analyst

Quantitative analysis tool for MTG Commander decks. Scores cards via **CER (Card Effectiveness Rating)**, fetches live prices from MTGStocks, detects combos via Commander Spellbook, and calculates **BPS (Bracket Proximity Score)**.

## Requirements

- Python 3.9+
- No external packages — stdlib only

## Quick Start

### Desde un export de Moxfield (recomendado)
```bash
# 1. En Moxfield: File → Export → Text  (o descarga desde el link del error)
# 2. Analiza con precios de Scryfall (TCGPlayer, precisos, sin IDs requeridos)
python3 cedh_analyst.py analyze \
  --decklist magda.txt \
  --commander "Magda, Brazen Outlaw" \
  --bracket 4 \
  --scryfall-prices \
  --output ./output/

open output/dashboard.html
```

### Intento directo con URL de Moxfield
```bash
# Funciona si Moxfield no bloquea la request; si falla, da instrucciones
python3 cedh_analyst.py analyze \
  --moxfield "https://www.moxfield.com/decks/TuDeckId" \
  --bracket 4 \
  --output ./output/
```

### Desde archivo local (JSON interno)
```bash
python3 cedh_analyst.py analyze \
  --decklist data/deck_cards.json \
  --commander "Magda, Brazen Outlaw" \
  --bracket 4 \
  --output ./output/
```

### Re-generar dashboard sin re-fetchear (usa caché)
```bash
# prices.json y combos.json ya existen en ./output/ → los carga automáticamente
python3 cedh_analyst.py analyze \
  --decklist magda.txt \
  --commander "Magda, Brazen Outlaw" \
  --bracket 4

# Forzar re-fetch de todo
python3 cedh_analyst.py analyze \
  --decklist magda.txt \
  --commander "Magda, Brazen Outlaw" \
  --bracket 4 \
  --refresh
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
  # Input (uno de los dos es requerido):
  --moxfield  URL    URL de un deck público en Moxfield
  --decklist  PATH   .txt / .csv / .json decklist

  # Opciones:
  --commander NAME   Comandante (auto-detectado desde Moxfield si no se especifica)
  --bracket   1-5    Power bracket (default: 4)
  --output    DIR    Output directory (default: ./output/)
  --scryfall-prices  Usa Scryfall (TCGPlayer) para precios — sin MTGStocks print IDs
  --no-prices        Omite fetch de precios
  --no-combos        Omite fetch de combos de Commander Spellbook
  --refresh          Re-fetch precios y combos aunque exista caché
```

### Fuentes de precios

| Fuente | Cómo activarla | Precisión |
|---|---|---|
| **Scryfall** (recomendado) | `--scryfall-prices` | Alta — TCGPlayer actualizado diario |
| **MTGStocks** (legado) | default si hay `prices.json` | Variable — depende de print IDs correctos |
| **Moxfield API** | `--moxfield URL` | Alta — si no está bloqueado por Cloudflare |

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
