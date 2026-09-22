"""Causal research models for standalone Pine gate outputs."""
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VwapGateConfig:
    use_daily: bool = True
    use_weekly: bool = True
    mode: str = "D+W Confirm"
    band_bps: int = 0
    persist_bars: int = 3
    daily_source: str = "hlc3"
    weekly_source: str = "hlc3"


def price_source(bars: pd.DataFrame, name: str) -> pd.Series:
    if name in {"open", "high", "low", "close"}:
        return bars[name].astype(float)
    columns = {"hl2": ["high", "low"], "hlc3": ["high", "low", "close"],
               "ohlc4": ["open", "high", "low", "close"]}
    if name not in columns:
        raise ValueError(f"Unsupported price source: {name}; external plot data required")
    return bars[columns[name]].astype(float).mean(axis=1)


def _anchored_vwap(source: pd.Series, volume: pd.Series, period: pd.Series) -> pd.Series:
    pv = source * volume
    cumulative_pv = pv.groupby(period).cumsum()
    cumulative_volume = volume.groupby(period).cumsum()
    return cumulative_pv / cumulative_volume.replace(0, np.nan)


def vwap_ext_score(bars: pd.DataFrame, config: VwapGateConfig = VwapGateConfig()) -> pd.DataFrame:
    """Reproduce the confirmed historical output of sources/vwap gate.pine."""
    if config.mode not in {"Daily Only", "Weekly Only", "D+W Confirm"}:
        raise ValueError("Unsupported VWAP gate mode")
    if config.band_bps < 0 or config.persist_bars < 1:
        raise ValueError("VWAP band must be nonnegative and persistence at least one bar")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("VWAP gate requires timezone-aware bars")
    daily_source = price_source(bars, config.daily_source)
    weekly_source = price_source(bars, config.weekly_source)
    volume = bars.volume.astype(float)
    day = pd.Series(bars.index.floor("D"), index=bars.index)
    # Monday-start week, matching TradingView/Binance weekly boundaries in UTC.
    week = pd.Series((bars.index - pd.to_timedelta(bars.index.weekday, unit="D")).floor("D"), index=bars.index)
    daily = _anchored_vwap(daily_source, volume, day)
    weekly = _anchored_vwap(weekly_source, volume, week)
    band = config.band_bps * 0.0001
    d_reg = pd.Series(np.where(bars.close > daily * (1 + band), 1,
                              np.where(bars.close < daily * (1 - band), -1, 0)), index=bars.index, dtype=int)
    w_reg = pd.Series(np.where(bars.close > weekly * (1 + band), 1,
                              np.where(bars.close < weekly * (1 - band), -1, 0)), index=bars.index, dtype=int)
    if config.mode == "Daily Only":
        raw = d_reg if config.use_daily else pd.Series(0, index=bars.index, dtype=int)
    elif config.mode == "Weekly Only":
        raw = w_reg if config.use_weekly else pd.Series(0, index=bars.index, dtype=int)
    elif config.use_daily and config.use_weekly:
        raw = pd.Series(np.where((d_reg == 1) & (w_reg == 1), 1,
                                 np.where((d_reg == -1) & (w_reg == -1), -1, 0)), index=bars.index, dtype=int)
    elif config.use_daily:
        raw = d_reg
    elif config.use_weekly:
        raw = w_reg
    else:
        raw = pd.Series(0, index=bars.index, dtype=int)
    stable, same_count, previous = 0, 0, None
    scores = []
    for value in raw.astype(int):
        changed = previous is not None and value != previous
        same_count = 1 if changed else same_count + 1
        if same_count >= config.persist_bars:
            stable = value
        scores.append(stable)
        previous = value
    return pd.DataFrame({"daily_vwap": daily, "weekly_vwap": weekly,
                         "daily_regime": d_reg, "weekly_regime": w_reg,
                         "raw_regime": raw, "ext_score": scores}, index=bars.index)


def strategy_gate_open(ext_score: pd.Series, wiring: str = "DIRECT") -> pd.Series:
    """Apply the explicit [1] delays in the selected Pine wiring topology."""
    delays = {"DIRECT": 1, "MASTER_GATE_G6": 2}
    if wiring not in delays:
        raise ValueError("Unsupported gate wiring")
    return ext_score.shift(delays[wiring]).fillna(0).gt(0)


