"""Flag loaded or judgemental words in the model's own voice (a safety net behind prompt rule 9).

Briefings must report in neutral words; loaded words may appear only inside a quotation from the speaker who
used them. Flags are shown to the reader, like unsupported text: the words stay, highlighted, so nothing is
silently rewritten. Ambiguous words with common neutral senses ("claims" for benefits, "extreme weather",
"lie ahead") are deliberately not on the list.
"""

from __future__ import annotations

import re
from typing import Any

from hansard.rag.british import outside_quotes

LOADED = re.compile(
    r"\b(lied|liar|liars|misled|misleading|dishonest|dishonesty|hypocrisy|hypocrite|hypocrites|hypocritical|"
    r"u-turn|u-turns|u-turned|backtracked|backtracking|flip-flopped|flip-flop|flip-flops|admitted|conceded|"
    r"claimed|attacked|slammed|blasted|dodged|evaded|evasive|refused to answer|gaffe|gaffes|scandal|scandals|"
    r"scandalous|disgraceful|shameful|incompetent|incompetence)\b",
    re.IGNORECASE,
)


def flag_loaded(segments: list[Any]) -> int:
    """Set ``segment.loaded`` to the [start, end] spans of loaded words outside quotations; return the count."""
    total = 0
    for i, start, text in outside_quotes(segments):
        seg = segments[i]
        for m in LOADED.finditer(text):
            seg.loaded.append({"start": start + m.start(), "end": start + m.end(), "word": m.group(0)})
            total += 1
    return total
