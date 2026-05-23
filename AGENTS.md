# Repository Guidelines

## Project Structure & Module Organization

This is a lightweight local web app:

- `csp_tracker.html` - CSP Tracker UI, styling, storage, Black-Scholes helpers, rendering, and event handlers.
- `server.py` - optional local HTTP server and `/api/eod` endpoint backed by `yfinance`.
- `requirements.txt` - Python dependency list for EOD price fetching.

There are no separate `src/`, `tests/`, or asset directories yet. If the app grows, group code by responsibility before splitting files: storage, pricing/math, rendering, and user actions. Keep visual assets under `assets/`.

## Build, Test, and Development Commands

No build step is required.

- Open `csp_tracker.html` directly in a browser to run the app.
- `pip install -r requirements.txt` - install `yfinance` for EOD price fetching.
- `python server.py` - serve `http://127.0.0.1:8000` and enable `/api/eod?ticker=QQQ`.

Do not add package-manager tooling unless the project needs a dependency or repeatable build.

## Coding Style & Naming Conventions

Use plain HTML, CSS, and JavaScript consistent with the existing file. Follow two-space indentation, compact helpers, and semicolon-terminated JavaScript. Prefer `const` for fixed values and `let` only for mutable state.

Keep CSS custom properties in `:root` for shared colors and spacing. Use short class names matching `.summary`, `.card`, `.pos`, `.metrics`, and `.readout`. User-facing labels may remain bilingual.

## Testing Guidelines

There is no automated test suite. Verify changes manually in a modern browser:

- Add a position with required fields only.
- Add a position with underlying price and IV to exercise the Black-Scholes curve.
- Update mark, fetch EOD, update underlying/IV, delete a position, bind/read a JSON file, and reload to confirm storage behavior.
- Check desktop and narrow mobile widths for layout regressions.

If automated tests are introduced, place them under `tests/` and document the runner here.

## Commit & Pull Request Guidelines

This checkout has no Git history, so no repository-specific convention can be inferred. Use concise imperative messages, for example `Add mark update validation`.

Pull requests should include a behavior summary, manual test notes, linked issue if applicable, and screenshots for visible UI changes.

## Security & Configuration Tips

The app stores position data through `window.storage` when available, falls back to `localStorage`, supports JSON export/import, and can bind a JSON file for live writes when supported. EOD data comes from Yahoo Finance via `yfinance`; handle network or rate-limit failures gracefully. Do not commit real brokerage data, credentials, API keys, or account screenshots.
