"""Filter rule configuration loaded from an INI file."""

from __future__ import annotations

import configparser
import re
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Evaluation order                                                             #
# --------------------------------------------------------------------------- #

VALID_ORDER_TOKENS = frozenset(
    {"url_include", "url_exclude", "folder_include", "folder_exclude"},
)
DEFAULT_ORDER = ["url_include", "url_exclude", "folder_include", "folder_exclude"]

# --------------------------------------------------------------------------- #
#  Default config written on first run                                         #
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG_TEXT = """\
# chrome2foam filter configuration
# ==================================
# Patterns are Python regexes matched case-insensitively.
# Copy filter.ini.example to filter.ini and customize it to your needs.
#
# Each bookmark is evaluated against four rule sections in order:
#   url_include    -> always keep  (matched against URL)
#   url_exclude    -> ignore       (matched against URL)
#   folder_include -> always keep  (matched against folder path, e.g. "Bar/Tech")
#   folder_exclude -> ignore       (matched against folder path)
#
# The first matching rule wins.  If nothing matches, the bookmark is kept.
# Override the order in [settings] evaluation_order.
#
# Syntax tips:
#   - One pattern per line; lines starting with # are comments
#   - .  = any single character,   .* = any sequence
#   - ^  = start,  $ = end
#   - Use \\. to match a literal dot  (e.g. medium\\.com)

# ============================================================
# [settings]  Optional global options
# ============================================================
[settings]
# Customise evaluation order (comma-separated, any subset is valid):
# evaluation_order = url_include, url_exclude, folder_include, folder_exclude

# ============================================================
# [url_include]  Always keep URLs matching these patterns.
# Checked before url_exclude -- use this to rescue specific URLs.
# ============================================================
[url_include]
patterns =
    # (remove the leading # to activate a pattern)
    # medium\\.com/
    # dev\\.to/
    # substack\\.com/p/
    # hashnode\\.dev/

# ============================================================
# [url_exclude]  Ignore URLs matching these patterns.
# ============================================================
[url_exclude]
patterns =
    # Chrome internal pages and extensions
    ^chrome:
    ^chrome-extension:

    # Top-level pages with no meaningful path
    ^https?://[^/]+/?$

    # Login / sign-up pages
    /login
    /signin
    /signup
    /register
    /account

    # Social media / video (uncomment to enable)
    # twitter\\.com
    # x\\.com
    # youtube\\.com
    # instagram\\.com
    # facebook\\.com

    # Shopping / misc (uncomment to enable)
    # amazon\\.com

# ============================================================
# [folder_include]  Always keep bookmarks in matching folders.
# Matched against the folder path, e.g. "Bookmarks Bar/Tech/Python".
# ============================================================
[folder_include]
patterns =
    # (remove the leading # to activate a pattern)
    # ^Bookmarks Bar/Reading

# ============================================================
# [folder_exclude]  Ignore bookmarks in matching folders.
# ============================================================
[folder_exclude]
patterns =
    # (remove the leading # to activate a pattern)
    # /Shopping
    # /Social
"""


# --------------------------------------------------------------------------- #
#  Public API                                                                  #
# --------------------------------------------------------------------------- #


def ensure_config(path: Path) -> bool:
    """Copy filter.ini.example to *path* if it does not exist yet.

    Falls back to the built-in DEFAULT_CONFIG_TEXT when the example file is
    not available (e.g. installed via ``uv tool install``).

    Returns True when a new file was created.
    """
    if path.exists():
        return False
    example = Path(__file__).parent.parent.parent / "filter.ini.example"
    if example.exists():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return True


# Rule = (field, action, compiled_patterns)
#   field  : "url" or "folder"
#   action : "include" or "exclude"
Rule = tuple[str, str, list[re.Pattern]]


def load_rules(path: Path) -> list[Rule]:
    """Parse *path* and return an ordered list of ``(field, action, patterns)``.

    Evaluation proceeds through the list; the first matching rule determines
    the outcome ("include" = keep, "exclude" = ignore).
    """
    cp = configparser.ConfigParser(
        comment_prefixes=("#", ";"),
        inline_comment_prefixes=None,  # keep '#' inside multiline values as-is
    )
    cp.read(path, encoding="utf-8")

    # Resolve evaluation order
    order_raw = cp.get("settings", "evaluation_order", fallback="").strip()
    if order_raw:
        order = [t.strip() for t in order_raw.split(",") if t.strip()]
        invalid = [t for t in order if t not in VALID_ORDER_TOKENS]
        if invalid:
            raise ValueError(
                f"Unknown evaluation_order token(s) in {path}: {invalid!r}."
                f" Valid tokens: {sorted(VALID_ORDER_TOKENS)}",
            )
    else:
        order = DEFAULT_ORDER

    # Compile all sections upfront
    section_patterns: dict[str, list[re.Pattern]] = {
        token: _compile(cp.get(token, "patterns", fallback=""), path)
        for token in VALID_ORDER_TOKENS
    }

    # Build ordered rule list, skipping empty sections
    rules: list[Rule] = []
    for token in order:
        field, action = token.split("_", 1)  # "url_include" -> ("url", "include")
        patterns = section_patterns[token]
        if patterns:
            rules.append((field, action, patterns))
    return rules


def should_keep(url: str, folder_path: str, rules: list[Rule]) -> tuple[bool, str]:
    """Return ``(keep, reason)`` for the bookmark.

    *keep* is True if the bookmark should remain PENDING.
    *reason* is a human-readable string identifying the matching rule,
    or ``"(no match: default keep)"`` when nothing matched.

    Rules are evaluated in order; the first match determines the outcome.
    """
    for field, action, patterns in rules:
        section = f"{'url' if field == 'url' else 'folder'}_{action}"
        target = url if field == "url" else folder_path
        for p in patterns:
            if p.search(target):
                return action == "include", f"{section}[{p.pattern}]"
    return True, "(no match: default keep)"


# --------------------------------------------------------------------------- #
#  Internal helpers                                                            #
# --------------------------------------------------------------------------- #


def _compile(raw: str, source: Path) -> list[re.Pattern]:
    patterns: list[re.Pattern] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            patterns.append(re.compile(stripped, re.IGNORECASE))
        except re.error as exc:
            raise ValueError(
                f"{source}:{lineno}: invalid regex {stripped!r}: {exc}",
            ) from exc
    return patterns
