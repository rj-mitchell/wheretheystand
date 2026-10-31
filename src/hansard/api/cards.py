"""Link-preview cards (1200 x 630 PNG) for shared briefings, in the site's own typefaces and colours."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from hansard.config import STATIC_DIR

W, H = 1200, 630
PAPER = (247, 243, 234)
INK = (35, 31, 26)
INK_SOFT = (77, 70, 61)
INK_FAINT = (109, 102, 92)
COMMONS = (29, 69, 53)
FONTS = STATIC_DIR / "fonts"
LEFT, RIGHT = 96, 1120


@lru_cache(maxsize=32)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _spaced(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill, spacing: float) -> int:
    """Draw letter-spaced text (Pillow has no tracking); returns the x after the last letter."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return int(x)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit_question(draw: ImageDraw.ImageDraw, question: str, width: int, max_lines: int) -> tuple[Any, list[str]]:
    for size in (64, 58, 52, 47, 43, 40):
        font = _font("SourceSerif4-Semibold.ttf", size)
        lines = _wrap(draw, question, font, width)
        if len(lines) <= max_lines:
            return font, lines
    lines = _wrap(draw, question, font, width)[:max_lines]
    while lines and draw.textlength(lines[-1] + "…", font=font) > width:
        lines[-1] = lines[-1].rsplit(" ", 1)[0]
    lines[-1] += "…"
    return font, lines


def evidence_line(evidence: dict[str, Any] | None) -> str:
    if not evidence or not evidence.get("records"):
        return ""
    parts = [f"{evidence['records']} contributions from {evidence['speakers']} speakers"]
    parties = sorted((evidence.get("parties") or {}).items(), key=lambda kv: -kv[1])
    if parties:
        parts.append("Parties: " + ", ".join(f"{p} {n}" for p, n in parties[:4]))
    return " · ".join(parts)


def render_card(question: str, prepared: str, records_up_to: str, evidence: dict[str, Any] | None) -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 12, H), fill=COMMONS)  # the Commons-green spine

    icon = Image.open(STATIC_DIR / "brand" / "icon-192.png").convert("RGBA").resize((76, 76), Image.LANCZOS)
    img.paste(icon, (LEFT, 54), icon)
    _spaced(d, (LEFT + 100, 58), "WHERE MPs STAND", _font("SourceSerif4-Semibold.ttf", 30), INK, 4.2)
    d.text((LEFT + 100, 100), "Sourced briefings on the record of Parliament", font=_font("SourceSerif4-It.ttf", 23),
           fill=INK_SOFT)
    d.rectangle((LEFT, 160, RIGHT, 163), fill=(46, 42, 36))  # thick-and-thin rule, as on the printed report
    d.rectangle((LEFT, 168, RIGHT, 169), fill=(46, 42, 36))

    _spaced(d, (LEFT, 196), "BRIEFING · WRITTEN BY AI FROM HANSARD", _font("SourceSans3-Semibold.ttf", 19), INK_FAINT,
            2.4)
    font, lines = _fit_question(d, question, RIGHT - LEFT, 4)
    y = 236
    line_h = int(font.size * 1.18)
    for line in lines:
        d.text((LEFT, y), line, font=font, fill=INK)
        y += line_h

    ev = evidence_line(evidence)
    small = _font("SourceSans3-Regular.ttf", 23)
    if ev:
        while d.textlength(ev, font=small) > RIGHT - LEFT and " " in ev:
            ev = ev.rsplit(" ", 1)[0].rstrip(",") + "…"
        d.text((LEFT, 506), ev, font=small, fill=INK_SOFT)
    d.rectangle((LEFT, 548, RIGHT, 549), fill=(200, 193, 180))
    stamp = f"Prepared {prepared}" + (f" from records up to {records_up_to}" if records_up_to else "")
    d.text((LEFT, 566), stamp, font=small, fill=INK_FAINT)
    url_font = _font("SourceSans3-Semibold.ttf", 23)
    d.text((RIGHT - d.textlength("wherempsstand.com", font=url_font), 566), "wherempsstand.com", font=url_font,
           fill=COMMONS)
    return img


def render_brand(icon: Path) -> Image.Image:
    """The site's own preview image (web/public/og-image.png), used when any page or briefing is shared:
    `python -c "from hansard.api.cards import write_brand; write_brand()"` rebuilds it."""
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    mark = Image.open(icon).convert("RGBA").resize((200, 200), Image.LANCZOS)
    img.paste(mark, (80, 215), mark)
    left = 336
    _spaced(d, (left, 192), "WHERE MPs STAND", _font("SourceSerif4-Semibold.ttf", 64), INK, 8.0)
    d.text((left, 286), "Sourced briefings on the record of Parliament", font=_font("SourceSerif4-It.ttf", 33),
           fill=INK_SOFT)
    d.rectangle((left, 355, RIGHT, 358), fill=(46, 42, 36))  # thick-and-thin rule, as on the printed report
    d.rectangle((left, 366, RIGHT, 367), fill=(46, 42, 36))
    _spaced(d, (left, 392), "INDEPENDENT · SOURCED TO HANSARD", _font("SourceSans3-Regular.ttf", 23), INK_FAINT, 2.6)
    url_font = _font("SourceSans3-Semibold.ttf", 23)
    d.text((RIGHT - d.textlength("wherempsstand.com", font=url_font), 570), "wherempsstand.com", font=url_font,
           fill=COMMONS)
    return img


def write_brand(out: Path | None = None) -> Path:
    root = Path(__file__).resolve().parents[3]
    out = out or root / "web" / "public" / "og-image.png"
    render_brand(root / "web" / "public" / "icon-512.png").save(out, "PNG", optimize=True)
    return out


def card_png(cache_dir: Path, bid: str, question: str, prepared: str, records_up_to: str,
             evidence: dict[str, Any] | None) -> Path:
    """Render once, then serve the saved file."""
    path = cache_dir / f"{bid}.png"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        render_card(question, prepared, records_up_to, evidence).save(tmp, "PNG", optimize=True)
        tmp.replace(path)
    return path
