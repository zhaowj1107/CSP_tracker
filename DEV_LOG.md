# Development Log

## 2026-05-23

- Added a top-right settings menu with language switching, CSP margin ratio, and dark/light theme controls.
- Added English UI support while preserving the existing bilingual Chinese interface as the default.
- Wired CSP margin ratio into cash-secured capital, yield, and annualized return calculations.
- Added automatic IV fetch on the new-position form using ticker, option type, expiry, and strike.
- Added SOI fallback to `SOI.PA` for yfinance EOD prices; Yahoo still does not provide SOI option chains.
- Simplified market data date display and removed extra quote metadata from position details.
- Moved risk alerts to the top of expanded position details and only show them when `OTM < 15%` and `DTE < 21`.
- Fixed summary number alignment by separating position-card styling from positive-number color styling.
