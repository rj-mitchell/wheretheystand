"""Readable text in each page's HTML, for anything that reads pages without running their scripts: AI assistants,
search engines, readers with scripts off. It goes where the app later draws the page, so people see no difference.
Plain HTML only, every value escaped."""

from __future__ import annotations

import re
from html import escape as e
from typing import Any

from hansard.api.briefings import fmt_date

_ROOT = re.compile(r'(<div id="[\w-]+-root">)\s*<p class="muted">Loading…</p>\s*(</div>)')
_EMPTY_ROOT = '<div id="root"></div>'
MAX_ITEMS = 60


def with_text(page: str, fragment: str | None) -> str:
    """The page with ``fragment`` placed in its app root (replaced by the app when it starts)."""
    if not fragment:
        return page
    if _EMPTY_ROOT in page:
        return page.replace(_EMPTY_ROOT, f'<div id="root"><main class="thread prose">{fragment}</main></div>', 1)
    return _ROOT.sub(lambda m: m.group(1) + fragment + m.group(2), page, count=1)


def _link(href: str | None, text: str) -> str:
    return f'<a href="{e(href)}">{e(text)}</a>' if href else e(text)


def _inline(md: str) -> str:
    """Escaped text with **bold** kept."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", e(md))


def markdown_text(md: str) -> str:
    """Enough of a briefing's markdown for reading: headings, bold labels, bullets and paragraphs."""
    out, items = [], []
    for block in re.split(r"\n\s*\n", md.strip()):
        for line in block.splitlines():
            line = line.rstrip()
            if re.match(r"^\s*[-*•]\s+", line):
                items.append(f"<li>{_inline(re.sub(r'^\s*[-*•]\s+', '', line))}</li>")
                continue
            if items:
                out.append("<ul>" + "".join(items) + "</ul>")
                items = []
            if m := re.match(r"^(#{1,6})\s+(.*)", line):
                out.append(f"<h3>{_inline(m.group(2))}</h3>")
            elif line.strip():
                out.append(f"<p>{_inline(line.strip())}</p>")
    if items:
        out.append("<ul>" + "".join(items) + "</ul>")
    return "".join(out)


def briefing_text(view: dict[str, Any], heading: str | None = None) -> str:
    answer = view.get("answer") or {}
    parts = [f"<h1>{e(heading or view['question'])}</h1>"]
    if heading:
        parts.append(f"<p><em>{e(view['question'])}</em></p>")
    parts.append(f"<p>A briefing written by AI (Claude) from the Hansard records cited below, prepared "
                 f"{e(fmt_date(view['created_at'][:10]))}"
                 + (f" from records up to {e(fmt_date(view['records_up_to']))}" if view.get("records_up_to") else "")
                 + ". Check the sources before relying on it.</p>")
    if answer.get("markdown"):
        parts.append(markdown_text(answer["markdown"]))
    sources = view.get("sources") or []
    if sources:
        parts.append("<h2>Sources</h2><ol>")
        for s in sources[:MAX_ITEMS]:
            href = s.get("official_url") or s.get("twfy_url") or s.get("url")
            parts.append(f'<li value="{int(s.get("n") or 0)}">{_link(href, s.get("title") or "Source")}</li>')
        parts.append("</ol>")
    return "".join(parts)


def person_text(core: dict[str, Any], activity: dict[str, Any] | None) -> str:
    parts = [f"<h1>{e(core['name'])}</h1>", f"<p>{e(core.get('status') or '')}.</p>"]
    if core.get("memberships"):
        parts.append("<h2>Membership</h2><ul>")
        for m in reversed(core["memberships"]):
            what = f"MP for {m.get('constituency') or '—'}" if m["house"] == "commons" else "House of Lords"
            parts.append(f"<li>{e(what)}{', ' + e(m['party']) if m.get('party') else ''} "
                         f"({e(fmt_date(m['start']))} – {e(fmt_date(m['end'])) if m.get('end') else 'now'})</li>")
        parts.append("</ul>")
    if core.get("offices"):
        parts.append("<h2>Roles</h2><ul>")
        for o in core["offices"][:30]:
            parts.append(f"<li>{e(o['title'])} ({e(fmt_date(o['start']))} – "
                         f"{e(fmt_date(o['end'])) if o.get('end') else 'now'})</li>")
        parts.append("</ul>")
    for s in core.get("seats") or []:
        results = "; ".join(f"{r['election']}: majority {r['majority']:,}" + (f" ({r['pct']}%)" if r.get("pct") else "")
                            for r in s.get("results") or [] if r.get("majority") is not None)
        if results:
            parts.append(f"<p>{e(s['constituency'])}: {e(results)}.</p>")
    contributions = (activity or {}).get("contributions") or []
    if contributions:
        parts.append("<h2>Recent contributions</h2><ol>")
        for c in contributions:
            parts.append(f"<li><p>{e(fmt_date(c['date']))}, {e(c['chamber'])}"
                         + (f", {e(c['heading'])}" if c.get("heading") else "") + "</p>"
                         f"<p>{e(c['excerpt'])}</p><p>{_link(c.get('debate_path') or c.get('twfy_url'), 'Read in full')}</p></li>")
        parts.append("</ol>")
    voting = (activity or {}).get("voting")
    if voting and voting.get("votes"):
        parts.append(f"<h2>Voting record</h2><p>{voting['votes']:,} votes recorded, of which {voting['rebellions']:,} "
                     "were against the majority of their own party.</p>")
    return "".join(parts)


