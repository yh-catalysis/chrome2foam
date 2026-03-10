"""Parse Chrome Bookmarks JSON and yield (url, title, folder_path) tuples."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path


def parse_bookmarks(path: str | Path) -> Generator[dict[str, str], None, None]:
    """Read a Chrome *Bookmarks* JSON file and yield bookmark dicts.

    Each yielded dict has keys: ``url``, ``title``, ``folder_path``.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    roots = data.get("roots", {})
    for _root_name, root_node in roots.items():
        if not isinstance(root_node, dict):
            continue
        yield from _walk(root_node, parts=[])


def _walk(
    node: dict,
    parts: list[str],
) -> Generator[dict[str, str], None, None]:
    """Recursively walk a bookmark node tree."""
    node_type = node.get("type")

    if node_type == "url":
        yield {
            "url": node["url"],
            "title": node.get("name", ""),
            "folder_path": "/".join(parts),
        }
    elif node_type == "folder":
        folder_name = node.get("name", "")
        children = node.get("children", [])
        for child in children:
            yield from _walk(child, parts + [folder_name])
