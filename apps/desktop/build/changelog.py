#!/usr/bin/env python3
"""changelog.py — generate release notes and VERSION-file YAML from git log.

Parses conventional-commit subjects and bodies between the last release tag
and HEAD, then emits either:

  --format markdown   grouped release-notes body (for the GitHub release)
  --format yaml       VERSION-file content (version + changes with descriptions)

Only commits that change the desktop app (paths under apps/desktop/) are
included; cosmetic commits such as style-only reformatting are skipped.

Usage:
    python changelog.py --version 7.1.0 --format markdown
    python changelog.py --version 7.1.0 --format yaml
    python changelog.py --from 17cab03 --version 7.1.0 --format yaml
"""

from __future__ import annotations

import argparse
import random
import re
import subprocess
import sys
import urllib.request

import yaml

# Gist holding the cat gif URLs appended to the release notes, one per line.
# Add more URLs to the gist to grow the pool.
CAT_GIF_GIST_URL = (
    "https://gist.githubusercontent.com/K0oRui/a93baabc1f6b5c6b29d96426baee862e/raw"
)

# Section name -> conventional-commit prefix. "Other" is the catch-all.
SECTIONS: list[tuple[str, str | None]] = [
    ("Features", "feat"),
    ("Bug Fixes", "fix"),
    ("Performance", "perf"),
    ("Refactors", "refactor"),
    ("Documentation", "docs"),
    ("Other", None),
]

# Only commits touching the desktop app are included in release notes.
APP_PREFIX = "apps/desktop/"

# Commit types that are cosmetic and never user-facing.
SKIP_TYPES = {"style"}

_COMMIT_RE = re.compile(r"^([a-z]+)(?:\([^)]*\))?(!)?:")
_MIN_PARTS = 4


def run_git(args: list[str]) -> str:
    """Run a git command and return its stdout."""
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def last_bump() -> str | None:
    """Return the most recent version-bump commit reachable from HEAD, or None."""
    try:
        sha = run_git(
            ["log", "--format=%H", "--grep=^chore: bump version to", "-1"]
        ).strip()
    except subprocess.CalledProcessError:
        return None
    return sha or None


def commit_files(sha: str) -> list[str]:
    """Return the paths changed by a commit."""
    out = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", sha])
    return [line for line in out.splitlines() if line.strip()]


def is_desktop_change(commit: dict[str, str]) -> bool:
    """True if the commit changes the desktop app and isn't cosmetic."""
    if commit_type(commit["subject"]) in SKIP_TYPES:
        return False
    return any(path.startswith(APP_PREFIX) for path in commit["files"])


def parse_commits(from_tag: str | None) -> list[dict[str, str]]:
    """Parse commit sha, subject, and body from git log.

    Only commits that change the desktop app are returned; cosmetic commits
    (style-only reformatting, etc.) are skipped.
    """
    fmt = "--pretty=format:%x1f%H%x1f%s%x1f%b%x1e"
    if from_tag:
        log = run_git(["log", "--no-merges", f"{from_tag}..HEAD", fmt])
    else:
        log = run_git(["log", "--no-merges", fmt])
    commits: list[dict[str, str]] = []
    for raw_entry in log.split("\x1e"):
        entry = raw_entry.strip("\r\n")
        if not entry:
            continue
        parts = entry.split("\x1f")
        if len(parts) < _MIN_PARTS:
            continue
        commit = {
            "sha": parts[1],
            "subject": parts[2].strip(),
            "body": parts[3].strip(),
            "files": commit_files(parts[1]),
        }
        if is_desktop_change(commit):
            commits.append(commit)
    return commits


def commit_type(subject: str) -> str | None:
    """Return the conventional-commit type prefix, or None."""
    match = _COMMIT_RE.match(subject)
    return match.group(1) if match else None


def group_commits(commits: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Bucket commits into the SECTIONS by conventional-commit type."""
    groups: dict[str, list[dict[str, str]]] = {name: [] for name, _ in SECTIONS}
    for commit in commits:
        ctype = commit_type(commit["subject"])
        placed = False
        for name, prefix in SECTIONS:
            if prefix is not None and ctype == prefix:
                groups[name].append(commit)
                placed = True
                break
        if not placed:
            groups["Other"].append(commit)
    return groups


def fetch_cat_gif() -> str | None:
    """Fetch the cat gif list from the gist and pick one at random."""
    try:
        with urllib.request.urlopen(CAT_GIF_GIST_URL, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        urls = [line.strip() for line in text.splitlines() if line.strip()]
        return random.choice(urls) if urls else None
    except (OSError, UnicodeDecodeError):
        return None


def render_markdown(
    groups: dict[str, list[dict[str, str]]],
    cat_gif: str | None,
) -> str:
    """Render grouped release-notes markdown with a random cat gif on top."""
    lines = []
    if cat_gif:
        lines.append(f"![cat]({cat_gif})")
        lines.append("")
    lines.append("## What's Changed")
    lines.append("")
    for name, _ in SECTIONS:
        items = groups[name]
        if not items:
            continue
        lines.append(f"### {name}")
        lines.extend(f"- {c['subject']}" for c in items)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_yaml(version: str, commits: list[dict[str, str]]) -> str:
    """Render VERSION-file YAML."""
    changes = [{"subject": c["subject"], "description": c["body"]} for c in commits]
    return yaml.safe_dump(
        {"version": version, "changes": changes},
        sort_keys=False,
        allow_unicode=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from",
        dest="from_ref",
        default=None,
        help="Tag or commit to diff from (default: most recent version-bump commit)",
    )
    parser.add_argument(
        "--from-tag",
        dest="from_ref",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--version", required=True, help="New version, e.g. 7.1.0")
    parser.add_argument(
        "--format",
        choices=["markdown", "yaml"],
        required=True,
        help="Output format",
    )
    args = parser.parse_args()

    from_ref = args.from_ref or last_bump()
    commits = parse_commits(from_ref)

    if args.format == "markdown":
        groups = group_commits(commits)
        sys.stdout.write(render_markdown(groups, fetch_cat_gif()))
    else:
        sys.stdout.write(render_yaml(args.version, commits))


if __name__ == "__main__":
    main()
