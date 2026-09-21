#!/usr/bin/env python3
"""Twice-daily update for the AI Sentiment Forecast site.

Idempotent: safe to re-run; never duplicates days or forecast records.

Modes:
  --edition morning : publishes today's Pre-Market edition and makes the FINAL
                      call for today's session:
                        final_score = round_half_up((prev trading day's
                        post-close score + today's pre-market score) / 2)
  --edition evening : publishes today's Post-Close edition, makes the EARLY
                      call for the next trading day, and grades both calls
                      whose target day has now closed.

Inputs (written by the cron worker before running):
  --reports-json  local JSON file holding the artifact's listreports result
                  ({"data": {"reports": [...]}} or {"reports": [...]})
  --date          edition date YYYY-MM-DD (default: today, America/New_York)
  --take          analyst take text (overrides the take file)
  --take-file     take file path (default: data/takes/<date>.txt for evening,
                  data/takes/<date>-morning.txt for morning)

Both modes upsert data/daily.json and data/forecasts.json on GitHub
(contents API — no local git clone needed), regenerate index.html, and
commit the files. Grading happens only in evening mode, when the close is
known: BOTH calls target the same trading day and are graded vs
prior-trading-day close -> target-day close. A flat 0.00% day is a PUSH
(excluded from hit rate).

Early-call rule: post-close score > 50 -> UP; < 50 -> DOWN; == 50 ->
majority sentiment of that evening's macro drivers decides.
Final-call rule: final_score as above; > 50 -> UP; < 50 -> DOWN; == 50 ->
majority sentiment of this morning's drivers decides.
"""
import argparse
import base64
import json
import math
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


def get_raw(path):
    """Return (decoded text, sha) for any repo file, or (None, None) if missing."""
    cur = gh("GET", f"/repos/{OWNER}/{REPO}/contents/{path}?ref=main")
    if cur is None:
        return None, None
    return base64.b64decode(cur["content"]).decode(), cur["sha"]


def get_json(path):
    """Return (parsed JSON, sha) for a JSON repo file, or (None, None) if missing."""
    text, sha = get_raw(path)
    if text is None:
        return None, None
    return json.loads(text), sha


def get_sha(path):
    """Return the blob sha of a repo file, or None if missing."""
    _, sha = get_raw(path)
    return sha


def get_file(path):
    # Back-compat alias: JSON files only.
    return get_json(path)


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

def round_half_up(x):
    return int(math.floor(x + 0.5))


def majority_call(drivers):
    bears = sum(1 for d in drivers if d.get("sentiment") == "bearish")
    bulls = sum(1 for d in drivers if d.get("sentiment") == "bullish")
    return "DOWN" if bears > bulls else "UP"


def score_to_call(score, drivers):
    if score > 50:
        return "UP"
    if score < 50:
        return "DOWN"
    return majority_call(drivers)


def confidence(score):
    d = abs(score - 50)
    if d >= 20:
        return "High"
    if d >= 10:
        return "Moderate"
    return "Low"


def key_driver_title(drivers, call):
    want = "bullish" if call == "UP" else "bearish"
    key = next((d["title"] for d in drivers if d.get("sentiment") == want), None)
    return key or (drivers[0]["title"] if drivers else "")


def next_trading_day_label(d):
    """Best-guess target label: next calendar weekday (holidays corrected at grade time)."""
    cur = d + timedelta(days=1)
    while cur.weekday() >= 5:  # skip Sat/Sun
        cur += timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


def spy_closes():
    """Return {YYYY-MM-DD: close} for SPY over the last ~3 months (yfinance)."""
    import yfinance as yf
    hist = yf.Ticker("SPY").history(period="3mo", auto_adjust=False)
    return {ts.strftime("%Y-%m-%d"): float(row["Close"])
            for ts, row in hist.iterrows()}


def grade_pending(ledger, today):
    """Grade forecast records whose target trading day has closed.

    Both the early and final call for target day T are graded vs
    prior-trading-day close -> T close. If T has no data (holiday), the target
    is corrected to the next trading day with data and the ledger is updated.
    Returns list of (target_date, kind, outcome, pct).
    """
    pending = [f for f in ledger
               if f.get("hit") is None and not f.get("push")
               and f.get("target_date") and f["target_date"] <= today]
    if not pending:
        return []
    try:
        closes = spy_closes()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: yfinance fetch failed ({exc}); leaving forecasts pending")
        return []
    dates = sorted(closes)
    graded = []
    for f in pending:
        # first trading day >= target_date with data, and a predecessor
        t = next((d for d in dates if d >= f["target_date"] and d <= today), None)
        if t is None:
            continue
        i = dates.index(t)
        if i == 0:
            continue
        prev = dates[i - 1]
        c0, c1 = closes[prev], closes[t]
        f["target_date"] = t
        pct = round((c1 - c0) / c0 * 100, 2)
        f["spy_pct"] = pct
        if pct == 0.0:
            f["push"] = True
            graded.append((t, f.get("kind"), "PUSH", pct))
        else:
            f["hit"] = (pct > 0) == (f["call"] == "UP")
            graded.append((t, f.get("kind"), "HIT" if f["hit"] else "MISS", pct))
    return graded


