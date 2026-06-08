from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from calendar import monthcalendar, FRIDAY
from datetime import datetime, time, date as dt_date, timedelta
from json import dumps, loads
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import time as _time

import requests as http_requests


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


def load_dotenv():
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()
HOST = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
YF_CACHE = ROOT / ".yfinance_cache"
YAHOO_ALIASES = {
    "SOI": "SOI.PA",
}

# ── In-memory yfinance cache ──────────────────────────────────────────────────
_EOD_CACHE: dict = {}    # ticker -> {payload, ts}
_QUOTE_CACHE: dict = {}  # (ticker, expiry, strike, type) -> {payload, ts}
EOD_TTL   = 4 * 3600   # 4 h  — EOD prices from closed sessions don't change
QUOTE_TTL = 15 * 60    # 15 min — option quotes change during the session

def _cache_get(store, key, ttl):
    entry = store.get(key)
    if entry and _time.monotonic() - entry["ts"] < ttl:
        return entry["data"]
    return None

def _cache_set(store, key, data):
    store[key] = {"data": data, "ts": _time.monotonic()}

def _yf_retry(fn, retries=3):
    """Call fn(); on exception retry with 2 s → 4 s delays before giving up."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            _time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")

FOMC_DATES = [
    "2025-01-29","2025-03-19","2025-05-07","2025-06-18",
    "2025-07-30","2025-09-17","2025-10-29","2025-12-10",
    "2026-01-28","2026-03-18","2026-05-06","2026-06-17",
    "2026-07-29","2026-09-16","2026-10-28","2026-12-09",
]
CPI_DATES = [
    "2025-01-15","2025-02-12","2025-03-12","2025-04-10",
    "2025-05-13","2025-06-11","2025-07-11","2025-08-12",
    "2025-09-10","2025-10-15","2025-11-12","2025-12-10",
    "2026-01-14","2026-02-11","2026-03-11","2026-04-09",
    "2026-05-13","2026-06-10","2026-07-14","2026-08-12",
    "2026-09-09","2026-10-14","2026-11-11","2026-12-09",
]
NFP_DATES = [
    "2025-01-10","2025-02-07","2025-03-07","2025-04-04",
    "2025-05-02","2025-06-06","2025-07-03","2025-08-01",
    "2025-09-05","2025-10-03","2025-11-07","2025-12-05",
    "2026-01-09","2026-02-06","2026-03-06","2026-04-03",
    "2026-05-01","2026-06-05","2026-07-02","2026-08-07",
    "2026-09-04","2026-10-02","2026-11-06","2026-12-04",
]


def yahoo_symbols(ticker):
    symbols = [ticker]
    alias = YAHOO_ALIASES.get(ticker)
    if alias and alias not in symbols:
        symbols.append(alias)
    return symbols


def configure_yfinance(yf):
    YF_CACHE.mkdir(exist_ok=True)
    if hasattr(yf, "set_tz_cache_location"):
        yf.set_tz_cache_location(str(YF_CACHE))


AI_SYSTEM_PROMPT = """You are an expert options trading risk analyst specializing in Cash-Secured Puts (CSP) and Covered Calls.

Analyze the user's short option portfolio and provide:

1. **Portfolio Overview**: Overall risk assessment, total capital deployed, portfolio theta, and diversification.
2. **Position-by-Position Analysis**: For each position, evaluate:
   - Risk level (Low / Medium / High / Critical)
   - Whether the position is safe to hold to expiration or should be closed/rolled early
   - Key factors: DTE, OTM buffer, IV level, theta capture progress, unrealized P/L
3. **Action Recommendations**: Specific, prioritized recommendations (hold / close / roll / monitor)
4. **Risk Warnings**: Flag any positions with concerning metrics (OTM < 15%, DTE < 7, deep ITM, etc.)

Be concise and actionable. Use data-driven reasoning. Format with clear markdown headers and bullet points.
Respond in the same language as the user's input."""


def _call_openai(system_prompt, user_prompt):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    base_url = os.environ.get("OPENAI_RESPONSES_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    endpoint = base_url if base_url.endswith("/responses") else f"{base_url}/responses"
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    max_output_tokens = int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "3200"))

    response = http_requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "model": model,
            "instructions": system_prompt,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}],
            "stream": False,
            "max_output_tokens": max_output_tokens,
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    for item in data.get("output", []):
        if item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    return block.get("text")
    return data.get("output_text") or None


