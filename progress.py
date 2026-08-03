"""
progress.py — Discord progress-message tracker with visual progress bar.
"""

import time

# Steps and their approximate % completion (Artlist video flow)
_STEPS = [
    ("🔵 Analyzing request…",          3),
    ("🟣 Authenticating…",             8),
    ("🔵 Analyzing session…",         15),
    ("🟣 Session ready",              20),
    ("🔵 Analyzing generator…",       28),
    ("🟣 Analyzing prompt…",          38),
    ("🔵 Analyzing reference…",       46),
    ("🟣 Analyzing model…",           54),
    ("🔵 Analyzing generation…",      62),
    ("🟣 Analyzing output…",          72),
    ("🔵 Analyzing result…",          83),
    ("🟣 Analyzing final…",           91),
    ("🔵 Analyzing download…",        95),
]

_STEP_KEYWORDS = {
    # Login steps — must come before broader keywords
    "initializ":       3,
    "signing in":      8,
    "waiting for sign": 15,
    "signed in":       20,
    "opening ai":      28,   # "Opening AI Video Generator"
    "opening video":   28,
    "prompt":          38,
    "uploading ref":   46,
    "selecting":       54,
    "starting gen":    62,
    "video is gen":    72,   # "Video is generating"
    "still gen":       83,
    "almost":          91,
    "download":        95,
    "uploading":       97,   # catbox upload after generation
}

# Remap verbose internal step messages to cleaner "Analyzing" style
_MSG_REMAP = {
    "initializ":          "🔵 Analyzing request…",
    "signing in":         "🟣 Authenticating…",
    "waiting for sign":   "🔵 Analyzing session…",
    "signed in":          "🟣 Session ready ✓",
    "opening ai":         "🔵 Analyzing generator…",
    "opening video":      "🔵 Analyzing generator…",
    "opening image":      "🔵 Analyzing generator…",
    "entering prompt":    "🟣 Analyzing prompt…",
    "uploading ref":      "🔵 Analyzing reference image…",
    "choosing":           "🟣 Analyzing model…",
    "selecting model":    "🟣 Analyzing model…",
    "starting gen":       "🔵 Analyzing generation…",
    "generating":         "🟣 Analyzing output…",
    "still gen":          "🔵 Analyzing result…",
    "almost":             "🟣 Analyzing final output…",
    "download":           "🔵 Analyzing download…",
    "uploading to":       "🟣 Analyzing upload…",
}


def _remap_msg(message: str) -> str:
    """Remap verbose step messages to cleaner Analyzing-style output."""
    low = message.lower()
    for kw, replacement in _MSG_REMAP.items():
        if kw in low:
            return replacement
    return message


def _bar(pct: int, width: int = 12) -> str:
    filled = round(width * pct / 100)
    if filled == 0:
        return "⬛" * width
    # Active block is purple, completed blocks are blue, empty are black
    bar = "🔵" * (filled - 1) + "🟣" + "⬛" * (width - filled)
    return bar


class ProgressTracker:
    def __init__(self, title: str, prompt: str, update_cb, emoji: str = "⏳"):
        self.title     = title
        self.prompt    = prompt
        self.update_cb = update_cb
        self.emoji     = emoji
        self._start    = time.monotonic()
        self._pct      = 3

    def _elapsed(self) -> str:
        secs = int(time.monotonic() - self._start)
        m, s = divmod(secs, 60)
        return f"{m}m{s:02d}s" if m else f"{s}s"

    def _guess_pct(self, message: str) -> int:
        import re as _re
        low = message.lower()

        # Pull the real generation % out of the message first.
        m = _re.search(r'(\d{1,3})\s*%', message)
        if m:
            raw = int(m.group(1))
            if self._pct >= 62:
                remapped = 62 + round(raw * 33 / 100)
                return max(self._pct, min(remapped, 95))
            return max(self._pct, raw)

        for kw, pct in _STEP_KEYWORDS.items():
            if kw in low:
                return pct
        return self._pct  # don't go backwards

    async def step(self, message: str) -> None:
        pct = max(self._pct, self._guess_pct(message))
        self._pct = pct
        bar  = _bar(pct)
        elapsed = self._elapsed()
        prompt_line = f"  •  🖊️ *{self.prompt[:100]}*" if self.prompt else ""

        # Use remapped message for clean display
        display_msg = _remap_msg(message)

        # If the message already has a % in it (generation progress), keep it
        import re as _re
        _pct_m = _re.search(r'\d{1,3}\s*%', message)
        if _pct_m:
            display_msg = f"🟣 Analyzing… {_pct_m.group()}"

        content = (
            f"{self.title}\n"
            f"{bar} **{pct}%** — {display_msg}\n"
            f"⏱️ `{elapsed}` elapsed{prompt_line}"
        )
        try:
            await self.update_cb(content)
        except Exception:
            pass

    async def done(self, message: str) -> None:
        bar = _bar(100)
        elapsed = self._elapsed()
        prompt_line = f"  •  🖊️ *{self.prompt[:100]}*" if self.prompt else ""
        content = (
            f"{self.title}\n"
            f"{bar} **100%** — ✅ {message}\n"
            f"⏱️ `{elapsed}` elapsed{prompt_line}"
        )
        try:
            await self.update_cb(content)
        except Exception:
            pass
