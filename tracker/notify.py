"""Push notifications. Each channel turns on when its secret exists:
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   (recommended)
  NTFY_TOPIC  (optional NTFY_SERVER, default https://ntfy.sh)
  DISCORD_WEBHOOK_URL
"""
import html
from datetime import datetime, timezone

from .config import env
from .http import SESSION

SPONSOR_BADGE = {"no_sponsor": "🛂 no sponsorship", "citizen": "🇺🇸 citizens/clearance",
                 "sponsors": "✅ sponsors visas"}


def badges(r):
    out = []
    if "newgrad" in r.get("tags", []):
        out.append("⭐ new grad")
    if r.get("start") == "fits":
        out.append("📅 2027 start ✓")
    elif r.get("start") == "too_early":
        out.append("⚠️ starts too early")
    if r.get("sponsorship") in SPONSOR_BADGE:
        out.append(SPONSOR_BADGE[r["sponsorship"]] + " (per JD)")
    elif r.get("h1b"):
        n = r["h1b"].get("tech", 0)
        out.append(f"🟢 H-1B history: {n:,} tech filings" if n >= 20 else
                   f"🟡 little H-1B history ({n})" if n else "⚪ no H-1B filings found")
    if r.get("match") is not None:
        out.append(f"🎯 {r['match']}% match ({r.get('resume')})")
    if r.get("salary"):
        out.append(f"💵 {r['salary']}")
    if r.get("min_years"):
        out.append(f"⏳ {r['min_years']}+ yrs")
    if "level2" in r.get("tags", []):
        out.append("Ⅱ level II")
    if r.get("phd"):
        out.append("🎓 PhD")
    if r.get("loc_status") in ("remote", "unknown"):
        out.append("🌐 remote" if r["loc_status"] == "remote" else "❔ location unclear")
    return out


def _loc(r):
    locs = r.get("locations") or []
    s = locs[0] if locs else "—"
    return s + (f" +{len(locs) - 1}" if len(locs) > 1 else "")


def _age(iso):
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        h = (datetime.now(timezone.utc) - d).total_seconds() / 3600
        return f"posted {int(h)}h ago" if h < 48 else f"posted {int(h // 24)}d ago"
    except ValueError:
        return ""


def _event_prefix(ev, r):
    fire = "🔥 " if r.get("priority") else ""
    return fire + _event_kind(ev, r)


STATUS_WORDS = {"applied": "you applied", "oa": "you got an assessment", "interviewing": "you interviewed",
                "offer": "you got an offer", "rejected": "you were rejected", "ghosted": "no response",
                "withdrawn": "you withdrew", "saved": "you saved it"}


def _event_kind(ev, r):
    if ev == "repost":
        prev = STATUS_WORDS.get(r.get("prev_status"))
        return (f"🔁 Repost (first seen {r.get('repost_first_seen', '')[:10]}"
                + (f"; {prev} last time" if prev else "") + ") · ")
    if ev == "reopened":
        return "↩️ Reopened · "
    return ""


def format_telegram(events, dashboard_url=None, header=None):
    lines = [header or f"<b>🆕 {len(events)} new role{'s' if len(events) != 1 else ''}</b>"]
    for ev, r in events:
        cats = "/".join(r.get("categories", []))
        meta = " · ".join(x for x in [_loc(r), cats, _age(r.get("posted_at"))] if x)
        b = ", ".join(badges(r))
        lines.append(
            f"\n{_event_prefix(ev, r)}<b>{html.escape(r['company'])}</b>\n"
            f"<a href=\"{html.escape(r['url'], quote=True)}\">{html.escape(r['title'])}</a>\n"
            f"{html.escape(meta)}" + (f"\n{html.escape(b)}" if b else ""))
    if dashboard_url:
        lines.append(f"\n<a href=\"{dashboard_url}\">Open dashboard</a>")
    return lines


def _chunks(lines, limit):
    buf = ""
    for line in lines:
        if len(buf) + len(line) + 1 > limit and buf:
            yield buf
            buf = ""
        buf += line + "\n"
    if buf:
        yield buf


def send_telegram(lines):
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    for chunk in _chunks(lines, 3800):
        r = SESSION.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
            "chat_id": chat, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True})
        if not r.ok:
            print("[notify] telegram error:", r.status_code, r.text[:200])
    return True


def send_ntfy(events, dashboard_url=None, title=None):
    topic = env("NTFY_TOPIC")
    if not topic:
        return False
    server = env("NTFY_SERVER") or "https://ntfy.sh"
    body = "\n".join(f"{_event_prefix(ev, r)}{r['company']}: {r['title']} ({_loc(r)})" for ev, r in events)
    headers = {"Title": title or f"{len(events)} new job(s)", "Tags": "briefcase"}
    if dashboard_url:
        headers["Click"] = dashboard_url
    elif len(events) == 1:
        headers["Click"] = events[0][1]["url"]
    r = SESSION.post(f"{server.rstrip('/')}/{topic}", data=body[:3900].encode(), headers=headers, timeout=20)
    if not r.ok:
        print("[notify] ntfy error:", r.status_code, r.text[:200])
    return True


def send_discord(events, dashboard_url=None, header=None):
    hook = env("DISCORD_WEBHOOK_URL")
    if not hook:
        return False
    lines = [header or f"**🆕 {len(events)} new role(s)**"]
    for ev, r in events:
        b = ", ".join(badges(r))
        lines.append(f"{_event_prefix(ev, r)}**{r['company']}** — [{r['title']}](<{r['url']}>) · {_loc(r)}" + (f" · {b}" if b else ""))
    if dashboard_url:
        lines.append(f"[Dashboard](<{dashboard_url}>)")
    for chunk in _chunks(lines, 1900):
        SESSION.post(hook, json={"content": chunk}, timeout=20)
    return True


def notify(events, dashboard_url=None, header=None):
    if not events:
        return
    sent = [send_telegram(format_telegram(events, dashboard_url, header)),
            send_ntfy(events, dashboard_url, header and html.unescape(header).replace("<b>", "").replace("</b>", "")),
            send_discord(events, dashboard_url, header and header.replace("<b>", "**").replace("</b>", "**"))]
    if not any(sent):
        print("[notify] no channel configured — printing instead:")
        for ev, r in events:
            print(f"  [{ev}] {r['company']} | {r['title']} | {_loc(r)} | {', '.join(badges(r))} | {r['url']}")


if __name__ == "__main__":
    # python -m tracker.notify  → sends a sample alert to every configured channel
    sample = {"company": "Test Robotics", "title": "Machine Learning Engineer, New Grad",
              "url": "https://example.com/job", "locations": ["San Francisco, CA"], "categories": ["ML", "CV"],
              "tags": ["newgrad"], "sponsorship": "sponsors", "match": 78, "resume": "ML/AI",
              "posted_at": datetime.now(timezone.utc).isoformat()}
    notify([("new", sample)], env("DASHBOARD_URL"), "<b>✅ Job tracker test notification</b>")
