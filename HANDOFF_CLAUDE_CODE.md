# cEDH Deck Analyst — Handoff a Claude Code
**Fecha:** Abril 3, 2026  
**Contexto:** Proyecto construido en Claude.ai — listo para continuar en Claude Code

---

## 1. QUÉ ES ESTE PROYECTO

Herramienta de análisis cuantitativo de decks de MTG Commander. Evalúa cartas mediante
un score llamado **CER (Card Effectiveness Rating)**, genera dashboards HTML interactivos
con precios en vivo desde MTGStocks, detecta combos vía Commander Spellbook, y calcula
el **BPS (Bracket Proximity Score)** — qué tan cerca está un deck de subir de bracket.

**Deck de prueba:** Magda, Brazen Outlaw (Mono-R, Bracket 4 Optimized)  
**CER promedio del deck:** 5.49  
**BPS hacia B5:** 32%  
**Precio total estimado:** ~$893 USD

---

## 2. ESTRUCTURA DE ARCHIVOS DEL SKILL

El skill está instalado en `/mnt/skills/user/cedh-deck-analyst/` (read-only en Claude.ai).
Para Claude Code, recrear esta estructura:

```
cedh-deck-analyst/
├── SKILL.md                          # Instrucciones completas del skill
├── references/
│   ├── data-sources.md               # Arquitectura de fuentes de datos
│   └── game-changers.md              # Lista de 53 Game Changers (feb 2026)
└── scripts/
    ├── scryfall_lookup.py            # Fetch de datos de cartas via Scryfall API
    ├── cer_calculator.py             # Calcula CER por carta y genera report.json
    └── generate_dashboard.py         # Genera dashboard HTML con Chart.js
```

**NOTA:** En Claude.ai el script `scryfall_lookup.py` no puede correr porque `api.scryfall.com`
no está en la allowlist de red. En Claude Code esto debería funcionar directamente.

---

## 3. METODOLOGÍA CER

```
CER = (WIR × 0.35) + (PIR × 0.25) + (TSC × 0.20) + (SYN × 0.10) + (FLX × 0.10)
```

| Componente | Peso | Descripción |
|---|---|---|
| WIR — Win Inclusion Rate | 35% | Apariciones en Top 4 / apariciones totales en torneo |
| PIR — Popularity-adjusted Inclusion Rate | 25% | Inclusión en decks del bracket / total decks |
| TSC — Turn Speed Contribution | 20% | Proxy CMC + tipo de carta vs. turno de victoria |
| SYN — Synergy Score | 10% | Co-ocurrencia con otras cartas ganadoras |
| FLX — Flexibility Score | 10% | Diversidad de arquetipos que la incluyen |

**Escala:**
- S-Tier (9.0-10.0): Auto-include
- A-Tier (7.5-8.9): Core
- B-Tier (6.0-7.4): Strong
- C-Tier (4.5-5.9): Viable
- D-Tier (3.0-4.4): Marginal
- F-Tier (0.0-2.9): Cut

---

## 4. BRACKET PROXIMITY SCORE (BPS)

Mide progreso hacia el siguiente bracket. 5 dimensiones:

| Dimensión | Peso | Rango B4 mín | Rango B5 mín |
|---|---|---|---|
| GC Density | 30% | 4 GCs | 9+ GCs |
| CER Promedio | 25% | 6.5 | 8.0+ |
| Win Speed | 20% | T6 | T4 |
| Tutor Density | 15% | 6 tutors | 10+ |
| Combo Presence | 10% | 2 combos | 3+ |

**BPS del deck Magda actual:** 32%
- GC Density: 6/9 → 40% (Chrome Mox, Ancient Tomb, The One Ring, Sensei's Top, Gamble, Jeska's Will)
- CER Promedio: 5.49 vs 8.0 → 0%
- Win Speed: T5 vs T4 → 50%
- Tutors: 4 vs 10 → 0%
- Combos: 4 → 100%

---

## 5. FUENTES DE DATOS

### APIs que FUNCIONAN (confirmado en browser):
```
GET https://api.mtgstocks.com/prints/{printId}
```
Devuelve precio de:
- `tcgplayer.latestPrice.market` — TCGPlayer market price
- `tcgplayer.latestPrice.avg` — TCGPlayer average
- `cardkingdom.latestPrice.avg` — Card Kingdom
- `starcitygames.latestPrice.avg` — Star City Games
- `cardmarket.latestPrice.avg` — Cardmarket (EUR)
- `tcgland.mxn` — TCG.land precio en MXN 🇲🇽
- `card.legal.commander` → `"legal"` | `"gc"` | `"banned"` (!!!)

**IMPORTANTE:** Este endpoint también devuelve el estatus de legalidad en Commander:
- `"gc"` = Game Changer
- `"banned"` = Baneada
- `"legal"` = Legal sin restricción

Esto permite **validar bans y GCs en tiempo real** sin listas hardcodeadas.

### APIs pendientes de implementar en Claude Code:
```
POST https://backend.commanderspellbook.com/find-my-combos
Body: { "main": ["Card1", "Card2"...], "commander": ["Magda, Brazen Outlaw"] }
```
En Claude.ai hubo CORS. En Claude Code (servidor) debería funcionar sin problema.

```
GET https://api.scryfall.com/cards/collection  (bulk lookup)
GET https://api.scryfall.com/cards/named?exact=Sol+Ring
```
No disponible en Claude.ai por allowlist. En Claude Code funciona.

---

## 6. ESTADO ACTUAL DEL DASHBOARD (v4)

El archivo `magda_dashboard_v4.html` (119KB) incluye:

