#!/usr/bin/env python3
"""Daily evening update for the AI Sentiment Forecast site.

Idempotent: safe to re-run; never duplicates days or forecast records.

Inputs (written by the cron worker before running):
  --reports-json  local JSON file holding the artifact's listreports result
                  ({"data": {"reports": [...]}} or {"reports": [...]})
  --date          edition date YYYY-MM-DD (default: today, America/New_York)
  --take-file     analyst take text file (default: data/takes/<date>.txt
                  next to this repo); --take overrides it

What it does:
  1. Finds today's Post-Close edition (and Pre-Market if present). If there is
     no Post-Close edition (market holiday, feed failure), prints SKIP and
     exits 0 without committing anything.
  2. Upserts the day object in data/daily.json and the forecast record in
     data/forecasts.json on GitHub (contents API, same auth pattern as
     publish_post.py — no local git clone needed).
  3. Grades every pending forecast whose target trading day has closed, using
     SPY closes from Yahoo Finance (close-to-close % from the forecast day to
     the target day). A flat 0.00% day is a PUSH (excluded from hit rate).
  4. Regenerates index.html and commits all three files.
  5. Prints a short summary the cron worker can echo to the user.

Forecast rule: post-close score > 50 -> UP; < 50 -> DOWN; == 50 -> majority
sentiment of that day's macro drivers decides (bearish majority -> DOWN).
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request  # noqa: E402

GH_ALLOWED = ("api.github.com",)
OWNER, REPO = "johnarks", "ai-sentiment-forecast"
ET = ZoneInfo("America/New_York")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import gen_site  # noqa: E402


# ---------- GitHub contents API ----------

def gh(method, path, body=None):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "sentiment-forecast-daily"},
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    add_surrogate_to_request(req, "custom.github", allowed_hosts=GH_ALLOWED)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw.decode()) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"GitHub HTTP {exc.code} {method} {path}: {detail}")


def get_file(path):
    cur = gh("GET", f"/repos/{OWNER}/{REPO}/contents/{path}?ref=main")
    if cur is None:
        return None, None
    return json.loads(base64.b64decode(cur["content"]).decode()), cur["sha"]


def put_file(path, obj_or_text, sha, message):
    if isinstance(obj_or_text, str):
        text = obj_or_text
    else:
        text = json.dumps(obj_or_text, indent=2)
    content = base64.b64encode(text.encode()).decode()
    body = {"message": message, "content": content, "branch": "main"}
    if sha:
        body["sha"] = sha
    gh("PUT", f"/repos/{OWNER}/{REPO}/contents/{path}", body)


# ---------- forecast logic ----------

def forecast_call(score, drivers):
    if score > 50:
        return "UP"
    if score < 50:
        return "DOWN"
    bears = sum(1 for d in drivers if d.get("sentiment") == "bearish")
    bulls = sum(1 for d in drivers if d.get("sentiment") == "bullish")
    return "DOWN" if bears > bulls else "UP"


def confidence(score):
    d = abs(score - 50)
    if d >= 20:
        return "High"
    if d >= 10:
        return "Moderate"
    return "Low"


def rationale(pc, call):
    score, label = pc["score"], pc["label"]
    drivers = pc.get("drivers", [])
    want = "bullish" if call == "UP" else "bearish"
    key = next((d["title"] for d in drivers if d.get("sentiment") == want), None)
    key = key or (drivers[0]["title"] if drivers else label)
    return f"Post-close score {score} ({label}): {key}."


def next_trading_day_label(d):
    """Best-guess 'for' label: next calendar weekday (holidays corrected at grade time)."""
    cur = d + timedelta(days=1)
    while cur.weekday() >= 5:  # skip Sat/Sun
        cur += timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


def spy_closes():
    """Return {YYYY-MM-DD: close} for SPY over the last ~60 days (yfinance)."""
    import yfinance as yf
    hist = yf.Ticker("SPY").history(period="3mo", auto_adjust=False)
    return {ts.strftime("%Y-%m-%d"): float(row["Close"])
            for ts, row in hist.iterrows()}


def first_trading_day_after(d, closes, today):
    cands = sorted(x for x in closes if d < x <= today)
    return cands[0] if cands else None


def grade_pending(ledger, today):
    """Grade forecasts whose next trading day has closed. Returns list of (date, outcome, pct).

    The actual target is the first trading day after the forecast date with
    available data (handles holidays); the ledger's target_date is corrected
    to match what was actually graded.
    """
    pending = [f for f in ledger if f.get("hit") is None and not f.get("push")
               and f.get("date") and f["date"] < today]
    if not pending:
        return []
    try:
        closes = spy_closes()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: yfinance fetch failed ({exc}); leaving forecasts pending")
        return []
    graded = []
    for f in pending:
        td = first_trading_day_after(f["date"], closes, today)
        c0 = closes.get(f["date"])
        c1 = closes.get(td) if td else None
        if td is None or c0 is None or c1 is None:
            continue  # not gradable yet — stay pending
        f["target_date"] = td
        pct = round((c1 - c0) / c0 * 100, 2)
        f["spy_pct"] = pct
        if pct == 0.0:
            f["push"] = True
            graded.append((f["date"], "PUSH", pct))
        else:
            f["hit"] = (pct > 0) == (f["call"] == "UP")
            graded.append((f["date"], "HIT" if f["hit"] else "MISS", pct))
    return graded


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports-json", required=True)
    ap.add_argument("--date", default=None)
    ap.add_argument("--take", default=None)
    ap.add_argument("--take-file", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    date = a.date or datetime.now(ET).strftime("%Y-%m-%d")
    today = datetime.now(ET).strftime("%Y-%m-%d")

    with open(a.reports_json) as fh:
        raw = json.load(fh)
    reports = raw.get("data", raw).get("reports", [])

    post = next((r for r in reports
                 if r.get("date") == date and r.get("edition") == "Post-Close"), None)
    if post is None:
        print(f"SKIP: no Post-Close edition for {date} (market holiday or missing feed) — no commit")
        return 0
    pre = next((r for r in reports
                if r.get("date") == date and r.get("edition") == "Pre-Market"), None)

    # analyst take
    take = None
    take_file = a.take_file or os.path.join(REPO_ROOT, "data", "takes", f"{date}.txt")
    if os.path.exists(take_file):
        with open(take_file) as fh:
            take = fh.read().strip()
    if a.take:
        take = a.take.strip()
    if not take:
        remote_take, _ = get_file(f"data/takes/{date}.txt")
        if isinstance(remote_take, str) and remote_take.strip():
            take = remote_take.strip()
    if not take:
        take = "No analyst take was written for this edition."

    pc = {
        "score": post["score"],
        "label": post["label"],
        "summary": post["summary"],
        "drivers": [{"title": d["title"], "detail": d["detail"],
                     "sentiment": d["sentiment"]}
                    for d in post.get("macroDrivers", [])],
        "strength": [{"ticker": s["ticker"], "move": s["move"], "note": s["note"]}
                     for s in post.get("strength", [])],
        "pressure": [{"ticker": s["ticker"], "move": s["move"], "note": s["note"]}
                     for s in post.get("pressure", [])],
    }
    day = {
        "date": date,
        "premarket": ({"score": pre["score"], "label": pre["label"],
                       "summary": pre["summary"]} if pre else None),
        "postclose": pc,
        "take": take,
        "forecast": None,
        "result": None,
    }
    call = forecast_call(pc["score"], pc["drivers"])
    conf = confidence(pc["score"])
    rat = rationale(pc, call)

    daily, daily_sha = get_file("data/daily.json")
    ledger, ledger_sha = get_file("data/forecasts.json")
    days = (daily or {}).get("days", [])
    forecasts = (ledger or {}).get("forecasts", [])

    # target label = next calendar weekday (grade time corrects for holidays)
    target = next_trading_day_label(datetime.strptime(date, "%Y-%m-%d").date())

    day["forecast"] = {"call": call, "confidence": conf,
                       "rationale": rat, "target_date": target}
    days = [d for d in days if d["date"] != date] + [day]

    rec = next((f for f in forecasts if f["date"] == date), None)
    if rec is None:
        rec = {"date": date, "score": pc["score"], "label": pc["label"],
               "call": call, "confidence": conf, "rationale": rat,
               "target_date": target, "spy_pct": None, "hit": None, "push": False}
        forecasts.append(rec)
    else:
        rec.update({"score": pc["score"], "label": pc["label"], "call": call,
                    "confidence": conf, "rationale": rat, "target_date": target})

    graded = grade_pending(forecasts, today)

    # sync day results from ledger
    by_date = {f["date"]: f for f in forecasts}
    for d in days:
        f = by_date.get(d["date"])
        if f and (f.get("hit") is not None or f.get("push") or f.get("spy_pct") is not None):
            d["result"] = {"spy_pct": f.get("spy_pct"), "hit": f.get("hit"),
                           "push": bool(f.get("push"))}
        elif d["date"] == date:
            d["result"] = None

    days.sort(key=lambda d: d["date"], reverse=True)
    forecasts.sort(key=lambda f: f["date"], reverse=True)

    gen_at = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
    html_out = gen_site.build_html(days, forecasts, gen_at)
    idx_cur, idx_sha = get_file("index.html")

    if a.dry_run:
        print(f"DRY-RUN: would upsert {date}, forecast {call} ({conf}) for {target}")
        for gdate, outcome, pct in graded:
            print(f"DRY-RUN: would grade {gdate}: {outcome} (SPY {pct:+.2f}%)")
        print(f"DRY-RUN: index.html would be {len(html_out)} bytes")
        return 0

    put_file("data/daily.json", {"days": days}, daily_sha,
             f"Daily update {date}: upsert day object")
    put_file("data/forecasts.json", {"forecasts": forecasts}, ledger_sha,
             f"Daily update {date}: forecast ledger")
    put_file("index.html", html_out, idx_sha,
             f"Daily update {date}: regenerate site")

    hits = sum(1 for f in forecasts if f.get("hit"))
    n = sum(1 for f in forecasts if f.get("hit") is not None)
    acc = f"{hits}/{n} ({100.0 * hits / n:.1f}%)" if n else "0/0"
    print(f"UPDATED {date}: forecast {call} ({conf}) for {target}")
    for gdate, outcome, pct in graded:
        print(f"GRADED {gdate}: {outcome} (SPY {pct:+.2f}%)")
    print(f"ACCURACY {acc} — pushed daily.json, forecasts.json, index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
