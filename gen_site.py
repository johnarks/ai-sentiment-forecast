#!/usr/bin/env python3
"""Generate index.html for the AI Sentiment Forecast site.

Reads data/daily.json and data/forecasts.json, writes a self-contained,
mobile-friendly web-app-style index.html with tabs:
Today | Forecasts | Archive | Methodology.

Data model (day object):
  {date, morning: edition|null, evening: edition|null,
   early_call: {call, confidence, rationale, made, scores_detail}|null,
   final_call: {...}|null,
   result: {spy_pct, early_hit, final_hit}|null}
Edition: {score, label, summary, drivers, strength, pressure,
          crossCurrents, take, published}
"""
import html
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SITE_NAME = "AI Sentiment Forecast"
TAGLINE = "Twice-daily investor sentiment, distilled — with graded next-day SPY calls."
ET = ZoneInfo("America/New_York")


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


def render_drivers(drivers):
    if not drivers:
        return '<p class="muted">No macro drivers recorded.</p>'
    cards = []
    for d in drivers:
        s = d.get("sentiment", "neutral")
        cards.append(f"""
        <div class="driver">
          <span class="pill {s}">{s.upper()}</span>
          <strong>{esc(d.get('title'))}</strong>
          <p>{esc(d.get('detail'))}</p>
        </div>""")
    return '<div class="drivers">' + "".join(cards) + "</div>"


def render_cc(ccs):
    if not ccs:
        return '<p class="muted">None recorded.</p>'
    items = "".join(
        f'<div class="cc"><strong>{esc(c.get("title"))}</strong>'
        f'<p>{esc(c.get("detail"))}</p></div>' for c in ccs)
    return f'<div class="ccs">{items}</div>'


def render_movers(items, kind):
    if not items:
        return '<p class="muted">None.</p>'
    rows = []
    for m in items:
        rows.append(f"""<tr><td class="ticker">{esc(m.get('ticker'))}</td>
        <td class="move {kind}">{esc(m.get('move'))}</td>
        <td>{esc(m.get('note'))}</td></tr>""")
    return f'<table class="movers"><tbody>{"".join(rows)}</tbody></table>'


def call_card(call, kind_label, graded_hit, spy_pct, highlight=False):
    """Call display card. kind_label: 'Early call' or 'Final call'."""
    if call is None:
        return (f'<div class="call-card empty {"hl" if highlight else ""}">'
                f'<div class="cc-kicker">{kind_label}</div>'
                '<div class="muted">Not yet made.</div></div>')
    up = call["call"] == "UP"
    arrow = "▲" if up else "▼"
    cls = "up" if up else "down"
    if graded_hit is True:
        verdict = (f'<div class="verdict hit">✓ HIT'
                   + (f" — SPY {spy_pct:+.2f}%" if spy_pct is not None else "") + "</div>")
    elif graded_hit is False:
        verdict = (f'<div class="verdict miss">✗ MISS'
                   + (f" — SPY {spy_pct:+.2f}%" if spy_pct is not None else "") + "</div>")
    else:
        verdict = '<div class="verdict pending">⏳ Graded after the close</div>'
    return f"""
    <div class="call-card {cls} {"hl" if highlight else ""}">
      <div class="cc-kicker">{kind_label} · made {esc(call.get('made', ''))}</div>
      <div class="cc-call">{arrow} {call['call']}</div>
      <div class="cc-conf">Confidence: {esc(call.get('confidence', ''))}</div>
      <p class="cc-scores">{esc(call.get('scores_detail', ''))}</p>
      <p class="cc-rationale">{esc(call.get('rationale', ''))}</p>
      {verdict}
    </div>"""


