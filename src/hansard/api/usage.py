"""Spend tracking, caps and the answer cache (every model-written briefing costs money).

The site is public, so limits apply per visitor as well as overall. A visitor is
identified by a salted hash of their IP address; the address itself is never stored.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

from hansard.store.state import StateDB, utcnow


def prices_for(prices: dict[str, Any], model: str | None) -> dict[str, Any]:
    """The [usage] prices, with any per-model prices (``[usage.models."<model>"]``) on top."""
    per_model = prices.get("models") or {}
    for name, own in per_model.items():
        if model and (model == name or model.startswith(name)):
            return {**prices, **own}
    return prices


def cost_usd(usage: dict[str, int] | None, prices: dict[str, Any], model: str | None = None) -> float:
    """Cost of one call. Cache writes cost 1.25x the input price; cache reads are priced separately."""
    if not usage:
        return 0.0
    prices = prices_for(prices, model)
    price_in = float(prices.get("price_input_per_mtok", 4.0))
    writes = usage.get("cache_write_tokens", 0)
    return round(
        (usage.get("input_tokens", 0) - writes) / 1e6 * price_in
        + writes / 1e6 * price_in * float(prices.get("cache_write_multiplier", 1.25))
        + usage.get("output_tokens", 0) / 1e6 * float(prices.get("price_output_per_mtok", 20.0))
        + usage.get("cache_read_tokens", 0) / 1e6 * float(prices.get("price_cache_read_per_mtok", 0.2)),
        6,
    )


def network_of(address: str | None) -> str:
    """The unit limits count by: an IPv4 address, or an IPv6 /64 (one home or phone connection is given a whole
    /64, so counting single IPv6 addresses would let one person appear as countless visitors)."""
    if not address:
        return "-"
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 4:
        return str(ip)
    if ip.ipv4_mapped:
        return str(ip.ipv4_mapped)
    return str(ip) if ip.is_loopback else str(ipaddress.ip_network(f"{ip}/64", strict=False))


def visitor_key(state: StateDB, address: str | None, day: str | None = None) -> str:
    """A code for a visitor's IP address, used for daily limits and visitor counts; the address itself is not kept.

    The secret mixed into the code changes every day (UTC) and old ones are deleted, so codes cannot be linked
    across days or turned back into an address.
    """
    name = f"visitor_salt:{day or datetime.now(UTC).strftime('%Y-%m-%d')}"
    salt = state.get(name)
    if salt is None:
        with state.connect() as con:
            con.execute("INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)", (name, secrets.token_hex(16)))
            con.execute("DELETE FROM kv WHERE key = 'visitor_salt' OR (key LIKE 'visitor_salt:%' AND key != ?)",
                        (name,))
        salt = state.get(name)
    return hashlib.sha256(f"{salt}|{network_of(address)}".encode()).hexdigest()[:24]


def record_usage(state: StateDB, user_id: int | None, kind: str, model: str | None,
                 usage: dict[str, int] | None, prices: dict[str, Any], visitor: str | None = None) -> float:
    cost = cost_usd(usage, prices, model)
    u = usage or {}
    with state.connect() as con:
        con.execute(
            """INSERT INTO usage (user_id, visitor, at, kind, model, input_tokens, output_tokens,
                                  cache_read_tokens, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, visitor, utcnow(), kind, model, u.get("input_tokens", 0), u.get("output_tokens", 0),
             u.get("cache_read_tokens", 0), cost))
    return cost


def new_briefings_today(state: StateDB, visitor: str) -> int:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        return con.execute("SELECT count(*) FROM usage WHERE visitor = ? AND kind = 'answer' AND at >= ?",
                           (visitor, day)).fetchone()[0]


def _supporter_key(visitor: str) -> str:
    return f"supporter:{datetime.now(UTC).strftime('%Y-%m-%d')}:{visitor}"


def mark_supporter(state: StateDB, visitor: str) -> None:
    """The visitor went through to the donation page: no more reminders today (visitor codes change daily)."""
    with state.connect() as con:
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, '1')", (_supporter_key(visitor),))
        con.execute("DELETE FROM kv WHERE key LIKE 'supporter:%' AND key NOT LIKE ?",
                    (f"supporter:{datetime.now(UTC).strftime('%Y-%m-%d')}:%",))


def support_reminder_due(state: StateDB, visitor: str | None, every: int) -> int:
    """After every ``every``-th new briefing a visitor has had written today, how many that is (else 0); never
    once they have gone through to donate today."""
    if not visitor or every <= 0:
        return 0
    n = new_briefings_today(state, visitor)
    return n if n > 0 and n % every == 0 and state.get(_supporter_key(visitor)) is None else 0


