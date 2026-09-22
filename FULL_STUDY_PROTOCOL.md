# Pilot13 External-Off Master — Full Study Protocol

Status: PRE-LAUNCH VALIDATION

This file is the persistent research contract. The local program performs the repetitive calculations without model calls. Codex reviews evidence and chooses only predeclared follow-up work. Pine source files and live execution are outside this study.

## Frozen question

With external gates, TP and Daily Limit disabled, what independent value is added by ER v2, Shadow/Weighted HOLD or CLOSE, and NONE/MOST/RSI-SMA/Tillson exits?

## Frozen design

- Universe: WCTUSDT, INITUSDT, DUSKUSDT, MYXUSDT, CETUSUSDT, TAOUSDT, DEXEUSDT, TAKEUSDT, BANANAS31USDT, SIRENUSDT, BROCCOLI714USDT, FHEUSDT, BTRUSDT.
- Market/timeframe: Binance USD-M perpetual, 30 minutes.
- Periods: 2025-09-01–2026-03-01; 2026-03-01–2026-06-01; 2026-06-01–2026-09-01. Start included, end excluded.
- These are chronological comparison windows, not pristine OOS. The project has already seen this history.
- Fixed categorical matrix: 4 exits × 2 ER states × 3 Shadow/Winrate states = 24 configurations.
- Fixed settings: SL 2.3%; trailing activation 2.2%; configured trailing distance 0%; commission 0.08% per order; 300 USDT/order; ER 9/0.17/2; Shadow Weighted 8/minimum 10/0.60.
- Maximum planned cells: 13 × 3 × 24 = 936.

## Data rules

- A coin uses only available bars inside the requested period. Different tested histories are reported and raw returns from unequal durations are not directly ranked.
- Missing leading/trailing history is PARTIAL. No data, fewer than 100 bars, internal gaps, conflicting duplicates, invalid OHLC/timestamps or non-finite values are explicit skips; prices are never invented.
- Candidate and control are paired only when symbol, window, actual timestamps and data hash match.
- BTC/ETH data may be inventoried as context, but it is not a strategy feature until causal wiring is implemented and separately tested.

## Predeclared comparisons

- ER effect: v2 minus OFF, holding exit and WR fixed.
- WR filtering effect: HOLD minus OFF, holding exit and ER fixed.
- WR close-authority effect: CLOSE minus OFF; forced-close marginal effect: CLOSE minus HOLD.
- Exit effect: MOST, RSI-SMA or Tillson minus NONE, holding ER and WR fixed.
- Report paired effects before any configuration ranking. Never search all windows and then call the highest result a winner.

## Evidence gates

Results below these levels are descriptive only: at least 100 pooled closed trades in candidate and control, 30 trades represented in every window, at least five contributing symbols, and no single symbol/month responsible for more than 25% of improvement.

A SCREEN CANDIDATE must show a meaningful paired expectancy improvement, favorable direction in all three windows, at least 70% favorable eligible blocks, leave-one-symbol-out stability, acceptable drawdown/tail loss, survival after removing the five largest wins, higher-cost stress, and conservative trailing-fill treatment. Multiple comparisons require family-wise correction. A risk-control candidate may sacrifice only negligible expectancy while improving drawdown and tail loss materially.

RESEARCH MORE means positive but statistically/sample/coverage/fill fragile. REJECT means negative expectancy without compensating risk reduction, adverse direction in at least two windows, unacceptable tail/DD damage, or failure under robustness tests. Negative results remain recorded.

## Known blockers before launch

- WCT REF-00 has exact decision sequence but 19 unresolved High-detail trailing prices; local closed PnL was about 33.87 USDT too optimistic. Other coins do not inherit WCT parity.
- Correct per-symbol price/quantity increments are not yet proven for all Pilot13 instruments; zero-distance trailing is sensitive to price increment.
- Window boundary warm-up/state behavior must be explicit and tested.
- The runner must prove restart/resume, retry, per-configuration failure isolation, correct CETUSUSDT identity, and actionable paired reporting.

## Allowed conclusion

The study may label a component redundant, harmful, retrospectively useful, or worthy of independent parity/OOS work. It cannot claim exact TradingView PnL, causality, optimal settings, universal edge, Pine equivalence, or production readiness.

## Next work after a valid screen

Freeze results; run paired-effect analysis; apply higher-cost, top-five-winner removal, leave-one-symbol-out and conservative-fill checks; carry at most one exit family, one ER decision and one WR role into a previously untouched external sample; only then request Pine golden-export parity.
