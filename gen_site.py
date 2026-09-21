#!/usr/bin/env python3
"""Generate index.html for the AI Sentiment Forecast site.

Reads data/daily.json and data/forecasts.json, writes a self-contained,
newsletter-style, mobile-friendly index.html.
"""
import html
import json
import os
import sys
from datetime import datetime, timezone

SITE_NAME = "AI Sentiment Forecast"
TAGLINE = "Daily investor sentiment, distilled — with a graded SPY forecast for tomorrow."


def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def score_tone(score):
    if score >= 65:
        return "bull"
    if score >= 55:
        return "bullsoft"
    if score >= 45:
        return "neutral"
    if score >= 35:
        return "bearsoft"
    return "bear"


def score_bar(score, label):
    tone = score_tone(score)
    pos = max(0, min(100, score))
    return f"""
    <div class="scorebar">
      <div class="scorebar-track">
        <div class="scorebar-mid"></div>
        <div class="scorebar-marker {tone}" style="left:{pos}%"></div>
      </div>
      <div class="scorebar-labels"><span>0 Bearish</span><span>50 Neutral</span><span>100 Bullish</span></div>
      <div class="score-line"><span class="score-num {tone}">{score}</span>
      <span class="score-label {tone}">{esc(label)}</span></div>
    </div>"""


def forecast_card(fc, result):
    call = fc["call"]
    up = call == "UP"
    arrow = "▲" if up else "▼"
    cls = "up" if up else "down"
    res_line = ""
    if result is not None:
        hit = result.get("hit")
        pct = result.get("spy_pct")
        if hit is True:
            res_line = f'<div class="verdict hit">✓ HIT — SPY {pct:+.2f}%</div>'
        elif hit is False:
            res_line = f'<div class="verdict miss">✗ MISS — SPY {pct:+.2f}%</div>'
        elif result.get("push"):
            res_line = f'<div class="verdict push">= PUSH — SPY {pct:+.2f}%</div>'
    else:
        res_line = '<div class="verdict pending">⏳ Grading after tomorrow\'s close</div>'
    return f"""
    <div class="forecast-card {cls}">
      <div class="fc-kicker">Tomorrow's call · {esc(fc['target_date'])}</div>
      <div class="fc-call">{arrow} {call}</div>
      <div class="fc-conf">Confidence: {esc(fc['confidence'])}</div>
      <p class="fc-rationale">{esc(fc['rationale'])}</p>
      {res_line}
    </div>"""


def render_drivers(drivers):
    if not drivers:
        return '<p class="muted">No macro drivers recorded.</p>'
    cards = []
    for d in drivers:
        s = d.get("sentiment", "neutral")
        cards.append(f"""
        <div class="driver {s}">
          <span class="pill {s}">{s.upper()}</span>
          <strong>{esc(d.get('title'))}</strong>
          <p>{esc(d.get('detail'))}</p>
        </div>""")
    return '<div class="drivers">' + "".join(cards) + "</div>"


def render_movers(items, kind):
    if not items:
        return '<p class="muted">None.</p>'
    rows = []
    for m in items:
        rows.append(f"""<tr><td class="ticker">{esc(m.get('ticker'))}</td>
        <td class="move {kind}">{esc(m.get('move'))}</td>
        <td>{esc(m.get('note'))}</td></tr>""")
    return f'<table class="movers"><tbody>{"".join(rows)}</tbody></table>'


