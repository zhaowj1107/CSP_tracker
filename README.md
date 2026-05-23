# CSP Tracker

A lightweight web app for tracking short put/call positions, premium capture, theta decay, EOD underlying prices, option bid/ask spreads, and closed trade history.

The app is local-first: users can start without an account, save data in their browser, and optionally enable Supabase cloud sync.

## Features

- Track open short put and short call positions.
- Calculate premium, unrealized P/L, yield, annualized return, DTE, held days, OTM %, and daily theta.
- Fetch EOD underlying prices and option quote data with `yfinance`.
- Use bid/ask mid as the default current mark when available.
- Record closed trades and review historical trade timelines.
- Import/export JSON and optionally bind a local JSON file.
- Optional Supabase email/password login for cloud sync.
- English default UI with `?lang=en`; bilingual Chinese UI with `?lang=zh`.
- Dark and light themes.

## Project Structure

- `csp_tracker.html` - frontend UI, styles, storage, analytics, and interactions.
- `server.py` - local/Render HTTP server plus `/api/eod`, `/api/option_quote`, and `/api/config`.
- `requirements.txt` - Python dependencies.
- `supabase_schema.sql` - Supabase table and row-level security policies.
- `.env.example` - local environment variable template.
- `DEV_LOG.md` - development notes.

## Local Setup

Install dependencies:

```powershell
pip install -r requirements.txt
```

Optional cloud sync config:

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-supabase-anon-key
```

Run the app:

```powershell
python server.py
```

Open:

```text
http://127.0.0.1:8000/?lang=en
http://127.0.0.1:8000/?lang=zh
```

## Supabase Setup

1. Create a Supabase project.
2. Open `SQL Editor`.
3. Run the full contents of `supabase_schema.sql`.
4. Copy the project URL and `anon public` key into `.env` or Render environment variables.

Cloud sync stores one JSON snapshot per user in `public.user_snapshots`. Row-level security limits each user to their own snapshot.

## Render Deployment

Create a Render Python Web Service from this repository.

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
python server.py
```

Environment variables:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
```

Render provides `PORT`; `server.py` reads it automatically.

## Data Notes

Unauthenticated users store data locally in the browser and can use JSON import/export. Logged-in users can upload local data to Supabase and load cloud data on another device.

This tool is for tracking and education only, not investment advice. Always verify prices with your broker.