def edition_card(ed, icon, name):
    if not ed:
        return (f'<div class="edition-card empty"><div class="ec-head">'
                f'<span>{icon} {name}</span></div>'
                '<p class="muted">No edition published (market holiday or feed gap).</p></div>')
    pub = ed.get("published", "")
    pub_html = f'<span class="pub">Published {esc(pub)} ET</span>' if pub else ""
    return f"""
    <div class="edition-card">
      <div class="ec-head"><span class="ec-name">{icon} {name}</span>{pub_html}</div>
      {score_bar(ed['score'], ed['label'])}
      <p class="ec-summary">{esc(ed.get('summary'))}</p>
      <details><summary>What moved sentiment</summary>
        {render_drivers(ed.get('drivers', []))}</details>
      <details><summary>Winners &amp; losers</summary>
        <div class="movers-grid">
          <div><h4>📈 Strength</h4>{render_movers(ed.get('strength', []), 'pos')}</div>
          <div><h4>📉 Pressure</h4>{render_movers(ed.get('pressure', []), 'neg')}</div>
        </div>
      </details>
      <details><summary>Cross-currents</summary>{render_cc(ed.get('crossCurrents', []))}</details>
      <details><summary>Analyst take</summary><p class="take-p">{esc(ed.get('take'))}</p></details>
    </div>"""


def next_update_info(days, now):
    """Return (aware datetime, label) for the next scheduled site update."""
    def next_wd_915(dt):
        d = dt.date() + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return datetime(d.year, d.month, d.day, 9, 15, tzinfo=ET)

    latest = days[0] if days else None
    if latest and latest.get("evening"):
        nxt = next_wd_915(now)
    elif latest and latest.get("morning") and latest["date"] == now.strftime("%Y-%m-%d"):
        eod = datetime(now.year, now.month, now.day, 18, 30, tzinfo=ET)
        nxt = eod if now < eod else next_wd_915(now)
    else:
        nxt = next_wd_915(now)
    label = nxt.strftime("%a, %b %-d · %-I:%M %p ET")
    return nxt, label


def render_today(day, forecasts, now):
    nxt, nxt_label = next_update_info([day], now)
    res = day.get("result") or {}
    early = day.get("early_call")
    final = day.get("final_call")
    body = [f"""
    <div class="next-update" id="next-update" data-iso="{nxt.isoformat()}">
      <span class="nu-dot"></span>
      <span>Next update: <strong>{esc(nxt_label)}</strong></span>
      <span class="nu-count" id="countdown"></span>
    </div>
    <h2 class="pane-title">Today's read — {esc(day['date'])}</h2>
    <div class="call-cards">
      {call_card(early, "Early call <span class='tag-sm'>(yesterday evening)</span>",
                 res.get('early_hit'), res.get('spy_pct'))}
      {call_card(final, "Final call <span class='tag-sm'>(this morning)</span>",
                 res.get('final_hit'), res.get('spy_pct'), highlight=True)}
    </div>
    <div class="editions">
      {edition_card(day.get('morning'), '☀️', 'Morning')}
      {edition_card(day.get('evening'), '🌙', 'Evening')}
    </div>"""]
    return "".join(body)


def kind_stats(forecasts, kind):
    graded = [f for f in forecasts
              if f.get("kind") == kind and f.get("hit") is not None]
    hits = sum(1 for f in graded if f["hit"])
    n = len(graded)
    pct = (100.0 * hits / n) if n else 0.0
    return hits, n, pct


def final_streak(forecasts):
    """Consecutive final-call hits ending at the most recent graded final."""
    recs = sorted((f for f in forecasts
                   if f.get("kind") == "final" and f.get("hit") is not None),
                  key=lambda f: f["target_date"], reverse=True)
    streak, w = 0, 0
    for f in recs:
        if f["hit"]:
            streak += 1
        else:
            break
    w = streak
    return w


