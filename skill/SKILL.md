---
name: cedh-deck-analyst
description: >
  Analista cuantitativo de decks de MTG Commander por bracket (1-5), basado en datos
  de torneos competitivos (cEDH y EDH). Úsalo siempre que el usuario mencione: análisis
  de deck Commander, mejoras competitivas de deck, evaluación de cartas por bracket,
  winrate de comandantes, meta cEDH, meta EDH, staples por bracket, card effectiveness,
  qué cartas cortar o agregar a un deck, comparar decks, optimización de deck Commander,
  "qué tan bueno es mi deck", bracket rating, Game Changers, análisis de torneo EDH/cEDH,
  card performance, win contribution, o cualquier análisis data-driven de Commander.
  También actívalo cuando el usuario pegue una lista de cartas, un link de Moxfield, o
  pida recomendaciones de cartas basadas en resultados de torneos. Opera en español por
  defecto. Cubre brackets 1 (Exhibition) a 5 (cEDH).
---

# cEDH Deck Analyst

Skill para analizar decks de MTG Commander usando datos de torneos competitivos, asignando
valores cuantitativos a cada carta según su efectividad medida en resultados reales.

## Problema que resuelve

Los jugadores de Commander eligen cartas por intuición, recomendaciones de creadores de
contenido, o "vibes". Este skill convierte la selección de cartas en una decisión basada
en datos: porcentaje de inclusión en decks ganadores, contribución a la velocidad de
victoria, y rendimiento por bracket.

## Fuentes de datos

El skill utiliza un pipeline de datos multi-fuente. Lee `references/data-sources.md`
para entender la arquitectura completa de cada fuente antes de hacer cualquier consulta.

### Fuentes primarias (consultables vía API)

1. **Scryfall API** — Datos de cartas (Oracle text, CMC, tipo, colores, legalidad, precios)
   - Endpoint: `https://api.scryfall.com/cards/search`
   - Rate limit: 10 req/seg, respetar delay de 100ms entre requests
   - Bulk data disponible para descargas masivas

2. **EDH Top 16** — Datos de torneos cEDH (resultados, decklists, comandantes)
   - Sitio: `https://edhtop16.com`
   - Usa GraphQL internamente (Next.js + Relay)
   - Filtros disponibles: tamaño de torneo (16+, 30+, 50+, 100+, 250+), período, comandante
   - La API no está documentada oficialmente — usar web scraping o datos agregados

3. **cEDH Analytics (Carrot Compost)** — Metagame agregado del último año
   - Sitio: `https://www.cedh-analytics.com`
   - Ya procesa el pipeline: EDH Top 16 → Moxfield → Scryfall/MTGJSON
   - Criterio: torneos con 48+ jugadores, decklists de Moxfield
   - Secciones: Metagame, Tournaments, Metagame Cards, DDB Cards

4. **Commander Spellbook** — Combos y estimación de bracket
   - Endpoint: `https://backend.commanderspellbook.com/estimate-bracket`
   - Swagger: `https://backend.commanderspellbook.com/schema/swagger/`
   - Mapeo de buckets a brackets: Ruthless→4, Spicy→3, Powerful→3, Oddball→2, Precon Appropriate→2, Casual→1

5. **Moxfield** — Decklists individuales
   - API no oficial, requiere User-Agent específico
   - Librería community: `moxfield-api` (npm) o wrapper FastAPI
   - Endpoint pattern: `https://api2.moxfield.com/v3/decks/all/{deck_id}`

6. **Spicerack** — Datos de torneos con decklists y standings
   - Endpoint: `https://api.spicerack.gg/api/export-decklists/`
   - Requiere API key, incluye W/L record por jugador

### Fuentes secundarias (contexto y referencia)

7. **MTG Top 8** — Datos históricos de cEDH: `https://www.mtgtop8.com/format?f=cEDH`
8. **cEDH Hub (cedhhub.com)** — Tournament Top 16 decklists curadas
9. **EDHREC** — Popularidad de cartas en Commander general, guías de brackets
10. **cEDH Data (cedhdata.com)** — Analytics avanzado de cartas competitivas
11. **cedh.io** — Herramienta de análisis de deck contra resultados de torneos

## Sistema de Brackets (Febrero 2025 - Actualizado Febrero 2026)

