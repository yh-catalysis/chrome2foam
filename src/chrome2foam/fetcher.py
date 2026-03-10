"""Fetch Markdown via Cloudflare Workers API and save to disk."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import httpx

from chrome2foam.models import Article


def fetch_markdown(
    article: Article, endpoint: str, secret: str | None = None, timeout: float = 30.0
) -> str:
    """POST *article.url* to the Cloudflare Workers *endpoint* and return Markdown text."""
    headers: dict[str, str] = {}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    resp = httpx.post(
        endpoint,
        json={"url": article.url},
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        return resp.json()["markdown"]
    except (ValueError, KeyError):
        return resp.text


def build_frontmatter(article: Article) -> str:
    """Return a YAML front-matter block for *article*."""
    today = date.today().isoformat()
    return (
        "---\n"
        f'title: "{article.title}"\n'
        f'source: "{article.url}"\n'
        f"date: {today}\n"
        f'category: "{article.folder_path}"\n'
        "---\n\n"
    )


def sanitize_filename(name: str, max_bytes: int = 180) -> str:
    """Replace characters that are invalid in filenames, capped at max_bytes UTF-8 bytes.

    The default of 180 leaves adequate room for a YYYY-MM-DD- date prefix (11 bytes)
    and a .md suffix (3 bytes) within the 255-byte Linux filename limit.
    Japanese/CJK characters are 3 bytes each, so 180 bytes ~ 60 CJK chars.
    """
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = name.strip(". ")
    encoded = name.encode("utf-8")
    if len(encoded) > max_bytes:
        name = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return name or "untitled"


def save_markdown(
    article: Article,
    markdown_body: str,
    output_dir: str | Path,
) -> Path:
    """Write Markdown with front-matter to *output_dir* and return the file path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    safe_title = sanitize_filename(article.title)
    filename = f"{today}-{safe_title}.md"

    content = build_frontmatter(article) + markdown_body
    dest = output_dir / filename
    dest.write_text(content, encoding="utf-8")
    return dest
