"""CLI commands powered by Typer."""

from __future__ import annotations

import os
import re
from pathlib import Path

import typer
from dotenv import load_dotenv
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from chrome2foam.config import default_chrome_bookmarks_path
from chrome2foam.database import get_session, init_db
from chrome2foam.extractor import parse_bookmarks
from chrome2foam.fetcher import fetch_markdown, save_markdown
from chrome2foam.filter_config import ensure_config, ensure_example, load_rules, should_keep
from chrome2foam.models import Article

app = typer.Typer(help="chrome2foam - Sync Chrome bookmarks to Markdown inbox.")

_ENV_EXAMPLE_TEXT = """\
# chrome2foam environment variables
# ===================================
# Copy this file to .env and fill in your values.
# .env contains secrets and must NOT be committed to version control.

# Cloudflare Workers endpoint for the Web2Markdown API.
# Example: https://web2markdown-worker.<your-subdomain>.workers.dev/api/fetch
CHROME2FOAM_ENDPOINT=

# Bearer token set via: npx wrangler secret put AUTH_TOKEN
CHROME2FOAM_SECRET=
"""


def _ensure_env_example(path: Path = Path(".env.example")) -> bool:
    """Write .env.example from built-in template if it does not exist yet.

    Returns True when a new file was created.
    """
    if path.exists():
        return False
    path.write_text(_ENV_EXAMPLE_TEXT, encoding="utf-8")
    return True


def _ensure_env_in_gitignore() -> bool:
    """Create a minimal .gitignore containing .env if no .gitignore exists in cwd.

    Never modifies an existing .gitignore.
    Returns True when a new file was created.
    """
    gitignore = Path(".gitignore")
    if gitignore.exists():
        return False
    gitignore.write_text(".env\n*.db", encoding="utf-8")
    return True