El sistema tiene 5 niveles. Es fundamental para el análisis porque una carta puede ser
excelente en Bracket 3 pero irrelevante en Bracket 5, y viceversa.

| Bracket | Nombre | Descripción | Turnos esperados | Game Changers |
|---------|--------|-------------|------------------|---------------|
| 1 | Exhibition | Ultra-casual, temático, sin optimizar | 10+ | No permitidos |
| 2 | Core | Directo, sin optimizar, bread-and-butter | 8-10 | No permitidos |
| 3 | Upgraded | Sinergia fuerte, calidad alta de cartas | 6-8 | Algunos permitidos |
| 4 | Optimized | Letal, consistente, rápido | 4-6 | Esperados |
| 5 | cEDH | Play to Win, sin restricciones de budget | 2-4 | Definitorios |

### Game Changers (Lista Vigente Oct 2025)
Cartas con impacto desproporcionado en el juego. Ver `references/game-changers.md`
para la lista completa actualizada.

Incluye: Rhystic Study, Cyclonic Rift, Demonic Tutor, Necropotence, Gaea's Cradle,
Mana Vault, The One Ring, Thassa's Oracle, Underworld Breach, entre otros.

Removidos en Oct 2025: Expropriate, Yuriko, Winota, Kinnan, Deflecting Swat.

## Metodología de Análisis: Card Effectiveness Rating (CER)

El CER es el valor central que asigna este skill a cada carta. Es un score compuesto
que mide qué tan efectivamente una carta contribuye a ganar partidas en torneos.

### Componentes del CER

```
CER = (WIR × 0.35) + (PIR × 0.25) + (TSC × 0.20) + (SYN × 0.10) + (FLX × 0.10)
```

Donde:

1. **WIR (Win Inclusion Rate)** — 35% del peso
   - Fórmula: `(Apariciones en decks Top 4) / (Apariciones totales en torneo)`
   - Mide: ¿Esta carta aparece más en decks que ganan vs. decks que pierden?
   - Fuente: EDH Top 16, Spicerack standings data

2. **PIR (Popularity-adjusted Inclusion Rate)** — 25% del peso
   - Fórmula: `(Inclusión en decks del bracket) / (Total decks del bracket)`
   - Ajustado por: disponibilidad de colores del comandante
   - Mide: ¿Qué tan "staple" es esta carta dentro de su bracket?
   - Fuente: cEDH Analytics metagame cards, EDHREC

3. **TSC (Turn Speed Contribution)** — 20% del peso
   - Proxy basado en: CMC de la carta, tipo (mana rock, tutor, combo piece, wincon)
   - Cruzado con: turno promedio de victoria de decks que la incluyen
   - Mide: ¿Esta carta acelera o ralentiza el reloj de victoria?
   - Fuente: Scryfall (CMC, tipo), torneos (turno de victoria cuando disponible)

4. **SYN (Synergy Score)** — 10% del peso
   - Basado en: co-ocurrencia con otras cartas en decks ganadores
   - Mide: ¿Esta carta habilita combos o estrategias ganadoras?
   - Fuente: Commander Spellbook (combos), co-ocurrencia en decklists

5. **FLX (Flexibility Score)** — 10% del peso
   - Basado en: número de arquetipos/comandantes diferentes que la incluyen
   - Mide: ¿Es versátil o es narrow?
   - Fuente: EDH Top 16 (diversidad de comandantes que la usan)

### Escala CER

| CER | Calificativo | Significado |
|-----|-------------|-------------|
| 9.0-10.0 | S-Tier (Staple Absoluto) | Auto-include en cualquier deck del bracket con sus colores |
| 7.5-8.9 | A-Tier (Core) | Incluir salvo razón específica para no hacerlo |
| 6.0-7.4 | B-Tier (Strong) | Sólida, incluir si la estrategia lo permite |
| 4.5-5.9 | C-Tier (Viable) | Funcional pero con alternativas mejores |
| 3.0-4.4 | D-Tier (Marginal) | Solo en builds muy específicos o budget |
| 0.0-2.9 | F-Tier (Cut) | No justificable en el bracket, candidata a corte |

### CER por Bracket