### ✅ Funcionando
- 6 gráficas con Chart.js (Tier donut, CER histogram, Mana curve, Type pie, CER vs Price scatter, Radar WIR/PIR/TSC/SYN/FLX)
- Precios en vivo desde MTGStocks API (fetch en browser, 180ms delay entre cartas)
- Multi-tienda: TCGPlayer, Card Kingdom, Star City, TCG.land MXN, Cardmarket
- Sort/filter en tabla: por CER, A-Z, precio ↓, precio ↑, tier
- BPS gauge SVG con barras por dimensión
- Banner de correcciones (Dockside/Mana Crypt banned, GCs reales)
- Upgrades corregidos para identidad de color Mono-R

### 🚧 Parcialmente implementado (CORS en browser)
- Combos Commander Spellbook: el HTML tiene el código y fallback, pero `find-my-combos` 
  puede bloquearse por CORS en algunos browsers. En Claude Code hacer el fetch server-side
  y embeber los combos en el HTML generado.

### ❌ Pendiente
- Scryfall lookup real (IDs de print hardcodeados, idealmente resolverlos por nombre)
- Historiales de precio (endpoint `/prints/{id}/prices` devuelve 32K fields pero no se usa aún)
- Comparativa vs. decks de torneo reales de EDH Top 16

---

## 7. DATOS DEL DECK DE PRUEBA (Magda, Brazen Outlaw)

### Cartas con print IDs de MTGStocks confirmados
Archivo: `deck_cards.json` — 80 cartas con:
```json
{
  "name": "Chrome Mox",
  "printId": 5858,
  "cer": 7.9,
  "tier": "A-Tier",
  "gc": true,
  "cmc": 0,
  "type_line": "Artifact"
}
```

### Game Changers reales en el deck (lista feb 2026, 53 GCs total)
1. Ancient Tomb
2. Chrome Mox
3. The One Ring
4. Sensei's Divining Top
5. Gamble
6. Jeska's Will

### Cartas custom/inválidas en la lista original (excluir del análisis)
- Fire Nation Turret
- Knuckles the Echidna
- RMS Titanic
- Vivi's Thunder Magic
- Ya viene el coco

### Banlist vigente (sep 2024, confirmado feb 2026)
- Dockside Extortionist — BANNED
- Mana Crypt — BANNED
- Jeweled Lotus — BANNED
- Nadu, Winged Wisdom — BANNED

---

## 8. TAREAS PRIORITARIAS PARA CLAUDE CODE

### Prioridad 1 — Pipeline completo funcional
```bash
# 1. Resolver print IDs de MTGStocks por nombre de carta
#    (actualmente hardcodeados en deck_cards.json)
python3 scripts/resolve_mtgstocks_ids.py --deck decklist.txt --output deck_with_ids.json

# 2. Fetch precios en batch (server-side, sin CORS)
python3 scripts/fetch_prices.py --cards deck_with_ids.json --output prices.json

# 3. Fetch combos (server-side)
python3 scripts/fetch_combos.py --cards decklist.txt --commander "Magda, Brazen Outlaw" --output combos.json

# 4. Generar dashboard completo
python3 scripts/generate_dashboard.py --data report.json --prices prices.json --combos combos.json --output dashboard.html
```

### Prioridad 2 — Resolver MTGStocks print IDs dinámicamente
MTGStocks no tiene un endpoint de búsqueda por nombre documentado. Opciones:
- Scryfall → obtener `name` → buscar en MTGStocks por slug `{id}-{name-slugified}`
- O usar el endpoint de búsqueda de MTGStocks que aparece en el sitio (inspeccionar Network tab)

### Prioridad 3 — Integración real con EDH Top 16
- Scrapin o GraphQL de `edhtop16.com` para WIR real de Bracket 5
- Reemplazar CER proxy por datos reales de torneos

### Prioridad 4 — CLI completo
```bash
cedh-analyst analyze --commander "Magda, Brazen Outlaw" --decklist magda.txt --bracket 4 --output ./output/
```

---

## 9. BANLIST Y GAME CHANGERS — FUENTES AUTORITATIVAS

| Recurso | URL | Notas |
|---|---|---|
| Banlist oficial | `mtgcommander.net/index.php/banned-list/` | Última actualización feb 2026 |
| Game Changers | `scrollvault.net/guides/game-changers.html` | 53 cartas, feb 9 2026 |
| MTGStocks live | `api.mtgstocks.com/prints/{id}` → `card.legal.commander` | Tiempo real |
| Precios | `api.mtgstocks.com/prints/{id}` | TCGPlayer, CK, SCG, MXN |

---

## 10. LINKS ÚTILES

- Dashboard v4 con precios en vivo: `magda_dashboard_v4.html`
- Deck cards con print IDs: `deck_cards.json`
- Skill completo: `cedh-deck-analyst.skill`
- Commander Spellbook API: `https://backend.commanderspellbook.com/schema/swagger/`
- MTGStocks Game Changers list: `https://www.mtgstocks.com/lists/124-commander-game-changers`

---

## 11. NOTAS DE DISEÑO

- Dashboard usa **JetBrains Mono + Space Grotesk**, tema dark (`#0a0a0f`)
- Paleta: gold `#FFD700`, green `#4CAF50`, blue `#2196F3`, orange `#FF9800`, red `#F44336`, accent `#7C4DFF`
- Chart.js 4.4.7 via CDN
- El skill opera en **español** por defecto
- Identidad de color del deck de prueba: **Mono-R** — todas las sugerencias deben respetar esto
- Rate limit MTGStocks: 180ms entre requests funciona sin ban