# ---------- shared helpers ----------

def load_reports(path):
    with open(path) as fh:
        raw = json.load(fh)
    return raw.get("data", raw).get("reports", [])


def find_edition(reports, date, edition):
    return next((r for r in reports
                 if r.get("date") == date and r.get("edition") == edition), None)


def edition_obj(report, take, created_at):
    pub = ""
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            pub = dt.astimezone(ET).strftime("%-I:%M %p")
        except Exception:  # noqa: BLE001
            pass
    return {
        "score": report["score"],
        "label": report["label"],
        "summary": report["summary"],
        "drivers": [{"title": d["title"], "detail": d["detail"],
                     "sentiment": d["sentiment"]}
                    for d in report.get("macroDrivers", [])],
        "strength": [{"ticker": s["ticker"], "move": s["move"], "note": s["note"]}
                     for s in report.get("strength", [])],
        "pressure": [{"ticker": s["ticker"], "move": s["move"], "note": s["note"]}
                     for s in report.get("pressure", [])],
        "crossCurrents": [{"title": c["title"], "detail": c["detail"]}
                          for c in report.get("crossCurrents", [])],
        "take": take,
        "published": pub,
    }


def resolve_take(a, default_name):
    if a.take:
        return a.take.strip()
    take_file = a.take_file or os.path.join(REPO_ROOT, "data", "takes", default_name)
    if os.path.exists(take_file):
        with open(take_file) as fh:
            t = fh.read().strip()
            if t:
                return t
    remote, _ = get_raw(f"data/takes/{default_name}")
    if isinstance(remote, str) and remote.strip():
        return remote.strip()
    return "No analyst take was written for this edition."


def upsert_forecast(forecasts, rec):
    """Upsert by (target_date, kind)."""
    cur = next((f for f in forecasts
                if f.get("target_date") == rec["target_date"]
                and f.get("kind") == rec["kind"]), None)
    if cur is None:
        forecasts.append(rec)
    else:
        cur.update(rec)


def new_day(date):
    return {"date": date, "morning": None, "evening": None,
            "early_call": None, "final_call": None, "result": None}


def acc_line(forecasts):
    bits = []
    for kind in ("final", "early"):
        graded = [f for f in forecasts
                  if f.get("kind") == kind and f.get("hit") is not None]
        hits = sum(1 for f in graded if f["hit"])
        n = len(graded)
        bits.append(f"{kind} {hits}/{n} ({100.0 * hits / n:.1f}%)" if n else f"{kind} 0/0")
    return ", ".join(bits)


# ---------- modes ----------

def run_morning(a, reports, date):
    pre = find_edition(reports, date, "Pre-Market")
    if pre is None:
        print(f"SKIP: no Pre-Market edition for {date} (market holiday or feed not ready) — no commit")
        return 0

    daily, daily_sha = get_json("data/daily.json")
    ledger, ledger_sha = get_json("data/forecasts.json")
    days = (daily or {}).get("days", [])
    forecasts = (ledger or {}).get("forecasts", [])

    take = resolve_take(a, f"{date}-morning.txt")
    morning_ed = edition_obj(pre, take, pre.get("createdAt"))

    day = next((d for d in days if d["date"] == date), None)
    if day is None:
        day = new_day(date)
        days.append(day)
    day["morning"] = morning_ed

    # previous trading day's evening score
    prev = next((d for d in sorted(days, key=lambda x: x["date"], reverse=True)
                 if d["date"] < date and d.get("evening")), None)
    if prev is None:
        print(f"SKIP: no previous evening edition to blend with for {date} — no commit")
        return 0
    ps, ms = prev["evening"]["score"], morning_ed["score"]
    fs = round_half_up((ps + ms) / 2)
    call = score_to_call(fs, morning_ed["drivers"])
    conf = confidence(fs)
    key = key_driver_title(morning_ed["drivers"], call)
    rat = (f"Blended score {fs} (prev post-close {ps} on {prev['date']} + "
           f"pre-market {ms}): {key}." if key else
           f"Blended score {fs} (prev post-close {ps} on {prev['date']} + pre-market {ms}).")
    scores_detail = (f"Post-close {ps} ({prev['date']}) + pre-market {ms} "
                     f"({date}) = {fs}")
    day["final_call"] = {"call": call, "confidence": conf, "rationale": rat,
                         "made": date, "scores_detail": scores_detail}
    upsert_forecast(forecasts, {"target_date": date, "kind": "final",
                                "date": date, "scores_detail": scores_detail,
                                "call": call, "confidence": conf,
                                "rationale": rat, "spy_pct": None,
                                "hit": None, "push": False})

    days.sort(key=lambda d: d["date"], reverse=True)
    forecasts.sort(key=lambda f: (f["target_date"], f.get("kind", "")), reverse=True)

    gen_at = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
    html_out = gen_site.build_html(days, forecasts, gen_at)

    if a.dry_run:
        print(f"DRY-RUN: morning {date}: final call {call} ({conf}), blended {fs}")
        print(f"DRY-RUN: index.html would be {len(html_out)} bytes")
        return 0

    put_file("data/daily.json", {"days": days}, daily_sha,
             f"Morning update {date}: upsert morning edition + final call")
    put_file("data/forecasts.json", {"forecasts": forecasts}, ledger_sha,
             f"Morning update {date}: final call ledger")
    idx_sha = get_sha("index.html")
    put_file("index.html", html_out, idx_sha,
             f"Morning update {date}: regenerate site")
    print(f"UPDATED {date} morning: FINAL call {call} ({conf}) for today, blended {fs}")
    print(f"ACCURACY {acc_line(forecasts)}")
    return 0


