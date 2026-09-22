# HL 5m / 15m / 60m research mode

Status: design only; no production or Pine-parity claim.

## Verified source behavior

`sources/hl gate.pine` accepts a configuration only when `chart < MTF < HTF`.
Its default `15m / 60m` is therefore valid on a 5m chart. The MTF and HTF
calls use `request.security(..., f_structure(true), lookahead_on)`, where
`f_structure(true)` applies `[1]` to each primitive series. A changed event
sequence is consumed only on a confirmed chart bar.

The local `hl_model.py` aggregates a finer calculation feed and shifts the
result one source bar before aligning it to the decision index. This is an
intended causal approximation, but it has not passed a Golden Export parity
test against the Pine 5m chart.

## Current blocker

`quant_lab.checked_bars()` is explicitly calibrated to a 30m decision series,
and `run_job()` contains a 30m reconciliation aggregation. Downloading 5m
archives alone therefore cannot produce a valid 5m HL strategy comparison.

## Implementation sequence

1. Add a job-level `decision_interval` that supports 5m and preserves 30m as
   the current default. Replace hard-coded 30m expected-bar, close-time, and
   reconciliation calculations with this value.
2. Require a 1m calculation feed for 5m HL jobs, aggregate it to 5m for the
   decision series, and fail closed if it differs from the pinned/native 5m
   candles under the selected execution-data policy.
3. Add an HL golden-export fixture from TradingView for one symbol and a fixed
   period. Compare event time, event type, gate state, latch close, and first
   executable decision bar; reject any mismatch before PnL comparison.
4. Only then add a separate `5m / 15m / 60m` test profile and permit an Excel
   comparison when symbol, exact period, execution assumptions, and timeframe
   match.

## Acceptance criteria

- No event reaches the 5m decision bar before the Pine-confirmed event bar.
- Native and calculation-derived decision candles have an explicit PASS/FAIL
  reconciliation result.
- The 5m mode remains labelled `RESEARCH_APPROXIMATION` until a Pine Golden
  Export parity test passes.
