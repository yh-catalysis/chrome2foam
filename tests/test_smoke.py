"""Run the CLI from sync to fetch, so that CI notices when a dependency update breaks it."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chrome2foam.cli import app

BOOKMARKS = {
    "roots": {
        "bookmark_bar": {
            "type": "folder",
            "name": "Bar",
            "children": [
                {"type": "url", "name": "Article", "url": "https://example.com/article"},
                # The default filter rules ignore top-level pages.
                {"type": "url", "name": "Top", "url": "https://example.com/"},
            ],
        },
    },
}


class _FakeWorker(BaseHTTPRequestHandler):
    """Stand-in for the Cloudflare Workers endpoint."""

    received: list[tuple[str | None, dict]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        self.received.append((self.headers["Authorization"], json.loads(self.rfile.read(length))))
        body = json.dumps({"markdown": "# Hello"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the test output clean."""


@pytest.fixture
def worker_url() -> Iterator[str]:
    _FakeWorker.received.clear()
    server = HTTPServer(("127.0.0.1", 0), _FakeWorker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/api/fetch"
    server.shutdown()
    server.server_close()
    thread.join()


def _run(*args: str) -> str:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_sync_filter_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_url: str
) -> None:
    # sync and filter write their config files to the current directory.
    monkeypatch.chdir(tmp_path)
    Path("Bookmarks").write_text(json.dumps(BOOKMARKS), encoding="utf-8")

    assert "Processed 2 bookmarks" in _run("sync", "--bookmarks", "Bookmarks")
    # The second run goes through the conflict path of the UPSERT.
    assert "Processed 2 bookmarks" in _run("sync", "--bookmarks", "Bookmarks")

    assert "IGNORED:      0 ->      1" in _run("filter")

    assert "Fetched 1, errors 0." in _run("fetch", "--endpoint", worker_url, "--secret", "token")
    assert _FakeWorker.received == [("Bearer token", {"url": "https://example.com/article"})]

    saved = list(Path("inbox/Bar").glob("*-Article.md"))
    assert len(saved) == 1
    assert "# Hello" in saved[0].read_text(encoding="utf-8")

    assert _run("folders", "--status", "FETCHED").strip() == "Bar"
    assert "No ERROR articles." in _run("errors")
