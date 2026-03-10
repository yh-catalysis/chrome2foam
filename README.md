# Chrome2Foam

[English](#english) | [日本語](#日本語)

---

## English

A CLI tool that syncs Google Chrome bookmarks into a local SQLite database and
converts articles to Markdown via a Cloudflare Workers endpoint.
Designed as an inbox for knowledge-base tools like [Foam](https://foambubble.github.io/foam/) and [Obsidian](https://obsidian.md/).

### Installation

Install directly from GitHub with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/yh-catalysis/chrome2foam.git
```

This makes the `chrome2foam` command available globally.

### Local Development

```bash
git clone https://github.com/yh-catalysis/chrome2foam.git
cd chrome2foam
uv sync
uv run chrome2foam --help
```

### Usage

#### 1. `sync` - Import bookmarks

```bash
chrome2foam sync
```

Reads Chrome's `Bookmarks` JSON and UPSERTs entries into the local SQLite database:

- **New** bookmarks are added with status `PENDING`.
- **Existing** bookmarks have their `title` and `folder_path` updated to match Chrome.
- Bookmarks **deleted from Chrome** are removed from the DB (inbox Markdown files are intentionally kept).
- The `inbox` directory is scanned to restore `FETCHED` status for already-saved files,
  recovering gracefully from interrupted fetch runs.

`--bookmarks` is optional; the path is auto-detected for macOS, Linux, and WSL2.

#### 2. `filter` - Apply filter rules

```bash
chrome2foam filter
chrome2foam filter --dry-run --verbose
```

On first run, `filter.ini` is created by copying `filter.ini.example`.
Edit it to define include/exclude rules using Python regex patterns.
`filter.ini` is listed in `.gitignore` and is never committed.

Evaluates **both `PENDING` and `ERROR` articles**:

| Source status | Rule match | Result |
| --- | --- | --- |
| `PENDING` | ignore rule | `IGNORED` |
| `ERROR` | keep rule | `PENDING` (retried on next fetch) |
| `ERROR` | ignore rule | `IGNORED` |

After running `filter`, no `ERROR` rows should remain.

Rule sections (first match wins):

| Section | Matched against | Outcome |
| --- | --- | --- |
| `[url_include]` | Full URL | Keep |
| `[url_exclude]` | Full URL | Ignore |
| `[folder_include]` | Folder path (e.g. `Bar/Tech`) | Keep |
| `[folder_exclude]` | Folder path | Ignore |

No match -> bookmark is kept. Order is configurable via `evaluation_order` in `[settings]`.

Options: `--verbose` / `-v` (print rule details per bookmark), `--dry-run` / `-n` (preview without writing), `--config` / `-c` (custom INI path).

#### 3. `fetch` - Convert to Markdown

Copy `.env.example` to `.env` and fill in your credentials:

```env
CHROME2FOAM_ENDPOINT=https://your-worker.workers.dev/api/fetch
CHROME2FOAM_SECRET=your_auth_token
```

```bash
chrome2foam fetch
# or pass credentials inline (overrides .env)
chrome2foam fetch --endpoint https://... --secret your_token
```

Sends each `PENDING` URL to the Cloudflare Workers endpoint, saves the returned Markdown
with YAML front-matter to `./inbox`, and marks the status as `FETCHED` or `ERROR`.
Each article is committed to the DB immediately, so a Ctrl+C interruption does not lose progress.
Re-run `sync` after any interruption to recover `FETCHED` status from inbox files,
then `fetch` again to continue with the remaining `PENDING` items.

#### 4. `folders` - List folder paths

```bash
chrome2foam folders
chrome2foam folders --status PENDING
```

Lists all unique `folder_path` values in the DB.
Use `--status` to filter by `PENDING`, `IGNORED`, `FETCHED`, `ERROR`, or `ALL`.

#### 5. `errors` - List error articles

```bash
chrome2foam errors
```

Lists all `ERROR` articles sorted by folder path (tab-separated: `folder_path<TAB>url`).
Run `filter` to triage errors into `IGNORED` or `PENDING`.

### License

MIT

---

## 日本語

Google ChromeのブックマークをローカルSQLiteデータベースに同期し、Cloudflare Workers経由でMarkdownに変換するCLIツールです。
[Foam](https://foambubble.github.io/foam/) や [Obsidian](https://obsidian.md/) などのナレッジベースツールのインボックスとして使用します。

### インストール

[uv](https://docs.astral.sh/uv/) でGitHubから直接インストール:

```bash
uv tool install git+https://github.com/yh-catalysis/chrome2foam.git
```

`chrome2foam` コマンドがグローバルに利用できるようになります。

### ローカル開発

```bash
git clone https://github.com/yh-catalysis/chrome2foam.git
cd chrome2foam
uv sync
uv run chrome2foam --help
```

### 使い方

#### 1. `sync` - ブックマーク同期

```bash
chrome2foam sync
```

ChromeのBookmarks JSONを読み込み、SQLiteにUPSERT:

- **新規**ブックマークは `PENDING` として追加。
- **既存**ブックマークは `title` と `folder_path` を最新値に更新。
- Chromeから**削除**されたブックマークはDBからも除去（inboxのMarkdownは保持）。
- inboxディレクトリをスキャンし、保存済みMarkdownに対応する記事を `FETCHED` に復元
  （中断したfetchを続きから再開できる）。

`--bookmarks` を省略するとmacOS・Linux・WSL2のパスを自動検出。

#### 2. `filter` - フィルタリング

```bash
chrome2foam filter
chrome2foam filter --dry-run --verbose
```

初回実行時に `filter.ini.example` から `filter.ini` が自動生成されます。
Pythonの正規表現でルールを定義してください。`filter.ini` は `.gitignore` に含まれています。

**`PENDING` と `ERROR` の両方を評価します:**

| 元ステータス | マッチしたルール | 結果 |
| --- | --- | --- |
| `PENDING` | ignore ルール | `IGNORED` |
| `ERROR` | keep ルール | `PENDING`（次回fetchで再試行） |
| `ERROR` | ignore ルール | `IGNORED` |

`filter` 実行後、`ERROR` 行は残らないはずです。

ルールセクション（最初にマッチしたルールが優先）:

| セクション | マッチ対象 | 結果 |
| --- | --- | --- |
| `[url_include]` | URL全体 | Keep |
| `[url_exclude]` | URL全体 | Ignore |
| `[folder_include]` | フォルダパス（例: `Bar/Tech`） | Keep |
| `[folder_exclude]` | フォルダパス | Ignore |

マッチなし -> デフォルトでKeep。`[settings]` の `evaluation_order` で評価順を変更可能。

オプション: `--verbose` / `-v`（ルール詳細を表示）、`--dry-run` / `-n`（書き込みなし）、`--config` / `-c`（INIファイル指定）。

#### 3. `fetch` - Markdown取得

`.env.example` を `.env` にコピーして認証情報を設定:

```env
CHROME2FOAM_ENDPOINT=https://your-worker.workers.dev/api/fetch
CHROME2FOAM_SECRET=your_auth_token
```

```bash
chrome2foam fetch
# または引数で直接指定（.envより優先）
chrome2foam fetch --endpoint https://... --secret your_token
```

`PENDING` のURLをCloudflare Workers APIに送信し、YAMLフロントマター付きMarkdownを
`./inbox` に保存します。1件ごとにDBへ書き込むため、Ctrl+Cで中断しても途中結果は保持されます。
中断後は `sync` -> `fetch` で続きから再開できます。

#### 4. `folders` - フォルダ一覧

```bash
chrome2foam folders
chrome2foam folders --status PENDING
```

DB内のユニークなフォルダパスを一覧表示。
`--status PENDING | IGNORED | FETCHED | ERROR | ALL` で絞り込み可能。

#### 5. `errors` - エラー一覧

```bash
chrome2foam errors
```

`ERROR` ステータスの記事をフォルダパス順で一覧表示（タブ区切り: `フォルダパス<TAB>URL`）。
`filter` を実行してERRORを `IGNORED` または `PENDING` に振り分けてください。

### ライセンス

MIT