def render_day(day, latest=False):
    d = day["date"]
    pm = day.get("premarket")
    pc = day["postclose"]
    fc = day["forecast"]
    res = day.get("result")
    anchor = f"day-{d}"
    parts = [f'<section class="day {"latest" if latest else ""}" id="{anchor}">']
    parts.append(f'<h2 class="day-date">{"📰 Latest edition — " if latest else ""}{esc(d)}</h2>')
    parts.append('<div class="editions">')
    if pm:
        parts.append(f"""<div class="edition">
          <h3>☀️ Pre-Market</h3>{score_bar(pm['score'], pm['label'])}
          <p>{esc(pm['summary'])}</p></div>""")
    parts.append(f"""<div class="edition">
      <h3>🌙 Post-Close</h3>{score_bar(pc['score'], pc['label'])}
      <p>{esc(pc['summary'])}</p></div>""")
    parts.append("</div>")
    parts.append("<h3>What moved sentiment</h3>")
    parts.append(render_drivers(pc.get("drivers", [])))
    parts.append('<div class="movers-grid">')
    parts.append(f"<div><h3>📈 Strength</h3>{render_movers(pc.get('strength', []), 'pos')}</div>")
    parts.append(f"<div><h3>📉 Pressure</h3>{render_movers(pc.get('pressure', []), 'neg')}</div>")
    parts.append("</div>")
    parts.append(f"""<div class="take"><h3>✍️ Analyst take</h3><p>{esc(day.get('take'))}</p></div>""")
    parts.append(forecast_card(fc, res))
    parts.append("</section>")
    return "".join(parts)


def accuracy_stats(forecasts):
    graded = [f for f in forecasts if f.get("hit") is not None]
    hits = sum(1 for f in graded if f["hit"])
    n = len(graded)
    pct = (100.0 * hits / n) if n else 0.0
    return hits, n, pct


def render_ledger(forecasts):
    rows = []
    for f in forecasts:
        call = f["call"]
        cls = "up" if call == "UP" else "down"
        arrow = "▲" if call == "UP" else "▼"
        if f.get("hit") is True:
            verdict = f'<span class="v-hit">✓ HIT</span>'
        elif f.get("hit") is False:
            verdict = f'<span class="v-miss">✗ MISS</span>'
        elif f.get("push"):
            verdict = '<span class="v-push">= PUSH</span>'
        else:
            verdict = '<span class="v-pend">⏳ PENDING</span>'
        spy = f"{f['spy_pct']:+.2f}%" if f.get("spy_pct") is not None else "—"
        rows.append(f"""<tr>
          <td>{esc(f['date'])}</td>
          <td><span class="score-mini {score_tone(f['score'])}">{f['score']}</span></td>
          <td class="{cls}"><strong>{arrow} {call}</strong></td>
          <td>{esc(f['target_date'])}</td>
          <td>{spy}</td>
          <td>{verdict}</td>
        </tr>""")
    return f"""<table class="ledger">
      <thead><tr><th>Date</th><th>Score</th><th>Forecast</th><th>For</th>
      <th>SPY %</th><th>Result</th></tr></thead>
      <tbody>{"".join(rows)}</tbody></table>"""


