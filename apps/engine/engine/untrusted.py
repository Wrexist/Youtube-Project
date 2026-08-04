"""Fencing for text that came from outside.

Scraped web pages reach the model as the whole basis of a script, and from there the
title, the description and a video published under the operator's name. A page that
ranks for the topic can therefore write instructions and, with nothing between it and
the prompt, be obeyed. That was the state of `ResearchStage`: the digest was
interpolated raw.

Nothing here is a guarantee — no string transformation makes a model immune to
persuasion. What it does is remove the *cheap* version of the attack: closing the
fence, impersonating the system role, and pretending the conversation moved on.
The instruction that accompanies the fence at the call site does the rest.
"""

from __future__ import annotations

import re

#: Sequences that let untrusted text pose as part of the prompt's own structure.
#: Neutralised rather than deleted so the summary of a page *about* prompt injection
#: still says what the page said.
_IMPERSONATION = re.compile(
    r"</?(?:source_material|system|assistant|user|human|instructions?)\s*>"
    r"|(?:^|\n)\s*(?:system|assistant|human|user)\s*:",
    re.IGNORECASE,
)

#: A zero-width or bidirectional control character is invisible in every review tool
#: and can reorder how a line reads. There is no legitimate use in scraped prose.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁦-⁩﻿]")


def fence(text: str, *, limit: int = 60_000) -> str:
    """Make `text` safe to interpolate inside a delimited block.

    Three things, in order: strip invisible control characters, defuse anything
    shaped like a role marker or a closing tag, and cap the length. The cap matters
    independently of the injection: eight sources at 6,000 characters is the normal
    case, and a single source that somehow arrives far larger would otherwise decide
    the whole prompt budget on its own.
    """
    cleaned = _INVISIBLE.sub("", text)
    cleaned = _IMPERSONATION.sub(lambda m: m.group(0).replace("<", "‹").replace(":", "∶"), cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "\n[truncated]"
    return cleaned