def check_caps(state: StateDB, user_id: int | None, limits: dict[str, Any], visitor: str | None = None,
               pending_usd: float = 0.0) -> str | None:
    """Return a plain-English reason if this person may not have another briefing written now. ``pending_usd`` is
    the estimated cost of briefings being written at this moment, whose cost is not recorded yet."""
    now = datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    with state.connect() as con:
        if user_id is not None:
            asked = con.execute("SELECT count(*) FROM usage WHERE user_id = ? AND kind = 'answer' AND at >= ?",
                                (user_id, day)).fetchone()[0]
            if asked >= int(limits.get("per_user_daily_questions", 60)):
                return "You have reached today's question limit. It resets at midnight UTC."
        elif visitor is not None:
            asked = con.execute("SELECT count(*) FROM usage WHERE visitor = ? AND kind = 'answer' AND at >= ?",
                                (visitor, day)).fetchone()[0]
            if asked >= int(limits.get("per_visitor_daily_questions", 40)):
                return ("You have reached today's limit for new briefings. It resets at midnight UTC; "
                        "briefings others have already asked for still load.")
        spent_today = con.execute("SELECT coalesce(sum(cost_usd), 0) FROM usage WHERE at >= ?", (day,)).fetchone()[0]
        spent_month = con.execute("SELECT coalesce(sum(cost_usd), 0) FROM usage WHERE at >= ?", (month,)).fetchone()[0]
    if spent_today + pending_usd >= float(limits.get("daily_spend_cap_usd", 10.0)):
        return "Today's budget for new briefings has been used up. It resets at midnight UTC."
    if spent_month + pending_usd >= float(limits.get("monthly_spend_cap_usd", 150.0)):
        return "This month's budget for new briefings has been used up."
    return None


class InFlight:
    """Briefings being written right now. Their cost is only known when they finish, so without this, many
    requests at once would all pass the spending caps before any was counted: each is reserved at an estimated
    cost while it runs, and each reader (and the site as a whole) may have only a few at once."""

    def __init__(self, per_key: int = 2, total: int = 8, estimate_usd: float = 0.15):
        self.per_key, self.total, self.estimate_usd = per_key, total, estimate_usd
        self.running: dict[str, int] = defaultdict(int)
        self.lock = threading.Lock()

    def start(self, key: str) -> str | None:
        """Reserve a place, or say why not."""
        with self.lock:
            if self.running[key] >= self.per_key:
                return "A briefing is already being written for you; please wait for it to finish."
            if sum(self.running.values()) >= self.total:
                return "Many briefings are being written at the moment; please try again in a minute."
            self.running[key] += 1
            return None

    def finish(self, key: str) -> None:
        with self.lock:
            self.running[key] -= 1
            if self.running[key] <= 0:
                del self.running[key]

    def pending_usd(self, besides: int = 0) -> float:
        """Estimated cost of the briefings in progress, not counting ``besides`` of them (the caller's own)."""
        with self.lock:
            return max(sum(self.running.values()) - besides, 0) * self.estimate_usd