CSS = """
:root{
  --bg:#0f1420; --panel:#161d2e; --panel2:#1c2540; --ink:#eef1f7; --muted:#9aa4bf;
  --line:#2a3552; --gold:#e8b44a; --green:#35c26e; --green2:#7ee2a8;
  --red:#e5484d; --red2:#ff8a8e; --amber:#e8a13c; --blue:#5aa2ff;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  line-height:1.6}
.wrap{max-width:860px;margin:0 auto;padding:0 18px 60px}
.masthead{text-align:center;padding:38px 0 10px;border-bottom:1px solid var(--line)}
.masthead .brand{font-family:Georgia,"Times New Roman",serif;font-size:13px;
  letter-spacing:4px;color:var(--gold);text-transform:uppercase}
.masthead h1{font-family:Georgia,"Times New Roman",serif;font-size:38px;margin:10px 0 6px}
.masthead .tag{color:var(--muted);font-size:15px;margin:0 0 8px}
.dateline{color:var(--muted);font-size:13px}
.hero{margin-top:26px}
section.day{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:26px;margin:26px 0}
section.day.latest{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold)}
.day-date{font-family:Georgia,serif;font-size:26px;margin:0 0 14px}
.editions{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:8px}
@media(max-width:640px){.editions{grid-template-columns:1fr}}
.edition h3,.day h3{margin:14px 0 8px;font-size:16px}
.scorebar{margin:10px 0 14px}
.scorebar-track{position:relative;height:10px;border-radius:6px;
  background:linear-gradient(90deg,var(--red) 0%,var(--amber) 40%,var(--muted) 50%,var(--green2) 60%,var(--green) 100%);
  opacity:.85}
.scorebar-mid{position:absolute;left:50%;top:-4px;bottom:-4px;width:2px;background:#fff8; }
.scorebar-marker{position:absolute;top:-6px;width:4px;height:22px;background:#fff;
  border-radius:2px;transform:translateX(-50%);box-shadow:0 0 6px #000}
.scorebar-labels{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:6px}
.score-line{margin-top:8px}
.score-num{font-size:34px;font-weight:800;font-family:Georgia,serif}
.score-label{font-size:17px;font-weight:700;margin-left:10px}
.bull{color:var(--green)} .bullsoft{color:var(--green2)} .neutral{color:var(--muted)}
.bearsoft{color:var(--amber)} .bear{color:var(--red2)}
.drivers{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:640px){.drivers{grid-template-columns:1fr}}
.driver{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.driver p{margin:6px 0 0;font-size:14px;color:var(--muted)}
.driver strong{font-size:14.5px}
.pill{font-size:10.5px;font-weight:800;letter-spacing:1px;border-radius:20px;
  padding:2px 9px;margin-right:8px;vertical-align:1px}
.pill.bullish{background:#123c24;color:var(--green2)}
.pill.bearish{background:#43161a;color:var(--red2)}
.pill.neutral{background:#2a3552;color:var(--muted)}
.movers-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:640px){.movers-grid{grid-template-columns:1fr}}
table.movers{width:100%;border-collapse:collapse;font-size:13.5px}
table.movers td{padding:7px 6px;border-top:1px solid var(--line);vertical-align:top}
.ticker{font-weight:800;white-space:nowrap}
.move.pos{color:var(--green2);font-weight:700;white-space:nowrap}
.move.neg{color:var(--red2);font-weight:700;white-space:nowrap}
.take{background:#211a08;border:1px solid #6b5316;border-radius:10px;padding:14px 18px;margin:20px 0}
.take h3{margin:0 0 6px;color:var(--gold)}
.take p{margin:0;font-size:15px}
.forecast-card{border-radius:12px;padding:20px;margin-top:18px;text-align:center;border:1px solid}
.forecast-card.up{background:#0e2a1a;border-color:#1f5c36}
.forecast-card.down{background:#2c1214;border-color:#6e2228}
.fc-kicker{font-size:12px;letter-spacing:2px;color:var(--muted);text-transform:uppercase}
.fc-call{font-size:44px;font-weight:900;font-family:Georgia,serif;margin:6px 0}
.forecast-card.up .fc-call{color:var(--green2)}
.forecast-card.down .fc-call{color:var(--red2)}
.fc-conf{font-size:14px;color:var(--muted)}
.fc-rationale{font-size:14.5px;max-width:640px;margin:10px auto 0}
.verdict{margin-top:12px;font-weight:800;font-size:15px}
.verdict.hit{color:var(--green2)} .verdict.miss{color:var(--red2)}
.verdict.push{color:var(--amber)} .verdict.pending{color:var(--muted)}
.acc{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:26px;margin:26px 0}
.acc h2,.method h2,.archive-head{font-family:Georgia,serif;font-size:26px;margin:0 0 10px}
.acc-big{display:flex;align-items:baseline;gap:14px;margin:6px 0 14px}
.acc-pct{font-size:52px;font-weight:900;font-family:Georgia,serif;color:var(--gold)}
.acc-sub{color:var(--muted);font-size:14px}
table.ledger{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}
table.ledger th,table.ledger td{padding:9px 8px;border-top:1px solid var(--line);text-align:left}
table.ledger th{color:var(--muted);font-size:12px;letter-spacing:1px;text-transform:uppercase;border-top:none}
.score-mini{font-weight:800;padding:2px 10px;border-radius:16px;background:var(--panel2)}
.up{color:var(--green2)} .down{color:var(--red2)}
.v-hit{color:var(--green2);font-weight:800}.v-miss{color:var(--red2);font-weight:800}
.v-push{color:var(--amber);font-weight:800}.v-pend{color:var(--muted)}
.method{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:26px;margin:26px 0}
.method ol{margin:8px 0;padding-left:22px}
.method li{margin:8px 0;font-size:14.5px}
.method code{background:var(--panel2);padding:1px 7px;border-radius:5px;font-size:13.5px}
footer{color:var(--muted);font-size:12.5px;text-align:center;margin-top:34px;
  border-top:1px solid var(--line);padding-top:18px}
.muted{color:var(--muted)}
"""