def run_evening(a, reports, date, today):
    post = find_edition(reports, date, "Post-Close")
    if post is None:
        print(f"SKIP: no Post-Close edition for {date} (market holiday or feed failure) — no commit")
        return 0

    daily, daily_sha = get_json("data/daily.json")
    ledger, ledger_sha = get_json("data/forecasts.json")
    days = (daily or {}).get("days", [])
    forecasts = (ledger or {}).get("forecasts", [])

    take = resolve_take(a, f"{date}.txt")
    evening_ed = edition_obj(post, take, post.get("createdAt"))

    day = next((d for d in days if d["date"] == date), None)
    if day is None:
        day = new_day(date)
        days.append(day)
    day["evening"] = evening_ed

    # early call for the next trading day
    target = next_trading_day_label(datetime.strptime(date, "%Y-%m-%d").date())
    call = score_to_call(evening_ed["score"], evening_ed["drivers"])
    conf = confidence(evening_ed["score"])
    key = key_driver_title(evening_ed["drivers"], call)
    rat = (f"Post-close score {evening_ed['score']} ({evening_ed['label']}): {key}."
           if key else
           f"Post-close score {evening_ed['score']} ({evening_ed['label']}).")
    tday = next((d for d in days if d["date"] == target), None)
    if tday is None:
        tday = new_day(target)
        days.append(tday)
    tday["early_call"] = {"call": call, "confidence": conf, "rationale": rat,
                          "made": date,
                          "scores_detail": f"Post-close {evening_ed['score']} ({date})"}
    upsert_forecast(forecasts, {"target_date": target, "kind": "early",
                                "date": date, "score": evening_ed["score"],
                                "label": evening_ed["label"], "call": call,
                                "confidence": conf, "rationale": rat,
                                "spy_pct": None, "hit": None, "push": False})

    graded = grade_pending(forecasts, today)

    # sync day results from the ledger (both calls, same grading window)
    by_target = {}
    for f in forecasts:
        by_target.setdefault(f["target_date"], []).append(f)
    for d in days:
        recs = by_target.get(d["date"], [])
        early = next((f for f in recs if f.get("kind") == "early"), None)
        final = next((f for f in recs if f.get("kind") == "final"), None)
        spy = next((f.get("spy_pct") for f in recs
                    if f.get("spy_pct") is not None), None)
        if spy is not None or (early and early.get("hit") is not None) \
                or (final and final.get("hit") is not None):
            d["result"] = {
                "spy_pct": spy,
                "early_hit": early.get("hit") if early else None,
                "final_hit": final.get("hit") if final else None,
            }
        elif d["date"] == date and d.get("result") is None:
            d["result"] = None

    days.sort(key=lambda d: d["date"], reverse=True)
    forecasts.sort(key=lambda f: (f["target_date"], f.get("kind", "")), reverse=True)

    gen_at = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
    html_out = gen_site.build_html(days, forecasts, gen_at)

    if a.dry_run:
        print(f"DRY-RUN: evening {date}: early call {call} ({conf}) for {target}")
        for t, kind, outcome, pct in graded:
            print(f"DRY-RUN: would grade {t} {kind}: {outcome} (SPY {pct:+.2f}%)")
        print(f"DRY-RUN: index.html would be {len(html_out)} bytes")
        return 0

    put_file("data/daily.json", {"days": days}, daily_sha,
             f"Evening update {date}: upsert evening edition + early call")
    put_file("data/forecasts.json", {"forecasts": forecasts}, ledger_sha,
             f"Evening update {date}: early call + grading")
    idx_sha = get_sha("index.html")
    put_file("index.html", html_out, idx_sha,
             f"Evening update {date}: regenerate site")

    print(f"UPDATED {date} evening: early call {call} ({conf}) for {target}")
    for t, kind, outcome, pct in graded:
        print(f"GRADED {t} {kind}: {outcome} (SPY {pct:+.2f}%)")
    print(f"ACCURACY {acc_line(forecasts)} — pushed daily.json, forecasts.json, index.html")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports-json", required=True)
    ap.add_argument("--edition", choices=["morning", "evening"], default="evening")
    ap.add_argument("--date", default=None)
    ap.add_argument("--take", default=None)
    ap.add_argument("--take-file", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    date = a.date or datetime.now(ET).strftime("%Y-%m-%d")
    today = datetime.now(ET).strftime("%Y-%m-%d")
    reports = load_reports(a.reports_json)

    if a.edition == "morning":
        return run_morning(a, reports, date)
    return run_evening(a, reports, date, today)


if __name__ == "__main__":
    sys.exit(main())
