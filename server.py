from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, time
from json import dumps
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo


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
        return super().do_GET()

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
                hist = yf.download(
                    yahoo_symbol,
                    period="10d",
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
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
                return self.send_json(200, {
                    "ticker": ticker,
                    "yahooSymbol": yahoo_symbol,
                    "date": last_date.strftime("%Y-%m-%d"),
                    "close": round(price, 4),
                    "source": "yfinance",
                })
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
                expirations = list(tk.options)
                if not expirations:
                    errors.append(f"{yahoo_symbol}: no option expirations")
                    continue
                if expiry not in expirations:
                    errors.append(f"{yahoo_symbol}: expiry {expiry} not found; available {', '.join(expirations[:6])}")
                    continue

                chain = tk.option_chain(expiry)
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

                return self.send_json(200, {
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
                    "source": "yfinance",
                })
            except Exception as exc:
                errors.append(f"{yahoo_symbol}: {exc}")
        return self.send_json(404, {"error": "; ".join(errors) or f"No option data found for {ticker}"})


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((HOST, PORT), CSPHandler)
    print(f"CSP Tracker running at http://{HOST}:{PORT}")
    httpd.serve_forever()