def ledger_rows(forecasts):
    by_target = {}
    for f in forecasts:
        by_target.setdefault(f["target_date"], {})[f.get("kind")] = f
    rows = []
    for td in sorted(by_target, reverse=True):
        recs = by_target[td]
        cells = []
        spy = None
        for kind in ("early", "final"):
            r = recs.get(kind)
            if r is None:
                cells.append('<td class="muted">—</td>')
                continue
            up = r["call"] == "UP"
            arrow = "▲" if up else "▼"
            cls = "up" if up else "down"
            if r.get("hit") is True:
                v = '<div class="v-hit">✓ HIT</div>'
            elif r.get("hit") is False:
                v = '<div class="v-miss">✗ MISS</div>'
            elif r.get("push"):
                v = '<div class="v-push">= PUSH</div>'
            else:
                v = '<div class="v-pend">⏳ PENDING</div>'
            if r.get("spy_pct") is not None:
                spy = r["spy_pct"]
            cells.append(f'<td class="{cls}"><strong>{arrow} {r["call"]}</strong>'
                         f'<div class="cell-conf">{esc(r.get("confidence", ""))}</div>{v}</td>')
        spy_txt = f"{spy:+.2f}%" if spy is not None else "—"
        rats = []
        for kind in ("early", "final"):
            r = recs.get(kind)
            if r:
                rats.append(f"<p><strong>{kind.title()} call</strong> "
                            f"(made {esc(r.get('date', ''))}): {esc(r.get('rationale', ''))}"
                            + (f"<br><span class='muted'>Scores: {esc(r.get('scores_detail') or r.get('score', ''))}</span>" if r.get('scores_detail') or r.get('score') is not None else "")
                            + "</p>")
        rows.append(f"""<tr>
          <td><strong>{esc(td)}</strong></td>{cells[0]}{cells[1]}<td>{spy_txt}</td>
        </tr>
        <tr class="rat-row"><td colspan="4"><details><summary>Why these calls</summary>
        {''.join(rats)}</details></td></tr>""")
    return f"""<table class="ledger">
      <thead><tr><th>Target day</th><th>Early call</th><th>Final call</th><th>SPY %</th></tr></thead>
      <tbody>{"".join(rows)}</tbody></table>"""


def render_forecasts(forecasts):
    eh, en, ep = kind_stats(forecasts, "early")
    fh, fn, fp = kind_stats(forecasts, "final")
    streak = final_streak(forecasts)
    return f"""
    <h2 class="pane-title">Forecast accuracy</h2>
    <div class="acc-cards">
      <div class="acc-card"><div class="acc-k">Final-call accuracy</div>
        <div class="acc-v">{fp:.1f}%</div>
        <div class="acc-s">{fh}/{fn} graded</div></div>
      <div class="acc-card"><div class="acc-k">Early-call accuracy</div>
        <div class="acc-v">{ep:.1f}%</div>
        <div class="acc-s">{eh}/{en} graded</div></div>
      <div class="acc-card"><div class="acc-k">Final-call streak</div>
        <div class="acc-v">{'W' + str(streak) if streak else '—'}</div>
        <div class="acc-s">consecutive hits</div></div>
    </div>
    {ledger_rows(forecasts)}
    <p class="muted fine">Both calls target the same trading day. Graded on SPY
    close-to-close % from the previous trading day's close to the target day's
    close (Yahoo Finance). A flat 0.00% day is a push, excluded from hit rates.</p>"""


def render_archive(days):
    cards = []
    for d in days:
        res = d.get("result") or {}
        early = d.get("early_call")
        final = d.get("final_call")
        head_bits = []
        if final:
            up = final["call"] == "UP"
            head_bits.append(f'<span class="{("up" if up else "down")} fc-mini">'
                             f'{"▲" if up else "▼"} {final["call"]}</span>')
        if res.get("spy_pct") is not None:
            head_bits.append(f'<span class="spy-mini">SPY {res["spy_pct"]:+.2f}%</span>')
        cards.append(f"""
        <details class="day-card">
          <summary><strong>{esc(d['date'])}</strong> {' '.join(head_bits)}</summary>
          <div class="call-cards small">
            {call_card(early, 'Early call', res.get('early_hit'), res.get('spy_pct'))}
            {call_card(final, 'Final call', res.get('final_hit'), res.get('spy_pct'), highlight=True)}
          </div>
          <div class="editions">
            {edition_card(d.get('morning'), '☀️', 'Morning')}
            {edition_card(d.get('evening'), '🌙', 'Evening')}
          </div>
        </details>""")
    return ("<h2 class='pane-title'>Archive</h2>"
            + ("".join(cards) if cards else '<p class="muted">No editions yet.</p>'))


