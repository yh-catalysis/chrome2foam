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
from chrome2foam.filter_config import ensure_config, load_rules, should_keep
from chrome2foam.models import Article

app = typer.Typer(help="chrome2foam - Sync Chrome bookmarks to Markdown inbox.")


def _recover_from_inbox(engine, inbox: Path) -> int:
    """Scan inbox for saved Markdown files and mark matching DB articles as FETCHED."""
    recovered = 0
    with get_session(engine) as session:
        for md_file in sorted(inbox.glob("*.md")):
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
        targets = session.query(Article).filter(Article.status.in_(["PENDING", "ERROR"])).all()
        ignored = 0
        reset = 0
        for article in targets:
            keep, reason = should_keep(article.url, article.folder_path, rules)
            if verbose:
                tag = "KEEP  " if keep else "IGNORE"
                typer.echo(f"{tag}  [{reason}]  ({article.status})")
                typer.echo(f"        url:    {article.url}")
                typer.echo(f"        folder: {article.folder_path}")
            if keep:
                if article.status == "ERROR":
                    if not dry_run:
                        article.status = "PENDING"
                    reset += 1
            else:
                if not dry_run:
                    article.status = "IGNORED"
                ignored += 1
        if not dry_run:
            session.commit()

    suffix = "  (dry-run, no changes written)" if dry_run else ""
    typer.echo(
        f"Ignored {ignored}, reset to PENDING {reset} (from ERROR)"
        f"  [{len(targets)} evaluated]{suffix}"
    )


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
    load_dotenv()

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