def _recover_from_inbox(engine, inbox: Path) -> int:
    """Scan inbox for saved Markdown files and mark matching DB articles as FETCHED."""
    recovered = 0
    with get_session(engine) as session:
        for md_file in sorted(inbox.rglob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            m = re.search(r'^source:\s*"([^"]+)"', text, re.MULTILINE)
            if not m:
                continue
            source_url = m.group(1)
            article = session.query(Article).filter(Article.url == source_url).first()
            if article and article.status != "FETCHED":
                article.status = "FETCHED"
                recovered += 1
        session.commit()
    return recovered


@app.command()
def sync(
    bookmarks: Path | None = typer.Option(
        None,
        "--bookmarks",
        "-b",
        help="Path to Chrome Bookmarks JSON. Auto-detected if omitted.",
    ),
    db: Path = typer.Option(
        "chrome2foam.db",
        "--db",
        help="Path to the SQLite database file.",
    ),
    inbox: Path = typer.Option(
        "./inbox",
        "--inbox",
        "-i",
        help="Inbox directory to scan for already-fetched Markdown files.",
    ),
) -> None:
    """Import Chrome bookmarks into the local SQLite database (UPSERT).

    Also scans the inbox directory and marks articles whose Markdown file
    already exists as FETCHED, recovering from interrupted fetch runs.
    """
    if bookmarks is None:
        bookmarks = default_chrome_bookmarks_path()

    if not bookmarks.exists():
        typer.echo(f"Bookmarks file not found: {bookmarks}", err=True)
        raise typer.Exit(code=1)

    engine = init_db(db)
    rows = list(parse_bookmarks(bookmarks))
    current_urls = {row["url"] for row in rows}

    with get_session(engine) as session:
        for row in rows:
            stmt = (
                sqlite_insert(Article)
                .values(
                    url=row["url"],
                    title=row["title"],
                    folder_path=row["folder_path"],
                    status="PENDING",
                )
                .on_conflict_do_update(
                    index_elements=["url"],
                    set_={
                        "title": row["title"],
                        "folder_path": row["folder_path"],
                        # status and added_at are preserved as-is
                    },
                )
            )
            session.execute(stmt)

        # Remove DB rows whose URLs no longer exist in the bookmarks file.
        # Inbox Markdown files are intentionally left on disk.
        db_urls = {url for (url,) in session.query(Article.url).all()}
        removed_urls = db_urls - current_urls
        if removed_urls:
            deleted = (
                session.query(Article)
                .filter(Article.url.in_(removed_urls))
                .delete(synchronize_session=False)
            )
        else:
            deleted = 0

        session.commit()

    typer.echo(f"Processed {len(rows)} bookmarks ({deleted} removed from DB).")

    if inbox.is_dir():
        recovered = _recover_from_inbox(engine, inbox)
        if recovered:
            typer.echo(f"Recovered {recovered} articles from {inbox}.")

    if ensure_example():
        typer.echo("Created filter.ini.example  (copy to filter.ini and customize rules)")
    if _ensure_env_example():
        typer.echo("Created .env.example  (copy to .env and fill in your credentials)")
    if _ensure_env_in_gitignore():
        typer.echo("Created .gitignore with .env and *.db (add other secrets manually if needed)")


def _evaluate_article(
    article: Article,
    rules: list,
    dry_run: bool,
    verbose: bool,
) -> tuple[int, int, int, int]:
    """Apply filter rules to one article.

    Returns (pending_kept, pending_ignored, error_reset, error_ignored).
    """
    keep, reason = should_keep(article.url, article.folder_path, rules)
    if verbose:
        tag = "KEEP  " if keep else "IGNORE"
        typer.echo(f"{tag}  [{reason}]  ({article.status})")
        typer.echo(f"        url:    {article.url}")
        typer.echo(f"        folder: {article.folder_path}")
    orig_status = article.status
    if keep:
        if orig_status == "ERROR":
            if not dry_run:
                article.status = "PENDING"
            return 0, 0, 1, 0
        return 1, 0, 0, 0
    if not dry_run:
        article.status = "IGNORED"
    if orig_status == "ERROR":
        return 0, 0, 0, 1
    return 0, 1, 0, 0


@app.command(name="filter")
def filter_cmd(
    db: Path = typer.Option("chrome2foam.db", "--db", help="SQLite database path."),
    config: Path = typer.Option(
        "filter.ini",
        "--config",
        "-c",
        help="Filter rules INI file (created with defaults if absent).",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print rule match details for every bookmark."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would happen without writing to DB."
    ),
) -> None:
    """Apply filter rules to PENDING and ERROR bookmarks.

    PENDING bookmarks that do not match any keep-rule are marked IGNORED.
    ERROR bookmarks are re-evaluated: matching a keep-rule resets them to
    PENDING (so they will be retried on the next fetch); otherwise they are
    marked IGNORED.  After this command, no ERROR rows should remain.
    """
    if ensure_config(config):
        typer.echo(f"Created filter config: {config}  (copied from filter.ini.example)")
        typer.echo("Edit it to customize the rules, then run this command again.")

    try:
        rules = load_rules(config)
    except ValueError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    engine = init_db(db)
    with get_session(engine) as session:
        total = session.query(Article).count()
        old_pending = session.query(Article).filter(Article.status == "PENDING").count()
        old_ignored = session.query(Article).filter(Article.status == "IGNORED").count()
        old_error = session.query(Article).filter(Article.status == "ERROR").count()
        targets = session.query(Article).filter(Article.status.in_(["PENDING", "ERROR"])).all()
        pending_kept = 0
        pending_ignored = 0
        error_reset = 0
        error_ignored = 0
        for article in targets:
            pk, pi, er, ei = _evaluate_article(article, rules, dry_run, verbose)
            pending_kept += pk
            pending_ignored += pi
            error_reset += er
            error_ignored += ei
        if not dry_run:
            session.commit()

    new_pending = pending_kept + error_reset
    new_ignored = old_ignored + pending_ignored + error_ignored
    nc_pending = "  (no change)" if new_pending == old_pending else ""
    nc_ignored = "  (no change)" if new_ignored == old_ignored else ""
    typer.echo(f"Total:   {total:>6,}")
    typer.echo(f"PENDING: {old_pending:>6,} -> {new_pending:>6,}{nc_pending}")
    typer.echo(f"IGNORED: {old_ignored:>6,} -> {new_ignored:>6,}{nc_ignored}")
    if old_error:
        typer.echo(
            f"ERROR:   {old_error:>6,} -> PENDING: {error_reset:,}, IGNORED: {error_ignored:,}"
        )
    if dry_run:
        typer.echo("(dry-run: no changes written)")


@app.command()
def fetch(
    db: Path = typer.Option("chrome2foam.db", "--db", help="SQLite database path."),
    endpoint: str | None = typer.Option(
        None,
        "--endpoint",
        "-e",
        help="Cloudflare Workers endpoint URL. Overrides CHROME2FOAM_ENDPOINT in .env.",
    ),
    secret: str | None = typer.Option(
        None,
        "--secret",
        "-s",
        help="Bearer token for authentication. Overrides CHROME2FOAM_SECRET in .env.",
    ),
    output: Path = typer.Option(
        "./inbox",
        "--output",
        "-o",
        help="Directory for saved Markdown files.",
    ),
) -> None:
    """Fetch Markdown for PENDING articles via Cloudflare Workers and save."""
    load_dotenv(dotenv_path=Path(".env"))

    resolved_endpoint = endpoint or os.getenv("CHROME2FOAM_ENDPOINT")
    resolved_secret = secret or os.getenv("CHROME2FOAM_SECRET")

    if not resolved_endpoint:
        typer.echo(
            "Error: endpoint is required. Set CHROME2FOAM_ENDPOINT in .env or use --endpoint.",
            err=True,
        )
        raise typer.Exit(code=1)

    engine = init_db(db)

    with get_session(engine) as session:
        pending = session.query(Article).filter(Article.status == "PENDING").all()
        ok = 0
        err = 0
        for article in pending:
            try:
                md = fetch_markdown(article, resolved_endpoint, resolved_secret)
                save_markdown(article, md, output)
                article.status = "FETCHED"
                ok += 1
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"ERROR fetching {article.url}: {exc}", err=True)
                article.status = "ERROR"
                err += 1
            session.commit()  # persist each result immediately; safe on Ctrl+C

    typer.echo(f"Fetched {ok}, errors {err}.")


@app.command()
def folders(
    db: Path = typer.Option("chrome2foam.db", "--db", help="SQLite database path."),
    status: str = typer.Option(
        "ALL",
        "--status",
        "-s",
        help="Filter by status: PENDING, IGNORED, FETCHED, ERROR, ALL.",
    ),
) -> None:
    """List unique folder paths recorded in the database."""
    engine = init_db(db)
    with get_session(engine) as session:
        q = session.query(Article)
        if status.upper() != "ALL":
            q = q.filter(Article.status == status.upper())
        fps = sorted({a.folder_path for a in q.all()})
    for fp in fps:
        typer.echo(fp)


@app.command()
def errors(
    db: Path = typer.Option("chrome2foam.db", "--db", help="SQLite database path."),
) -> None:
    """List all articles with ERROR status, sorted by folder path."""
    engine = init_db(db)
    with get_session(engine) as session:
        rows = (
            session.query(Article)
            .filter(Article.status == "ERROR")
            .order_by(Article.folder_path, Article.url)
            .all()
        )
    if not rows:
        typer.echo("No ERROR articles.")
        return
    for a in rows:
        typer.echo(f"{a.folder_path}\t{a.url}")