def bill_text(b: dict[str, Any]) -> str:
    parts = [f"<h1>{e(b['name'])}</h1>",
             f"<p>{b.get('contributions', 0):,} contributions in Parliament, {e(fmt_date(b['first']))} to "
             f"{e(fmt_date(b['last']))}.</p><h2>Sittings</h2><ul>"]
    for s in (b.get("sittings") or [])[:MAX_ITEMS]:
        stages = ", ".join(s.get("stages") or [])
        parts.append(f"<li>{e(fmt_date(s['date']))}, {'House of Lords' if s.get('house') == 'lords' else 'House of Commons'}"
                     f"{': ' + e(stages) if stages else ''} — {_link(s.get('path') or s.get('url'), 'read the debate')}</li>")
    parts.append("</ul>")
    return "".join(parts)


def debate_text(d: dict[str, Any]) -> str:
    parts = [f"<h1>{e(d['title'])}</h1>", f"<p>{e(d['chamber_name'])}, {e(fmt_date(d['date']))}"
             + (f", {e(d['department'])}" if d.get("department") else "") + ".</p>"]
    shown = 0
    for it in d.get("items") or []:
        if it.get("kind") == "heading":
            parts.append(f"<h2>{e(' '.join(it.get('paragraphs') or []) or it.get('text') or '')}</h2>")
            continue
        if it.get("kind") != "said":
            continue
        sp = it.get("speaker") or {}
        who = sp.get("name") or it.get("speaker_name_raw") or "A member"
        detail = ", ".join(x for x in (sp.get("party"), sp.get("constituency")) if x)
        body = "".join(f"<p>{e(p)}</p>" for p in it.get("paragraphs") or [])
        parts.append(f"<h3>{e(who)}{' (' + e(detail) + ')' if detail else ''}</h3>{body}")
        shown += 1
        if shown >= 200:
            parts.append(f"<p>{_link(d.get('official_url'), 'The rest of the debate is in Hansard')}.</p>")
            break
    return "".join(parts)


def week_text(w: dict[str, Any]) -> str:
    parts = [f"<h1>In Parliament, week of {e(fmt_date(w['start']))}</h1>"]
    for day in w.get("days") or []:
        parts.append(f"<h2>{e(fmt_date(day['date']))}</h2>")
        for ch in day.get("chambers") or []:
            parts.append(f"<h3>{e(ch['name'])}</h3><ul>")
            for deb in (ch.get("debates") or [])[:MAX_ITEMS]:
                kind = f" ({deb['kind']})" if deb.get("kind") else ""
                parts.append(f"<li>{_link(deb.get('path'), deb['title'])}{e(kind)}</li>")
            parts.append("</ul>")
    return "".join(parts)


def edm_text(m: dict[str, Any]) -> str:
    sponsor = m.get("sponsor") or {}
    parts = [f"<h1>{e(m['title'])}</h1>",
             f"<p>Early day motion {e(m.get('uin') or '')}, tabled {e(fmt_date(m['date_tabled']))}"
             + (f" by {e(sponsor['name'])}" + (f" ({e(sponsor['party'])})" if sponsor.get("party") else "")
                if sponsor.get("name") else "")
             + (f". Signed by {m['sponsors_count']} member{'' if m['sponsors_count'] == 1 else 's'}" if m.get("sponsors_count") else "") + ".</p>"]
    if m.get("text"):
        parts.append(f"<blockquote><p>{e(m['text'])}</p></blockquote>")
    if m.get("party_counts"):
        parts.append("<p>Signatures by party: " + e(", ".join(f"{p} {n}" for p, n in m["party_counts"])) + ".</p>")
    return "".join(parts)


def committee_text(d: dict[str, Any]) -> str:
    span = f"since {fmt_date(d['start'])}" if d.get("start") and not d.get("end") else \
        (f"{fmt_date(d['start'])} to {fmt_date(d['end'])}" if d.get("start") else "")
    parts = [f"<h1>{e(d['name'])}</h1>",
             f"<p>{e(' '.join(x for x in (d.get('house'), (d.get('category') or '').lower() + ' committee') if x))}"
             f"{', ' + e(span) if span else ''}. {_link(d.get('url'), 'Its page on the Parliament website')}.</p>"]
    if d.get("purpose"):
        parts.append(f"<p>{e(d['purpose'])}</p>")
    if d.get("inquiries"):
        parts.append("<h2>Inquiries</h2><ul>")
        for q in d["inquiries"][:MAX_ITEMS]:
            when = " to ".join(fmt_date(x) for x in (q.get("open"), q.get("close")) if x)
            parts.append(f"<li>{_link(f'/inquiries/{q['id']}', q['title'])}{' (' + e(when) + ')' if when else ''}</li>")
        parts.append("</ul>")
    if d.get("top_organisations"):
        parts.append("<h2>Organisations it has heard from most</h2><ul>" + "".join(
            f"<li>{e(o['organisation'])} ({o.get('n') or o.get('sessions') or 0})</li>"
            for o in d["top_organisations"][:20]) + "</ul>")
    return "".join(parts)


