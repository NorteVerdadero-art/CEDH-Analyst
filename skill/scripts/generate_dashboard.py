#!/usr/bin/env python3
"""
generate_dashboard.py — Genera un dashboard HTML con Chart.js para análisis de deck Commander

Usage:
    python3 generate_dashboard.py --data report.json --output dashboard.html
    python3 generate_dashboard.py --data report.json --commander "Kraum / Tymna" --output dashboard.html

Input: JSON output from cer_calculator.py (analyze_deck)
Output: Single HTML file with Chart.js visualizations (no dependencies externas)
"""

import json
import sys
import argparse
import html as html_module
from datetime import datetime


def generate_html(data: dict, commander: str = "Deck", bracket_name: str = "") -> str:
    """Generate complete HTML dashboard from CER analysis data."""

    bracket = data.get("bracket", 5)
    bracket_names = {1: "Exhibition", 2: "Core", 3: "Upgraded", 4: "Optimized", 5: "cEDH"}
    b_name = bracket_name or bracket_names.get(bracket, "Unknown")
    
    all_cards = data.get("all_cards", [])
    tier_dist = data.get("tier_distribution", {})
    avg_cer = data.get("avg_cer", 0)
    total_cards = data.get("total_cards", 0)
    gc_count = data.get("game_changers_count", 0)
    win_curve = data.get("win_curve", [])
    # Only show cards with CER > 0 in ranked tables
    scored_cards = [c for c in all_cards if c.get("cer", 0) > 0]
    top10 = sorted(scored_cards, key=lambda c: c.get("cer", 0), reverse=True)[:10]
    bottom10 = sorted(scored_cards, key=lambda c: c.get("cer", 0))[:10]
    
    # --- Prepare chart data ---
    
    # 1. Tier distribution (donut)
    tier_order = ["S-Tier", "A-Tier", "B-Tier", "C-Tier", "D-Tier", "F-Tier", "N/A", "BANNED"]
    tier_colors = {
        "S-Tier": "#1565C0", "A-Tier": "#1976D2", "B-Tier": "#455A64",
        "C-Tier": "#78909C", "D-Tier": "#90A4AE", "F-Tier": "#B0BEC5",
        "N/A": "#CFD8DC", "BANNED": "#C62828"
    }
    tier_labels = json.dumps([t for t in tier_order if tier_dist.get(t, 0) > 0])
    tier_values = json.dumps([tier_dist.get(t, 0) for t in tier_order if tier_dist.get(t, 0) > 0])
    tier_bg = json.dumps([tier_colors.get(t, "#666") for t in tier_order if tier_dist.get(t, 0) > 0])
    
    # 2. CER distribution (histogram)
    cer_bins = {"0-2": 0, "2-4": 0, "4-5": 0, "5-6": 0, "6-7.5": 0, "7.5-9": 0, "9-10": 0}
    for c in all_cards:
        cer = c.get("cer", 0)
        if cer < 2: cer_bins["0-2"] += 1
        elif cer < 4: cer_bins["2-4"] += 1
        elif cer < 5: cer_bins["4-5"] += 1
        elif cer < 6: cer_bins["5-6"] += 1
        elif cer < 7.5: cer_bins["6-7.5"] += 1
        elif cer < 9: cer_bins["7.5-9"] += 1
        else: cer_bins["9-10"] += 1
    hist_labels = json.dumps(list(cer_bins.keys()))
    hist_values = json.dumps(list(cer_bins.values()))
    
    # 3. Mana curve (bar chart by CMC)
    cmc_dist = {}
    for c in all_cards:
        cmc = int(c.get("cmc", 0))
        if cmc > 7: cmc = 7  # group 7+
        key = str(cmc) if cmc < 7 else "7+"
        cmc_dist[key] = cmc_dist.get(key, 0) + 1
    cmc_labels = json.dumps(["0", "1", "2", "3", "4", "5", "6", "7+"])
    cmc_values = json.dumps([cmc_dist.get(k, 0) for k in ["0", "1", "2", "3", "4", "5", "6", "7+"]])
    
    # 4. WCI top cards horizontal bar
    wci_cards = sorted([c for c in all_cards if c.get("wci", 0) > 0],
                       key=lambda c: c.get("wci", 0), reverse=True)[:20]
    wci_labels = json.dumps([c["name"] for c in wci_cards])
    wci_values = json.dumps([c["wci"] for c in wci_cards])

    # 5. Win probability curve (turns 1-10)
    win_curve_turn_labels = json.dumps([f"T{i}" for i in range(1, len(win_curve) + 1)] if win_curve else [f"T{i}" for i in range(1, 11)])
    win_curve_values_json = json.dumps(win_curve)

    # KPI: combo count
    combos_summary = data.get("combos_summary", {})
    combo_count_kpi = combos_summary.get("included", 0)
    # Win turn estimate: first turn where P(win) >= 50%, or "—"
    win_turn_est = "—"
    for idx, pwin in enumerate(win_curve):
        if pwin >= 50:
            win_turn_est = str(idx + 1)
            break

    # 6. Top 10 radar data (components breakdown) — only if any card has non-zero components
    radar_cards = top10[:6]
    radar_palette = ["#1565C0", "#1976D2", "#455A64", "#78909C", "#90A4AE", "#B0BEC5"]
    has_components = any(
        any(v != 0 for v in c.get("components", {}).values())
        for c in all_cards
    )
    radar_labels = json.dumps(["WIR", "PIR", "TSC", "SYN", "FLX"])
    radar_datasets = []
    if has_components:
        for i, c in enumerate(radar_cards):
            comp = c.get("components", {})
            radar_datasets.append({
                "label": c.get("name", "?"),
                "data": [comp.get("wir", 0), comp.get("pir", 0), comp.get("tsc", 0),
                         comp.get("syn", 0), comp.get("flx", 0)],
                "borderColor": radar_palette[i % len(radar_palette)],
                "backgroundColor": radar_palette[i % len(radar_palette)] + "33",
                "borderWidth": 2,
                "pointRadius": 3
            })
    radar_datasets_json = json.dumps(radar_datasets)
    
    # 6. Type distribution (pie)
    type_dist = {"Creature": 0, "Instant": 0, "Sorcery": 0, "Artifact": 0, 
                 "Enchantment": 0, "Planeswalker": 0, "Land": 0, "Other": 0}
    for c in all_cards:
        tl = c.get("type_line", "")
        matched = False
        for t in ["Land", "Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker"]:
            if t in tl:
                type_dist[t] += 1
                matched = True
                break
        if not matched:
            type_dist["Other"] += 1
    # Remove zeros
    type_labels = json.dumps([k for k, v in type_dist.items() if v > 0])
    type_values = json.dumps([v for v in type_dist.values() if v > 0])
    type_colors_list = {
        "Creature": "#1565C0", "Instant": "#1976D2", "Sorcery": "#1E88E5",
        "Artifact": "#78909C", "Enchantment": "#546E7A", "Planeswalker": "#455A64",
        "Land": "#90A4AE", "Other": "#B0BEC5"
    }
    type_bg = json.dumps([type_colors_list.get(k, "#666") for k, v in type_dist.items() if v > 0])
    
    # Build top/bottom tables
    def build_table_rows(cards):
        rows = ""
        for i, c in enumerate(cards):
            tier = c.get("tier", "?")
            tier_class = tier.lower().replace("-", "").replace(" ", "")
            cer = c.get("cer", 0)
            wci = c.get("wci", 0)
            name = html_module.escape(c.get("name", "?"))
            gc_badge = ' <span style="font-size:0.7rem;color:#1565C0;font-weight:700;">GC</span>' if c.get("gc") else ""
            comp_cell = ""
            if has_components:
                comp = c.get("components", {})
                comp_cell = f'<td class="components">{comp.get("wir","—")}/{comp.get("pir","—")}/{comp.get("tsc","—")}/{comp.get("syn","—")}/{comp.get("flx","—")}</td>'
            img_uri = html_module.escape(c.get("image_uri", ""))
            img_attr = f' data-img="{img_uri}"' if img_uri else ""
            rows += f"""<tr>
                <td>{i+1}</td>
                <td class="card-name"{img_attr}>{name}{gc_badge}</td>
                <td><span class="cer-badge {tier_class}">{cer:.2f}</span></td>
                <td><span class="tier-tag {tier_class}">{tier}</span></td>
                <td style="font-size:0.85rem;color:#455A64;">{wci:.0f}%</td>
                {comp_cell}
            </tr>"""
        return rows

    top_rows = build_table_rows(top10)
    bottom_rows = build_table_rows(bottom10)
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CER Analysis — {html_module.escape(commander)} | Bracket {bracket}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

  :root {{
    --bg-primary: #f5f7fa;
    --bg-card: #ffffff;
    --bg-card-hover: #f0f4f8;
    --border: #dde2ea;
    --text-primary: #1a202c;
    --text-secondary: #4a5568;
    --text-muted: #718096;
    --gold: #D4A017;
    --green: #2E7D32;
    --blue: #1565C0;
    --blue-light: #1E88E5;
    --orange: #E65100;
    --red: #C62828;
    --gray: #78909C;
    --dark: #37474F;
    --accent: #1565C0;
  }}
  
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  
  body {{
    background: var(--bg-primary);
    color: var(--text-primary);
    font-family: 'Inter', sans-serif;
    line-height: 1.6;
    min-height: 100vh;
  }}
  
  .container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
  }}
  
  /* Header */
  .header {{
    text-align: center;
    padding: 48px 24px 36px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 32px;
    position: relative;
  }}
  
  .header::before {{
    content: '';
    position: absolute;
    top: 0; left: 50%;
    transform: translateX(-50%);
    width: 200px;
    height: 3px;
    background: linear-gradient(90deg, var(--blue), var(--gray));
    border-radius: 2px;
  }}
  
  .header h1 {{
    font-size: 2.2rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin-bottom: 4px;
  }}
  
  .header .bracket-tag {{
    display: inline-block;
    background: var(--blue);
    color: white;
    padding: 4px 16px;
    border-radius: 20px;
    font-size: 0.85rem;
    font-weight: 600;
    margin-top: 8px;
  }}

  .header .date {{
    color: var(--text-muted);
    font-size: 0.8rem;
    margin-top: 8px;
    font-family: 'Inter', sans-serif;
  }}
  
  /* KPI Cards */
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  
  .kpi-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    text-align: center;
    transition: all 0.2s;
  }}
  
  .kpi-card:hover {{
    background: var(--bg-card-hover);
    border-color: var(--blue-light);
    box-shadow: 0 2px 8px rgba(21, 101, 192, 0.1);
  }}
  
  .kpi-card .value {{
    font-size: 2rem;
    font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
  }}
  
  .kpi-card .label {{
    color: var(--text-secondary);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 4px;
  }}
  
  .kpi-card.gold .value {{ color: var(--blue); }}
  .kpi-card.green .value {{ color: var(--blue-light); }}
  .kpi-card.blue .value {{ color: var(--dark); }}
  .kpi-card.orange .value {{ color: var(--gray); }}
  .kpi-card.accent .value {{ color: var(--blue); }}
  
  /* Chart Grid */
  .chart-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 24px;
    margin-bottom: 32px;
  }}
  
  @media (max-width: 900px) {{
    .chart-grid {{ grid-template-columns: 1fr; }}
  }}
  
  .chart-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
  }}
  
  .chart-card h3 {{
    font-size: 1rem;
    font-weight: 600;
    margin-bottom: 16px;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.85rem;
  }}
  
  .chart-card canvas {{
    max-height: 320px;
  }}
  
  .chart-card.wide {{
    grid-column: 1 / -1;
  }}
  
  /* Tables */
  .table-section {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    overflow-x: auto;
  }}
  
  .table-section h3 {{
    font-size: 1rem;
    font-weight: 600;
    margin-bottom: 16px;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.85rem;
  }}
  
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  
  th {{
    text-align: left;
    padding: 10px 12px;
    border-bottom: 2px solid var(--border);
    color: var(--text-muted);
    font-weight: 600;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }}
  
  td {{
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }}
  
  tr:hover td {{
    background: var(--bg-card-hover);
  }}
  
  .card-name {{
    font-weight: 600;
    color: var(--text-primary);
  }}
  
  .components {{
    font-family: 'Inter', sans-serif;
    font-size: 0.75rem;
    color: var(--text-muted);
  }}

  .note {{
    font-size: 0.8rem;
    color: var(--blue-light);
    font-style: italic;
  }}
  
  /* Badges */
  .cer-badge, .tier-tag {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    font-weight: 600;
  }}
  
  .cer-badge {{ background: var(--bg-primary); }}

  .tier-tag.stier, .cer-badge.stier {{ color: #1565C0; border: 1px solid #1565C0; background: #E3F2FD; }}
  .tier-tag.atier, .cer-badge.atier {{ color: #1976D2; border: 1px solid #1976D2; background: #E8F4FD; }}
  .tier-tag.btier, .cer-badge.btier {{ color: #455A64; border: 1px solid #455A64; background: #ECEFF1; }}
  .tier-tag.ctier, .cer-badge.ctier {{ color: #546E7A; border: 1px solid #90A4AE; background: #F5F7F8; }}
  .tier-tag.dtier, .cer-badge.dtier {{ color: #78909C; border: 1px solid #B0BEC5; background: #FAFAFA; }}
  .tier-tag.ftier, .cer-badge.ftier {{ color: #90A4AE; border: 1px solid #CFD8DC; background: #FAFAFA; }}
  .tier-tag.na, .cer-badge.na {{ color: #90A4AE; border: 1px solid #CFD8DC; }}
  .tier-tag.banned, .cer-badge.banned {{ color: #C62828; border: 1px solid #C62828; background: #FFEBEE; }}
  
  /* Footer */
  .footer {{
    text-align: center;
    padding: 32px 24px;
    border-top: 1px solid var(--border);
    margin-top: 32px;
    color: var(--text-muted);
    font-size: 0.75rem;
  }}
  
  .footer a {{ color: var(--blue-light); text-decoration: none; }}
  .footer a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>

<div class="container">
  
  <!-- Header -->
  <div class="header">
    <h1>{html_module.escape(commander)}</h1>
    <span class="bracket-tag">Bracket {bracket} — {html_module.escape(b_name)}</span>
    <div class="date">Análisis generado: {now} · CER v1.0</div>
  </div>
  
  <!-- KPIs -->
  <div class="kpi-grid">
    <div class="kpi-card gold">
      <div class="value">{avg_cer:.2f}</div>
      <div class="label">CER Promedio</div>
    </div>
    <div class="kpi-card green">
      <div class="value">{total_cards}</div>
      <div class="label">Cartas Analizadas</div>
    </div>
    <div class="kpi-card blue">
      <div class="value">{tier_dist.get('S-Tier', 0) + tier_dist.get('A-Tier', 0)}</div>
      <div class="label">Cartas S/A Tier</div>
    </div>
    <div class="kpi-card orange">
      <div class="value">{gc_count}</div>
      <div class="label">Game Changers</div>
    </div>
    <div class="kpi-card accent">
      <div class="value">{combo_count_kpi}</div>
      <div class="label">Combos Detectados</div>
    </div>
    <div class="kpi-card blue">
      <div class="value">{'T' + win_turn_est if win_turn_est != '—' else win_turn_est}</div>
      <div class="label">Puede ganar desde</div>
    </div>
  </div>
  
  <!-- Charts Row 1 -->
  <div class="chart-grid">
    <div class="chart-card">
      <h3>Distribución por Tier</h3>
      <canvas id="tierChart"></canvas>
    </div>
    <div class="chart-card">
      <h3>Distribución de CER</h3>
      <canvas id="cerHistogram"></canvas>
    </div>
    <div class="chart-card">
      <h3>Curva de Mana</h3>
      <canvas id="manaCurve"></canvas>
    </div>
    <div class="chart-card">
      <h3>Distribución por Tipo</h3>
      <canvas id="typeChart"></canvas>
    </div>
    <div class="chart-card wide">
      <h3>Win Contribution Index — Top 20 Cartas</h3>
      <canvas id="wciChart"></canvas>
    </div>
    <div class="chart-card wide">
      <h3>Probabilidad de Victoria por Turno</h3>
      <canvas id="winCurveChart"></canvas>
    </div>
    {'<div class="chart-card wide"><h3>Componentes CER — Top Cartas (Radar)</h3><canvas id="radarChart"></canvas></div>' if has_components else ''}
  </div>
  
  <!-- Top 10 Table -->
  <div class="table-section">
    <h3>Top 10 Cartas por CER</h3>
    <table>
      <thead>
        <tr>
          <th>#</th><th>Carta</th><th>CER</th><th>Tier</th><th>WCI</th>{'<th>WIR/PIR/TSC/SYN/FLX</th>' if has_components else ''}
        </tr>
      </thead>
      <tbody>{top_rows}</tbody>
    </table>
  </div>

  <!-- Bottom 10 Table -->
  <div class="table-section">
    <h3>Bottom 10 — Candidatas a Corte</h3>
    <table>
      <thead>
        <tr>
          <th>#</th><th>Carta</th><th>CER</th><th>Tier</th><th>WCI</th>{'<th>WIR/PIR/TSC/SYN/FLX</th>' if has_components else ''}
        </tr>
      </thead>
      <tbody>{bottom_rows}</tbody>
    </table>
  </div>
  
  <!-- Footer -->
  <div class="footer">
    cEDH Deck Analyst · CER (Card Effectiveness Rating) v1.0<br>
    Fuentes: <a href="https://edhtop16.com" target="_blank">EDH Top 16</a> ·
    <a href="https://www.cedh-analytics.com" target="_blank">cEDH Analytics</a> ·
    <a href="https://scryfall.com" target="_blank">Scryfall</a> ·
    <a href="https://commanderspellbook.com" target="_blank">Commander Spellbook</a><br>
    Los nombres de cartas y las imágenes son propiedad de Wizards of the Coast.
  </div>
  
</div>

<script>
// Chart.js global config
Chart.defaults.color = '#4a5568';
Chart.defaults.borderColor = '#dde2ea';
Chart.defaults.font.family = "'Inter', sans-serif";

// 1. Tier Distribution (Donut)
new Chart(document.getElementById('tierChart'), {{
  type: 'doughnut',
  data: {{
    labels: {tier_labels},
    datasets: [{{
      data: {tier_values},
      backgroundColor: {tier_bg},
      borderWidth: 0,
      hoverOffset: 8
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'right', labels: {{ padding: 12, usePointStyle: true }} }}
    }},
    cutout: '60%'
  }}
}});

// 2. CER Histogram
new Chart(document.getElementById('cerHistogram'), {{
  type: 'bar',
  data: {{
    labels: {hist_labels},
    datasets: [{{
      label: 'Cartas',
      data: {hist_values},
      backgroundColor: ['#CFD8DC', '#B0BEC5', '#90A4AE', '#78909C', '#455A64', '#1976D2', '#1565C0'],
      borderRadius: 6,
      borderSkipped: false
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ stepSize: 1 }} }},
      x: {{ grid: {{ display: false }} }}
    }}
  }}
}});

// 3. Mana Curve
new Chart(document.getElementById('manaCurve'), {{
  type: 'bar',
  data: {{
    labels: {cmc_labels},
    datasets: [{{
      label: 'Cartas',
      data: {cmc_values},
      backgroundColor: '#1565C0',
      borderRadius: 6,
      borderSkipped: false
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ stepSize: 2 }} }},
      x: {{ grid: {{ display: false }}, title: {{ display: true, text: 'CMC' }} }}
    }}
  }}
}});

// 4. Type Distribution (Pie)
new Chart(document.getElementById('typeChart'), {{
  type: 'pie',
  data: {{
    labels: {type_labels},
    datasets: [{{
      data: {type_values},
      backgroundColor: {type_bg},
      borderWidth: 0,
      hoverOffset: 8
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'right', labels: {{ padding: 12, usePointStyle: true }} }}
    }}
  }}
}});

// 5. WCI — Win Contribution Index horizontal bar
new Chart(document.getElementById('wciChart'), {{
  type: 'bar',
  data: {{
    labels: {wci_labels},
    datasets: [{{
      label: 'WCI %',
      data: {wci_values},
      backgroundColor: (ctx) => {{
        const v = ctx.raw;
        if (v >= 70) return '#1565C0';
        if (v >= 40) return '#1976D2';
        if (v >= 20) return '#455A64';
        return '#90A4AE';
      }},
      borderRadius: 4,
      borderSkipped: false
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => ` WCI: ${{ctx.raw.toFixed(1)}}%`
        }}
      }}
    }},
    scales: {{
      x: {{ beginAtZero: true, max: 100, title: {{ display: true, text: 'Win Contribution Index (%)' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 11 }} }} }}
    }}
  }}
}});

// 6. Win Probability by Turn
new Chart(document.getElementById('winCurveChart'), {{
  type: 'line',
  data: {{
    labels: {win_curve_turn_labels},
    datasets: [{{
      label: 'P(Victoria) %',
      data: {win_curve_values_json},
      borderColor: '#1565C0',
      backgroundColor: '#1565C022',
      borderWidth: 2.5,
      pointRadius: 4,
      pointBackgroundColor: '#1565C0',
      fill: true,
      tension: 0.35
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: (ctx) => ` P(win) = ${{ctx.raw.toFixed(1)}}%`
        }}
      }}
    }},
    scales: {{
      y: {{ beginAtZero: true, max: 100, title: {{ display: true, text: 'Probabilidad (%)' }},
             ticks: {{ callback: (v) => v + '%' }} }},
      x: {{ grid: {{ display: false }}, title: {{ display: true, text: 'Turno' }} }}
    }}
  }}
}});

