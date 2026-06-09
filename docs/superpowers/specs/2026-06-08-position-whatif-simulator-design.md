# Position What-if Simulator — Design

Date: 2026-06-08

## Goal

Add a "what-if" scenario simulator to each open position's detail view. The user
drags two sliders — **date** and **underlying price** — and sees the resulting
option price and return metrics for that scenario, computed live via
Black-Scholes.

The existing "Time Value Decay" chart already lets the user scrub across dates
with the underlying held constant. This feature adds the underlying-price
dimension and explicit numeric readouts.

## Placement & Trigger

- New independent block ("情景模拟 / What-if Simulator") inside each expanded
  position card (`pdetail`), rendered **below** the existing decay chart
  (`chartwrap`).
- Requires Black-Scholes inputs (`under` + `iv`), i.e. `haveModel === true`.
  When inputs are missing, render a short bilingual note (matching the decay
  chart's missing-input note style) instead of the sliders.
- IV is held **constant** across the scenario (same assumption as the decay
  chart). The existing footer disclaimer already covers this assumption.

## Sliders

| Slider | Range | Step | Default |
|--------|-------|------|---------|
| Date | open date → expiry date (same domain as decay chart x-axis) | 1 day | today, clamped to the legal endpoints |
| Underlying | current underlying ×0.7 → ×1.3 (±30%) | (range)/200 rounded to a sensible precision | current underlying |

Date is stored as a day index `0..totalDays` from the open date.

## Scenario Computation

Given the slider values:

- `S` = underlying slider value
- `T` = max(expiry − dateSliderValue, 0) / 365
- `K`, `r`, `iv` unchanged from the position

Then:

- `scenarioPrice = bsOptionAt(p, S, T)` — Black-Scholes option value
- `scenarioPL = (p.prem − scenarioPrice) * p.qty * p.mult`

## Readouts (update live on drag, no full re-render)

1. **Scenario date / DTE** — e.g. `2026-06-20 · 12 DTE`
2. **Scenario underlying** — slider value, with vs-current change label as context
   (e.g. `115.00 (+15%)`)
3. **Scenario option price vs current** — `current 1.85 → scenario 0.92 (Δ −50%)`,
   comparing against the position's current mark (`currentMark(p)`)
4. **Scenario P/L** — colored positive/negative (e.g. `+$93`)
5. **Premium capture %** — `(p.prem − scenarioPrice) / p.prem * 100`
6. **Return on capital %** — `scenarioPL / capital(p) * 100`

## Code Changes (single file: `csp_tracker.html`)

- Add BS helper `bsOptionAt(p, S, T)` that takes explicit `S` and `T`, reusing
  `bsPut` / `bsCall`. Does not modify the existing `bsOption(p, T)`.
- `posCard()`: insert simulator markup after `chartwrap` — two
  `<input type="range">` elements, a readout container, and bilingual labels.
  Each element carries `data-pid` / ids keyed by position id (matching the
  `chart_${id}` / `read_${id}` pattern).
- Add `wireSimulators()`, called from `renderAll()` alongside `wireCharts()`.
  It attaches `input` event handlers to the sliders, computes the scenario via
  Black-Scholes purely on the client, and updates the readout DOM directly —
  **without** triggering a full card re-render (mirrors the existing scrubber
  pattern, so slider position and expanded state are not reset mid-drag).
- Add bilingual `UI_TEXT` entries: block title, each readout label, and the
  missing-input note.

## Edge Cases

- Expired (`today > expiry`) or `today < open`: clamp the date slider default to
  the legal endpoint.
- `T <= 0`: Black-Scholes degrades to intrinsic value (already handled by the
  existing `bsPut` / `bsCall`).
- Slider positions are ephemeral. A data-driven re-render (e.g. EOD fetch)
  resets them to defaults (today + current price), which is a sensible reset
  point. No persistence (YAGNI).

## Non-Goals

- Persisting slider positions across re-renders.
- Varying IV within the scenario.
- Annualized return readout (explicitly not requested).
