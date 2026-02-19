# CLAUDE.md — AI Assistant Guide for email_finder

This file provides guidance for AI assistants (Claude and others) working on this codebase.

---

## Project Overview

**email_finder** is a Telegram bot that automatically discovers and extracts email addresses from websites. Users send a list of URLs (as a `.txt` file or plain text message) via Telegram, and the bot returns an Excel spreadsheet containing discovered emails.

- **Language**: Python 3.8+
- **Interface**: Telegram Bot
- **Output**: `.xlsx` Excel reports
- **UI language**: Russian (user-facing strings, comments, and docs are in Russian)

---

## Repository Structure

```
email_finder/
├── run.py              # Entry point — validates env and starts the bot
├── bot.py              # Telegram bot logic, session management, user interaction
├── email_scraper.py    # Core web scraping engine (HTTP + Playwright)
├── excel_handler.py    # Excel report generation via openpyxl
├── config.py           # Configuration dataclass, loads from .env
├── all.py              # Developer utility: project snapshot / version diff tool
├── requirements.txt    # Pinned Python dependencies
├── README.md           # Russian-language user documentation
└── .gitignore          # Standard Python gitignore
```

---

## Architecture

### Module Responsibilities

| Module | Class | Role |
|---|---|---|
| `run.py` | — | Entry point; validates `BOT_TOKEN`, calls `bot.main()` |
| `bot.py` | `EmailScraperBot` | Telegram interface, session state, progress updates |
| `email_scraper.py` | `EmailScraper` | Three-level scraping strategy, email extraction |
| `excel_handler.py` | `ExcelHandler` | Creates formatted `.xlsx` reports |
| `config.py` | `Config` | Dataclass with all configurable parameters |
| `all.py` | — | Developer tool for code snapshot diffs (not part of bot runtime) |

### Three-Level Scraping Strategy (`email_scraper.py`)

The scraper uses a progressive, intelligence-based approach:

1. **Level 1 — "Sniper shot"**: Scrapes the homepage, extracts emails, and prioritizes those matching the site domain or known corporate prefixes (`info@`, `contact@`, `support@`, etc.).
2. **Level 2 — "Tactical assault"**: If Level 1 yields no high-priority emails, the scraper discovers and scans contact/about pages (e.g., `/contact`, `/contacts`, `/kontakty`).
3. **Level 3 — "Extended search"**: If still insufficient, scans team/staff/about pages.

Early exit occurs as soon as enough emails are found (controlled by `MAX_EMAILS_PER_DOMAIN`).

### Hybrid HTTP + Browser Approach

- **Fast path**: `aiohttp` for pages that return emails in static HTML.
- **Fallback**: Playwright (headless Chromium) is used when a page requires JavaScript rendering.

### Concurrency Model

- `asyncio` is the async runtime throughout.
- `asyncio.Semaphore` limits concurrent site scans (`MAX_CONCURRENT_SITES`).
- `asyncio-throttle` is used for per-domain rate limiting.
- Each user's scraping task runs as an independent asyncio `Task`, supporting cancellation.

---

## Configuration (`config.py`)

The `Config` dataclass holds all runtime settings. Values are loaded from environment variables (with defaults):

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | *(hardcoded — see warning below)* | Telegram Bot API token |
| `GROQ_API_KEY` | `""` | Optional Groq AI API key |
| `MAX_FILE_SIZE_MB` | `10` | Max uploaded `.txt` file size |
| `MAX_PAGES_PER_DOMAIN` | `5` | Pages to scan per domain |
| `SITE_TIMEOUT_MINUTES` | `2` | Timeout per site |
| `MAX_CONCURRENT_SITES` | `3` | Parallel site scanning limit |
| `MAX_EMAILS_PER_DOMAIN` | `5` | Max emails returned per domain |

**Target contact pages** (hardcoded list in `config.py`): `contact`, `contacts`, `kontakt`, `kontakty`, `about`, `team`, `staff`, `o-nas`, and variants.

### Security Warning