def causal_most_gate1(bars: pd.DataFrame, length: int = 14, percent: float = 2.0,
                      requested_rule: str = "4h") -> pd.DataFrame:
    """G1-only counterfactual using the *prior confirmed* requested-TF MOST line.

    The Pine source's actual G1 pass compares chart close with the unshifted
    requested-TF `g1_line` returned under lookahead_on. That historical path is
    not causal. This function intentionally does not reproduce that path.
    It also assumes other Master Gate components and macro reset are disabled.
    """
    if length < 1 or percent < 0:
        raise ValueError("MOST length must be positive and percentage nonnegative")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("MOST gate requires timezone-aware bars")
    requested_close = bars.close.astype(float).resample(
        requested_rule, label="left", closed="left", origin="start_day").last().dropna()
    ema = requested_close.ewm(span=length, adjust=False).mean()
    stop = ema * percent / 100.0
    most_values = []
    previous_most = 0.0
    previous_ema = np.nan
    for current_ema, current_stop, source_close in zip(ema, stop, requested_close):
        if current_ema > previous_most and previous_ema > previous_most:
            current_most = max(previous_most, current_ema - current_stop)
        elif current_ema < previous_most and previous_ema < previous_most:
            current_most = min(previous_most, current_ema + current_stop)
        elif current_ema > previous_most:
            current_most = current_ema - current_stop
        else:
            current_most = current_ema + current_stop
        most_values.append(current_most)
        previous_most = current_most
        previous_ema = current_ema
    requested_most = pd.Series(most_values, index=requested_close.index, name="most_line")
    confirmed = requested_most.shift(1).reindex(bars.index, method="ffill")
    gate_pass = bars.close.astype(float).gt(confirmed).fillna(False)
    # Only G1 active: Master latch sets on pass and resets on failure. Strategy
    # external source adds exactly one chart-bar [1] delay.
    strategy_open = gate_pass.shift(1).fillna(False).astype(bool)
    return pd.DataFrame({"confirmed_most_line": confirmed, "gate_pass": gate_pass,
                         "strategy_gate_open": strategy_open}, index=bars.index)


def confirmed_tillson_gate2(bars: pd.DataFrame, length: int = 8, factor: float = 0.7,
                             mode: str = "Price vs T3", requested_rule: str = "1h") -> pd.DataFrame:
    """Isolated source G2 `Confirmed HTF` signal and Strategy [1] consumer.

    Other Master components and macro reset are intentionally disabled. The
    Pine `Live Cross` execution mode is excluded because it consumes an
    unshifted HTF line returned under lookahead_on on historical bars.
    """
    if length < 1 or factor <= 0 or mode not in {"Price vs T3", "T3 Slope"}:
        raise ValueError("Invalid Tillson configuration")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("Tillson gate requires timezone-aware bars")
    close = bars.close.astype(float).resample(
        requested_rule, label="left", closed="left", origin="start_day").last().dropna()
    e1 = close.ewm(span=length, adjust=False).mean()
    e2 = e1.ewm(span=length, adjust=False).mean()
    e3 = e2.ewm(span=length, adjust=False).mean()
    e4 = e3.ewm(span=length, adjust=False).mean()
    e5 = e4.ewm(span=length, adjust=False).mean()
    e6 = e5.ewm(span=length, adjust=False).mean()
    c1 = -factor ** 3
    c2 = 3.0 * factor ** 2 + 3.0 * factor ** 3
    c3 = -6.0 * factor ** 2 - 3.0 * factor - 3.0 * factor ** 3
    c4 = 1.0 + 3.0 * factor + factor ** 3 + 3.0 * factor ** 2
    t3 = c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3
    raw = close.gt(t3) if mode == "Price vs T3" else t3.gt(t3.shift(1).fillna(t3))
    confirmed = raw.shift(1).reindex(bars.index, method="ffill").fillna(False).astype(bool)
    strategy_open = confirmed.shift(1).fillna(False).astype(bool)
    return pd.DataFrame({"confirmed_t3_line": t3.shift(1).reindex(bars.index, method="ffill"),
                         "gate_pass": confirmed, "strategy_gate_open": strategy_open}, index=bars.index)