def _call_minimax(system_prompt, user_prompt):
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    client = OpenAI(base_url="https://api.minimax.chat/v1", api_key=api_key)
    response = client.chat.completions.create(
        model="MiniMax-M2.5",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout=90,
    )
    return response.choices[0].message.content


def ai_analyze(system_prompt, user_prompt):
    try:
        result = _call_openai(system_prompt, user_prompt)
        if result:
            return result, "openai"
    except Exception as e:
        print(f"OpenAI call failed, trying MiniMax fallback: {e}")

    try:
        result = _call_minimax(system_prompt, user_prompt)
        if result:
            return result, "minimax"
    except Exception as e:
        print(f"MiniMax call also failed: {e}")

    return None, None


class CSPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.path = "/csp_tracker.html"
            return super().do_GET()
        if parsed.path == "/api/eod":
            return self.handle_eod(parsed.query)
        if parsed.path == "/api/option_quote":
            return self.handle_option_quote(parsed.query)
        if parsed.path == "/api/config":
            return self.handle_config()
        if parsed.path == "/api/econ_calendar":
            return self.handle_econ_calendar(parsed.query)
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/ai_analyze":
            return self.handle_ai_analyze()
        self.send_json(404, {"error": "Not found"})

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def handle_ai_analyze(self):
        try:
            body = loads(self.read_body())
        except Exception:
            return self.send_json(400, {"error": "Invalid JSON body"})

        positions = body.get("positions", [])
        lang = body.get("lang", "en")
        if not positions:
            return self.send_json(400, {"error": "No positions to analyze"})

        if lang == "zh":
            header = "以下是我当前的期权卖出持仓，请用中文分析：\n\n"
        else:
            header = "Here are my current short option positions. Please analyze:\n\n"

        lines = []
        for i, p in enumerate(positions, 1):
            opt_type = "Put" if p.get("type") != "call" else "Call"
            sym = {"USD": "$", "EUR": "€", "HKD": "HK$", "CNY": "¥", "GBP": "£"}.get(p.get("ccy", "USD"), "")
            line = (
                f"{i}. {p.get('ticker','')} Sell {opt_type} {sym}{p.get('strike','')} "
                f"x{p.get('qty',1)} | Expiry: {p.get('exp','')} | "
                f"Premium: {sym}{p.get('prem','')} | Open: {p.get('open','')}"
            )
            extras = []
            if p.get("under") is not None:
                extras.append(f"Underlying: {p['under']}")
            if p.get("iv") is not None:
                extras.append(f"IV: {p['iv']}%")
            if p.get("mark") is not None:
                extras.append(f"Mark: {sym}{p['mark']}")
            if p.get("dte") is not None:
                extras.append(f"DTE: {p['dte']}")
            if p.get("otm") is not None:
                extras.append(f"OTM: {p['otm']}%")
            if p.get("dailyTheta") is not None:
                extras.append(f"Daily Theta: {sym}{p['dailyTheta']}")
            if p.get("unrealizedPL") is not None:
                extras.append(f"Unrealized P/L: {sym}{p['unrealizedPL']}")
            if p.get("annualized") is not None:
                extras.append(f"Annualized: {p['annualized']}%")
            if p.get("cashSecured") is not None:
                extras.append(f"Cash Secured: {sym}{p['cashSecured']}")
            if extras:
                line += " | " + " | ".join(extras)
            lines.append(line)

        user_prompt = header + "\n".join(lines)

        result, provider = ai_analyze(AI_SYSTEM_PROMPT, user_prompt)
        if not result:
            return self.send_json(503, {
                "error": "AI service unavailable. Configure OPENAI_API_KEY or MINIMAX_API_KEY in environment."
            })

        return self.send_json(200, {"analysis": result, "provider": provider})

    def handle_econ_calendar(self, query):
        qs = parse_qs(query)
        tickers = [t.strip().upper() for t in (qs.get("tickers") or [""])[0].split(",") if t.strip()]
        days = min(int((qs.get("days") or ["60"])[0]), 120)
        earnings_only = (qs.get("earnings_only") or ["0"])[0] == "1"

        today = datetime.now(ZoneInfo("America/New_York")).date()
        end_date = today + timedelta(days=days)

        events = []

        if not earnings_only:
            for d_str in FOMC_DATES:
                d = dt_date.fromisoformat(d_str)
                if today <= d <= end_date:
                    events.append({"date": d_str, "type": "fomc", "risk": "high", "title": "FOMC Rate Decision"})

            for d_str in CPI_DATES:
                d = dt_date.fromisoformat(d_str)
                if today <= d <= end_date:
                    events.append({"date": d_str, "type": "cpi", "risk": "medium", "title": "CPI Release"})

            for d_str in NFP_DATES:
                d = dt_date.fromisoformat(d_str)
                if today <= d <= end_date:
                    events.append({"date": d_str, "type": "nfp", "risk": "medium", "title": "Non-Farm Payrolls"})

            # Monthly OPEX: 3rd Friday (frontend also calculates this, kept here as fallback)
            cur = today.replace(day=1)
            for _ in range(4):
                fridays = [w[FRIDAY] for w in monthcalendar(cur.year, cur.month) if w[FRIDAY]]
                opex = dt_date(cur.year, cur.month, fridays[2])
                if today <= opex <= end_date:
                    events.append({"date": opex.isoformat(), "type": "opex", "risk": "low", "title": "Monthly OPEX"})
                m = cur.month + 1
                cur = cur.replace(year=cur.year + (1 if m > 12 else 0), month=(m if m <= 12 else 1))

        # Earnings from yfinance
        if tickers:
            try:
                import yfinance as yf
                configure_yfinance(yf)
                for ticker in tickers[:8]:
                    for ysym in yahoo_symbols(ticker):
                        try:
                            tk = yf.Ticker(ysym)
                            ed_df = tk.earnings_dates
                            if ed_df is not None and not ed_df.empty:
                                for idx in ed_df.index:
                                    ed = idx.date() if hasattr(idx, "date") else idx
                                    if today <= ed <= end_date:
                                        events.append({"date": ed.isoformat(), "type": "earnings",
                                            "risk": "high", "ticker": ticker,
                                            "title": f"{ticker} Earnings"})
                                break
                        except Exception:
                            pass
            except Exception:
                pass

        seen, unique = set(), []
        for e in events:
            k = (e["date"], e["type"], e.get("ticker", ""))
            if k not in seen:
                seen.add(k)
                unique.append(e)
        unique.sort(key=lambda e: e["date"])

        return self.send_json(200, {"events": unique, "generated": today.isoformat(), "window": days})

    def send_json(self, status, payload):
        body = dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_config(self):
        return self.send_json(200, {
            "supabaseUrl": os.environ.get("SUPABASE_URL", ""),
            "supabaseAnonKey": os.environ.get("SUPABASE_ANON_KEY", ""),
        })

    def handle_eod(self, query):
        ticker = (parse_qs(query).get("ticker") or [""])[0].strip().upper()
        if not ticker:
            return self.send_json(400, {"error": "Missing ticker"})

        cached = _cache_get(_EOD_CACHE, ticker, EOD_TTL)
        if cached:
            return self.send_json(200, {**cached, "source": "yfinance/cache"})

        try:
            import yfinance as yf
        except ModuleNotFoundError:
            return self.send_json(500, {
                "error": "Missing dependency: run `pip install -r requirements.txt` first"
            })
        configure_yfinance(yf)

        errors = []
        for yahoo_symbol in yahoo_symbols(ticker):
            try:
                hist = _yf_retry(lambda s=yahoo_symbol: yf.download(
                    s,
                    period="10d",
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                ))
                if hist.empty or "Close" not in hist:
                    errors.append(f"{yahoo_symbol}: no EOD data")
                    continue

                close = hist["Close"]
                if hasattr(close, "columns"):
                    close = close[yahoo_symbol] if yahoo_symbol in close.columns else close.iloc[:, 0]
                close = close.dropna()
                if close.empty:
                    errors.append(f"{yahoo_symbol}: no close price")
                    continue

                last_date = close.index[-1]
                market_now = datetime.now(ZoneInfo("America/New_York"))
                current_session = last_date.date() == market_now.date()
                if current_session and market_now.time() < time(17, 30) and len(close) > 1:
                    close = close.iloc[:-1]
                    last_date = close.index[-1]

                price = float(close.iloc[-1])
                payload = {
                    "ticker": ticker,
                    "yahooSymbol": yahoo_symbol,
                    "date": last_date.strftime("%Y-%m-%d"),
                    "close": round(price, 4),
                }
                _cache_set(_EOD_CACHE, ticker, payload)
                return self.send_json(200, {**payload, "source": "yfinance"})
            except Exception as exc:
                errors.append(f"{yahoo_symbol}: {exc}")
        return self.send_json(502, {"error": "; ".join(errors) or f"No EOD data found for {ticker}"})

    def handle_option_quote(self, query):
        qs = parse_qs(query)
        ticker = (qs.get("ticker") or [""])[0].strip().upper()
        expiry = (qs.get("expiry") or [""])[0].strip()
        opt_type = (qs.get("type") or ["put"])[0].strip().lower()
        try:
            strike = float((qs.get("strike") or [""])[0])
        except ValueError:
            return self.send_json(400, {"error": "Invalid strike"})

        if not ticker or not expiry or opt_type not in {"put", "call"}:
            return self.send_json(400, {"error": "Missing ticker, expiry, or type"})

        cache_key = (ticker, expiry, strike, opt_type)
        cached = _cache_get(_QUOTE_CACHE, cache_key, QUOTE_TTL)
        if cached:
            return self.send_json(200, {**cached, "source": "yfinance/cache"})

        try:
            import yfinance as yf
        except ModuleNotFoundError:
            return self.send_json(500, {
                "error": "Missing dependency: run `pip install -r requirements.txt` first"
            })
        configure_yfinance(yf)

        errors = []
        for yahoo_symbol in yahoo_symbols(ticker):
            try:
                tk = yf.Ticker(yahoo_symbol)
                expirations = _yf_retry(lambda t=tk: list(t.options))
                if not expirations:
                    errors.append(f"{yahoo_symbol}: no option expirations")
                    continue
                if expiry not in expirations:
                    errors.append(f"{yahoo_symbol}: expiry {expiry} not found; available {', '.join(expirations[:6])}")
                    continue

                chain = _yf_retry(lambda t=tk: t.option_chain(expiry))
                df = chain.calls if opt_type == "call" else chain.puts
                if df.empty:
                    errors.append(f"{yahoo_symbol}: no {opt_type} chain")
                    continue

                strikes = df["strike"].astype(float)
                idx = (strikes - strike).abs().idxmin()
                row = df.loc[idx]
                matched_strike = float(row["strike"])
                bid = float(row["bid"]) if row["bid"] == row["bid"] else None
                ask = float(row["ask"]) if row["ask"] == row["ask"] else None
                last = float(row["lastPrice"]) if row["lastPrice"] == row["lastPrice"] else None
                iv = float(row["impliedVolatility"]) if row["impliedVolatility"] == row["impliedVolatility"] else None
                last_trade_date = row.get("lastTradeDate")
                if last_trade_date == last_trade_date and last_trade_date is not None:
                    try:
                        last_trade_date = last_trade_date.strftime("%Y-%m-%d")
                    except AttributeError:
                        last_trade_date = str(last_trade_date)[:10]
                else:
                    last_trade_date = None
                mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None and ask > 0 else last
                spread = round(ask - bid, 4) if bid is not None and ask is not None else None
                spread_pct = round(spread / mid * 100, 2) if spread is not None and mid else None

                payload = {
                    "ticker": ticker,
                    "yahooSymbol": yahoo_symbol,
                    "type": opt_type,
                    "expiry": expiry,
                    "requestedStrike": strike,
                    "matchedStrike": matched_strike,
                    "exactStrike": abs(matched_strike - strike) < 0.0001,
                    "contractSymbol": str(row.get("contractSymbol", "")),
                    "quoteDate": last_trade_date,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "last": last,
                    "iv": round(iv * 100, 4) if iv is not None else None,
                    "spread": spread,
                    "spreadPct": spread_pct,
                    "openInterest": int(row["openInterest"]) if row.get("openInterest") == row.get("openInterest") else None,
                    "volume": int(row["volume"]) if row.get("volume") == row.get("volume") else None,
                }
                _cache_set(_QUOTE_CACHE, cache_key, payload)
                return self.send_json(200, {**payload, "source": "yfinance"})
            except Exception as exc:
                errors.append(f"{yahoo_symbol}: {exc}")
        return self.send_json(404, {"error": "; ".join(errors) or f"No option data found for {ticker}"})


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((HOST, PORT), CSPHandler)
    print(f"CSP Tracker running at http://{HOST}:{PORT}")
    httpd.serve_forever()