// 6. Radar — Top Cards Components (only rendered when component data exists)
if (document.getElementById('radarChart')) {{
  new Chart(document.getElementById('radarChart'), {{
    type: 'radar',
    data: {{
      labels: {radar_labels},
      datasets: {radar_datasets_json}
    }},
    options: {{
      responsive: true,
      scales: {{
        r: {{
          beginAtZero: true,
          max: 10,
          ticks: {{ stepSize: 2, backdropColor: 'transparent' }},
          grid: {{ color: '#dde2ea' }},
          angleLines: {{ color: '#dde2ea' }},
          pointLabels: {{ font: {{ size: 12, weight: '600' }} }}
        }}
      }},
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ padding: 16, usePointStyle: true }} }}
      }}
    }}
  }});
}}
</script>

<style>
#card-img-tooltip {{
  position: fixed; pointer-events: none; z-index: 9999; display: none;
  border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.35);
  overflow: hidden; width: 250px; border: 1px solid var(--border);
  background: #000;
}}
#card-img-tooltip img {{ width: 250px; display: block; }}
</style>

<div id="card-img-tooltip"><img id="card-img-tooltip-img" src="" alt=""></div>
<script>
(function() {{
  var tip = document.getElementById('card-img-tooltip');
  var tipImg = document.getElementById('card-img-tooltip-img');
  function show(e) {{
    var src = this.getAttribute('data-img');
    if (!src) return;
    tipImg.src = src;
    tip.style.display = 'block';
    move(e);
  }}
  function move(e) {{
    var x = e.clientX + 16, y = e.clientY + 16;
    var tw = 252, th = tip.offsetHeight || 350;
    if (x + tw > window.innerWidth)  x = e.clientX - tw - 16;
    if (y + th > window.innerHeight) y = window.innerHeight - th - 16;
    tip.style.left = Math.max(0, x) + 'px';
    tip.style.top  = Math.max(0, y) + 'px';
  }}
  function hide() {{ tip.style.display = 'none'; tipImg.src = ''; }}
  function attach() {{
    document.querySelectorAll('[data-img]').forEach(function(el) {{
      el.addEventListener('mouseover', show);
      el.addEventListener('mousemove', move);
      el.addEventListener('mouseout',  hide);
    }});
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', attach);
  }} else {{ attach(); }}
}})();
</script>

</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate CER analysis HTML dashboard")
    parser.add_argument("--data", "-d", required=True, help="JSON from cer_calculator.py")
    parser.add_argument("--commander", "-c", default="Commander Deck", help="Commander name")
    parser.add_argument("--output", "-o", default="cer_dashboard.html", help="Output HTML file")
    args = parser.parse_args()

    with open(args.data) as f:
        data = json.load(f)

    html_content = generate_html(data, commander=args.commander)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Dashboard saved to {args.output}", file=sys.stderr)
    print(f"Cards: {data.get('total_cards', 0)} · Avg CER: {data.get('avg_cer', 0)}", file=sys.stderr)


if __name__ == "__main__":
    main()