class RateLimiter:
    """At most ``per_minute`` requests per key in any rolling minute (in memory, per process)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            if len(self.hits) > 50_000:  # forget idle visitors
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 60]:
                    del self.hits[k]
            return True


# ---- answer cache ---------------------------------------------------------------------

def cache_key(parts: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def cached_events(state: StateDB, key: str, hours: float) -> list[dict[str, Any]] | None:
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with state.connect() as con:
        row = con.execute("SELECT events FROM answer_cache WHERE key = ? AND created_at >= ?", (key, since)).fetchone()
    return json.loads(row[0]) if row else None


def store_events(state: StateDB, key: str, events: list[dict[str, Any]], hours: float) -> None:
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with state.connect() as con:
        con.execute("DELETE FROM answer_cache WHERE created_at < ?", (since,))
        con.execute("INSERT OR REPLACE INTO answer_cache (key, created_at, events) VALUES (?, ?, ?)",
                    (key, utcnow(), json.dumps(events, default=str)))


# ---- feedback and cookie-free visit counts ----------------------------------------------

def record_feedback(state: StateDB, briefing_key: str | None, question: str, useful: bool,
                    visitor: str | None) -> None:
    with state.connect() as con:
        con.execute("INSERT INTO feedback (at, briefing_key, question, useful, visitor) VALUES (?, ?, ?, ?, ?)",
                    (utcnow(), briefing_key, question[:500], int(useful), visitor))


def record_failure(state: StateDB, kind: str, question: str | None, error: str) -> None:
    with state.connect() as con:
        con.execute("INSERT INTO failures (at, kind, question, error) VALUES (?, ?, ?, ?)",
                    (utcnow(), kind, (question or "")[:500], error[:500]))
        # keep the table small: the latest 500 are plenty to diagnose anything
        con.execute("DELETE FROM failures WHERE id NOT IN (SELECT id FROM failures ORDER BY id DESC LIMIT 500)")


def alerts(state: StateDB, usage_settings: dict[str, Any]) -> list[dict[str, str]]:
    """Things the operator should know about now: recent failures and spending close to the caps."""
    out: list[dict[str, str]] = []
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat(timespec="seconds")
    with state.connect() as con:
        n, latest = con.execute("SELECT count(*), max(at) FROM failures WHERE at >= ?", (since,)).fetchone()
        last_error = con.execute("SELECT kind, error FROM failures ORDER BY id DESC LIMIT 1").fetchone()
    if n:
        out.append({"level": "error" if n >= 3 else "warning",
                    "text": f"{n} briefing{'s' if n != 1 else ''} failed in the last 24 hours "
                            f"(latest: {last_error[0]}, {last_error[1]}). See Recent failures."})
    s = summary(state)
    for label, spent, cap in (("today", s["today_usd"], float(usage_settings.get("daily_spend_cap_usd", 0) or 0)),
                              ("this month", s["total_usd"],
                               float(usage_settings.get("monthly_spend_cap_usd", 0) or 0))):
        if cap and spent >= 0.8 * cap:
            out.append({"level": "error", "text": f"Spending {label} is ${spent:.2f}, {spent / cap:.0%} of the "
                                                  f"${cap:.0f} cap. New briefings pause at the cap."})
        elif cap and spent >= 0.5 * cap:
            out.append({"level": "warning", "text": f"Spending {label} is ${spent:.2f}, {spent / cap:.0%} of the "
                                                    f"${cap:.0f} cap."})
    return out


def recent_failures(state: StateDB, limit: int = 20) -> list[dict[str, Any]]:
    with state.connect() as con:
        rows = con.execute("SELECT at, kind, question, error FROM failures ORDER BY id DESC LIMIT ?",
                           (limit,)).fetchall()
    return [dict(r) for r in rows]


def count_question(state: StateDB, question: str) -> None:
    q = " ".join(question.lower().split())[:300]
    with state.connect() as con:
        con.execute("INSERT INTO question_counts (day, question, n) VALUES (?, ?, 1) "
                    "ON CONFLICT(day, question) DO UPDATE SET n = n + 1", (datetime.now(UTC).strftime("%Y-%m-%d"), q))


def popular_questions(state: StateDB, days: int = 30, limit: int = 20) -> list[dict[str, Any]]:
    """The questions asked most often (counts only, nothing about who asked)."""
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    with state.connect() as con:
        rows = con.execute("SELECT question, sum(n) total, max(day) last FROM question_counts WHERE day >= ? "
                           "GROUP BY question ORDER BY total DESC, last DESC LIMIT ?", (since, limit)).fetchall()
    return [{"question": q, "times": n, "last": last} for q, n, last in rows]


def record_visit(state: StateDB, path: str, referrer_host: str | None, visitor: str | None) -> None:
    """Count a page view. Only daily totals are kept; today's visitor hashes are rolled up and deleted."""
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        con.execute("INSERT INTO page_views (day, path, views) VALUES (?, ?, 1) "
                    "ON CONFLICT(day, path) DO UPDATE SET views = views + 1", (day, path[:100]))
        if referrer_host:
            con.execute("INSERT INTO referrers (day, host, views) VALUES (?, ?, 1) "
                        "ON CONFLICT(day, host) DO UPDATE SET views = views + 1", (day, referrer_host[:100]))
        if visitor:
            con.execute("INSERT OR IGNORE INTO day_visitors (day, visitor) VALUES (?, ?)", (day, visitor))
        con.execute("INSERT OR REPLACE INTO day_totals (day, visitors) "
                    "SELECT day, count(*) FROM day_visitors WHERE day < ? GROUP BY day", (day,))
        con.execute("DELETE FROM day_visitors WHERE day < ?", (day,))


# what a visitor can do that shows the site doing its job, counted once per visitor per day
EVENTS = ("ask", "follow_up", "source")