def build_html(days, forecasts, generated_at):
    days_sorted = sorted(days, key=lambda d: d["date"], reverse=True)
    fc_sorted = sorted(forecasts, key=lambda f: f["date"], reverse=True)
    hits, n, pct = accuracy_stats(forecasts)
    latest = days_sorted[0] if days_sorted else None

    hero = render_day(latest, latest=True) if latest else ""
    archive = "".join(render_day(d) for d in days_sorted[1:])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="latest-date" content="{esc(latest['date']) if latest else ''}">
<title>{SITE_NAME} — Daily AI Investor Sentiment + SPY Forecast</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div class="brand">AI Sentiment Forecast</div>
    <h1>Will the market be up or down tomorrow?</h1>
    <p class="tag">{TAGLINE}</p>
    <div class="dateline">Updated {esc(generated_at)} ET · Weekdays</div>
  </header>

  <div class="hero">{hero}</div>

  <div class="acc" id="accuracy">
    <h2>🎯 Forecast accuracy</h2>
    <div class="acc-big"><span class="acc-pct">{pct:.1f}%</span>
    <span class="acc-sub">{hits} hits out of {n} graded forecasts
    (pushes excluded)</span></div>
    {render_ledger(fc_sorted)}
    <p class="muted" style="font-size:13px">Each evening's post-close report forecasts
    the <em>next</em> trading day's SPY direction. Graded on SPY close-to-close
    return from the forecast day to the target day.</p>
  </div>

  <h2 class="archive-head">📚 Archive</h2>
  {archive if archive else '<p class="muted">No earlier editions yet.</p>'}

  <div class="method" id="methodology">
    <h2>⚙️ Methodology</h2>
    <ol>
      <li><strong>Sentiment scan.</strong> Every weekday, ~9 AM and ~5 PM ET, both
      finviz news feeds (macro + single-stock) are read and novelty-filtered:
      duplicates, intraday wraps, explainers of known conditions, and opinion
      pieces are discarded. Only genuinely new, potentially market-moving items count.</li>
      <li><strong>Score.</strong> Each edition gets a 0–100 sentiment score
      (50 = neutral). The evening <strong>Post-Close</strong> score drives the forecast.</li>
      <li><strong>Forecast rule.</strong> Post-close score &gt; 50 → <code>UP</code>;
      score &lt; 50 → <code>DOWN</code>; exactly 50 → the majority sentiment of
      that day's macro drivers decides (bearish majority → DOWN, otherwise UP).</li>
      <li><strong>Grading.</strong> The forecast covers the next trading day.
      Actual = SPY close-to-close % from the forecast day's close to the target
      day's close (Yahoo Finance data). Sign matches the call → HIT; a flat
      0.00% day is a PUSH and excluded from the hit rate.</li>
      <li><strong>Honesty policy.</strong> Misses are published, not hidden.
      The ledger above is the complete record.</li>
    </ol>
  </div>

  <footer>
    Sentiment data: finviz.com macro + single-stock news feeds, novelty-filtered daily.<br>
    Price data: SPY via Yahoo Finance. Forecasts are directional experiments,
    not financial advice.<br>
    Generated {esc(generated_at)} ET.
  </footer>
</div>
</body>
</html>"""


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base, "data")
    with open(os.path.join(data_dir, "daily.json")) as f:
        days = json.load(f)["days"]
    with open(os.path.join(data_dir, "forecasts.json")) as f:
        forecasts = json.load(f)["forecasts"]
    gen_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    out = build_html(days, forecasts, gen_at)
    with open(os.path.join(base, "index.html"), "w") as f:
        f.write(out)
    print(f"wrote index.html ({len(out)} bytes), {len(days)} days, {len(forecasts)} forecasts")


if __name__ == "__main__":
    main()