def inquiry_text(d: dict[str, Any]) -> str:
    by = " and ".join(c["name"] for c in d.get("committees") or [])
    when = " to ".join(fmt_date(x) for x in (d.get("open"), d.get("close")) if x)
    parts = [f"<h1>{e(d['title'])}</h1>",
             f"<p>{e(d.get('type') or 'Inquiry')}{' by the ' + e(by) if by else ''}{', ' + e(when) if when else ''}. "
             f"{_link(d.get('url'), 'Its page on the Parliament website')}.</p>"]
    if d.get("publications"):
        parts.append("<h2>Reports and responses</h2><ul>" + "".join(
            f"<li>{_link(p.get('url'), p.get('title') or 'Publication')}"
            f"{' (' + e(fmt_date(p['date'])) + ')' if p.get('date') else ''}</li>"
            for p in d["publications"][:MAX_ITEMS]) + "</ul>")
    if d.get("sessions"):
        parts.append("<h2>Evidence sessions</h2><ul>")
        for s in d["sessions"][:MAX_ITEMS]:
            who = "; ".join(", ".join(x for x in (w.get("name"), w.get("organisation")) if x)
                            for w in s.get("witnesses") or [])
            parts.append(f"<li>{e(fmt_date(s['date'])) if s.get('date') else ''}: {e(who) or 'witnesses not listed'}</li>")
        parts.append("</ul>")
    return "".join(parts)


def group_text(g: dict[str, Any]) -> str:
    parts = [f"<h1>{e(g['title'])}</h1>"]
    if g.get("purpose"):
        parts.append(f"<p>{e(g['purpose'])}</p>")
    facts = [x for x in (g.get("category"), g.get("categories")) if x]
    if facts:
        parts.append(f"<p>{e('; '.join(facts))}.</p>")
    if g.get("secretariat"):
        parts.append(f"<p>Secretariat: {e(g['secretariat'])}</p>")
    if g.get("members"):
        parts.append("<h2>Members</h2><ul>" + "".join(
            f"<li>{e(m.get('name') or '')}{' (' + e(m['role']) + ')' if m.get('role') else ''}</li>"
            for m in g["members"][:100]) + "</ul>")
    if g.get("benefits"):
        parts.append("<h2>Funding and benefits registered</h2><ul>" + "".join(
            f"<li>{e(b.get('funder') or '')}: {e(b.get('type') or '')}"
            f"{', £' + e(b['value']) if b.get('value') else ''}"
            f"{' (received ' + e(fmt_date(b['received'])) + ')' if b.get('received') else ''}</li>"
            for b in g["benefits"][:MAX_ITEMS]) + "</ul>")
    if g.get("source_url"):
        parts.append(f"<p>{_link(g['source_url'], 'The official register entry')}.</p>")
    return "".join(parts)


def devolved_day_text(d: dict[str, Any]) -> str:
    parts = [f"<h1>{e(d['body'])}, {e(fmt_date(d['date']))}</h1>",
             f"<p>{d.get('contributions', 0):,} contributions from {d.get('speakers', 0):,} members. "
             f"{_link(d.get('official_url'), 'The official report')}.</p>"]
    shown = 0
    for it in d.get("items") or []:
        if it.get("kind") == "heading":
            parts.append(f"<h2>{e(it.get('text') or '')}</h2>")
        elif it.get("kind") == "said":
            sp = it.get("speaker") or {}
            detail = ", ".join(x for x in (sp.get("party"), sp.get("seat")) if x)
            parts.append(f"<h3>{e(sp.get('name') or 'A member')}{' (' + e(detail) + ')' if detail else ''}</h3>"
                         + "".join(f"<p>{e(p)}</p>" for p in it.get("paragraphs") or []))
            shown += 1
            if shown >= 200:
                break
    return "".join(parts)


def evidence_text(t: dict[str, Any]) -> str:
    parts = [f"<h1>{e(t.get('title') or 'Oral evidence')}</h1>"]
    if t.get("date"):
        parts.append(f"<p>Oral evidence, {e(fmt_date(t['date']))}.</p>")
    for turn in (t.get("turns") or [])[:300]:
        parts.append(f"<h3>{e(turn.get('speaker') or '')}</h3><p>{e(turn.get('text') or '')}</p>")
    return "".join(parts)