METHOD_HTML = """
<h2 class="pane-title">Methodology</h2>
<div class="method">
<ol>
  <li><strong>Sentiment scan.</strong> Every weekday, ~9 AM (pre-market) and ~5 PM
  (post-close) ET, both finviz news feeds (macro + single-stock) are read and
  novelty-filtered: duplicates, intraday wraps, explainers of known conditions,
  and opinion pieces are discarded. Only genuinely new, potentially market-moving
  items count. Each edition gets a 0–100 sentiment score (50 = neutral).</li>
  <li><strong>Early call</strong> (evening, ~6:30 PM ET): made from the day's
  Post-Close score. Score &gt; 50 → <code>UP</code>; &lt; 50 → <code>DOWN</code>;
  exactly 50 → the majority sentiment of that evening's macro drivers decides
  (bearish majority → DOWN, otherwise UP). Targets the next trading day.</li>
  <li><strong>Final call</strong> (morning, ~9:15 AM ET): uses the last 24 hours
  of information. <code>final_score = round((previous trading day's post-close
  score + this morning's pre-market score) / 2)</code>, rounded half-up.
  &gt; 50 → <code>UP</code>; &lt; 50 → <code>DOWN</code>; exactly 50 → the
  majority sentiment of this morning's drivers decides. Targets today's session.</li>
  <li><strong>Grading.</strong> Both calls target the same trading day and are
  graded on SPY close-to-close % from the previous trading day's close to the
  target day's close (Yahoo Finance data). Sign matches the call → HIT; a flat
  0.00% day is a PUSH and is excluded from the hit rate.</li>
  <li><strong>Honesty policy.</strong> Misses are published, not hidden.
  The Forecasts tab is the complete record.</li>
  <li><strong>Limitations.</strong> This is a directional sentiment experiment,
  not financial advice. Sentiment scores are judgment calls; the sample of graded
  calls is small; markets can stay irrational longer than any model stays
  interesting.</li>
</ol>
</div>"""


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
.wrap{max-width:920px;margin:0 auto;padding:0 16px 60px}
.masthead{text-align:center;padding:30px 0 6px}
.masthead .brand{font-family:Georgia,"Times New Roman",serif;font-size:13px;
  letter-spacing:4px;color:var(--gold);text-transform:uppercase}
.masthead h1{font-family:Georgia,"Times New Roman",serif;font-size:32px;margin:10px 0 6px}
.masthead .tag{color:var(--muted);font-size:14px;margin:0 0 4px}
.dateline{color:var(--muted);font-size:12.5px}
.tabs{position:sticky;top:0;z-index:10;display:flex;gap:4px;justify-content:center;
  background:#0f1420ee;backdrop-filter:blur(6px);padding:12px 0;margin:14px 0 6px;
  border-bottom:1px solid var(--line)}
.tabs button{background:none;border:none;color:var(--muted);font-size:15px;font-weight:700;
  padding:9px 16px;border-radius:10px;cursor:pointer}
.tabs button.active{background:var(--panel2);color:var(--ink)}
.tabs button:hover{color:var(--ink)}
.tabpane{display:none}
.tabpane.active{display:block;animation:fade .18s ease-in}
@keyframes fade{from{opacity:.4}to{opacity:1}}
.pane-title{font-family:Georgia,serif;font-size:24px;margin:18px 0 12px}
.next-update{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:12px 16px;margin:14px 0;font-size:14.5px}
.nu-dot{width:9px;height:9px;border-radius:50%;background:var(--green);
  box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.4}}