El CER se calcula **por bracket**, no globalmente. Demonic Tutor puede ser:
- Bracket 5 (cEDH): CER 9.8 (S-Tier — staple absoluto)
- Bracket 3 (Upgraded): N/A (es Game Changer, no permitido en B1-B2, pero relevante en B3+)
- Bracket 1 (Exhibition): CER 0.0 (Game Changer, no aplica)

## Flujo de análisis de un deck

Cuando el usuario proporcione un deck (lista de cartas, link de Moxfield, o descripción):

### Paso 1: Identificar el deck
- Extraer la lista de cartas (desde texto, link de Moxfield, o input manual)
- Identificar el comandante
- Determinar la identidad de color

### Paso 2: Clasificar el bracket
- Usar Commander Spellbook `/estimate-bracket` si hay combos
- Cruzar con Game Changers list
- Evaluar la curva de mana, densidad de tutors, velocidad teórica
- Proponer bracket estimado (1-5) con justificación

### Paso 3: Calcular CER por carta
- Para cada carta, buscar datos en las fuentes disponibles
- Si hay datos de torneos directos (Bracket 5), usar WIR real
- Si no hay datos directos (Brackets 1-3), usar PIR de EDHREC + proxy de TSC
- Generar tabla ordenada por CER descendente

### Paso 4: Identificar mejoras
- Cartas con CER bajo → candidatas a corte (justificar por qué)
- Cartas ausentes con CER alto para ese bracket/colores → candidatas a inclusión
- Priorizar mejoras por impacto esperado (delta CER del deck promedio)

### Paso 5: Generar reporte
Usar esta estructura:

```
## Análisis de Deck: [Comandante]
### Bracket estimado: [X] ([Nombre del bracket])

### Resumen ejecutivo
- CER promedio del deck: X.X
- Cartas S-Tier: X/99
- Cartas por debajo de C-Tier: X/99
- Budget estimado: $X.XX USD

### Top 10 cartas por CER
| # | Carta | CER | Tier | Rol |
|---|-------|-----|------|-----|

### Bottom 10 cartas (candidatas a corte)
| # | Carta | CER | Tier | Razón del bajo score |
|---|-------|-----|------|---------------------|

### Mejoras sugeridas
| Cortar | CER actual | Incluir | CER esperado | Delta | Precio |
|--------|-----------|---------|-------------|-------|--------|

### Distribución por Tier
- S-Tier: X cartas (X%)
- A-Tier: X cartas (X%)
- ...

### Curva de mana vs. meta del bracket
[Comparar la curva del deck vs. decks ganadores del bracket]
```

## Análisis de metagame por bracket

Cuando el usuario pida un análisis general del meta (no de un deck específico):

1. Consultar EDH Top 16 con filtro de tamaño de torneo apropiado:
   - Bracket 5 (cEDH): torneos de 50+ jugadores
   - Bracket 4 (Optimized): torneos de 30+ jugadores
   - Brackets 1-3: datos de EDHREC y Commander general

2. Reportar:
   - Top 10 comandantes por meta share y winrate
   - Top 20 cartas más incluidas en decks ganadores
   - Tendencias (cartas subiendo/bajando en inclusión)
   - Combos más frecuentes en Top 4

3. Formato del reporte de metagame:
```
## Meta Report: Bracket [X] — [Período]
### Tier List de Comandantes
### Staples universales del bracket
### Cartas en ascenso (trending up)
### Cartas en descenso (trending down)
### Combos dominantes
### Recomendaciones para la mesa local
```

## Modo "Consulta Local"

Cuando el usuario mencione "mi ciudad", "mi LGS", "mi playgroup", o "mi mesa":

- Preguntar por el bracket predominante en su grupo
- Ajustar recomendaciones al contexto local:
  - Si es Bracket 3 local, no recomendar cartas de $50+ USD
  - Si es Bracket 5, priorizar consistencia sobre budget
  - Considerar el meta local descrito por el usuario como variable adicional

## Consultas a APIs

### Scryfall — Buscar datos de una carta
```bash
curl -s "https://api.scryfall.com/cards/named?exact=Rhystic+Study" \
  -H "User-Agent: CEDHDeckAnalyst/1.0" \
  -H "Accept: application/json"
```
Campos útiles: `name`, `mana_cost`, `cmc`, `type_line`, `oracle_text`,
`colors`, `color_identity`, `legalities`, `edhrec_rank`, `prices`