`BOT_TOKEN` is currently hardcoded in `config.py`. **This is a security issue.** When modifying this project, move `BOT_TOKEN` to a `.env` file and load it with `python-dotenv`:

```python
BOT_TOKEN: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
```

The `.gitignore` already excludes `.env` files.

---

## Development Workflow

### Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\activate       # Windows

pip install -r requirements.txt
playwright install chromium    # Required for JS-rendered pages
```

### Running the Bot

```bash
python run.py      # Recommended entry point
# or
python bot.py      # Direct execution also works
```

### Environment Variables

Create a `.env` file (excluded from git) with:

```env
BOT_TOKEN=your_telegram_bot_token_here
GROQ_API_KEY=your_groq_api_key_here   # Optional
```

### Developer Utility

`all.py` is a standalone tool for taking and diffing code snapshots. It is **not** part of the bot's runtime:

```bash
python all.py    # Creates/diffs project snapshots stored as JSON
```

---

## Dependencies

All dependencies are pinned in `requirements.txt`:

| Package | Version | Purpose |
|---|---|---|
| `python-telegram-bot` | 20.7 | Telegram Bot API |
| `aiohttp` | 3.9.1 | Async HTTP requests |
| `beautifulsoup4` | 4.12.2 | HTML parsing |
| `openpyxl` | 3.1.2 | Excel file generation |
| `playwright` | 1.41.1 | Headless browser (Chromium) |
| `python-dotenv` | 1.0.0 | `.env` file loading |
| `aiofiles` | 23.2.1 | Async file I/O |
| `validators` | 0.22.0 | URL/email validation |
| `asyncio-throttle` | 1.0.2 | Async rate limiting |

---

## Code Conventions

### Language

- All user-facing strings, log messages, comments, and docstrings are in **Russian**.
- Variable and function names follow Python conventions (snake_case), using Russian transliterations where appropriate.
- When adding new features, keep user-facing text in Russian to match the existing UX.

### Style

- Python type hints are used in function signatures (`List`, `Dict`, `Set`, `Tuple`).
- Async/await is used throughout — avoid blocking I/O.
- Use `async with` for resource management (HTTP sessions, Playwright browsers).
- Deduplication is done with `set` operations.
- Logging uses Python's built-in `logging` module; keep log messages in Russian.

### Error Handling

- Prefer specific exception types over bare `except:` clauses.
- User-facing errors should be reported back through Telegram with friendly Russian messages.
- Scraping errors for individual sites should be caught and logged without crashing the overall task.

### Telegram Bot Patterns (`bot.py`)

- Each user's active task is tracked in `self.user_tasks: Dict[int, asyncio.Task]`.
- Progress is communicated by editing a single "status" message rather than sending new messages.
- User commands: `/start`, `/help`, `/status`.
- Input is accepted as `.txt` file uploads or multi-line text messages containing URLs.

---

## Testing

There is currently **no test suite**. When adding tests:

- Use `pytest` with `pytest-asyncio` for async test support.
- Place tests in a `tests/` directory.
- Mock Telegram API calls and HTTP requests to avoid external dependencies in tests.

---

## Known Issues and Technical Debt

1. **Hardcoded `BOT_TOKEN`** in `config.py` — should be an environment variable.
2. **No tests** — the codebase has zero test coverage.
3. **Some bare `except` clauses** in `all.py` — should be narrowed to specific exceptions.
4. **`all.py` input validation** — the `extract_file_list` function does no input validation.
5. **No rate limiting on Telegram updates** — high-volume bots could hit API limits.

---

## Key Files to Read First

When onboarding to this codebase, read these files in order:

1. `config.py` — understand all configuration knobs
2. `run.py` — understand the entry point
3. `email_scraper.py` — core scraping logic (most complex file)
4. `bot.py` — Telegram interface and session management
5. `excel_handler.py` — simple report generation

---

## Git Workflow

- The default development branch is `master` (remote: `origin`).
- Feature branches follow the pattern `claude/<description>-<id>`.
- Commit messages should be descriptive and in English.