def confirmed_macro_reset(bars: pd.DataFrame, length: int = 200,
                          requested_rule: str = "4h") -> pd.DataFrame:
    """Source's confirmed HTF macro EMA and chart-TF fallback, for reset tests.

    Source expression is `ta.ema(close, htf_len)[1]` under lookahead_on.
    A missing HTF value falls back to `ta.sma(close[1], 200)` on chart TF.
    The macro condition is a *reset* (`close < armor`), not an additional
    open condition when CORE is disabled.
    """
    if length < 1:
        raise ValueError("Macro length must be positive")
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("Macro reset requires timezone-aware bars")
    close = bars.close.astype(float)
    requested_close = close.resample(requested_rule, label="left", closed="left",
                                      origin="start_day").last().dropna()
    confirmed_ema = requested_close.ewm(span=length, adjust=False).mean().shift(1)
    mapped_ema = confirmed_ema.reindex(bars.index, method="ffill")
    fallback_sma = close.shift(1).rolling(200, min_periods=200).mean()
    armor = mapped_ema.fillna(fallback_sma)
    return pd.DataFrame({"macro_armor": armor,
                         "macro_resets": close.lt(armor).fillna(False)}, index=bars.index)


def confirmed_bollinger_gate3(minute_bars: pd.DataFrame, chart_index: pd.DatetimeIndex,
                              length: int = 12, requested_rule: str = "24min",
                              allow_above: bool = True, allow_below: bool = False) -> pd.DataFrame:
    """G3 midpoint decision from 1m history, with source HTF/chart delays.

    This checks time alignment only, not full TradingView numeric parity.
    The source gate compares with `ta.bb`'s *middle* SMA; multiplier has no
    effect on the Boolean source expression.
    """
    if length < 1 or minute_bars.index.tz is None or chart_index.tz is None:
        raise ValueError("G3 requires a positive length and timezone-aware indexes")
    requested_close = minute_bars.close.astype(float).resample(
        requested_rule, label="left", closed="left", origin="start_day").last().dropna()
    midpoint = requested_close.rolling(length, min_periods=length).mean()
    raw = ((allow_above & requested_close.gt(midpoint)) |
           (allow_below & requested_close.lt(midpoint))).fillna(False)
    gate_pass = raw.shift(1).reindex(chart_index, method="ffill").fillna(False).astype(bool)
    strategy_open = gate_pass.shift(1).fillna(False).astype(bool)
    return pd.DataFrame({"confirmed_midpoint": midpoint.shift(1).reindex(chart_index, method="ffill"),
                         "gate_pass": gate_pass, "strategy_gate_open": strategy_open}, index=chart_index)


def _linreg_endpoint(values: pd.Series, length: int) -> pd.Series:
    """Rolling least-squares value at offset 0, matching ta.linreg's formula."""
    x = np.arange(length, dtype=float)
    centered = x - x.mean()
    slope_weights = centered / np.dot(centered, centered)
    weights = np.full(length, 1.0 / length) + (length - 1 - x.mean()) * slope_weights
    return values.rolling(length, min_periods=length).apply(
        lambda window: float(np.dot(window, weights)), raw=True)


def confirmed_momentum_gate4(minute_bars: pd.DataFrame, chart_index: pd.DatetimeIndex,
                             curve_length: int = 50, slope_length: int = 5,
                             threshold: float = 1.5,
                             requested_rule: str = "24min") -> pd.DataFrame:
    """G4's 24m momentum Boolean with prior-HTF and Strategy-chart offsets.

    Intended for causal/clock screening, not TradingView numeric parity.
    """
    if curve_length < 2 or slope_length < 1 or threshold < 0:
        raise ValueError("Invalid G4 parameters")
    if minute_bars.index.tz is None or chart_index.tz is None:
        raise ValueError("G4 requires timezone-aware indexes")
    close = minute_bars.close.astype(float).resample(
        requested_rule, label="left", closed="left", origin="start_day").last().dropna()
    curve = _linreg_endpoint(close, curve_length)
    slope_raw = curve.diff()
    slope_smoothed = slope_raw.ewm(span=slope_length, adjust=False).mean()
    hist = slope_smoothed - slope_smoothed.rolling(13, min_periods=13).mean()
    strength = hist.abs()
    average_strength = strength.rolling(20, min_periods=20).mean()
    rising = hist.gt(hist.shift(1).fillna(hist))
    raw = (rising & strength.gt(average_strength * threshold)).fillna(False)
    gate_pass = raw.shift(1).reindex(chart_index, method="ffill").fillna(False).astype(bool)
    strategy_open = gate_pass.shift(1).fillna(False).astype(bool)
    return pd.DataFrame({"gate_pass": gate_pass, "strategy_gate_open": strategy_open},
                        index=chart_index)