.nu-count{color:var(--muted);font-variant-numeric:tabular-nums}
.call-cards{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:12px 0}
.call-cards.small{grid-template-columns:1fr 1fr}
@media(max-width:640px){.call-cards,.call-cards.small{grid-template-columns:1fr}}
.call-card{border-radius:12px;padding:18px;border:1px solid var(--line);background:var(--panel)}
.call-card.up{border-color:#1f5c36;background:#0e2a1a}
.call-card.down{border-color:#6e2228;background:#2c1214}
.call-card.hl{box-shadow:0 0 0 1px var(--gold)}
.call-card.empty{border-style:dashed}
.cc-kicker{font-size:11.5px;letter-spacing:1.5px;color:var(--muted);text-transform:uppercase}
.tag-sm{letter-spacing:0;text-transform:none;font-weight:400}
.cc-call{font-size:38px;font-weight:900;font-family:Georgia,serif;margin:4px 0}
.call-card.up .cc-call{color:var(--green2)}
.call-card.down .cc-call{color:var(--red2)}
.cc-conf{font-size:13.5px;color:var(--muted)}
.cc-scores{font-size:13px;color:var(--muted);margin:8px 0 0}
.cc-rationale{font-size:14px;margin:8px 0 0}
.verdict{margin-top:10px;font-weight:800;font-size:15px}
.verdict.hit{color:var(--green2)} .verdict.miss{color:var(--red2)}
.verdict.pending{color:var(--muted)}
.editions{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:12px 0}
@media(max-width:700px){.editions{grid-template-columns:1fr}}
.edition-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px}
.edition-card.empty{border-style:dashed}
.ec-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}
.ec-name{font-size:16px;font-weight:800}
.pub{font-size:12px;color:var(--muted)}
.ec-summary{font-size:14px}
details{border:1px solid var(--line);border-radius:10px;margin:10px 0;background:var(--panel2)}
details summary{cursor:pointer;padding:10px 14px;font-weight:700;font-size:14px;list-style:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸ ";color:var(--gold)}
details[open] summary::before{content:"▾ "}
details>div,details>p,details>.take-p{padding:0 14px 12px}
.take-p{font-size:14.5px}
.scorebar{margin:10px 0 12px}
.scorebar-track{position:relative;height:10px;border-radius:6px;
  background:linear-gradient(90deg,var(--red) 0%,var(--amber) 40%,var(--muted) 50%,var(--green2) 60%,var(--green) 100%);
  opacity:.85}
.scorebar-mid{position:absolute;left:50%;top:-4px;bottom:-4px;width:2px;background:#fff8}
.scorebar-marker{position:absolute;top:-6px;width:4px;height:22px;background:#fff;
  border-radius:2px;transform:translateX(-50%);box-shadow:0 0 6px #000}
.scorebar-labels{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:6px}
.score-line{margin-top:8px}
.score-num{font-size:32px;font-weight:800;font-family:Georgia,serif}
.score-label{font-size:16px;font-weight:700;margin-left:10px}
.bull{color:var(--green)} .bullsoft{color:var(--green2)} .neutral{color:var(--muted)}
.bearsoft{color:var(--amber)} .bear{color:var(--red2)}
.drivers{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:0 14px 12px}
@media(max-width:640px){.drivers{grid-template-columns:1fr}}
.driver{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.driver p{margin:6px 0 0;font-size:13.5px;color:var(--muted)}
.driver strong{font-size:14px}
.pill{font-size:10.5px;font-weight:800;letter-spacing:1px;border-radius:20px;
  padding:2px 9px;margin-right:8px;vertical-align:1px}
.pill.bullish{background:#123c24;color:var(--green2)}
.pill.bearish{background:#43161a;color:var(--red2)}
.pill.neutral{background:#2a3552;color:var(--muted)}
.ccs{padding:0 14px 12px}
.cc{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0}
.cc p{margin:6px 0 0;font-size:13.5px;color:var(--muted)}
.movers-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:0 14px 12px}
@media(max-width:640px){.movers-grid{grid-template-columns:1fr}}
.movers-grid h4{margin:0 0 6px;font-size:14px}
table.movers{width:100%;border-collapse:collapse;font-size:13px}
table.movers td{padding:6px 5px;border-top:1px solid var(--line);vertical-align:top}
.ticker{font-weight:800;white-space:nowrap}
.move.pos{color:var(--green2);font-weight:700;white-space:nowrap}
.move.neg{color:var(--red2);font-weight:700;white-space:nowrap}
.acc-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:12px 0}
@media(max-width:640px){.acc-cards{grid-template-columns:1fr}}
.acc-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:16px;text-align:center}
.acc-k{font-size:12px;letter-spacing:1.5px;color:var(--muted);text-transform:uppercase}
.acc-v{font-size:40px;font-weight:900;font-family:Georgia,serif;color:var(--gold)}
.acc-s{font-size:13px;color:var(--muted)}
table.ledger{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
table.ledger th,table.ledger td{padding:9px 8px;border-top:1px solid var(--line);
  text-align:left;vertical-align:top}
table.ledger th{color:var(--muted);font-size:11.5px;letter-spacing:1px;
  text-transform:uppercase;border-top:none}
.cell-conf{font-size:12px;color:var(--muted)}
tr.rat-row td{border-top:none;padding-top:0}
tr.rat-row details{margin:0}
.up{color:var(--green2)} .down{color:var(--red2)}
.v-hit{color:var(--green2);font-weight:800}.v-miss{color:var(--red2);font-weight:800}
.v-push{color:var(--amber);font-weight:800}.v-pend{color:var(--muted)}
.fc-mini{font-weight:800;padding:2px 10px;border-radius:14px;background:var(--panel2);font-size:13px}
.spy-mini{font-size:13px;color:var(--muted);margin-left:8px}
.day-card{margin:12px 0}
.day-card summary{font-size:15px}
.method{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:8px 26px;margin:12px 0}
.method ol{margin:8px 0;padding-left:22px}
.method li{margin:10px 0;font-size:14.5px}
.method code{background:var(--panel2);padding:1px 7px;border-radius:5px;font-size:13px}
footer{color:var(--muted);font-size:12.5px;text-align:center;margin-top:30px;
  border-top:1px solid var(--line);padding-top:16px}
.muted{color:var(--muted)}
.fine{font-size:12.5px;margin-top:14px}
"""

JS = """
document.querySelectorAll('.tabs button').forEach(function(b){
  b.addEventListener('click', function(){
    document.querySelectorAll('.tabs button').forEach(function(x){x.classList.remove('active')});
    b.classList.add('active');
    document.querySelectorAll('.tabpane').forEach(function(p){
      p.classList.toggle('active', p.id === 'tab-' + b.dataset.tab);
    });
  });
});
(function(){
  var el = document.getElementById('next-update');
  if(!el) return;
  var target = new Date(el.getAttribute('data-iso'));
  var cd = document.getElementById('countdown');
  function tick(){
    var ms = target - new Date();
    if(ms <= 0){ cd.textContent = '(updating soon — refresh the page)'; return; }
    var s = Math.floor(ms/1000), d = Math.floor(s/86400); s %= 86400;
    var h = Math.floor(s/3600); s %= 3600;
    var m = Math.floor(s/60); var sec = s % 60;
    var parts = [];
    if(d) parts.push(d + 'd');
    if(h || d) parts.push(h + 'h');
    parts.push(m + 'm'); parts.push(sec + 's');
    cd.textContent = 'in ' + parts.join(' ');
  }
  tick(); setInterval(tick, 1000);
})();
"""


def build_html(days, forecasts, generated_at):
    days_sorted = sorted(days, key=lambda d: d["date"], reverse=True)
    now = datetime.now(ET)
    latest = days_sorted[0] if days_sorted else None

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="latest-date" content="{esc(latest['date']) if latest else ''}">
<title>{SITE_NAME} — Daily AI Investor Sentiment + SPY Calls</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div class="brand">AI Sentiment Forecast</div>
    <h1>Will the market be up or down?</h1>
    <p class="tag">{TAGLINE}</p>
    <div class="dateline">Updated {esc(generated_at)} ET · Weekdays, twice daily</div>
  </header>

  <nav class="tabs">
    <button data-tab="today" class="active">Today</button>
    <button data-tab="forecasts">Forecasts</button>
    <button data-tab="archive">Archive</button>
    <button data-tab="method">Methodology</button>
  </nav>

  <div class="tabpane active" id="tab-today">
    {render_today(latest, forecasts, now) if latest else '<p class="muted">No editions yet.</p>'}
  </div>
  <div class="tabpane" id="tab-forecasts">
    {render_forecasts(forecasts)}
  </div>
  <div class="tabpane" id="tab-archive">
    {render_archive(days_sorted)}
  </div>
  <div class="tabpane" id="tab-method">
    {METHOD_HTML}
  </div>

  <footer>
    Sentiment data: finviz.com macro + single-stock news feeds, novelty-filtered twice daily.<br>
    Price data: SPY via Yahoo Finance. Forecasts are directional experiments,
    not financial advice.<br>
    Generated {esc(generated_at)} ET.
  </footer>
</div>
<script>{JS}</script>
</body>
</html>"""


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base, "data")
    with open(os.path.join(data_dir, "daily.json")) as f:
        days = json.load(f)["days"]
    with open(os.path.join(data_dir, "forecasts.json")) as f:
        forecasts = json.load(f)["forecasts"]
    gen_at = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
    out = build_html(days, forecasts, gen_at)
    with open(os.path.join(base, "index.html"), "w") as f:
        f.write(out)
    print(f"wrote index.html ({len(out)} bytes), {len(days)} days, {len(forecasts)} forecasts")


if __name__ == "__main__":
    main()