def record_event(state: StateDB, event: str, visitor: str | None) -> None:
    """Note that today's visitor did this (once a day each); earlier days are rolled up into totals and the visitor
    hashes deleted, so only daily counts are kept."""
    if event not in EVENTS or not visitor:
        return
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        con.execute("INSERT OR IGNORE INTO day_events (day, event, visitor) VALUES (?, ?, ?)", (day, event, visitor))
        con.execute("INSERT OR REPLACE INTO event_totals (day, event, visitors) "
                    "SELECT day, event, count(*) FROM day_events WHERE day < ? GROUP BY day, event", (day,))
        con.execute("DELETE FROM day_events WHERE day < ?", (day,))


def task_summary(state: StateDB, days: int = 30) -> dict[str, Any]:
    """Of the visitors in the last ``days`` days, how many asked a question, asked a follow-up and opened a source,
    and how many briefings readers rated useful: the signs that the site does what it is for."""
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        visitors = (con.execute("SELECT coalesce(sum(visitors), 0) FROM day_totals WHERE day >= ? AND day < ?",
                                (since, today)).fetchone()[0]
                    + con.execute("SELECT count(*) FROM day_visitors WHERE day = ?", (today,)).fetchone()[0])
        did = dict(con.execute("SELECT event, sum(visitors) FROM event_totals WHERE day >= ? AND day < ? "
                               "GROUP BY event", (since, today)).fetchall())
        for event, n in con.execute("SELECT event, count(*) FROM day_events WHERE day = ? GROUP BY event", (today,)):
            did[event] = did.get(event, 0) + n
        fb = con.execute("SELECT coalesce(sum(useful), 0), count(*) FROM feedback WHERE at >= ?", (since,)).fetchone()
    def share(n: int) -> int | None:
        return round(100 * n / visitors) if visitors else None

    return {"days": days, "visitors": visitors,
            **{e: {"visitors": did.get(e, 0), "pct": share(did.get(e, 0))} for e in EVENTS},
            "useful": {"yes": fb[0], "rated": fb[1], "pct": round(100 * fb[0] / fb[1]) if fb[1] else None}}


def visit_summary(state: StateDB, days: int = 30) -> dict[str, Any]:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        views = con.execute("SELECT day, sum(views) FROM page_views WHERE day >= ? GROUP BY day ORDER BY day",
                            (since,)).fetchall()
        visitors = dict(con.execute("SELECT day, visitors FROM day_totals WHERE day >= ?", (since,)).fetchall())
        visitors[today] = con.execute("SELECT count(*) FROM day_visitors WHERE day = ?", (today,)).fetchone()[0]
        pages = con.execute("SELECT path, sum(views) v FROM page_views WHERE day >= ? GROUP BY path "
                            "ORDER BY v DESC LIMIT 20", (since,)).fetchall()
        refs = con.execute("SELECT host, sum(views) v FROM referrers WHERE day >= ? GROUP BY host "
                           "ORDER BY v DESC LIMIT 20", (since,)).fetchall()
        fb = con.execute("SELECT sum(useful), count(*) FROM feedback WHERE at >= ?", (since,)).fetchone()
        unhelpful = con.execute("SELECT at, question FROM feedback WHERE useful = 0 AND at >= ? "
                                "ORDER BY at DESC LIMIT 20", (since,)).fetchall()
    return {
        "days": days,
        "by_day": [{"day": d, "views": v, "visitors": visitors.get(d, 0)} for d, v in views],
        "top_pages": [{"path": p, "views": v} for p, v in pages],
        "top_referrers": [{"host": h, "views": v} for h, v in refs],
        "feedback": {"useful": fb[0] or 0, "total": fb[1] or 0,
                     "recent_not_useful": [{"at": a, "question": q} for a, q in unhelpful]},
    }


def summary(state: StateDB) -> dict[str, Any]:
    month = datetime.now(UTC).strftime("%Y-%m")
    with state.connect() as con:
        rows = con.execute(
            """SELECT coalesce(u.email, CASE WHEN x.visitor IS NULL THEN '(system)' ELSE '(public)' END),
                      count(*), round(sum(cost_usd), 4), count(DISTINCT x.visitor)
               FROM usage x LEFT JOIN users u ON u.user_id = x.user_id
               WHERE x.at >= ? GROUP BY 1 ORDER BY 3 DESC""", (month,)).fetchall()
        today = con.execute("SELECT coalesce(sum(cost_usd), 0), count(*) FROM usage WHERE at >= ?",
                            (datetime.now(UTC).strftime("%Y-%m-%d"),)).fetchone()
    return {"month": month,
            "today_usd": round(today[0], 4), "today_calls": today[1],
            "by_user": [{"email": r[0], "calls": r[1], "cost_usd": r[2], "visitors": r[3]} for r in rows],
            "total_usd": round(sum(r[2] or 0 for r in rows), 4)}
