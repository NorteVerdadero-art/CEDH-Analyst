# Data Sources Reference — cEDH Deck Analyst

## Tabla de Contenidos
1. [Scryfall API](#scryfall-api)
2. [EDH Top 16](#edh-top-16)
3. [cEDH Analytics](#cedh-analytics)
4. [Commander Spellbook](#commander-spellbook)
5. [Moxfield](#moxfield)
6. [Spicerack](#spicerack)
7. [EDHREC](#edhrec)
8. [MTG Top 8](#mtg-top-8)
9. [Pipeline de Datos Recomendado](#pipeline-de-datos)

---

## Scryfall API

La fuente autoritativa para datos de cartas individuales.

### Endpoints principales

| Endpoint | Método | Uso |
|----------|--------|-----|
| `/cards/named?exact={name}` | GET | Buscar carta por nombre exacto |
| `/cards/named?fuzzy={name}` | GET | Buscar carta por nombre aproximado |
| `/cards/search?q={query}` | GET | Búsqueda avanzada con sintaxis Scryfall |
| `/cards/collection` | POST | Buscar múltiples cartas (hasta 75 por request) |
| `/bulk-data` | GET | URLs de descarga masiva (actualización diaria) |

### Campos relevantes para CER

```json
{
  "name": "Rhystic Study",
  "mana_cost": "{2}{U}",
  "cmc": 3.0,
  "type_line": "Enchantment",
  "oracle_text": "Whenever an opponent casts a spell...",
  "colors": ["U"],
  "color_identity": ["U"],
  "legalities": {"commander": "legal"},
  "edhrec_rank": 5,
  "prices": {"usd": "38.50", "usd_foil": "42.00"},
  "keywords": ["Draw"],
  "rarity": "rare"
}
```

### Rate Limits
- 10 requests/segundo (100ms entre requests)
- Usar `/cards/collection` para batches de hasta 75 cartas
- Para análisis masivo, descargar bulk data (JSON diario)

### Sintaxis de búsqueda útil
- `f:commander` — Legal en Commander
- `ci:wubr` — Identidad de color (White, Blue, Black, Red)
- `t:instant cmc<=2` — Instants con CMC ≤ 2
- `is:gameChanger` — No disponible en Scryfall, usar lista interna

---

## EDH Top 16

La fuente principal de resultados de torneos competitivos de EDH.

### Arquitectura
- Next.js frontend + Relay (GraphQL client)
- Los datos se cargan vía queries GraphQL internas
- No hay API REST documentada públicamente (Issue #285 abierto)

### Datos disponibles en el sitio
- **Comandantes**: meta share, entries totales, filtros por tamaño de torneo
- **Torneos**: fecha, nombre, # jugadores, ganador, bracket
- **Decklists**: link a Moxfield del decklist registrado
- **Filtros de torneo**: 16+, 30+, 50+, 100+, 250+ jugadores
- **Filtros de tiempo**: 1 mes, 3 meses, 6 meses, Post Ban, 1 año, All Time

### Cómo obtener datos
1. **Web search** para consultar el estado actual del metagame
2. **Web fetch** de páginas específicas del sitio
3. **cEDH Analytics** como fuente pre-procesada de los mismos datos

### Meta share actual (Bracket 5, último año, 50+ jugadores)
Los top comandantes se actualizan frecuentemente. Siempre verificar con web search.
Como referencia, los más representados históricamente incluyen:
- Kraum/Tymna (Blue Farm) ~10% meta share
- Kinnan, Bonder Prodigy ~7-8%
- Rograkh/Thrasios ~5-6%
- Rograkh/Silas Renn ~4-5%
- Sisay, Weatherlight Captain ~3-4%

---

## cEDH Analytics (Carrot Compost)

Proyecto chileno que ya realiza el pipeline EDH Top 16 → Moxfield → Scryfall/MTGJSON.

### Metodología
- Timeframe: 1 año rolling desde la fecha actual
- Solo torneos con 48+ jugadores
- Solo decklists de Moxfield (links rotos excluidos)
- Secciones: Metagame, Tournaments, Metagame Cards, DDB Cards

### Secciones del sitio
- `/metagame` — Meta share de comandantes, tendencias
- `/tournaments` — Lista de torneos incluidos en el análisis
- `/metagame/cards` — Cartas más jugadas en el meta competitivo
- DDB Cards — Cartas de la cEDH Decklist Database

### Valor para el skill
Esta fuente ya hizo el trabajo pesado de agregar datos. Usarla como primera
referencia para análisis de Bracket 5, luego complementar con Scryfall para
datos de carta y Commander Spellbook para combos.

---

## Commander Spellbook

Base de datos de combos de Commander con estimación de bracket.

### Endpoint de estimación de bracket
```
POST https://backend.commanderspellbook.com/estimate-bracket
Content-Type: application/json

{"main": ["Sol Ring", "Mana Crypt", "Thassa's Oracle", ...]}
```

### Mapeo de buckets a brackets numéricos
| Bucket Spellbook | Bracket numérico |
|-----------------|-----------------|
| Casual | 1 |
| Precon Appropriate | 2 |
| Oddball | 2 |
| Powerful | 3 |
| Spicy | 3 |
| Ruthless | 4 |

Nota: Bracket 5 (cEDH) no tiene mapeo directo — se infiere cuando el deck
tiene múltiples combos "Ruthless" y alta densidad de tutors/fast mana.

### Endpoint find-my-combos
Para obtener todos los combos posibles de un deck:
```
POST https://backend.commanderspellbook.com/find-my-combos
Content-Type: application/json

{"main": ["Card1", "Card2", ...]}
```

---

## Moxfield

Plataforma de deckbuilding más usada en la comunidad.

### API no oficial
- Base: `https://api2.moxfield.com/v3/decks/all/{deck_id}`
- Requiere header `User-Agent` válido
- Protegido por Cloudflare — puede requerir workarounds
- Librerías community: `moxfield-api` (npm), `mtg-parser` (Python)

### Datos disponibles por decklist
- Lista completa de cartas con cantidades
- Comandante(s)
- Categorías/tags del usuario
- Fecha de última modificación
- Precio estimado del deck

### Limitación importante
Los decklists de Moxfield son dinámicos — representan el estado ACTUAL del deck,
no la versión jugada en un torneo específico. EDH Top 16 ha implementado
"snapshots" para eventos de Command Tower, pero no es universal.

---

## Spicerack

Plataforma de gestión de torneos con API pública de decklists.

### Endpoint
```
GET https://api.spicerack.gg/api/export-decklists/
  ?num_days=365
  &event_format=COMMANDER
  &decklist_as_text=true
Headers: X-API-Key: {api_key}
```

### Datos incluidos
- Tournament ID, nombre, formato
- # jugadores, rondas suizas, tamaño del top cut
- Standings: nombre, decklist URL, W/L en suizo y bracket
- Decklists como texto cuando `decklist_as_text=true`

### Valor único
Es la única fuente que incluye record W/L individual por jugador,
lo que permite calcular WIR (Win Inclusion Rate) con mayor precisión.

---

## EDHREC

La fuente más completa para Commander casual/mid-power.

### No tiene API pública
- Consultar vía web search: "site:edhrec.com [card name] commander"
- Datos útiles: % de decks que incluyen una carta, synergia con comandante
- Guías de brackets disponibles

### Valor para brackets 1-4
Donde los datos de torneos son escasos, EDHREC proporciona:
- Popularidad relativa de cartas por comandante
- Synergy score (qué tan específica es una carta para ese comandante)
- "Salt score" (percepción social de poder)
- Recomendaciones de cortes y adiciones

---

## MTG Top 8

Fuente histórica de datos de torneos en múltiples formatos.

### cEDH en MTG Top 8
- URL: `https://www.mtgtop8.com/format?f=cEDH`
- Datos más limitados que EDH Top 16 para Commander específicamente
- Útil como referencia cruzada para validar tendencias

---

## Pipeline de Datos Recomendado

### Para análisis de Bracket 5 (cEDH)

```
1. Web search → edhtop16.com (meta share, top comandantes)
2. Web search → cedh-analytics.com (cartas más jugadas, tendencias)
3. Scryfall API → datos de cada carta del deck
4. Commander Spellbook → combos del deck, estimación de bracket
5. Cruzar: cartas del deck vs. cartas de decks ganadores
6. Calcular CER por carta
7. Generar reporte
```

### Para análisis de Brackets 1-4

```
1. Commander Spellbook → estimate-bracket del deck
2. Scryfall API → datos de cada carta
3. Web search → edhrec.com (popularidad, synergy)
4. Comparar Game Changers list vs. cartas del deck
5. Estimar CER usando proxies (CMC, tipo, EDHREC rank)
6. Generar reporte
```

### Para análisis de metagame general

```
1. Web search → edhtop16.com con filtros de tamaño y período
2. Web search → cedh-analytics.com/metagame
3. Compilar top comandantes y meta shares
4. Para cada comandante top, identificar staples
5. Identificar tendencias (cartas subiendo/bajando)
6. Generar meta report
```
