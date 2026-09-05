"""utils.py — Pure helper functions used across MochaTools.

No Qt imports, no side effects, no dependency on the MochaTools instance.
"""

import re

# ── Release notes ────────────────────────────────────────────────────────────


def parse_release_notes_md(notes: str) -> str:
    """Extract just the "What's New" section from a GitHub release body, as
    markdown — for feeding straight into a QLabel with
    setTextFormat(Qt.TextFormat.MarkdownText), which renders bullets/bold/etc
    natively without any manual HTML conversion.

    Strips the leading <img> (the gif/screenshot always put at the top of a
    release), the "## What's New" heading itself, and everything after the
    section (additional headings, the "Full Changelog: ...compare/..."
    footer) — but leaves the remaining markdown syntax (bullets, bold,
    links) untouched so the renderer can do its job.
    """
    if not notes:
        return ""

    # Normalize line endings FIRST. GitHub's API returns release bodies
    # with \r\n line endings; with re.MULTILINE, the trailing \r before \n
    # breaks the $ anchor in the heading regex below (it doesn't match
    # whitespace), which silently fails the heading match — and that
    # failure cascades into the "cut at next heading" step truncating the
    # body down to nothing. Normalizing up front avoids all of that.
    text = notes.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Strip any <img ...> or <img ...>...</img> tag anywhere in the body.
    text = re.sub(
        r"<img\b[^>]*?/?>(?:.*?</img>)?",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Find the "What's New" heading (## What's New, ### What's New, etc,
    # tolerant of straight/curly apostrophes or a missing apostrophe).
    heading_re = re.compile(
        r"^[ \t]*#{1,6}[ \t]*what.?s\s+new[ \t]*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = heading_re.search(text)
    body = text[m.end() :] if m else text

    # Cut off at the next markdown heading, or a "Full Changelog"/compare
    # link line — whichever comes first.
    cutoffs = []
    next_heading = re.search(r"^[ \t]*#{1,6}\s+\S", body, re.MULTILINE)
    if next_heading:
        cutoffs.append(next_heading.start())
    changelog_line = re.search(
        r"^.*(Full Changelog|github\.com/.+/compare/).*$",
        body,
        re.IGNORECASE | re.MULTILINE,
    )
    if changelog_line:
        cutoffs.append(changelog_line.start())
    if cutoffs:
        body = body[: min(cutoffs)]

    return body.strip()


# ── Formatting helpers ───────────────────────────────────────────────────────

_KB = 1024


def fmt_bytes(n: int) -> str:
    """Format a byte count into a human-readable string (B, KB, MB, GB)."""
    if n < _KB:
        return f"{n} B"
    if n < _KB**2:
        return f"{n / _KB:.3f} KB"
    if n < _KB**3:
        return f"{n / _KB**2:.3f} MB"
    return f"{n / _KB**3:.3f} GB"


def fmt_speed(bps: float) -> str:
    """Format a bytes-per-second value into a human-readable speed string."""
    if bps < _KB:
        return f"{bps:.3f} B/s"
    if bps < _KB**2:
        return f"{bps / _KB:.3f} KB/s"
    return f"{bps / _KB**2:.3f} MB/s"


def fmt_eta(seconds: float) -> str:
    """Format a duration in seconds into a human-readable ETA string."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}h {m:02d}m"
    if m:
        return f"{m:d}m {s:02d}s"
    return f"{s:d}s"