### Scryfall — Buscar múltiples cartas
```bash
curl -s "https://api.scryfall.com/cards/collection" \
  -H "User-Agent: CEDHDeckAnalyst/1.0" \
  -H "Content-Type: application/json" \
  -d '{"identifiers":[{"name":"Sol Ring"},{"name":"Mana Crypt"}]}'
```

### Commander Spellbook — Estimar bracket de un deck
```bash
curl -s -X POST "https://backend.commanderspellbook.com/estimate-bracket" \
  -H "Content-Type: application/json" \
  -d '{"main":["Sol Ring","Mana Crypt","Thassa'\''s Oracle",...]}'
```

### EDH Top 16 — Datos de la web
El sitio no expone una API REST documentada. Para obtener datos:
- Usar web search para consultar el meta actual del sitio
- Parsear datos de la página con filtros de tamaño y período
- Alternativa: usar datos pre-procesados de cEDH Analytics

### EDHREC — Datos de popularidad
- No tiene API pública oficial
- Usar como referencia cualitativa vía web search
- Útil para Brackets 1-4 donde datos de torneos son escasos

## Visualización: Dashboard HTML con Chart.js

Después del análisis CER, generar un dashboard visual descargable usando
`scripts/generate_dashboard.py`. El dashboard incluye 6 gráficas + 2 tablas:

### Gráficas incluidas

1. **Distribución por Tier** (Donut) — Cuántas cartas hay en cada tier S/A/B/C/D/F
2. **Distribución de CER** (Histograma) — Cómo se distribuyen los scores del deck
3. **Curva de Mana** (Bar) — Cartas por CMC, comparable con el meta del bracket
4. **Distribución por Tipo** (Pie) — Creature vs. Instant vs. Artifact, etc.
5. **CER vs. Precio USD** (Scatter) — Value map para encontrar upgrades eficientes
6. **Componentes CER Radar** (Radar) — Desglose WIR/PIR/TSC/SYN/FLX de las top cartas

### Tablas incluidas
- Top 10 cartas por CER (con desglose de componentes)
- Bottom 10 cartas (candidatas a corte, con notas)

### Pipeline de generación

```bash
# Paso 1: Obtener datos de Scryfall
python3 scripts/scryfall_lookup.py --file decklist.txt --output cards.json

# Paso 2: Calcular CER
python3 scripts/cer_calculator.py --cards cards.json --bracket 5 --output report.json

# Paso 3: Generar dashboard
python3 scripts/generate_dashboard.py --data report.json --commander "Kraum / Tymna" --output dashboard.html
```

El HTML resultante es un archivo standalone (usa Chart.js vía CDN) con tema dark,
tipografía JetBrains Mono + Space Grotesk, y tooltips interactivos. Se puede abrir
directamente en cualquier navegador.

Cuando Claude ejecute el análisis dentro de la conversación, debe:
1. Ejecutar los scripts en secuencia
2. Generar el HTML y ofrecerlo como archivo descargable
3. Además, presentar un resumen en texto con los hallazgos clave

## Limitaciones y supuestos explícitos

1. **Datos de Brackets 1-3 son limitados**: La mayoría de datos de torneos viene de
   cEDH (Bracket 5). Para brackets más bajos, el CER se basa más en proxies
   (EDHREC popularity, CMC analysis, combo potential) que en winrates reales.

2. **Decklists no son estáticas**: Los links de Moxfield apuntan a versiones actuales,
   no a la versión jugada en el torneo. Esto introduce ruido en el análisis.

3. **Multiplayer vs. 1v1**: Commander es multiplayer. Los datos de torneos capturan
   quién ganó, pero no la dinámica social de mesa (kingmaking, threat assessment).

4. **El CER es una guía, no un veredicto**: La sinergia específica de un deck puede
   hacer que una carta C-Tier sea la mejor opción para esa estrategia particular.

5. **Precios fluctúan**: Los precios de Scryfall se actualizan diariamente pero son
   aproximados. No usar para decisiones de compra/venta inmediatas.

## Idioma

Este skill opera en **español** por defecto. Si el usuario escribe en inglés,
responder en inglés. Los nombres de cartas se usan siempre en inglés (nombre oficial).
