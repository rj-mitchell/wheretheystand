"""RSS feeds, so readers can follow a member, the topic briefings, bills, changes and corrections in a feed reader."""

from __future__ import annotations

import html
from datetime import UTC, date, datetime, time
from email.utils import format_datetime
from typing import Any

FEED_TYPE = "application/rss+xml; charset=utf-8"


def _when(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        dt = datetime.combine(d, time(12), tzinfo=UTC)
    return format_datetime(dt)


def rss(*, title: str, link: str, description: str, self_url: str, items: list[dict[str, Any]]) -> str:
    """An RSS 2.0 document. Each item has title, link, description and date (and optionally guid)."""
    e = html.escape
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
           "<channel>",
           f"<title>{e(title)}</title>", f"<link>{e(link)}</link>", f"<description>{e(description)}</description>",
           "<language>en-gb</language>",
           f'<atom:link href="{e(self_url)}" rel="self" type="application/rss+xml" />']
    if items:
        out.append(f"<lastBuildDate>{_when(max(i['date'] for i in items))}</lastBuildDate>")
    for i in items:
        out += ["<item>", f"<title>{e(i['title'])}</title>", f"<link>{e(i['link'])}</link>",
                f"<description>{e(i.get('description') or '')}</description>",
                f"<pubDate>{_when(i['date'])}</pubDate>",
                f'<guid isPermaLink="{"false" if i.get("guid") else "true"}">{e(i.get("guid") or i["link"])}</guid>',
                "</item>"]
    out += ["</channel>", "</rss>"]
    return "\n".join(out)
