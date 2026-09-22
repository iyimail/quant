"""Historical bar-close math. Pine/TradingView golden validation is separate."""
import numpy as np
import pandas as pd


def rma(s, n):
    seed, previous, result = [], np.nan, []
    for value in s:
        if pd.notna(value):
            if np.isnan(previous):
                seed.append(value)
                if len(seed) == n:
                    previous = float(np.mean(seed))
            else:
                previous += (value - previous) / n
        result.append(previous)
    return pd.Series(result, index=s.index)


def tr(b, handle_na=True):
    previous = b.close.shift(1)
    result = pd.concat([b.high-b.low, (b.high-previous).abs(), (b.low-previous).abs()], axis=1).max(axis=1)
    return result if handle_na else result.where(previous.notna())


def atr(b, n):
    return rma(tr(b), n)


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def wma(s, n):
    weights = np.arange(1, n+1)
    return s.rolling(n).apply(lambda a: np.dot(a, weights)/weights.sum(), raw=True)


def linreg(s, n, offset=0):
    x = np.arange(n)
    if n < 2:
        return s.copy()
    centered = x-x.mean()
    return s.rolling(n).apply(lambda a: a.mean() + np.dot(centered, a)/np.dot(centered, centered)*(n-1-offset-x.mean()), raw=True)


def aggregate(bars, minutes):
    if len(bars) < 2 or not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("Timezone-aware market bars required")
    step = bars.index[1]-bars.index[0]
    target = pd.Timedelta(minutes=minutes)
    if target < step or target % step:
        raise ValueError(f"{minutes}m computation requires finer OHLCV data (available: {step})")
    if not bars.index.is_unique or not (bars.index.to_series().diff().dropna() == step).all():
        raise ValueError("Duplicate/gapped/unordered calculation feed")
    if not bars.index.equals(bars.index.floor(step)):
        raise ValueError("Misaligned calculation feed")
    rule = f"{minutes}min"
    result = bars.resample(rule, origin="start_day", closed="left", label="left").agg(
        {"open":"first", "high":"max", "low":"min", "close":"last", "volume":"sum"})
    if len(result) and bars.index[0] != result.index[0]:
        result = result.iloc[1:]
    return result


def confirmed(series, chart_index, minutes):
    # For LTF requests, sample at chart open: same first-intrabar lookahead_on
    # selection as the source, then apply the requested-context [1].
    return series.shift(1).reindex(chart_index, method="ffill")


def supertrend(b, n, mult):
    av = atr(b, n)
    upper = (b.high+b.low)/2 + mult*av
    lower = (b.high+b.low)/2 - mult*av
    pu = pl = 0.
    previous_st = np.nan
    direction = 1
    out = []
    for i in range(len(b)):
        u, l = upper.iloc[i], lower.iloc[i]
        pc = b.close.iloc[i-1] if i else np.nan
        l = l if l > pl or pc < pl else pl
        u = u if u < pu or pc > pu else pu
        if i == 0 or pd.isna(av.iloc[i-1]):
            direction = 1
        elif previous_st == pu:
            direction = -1 if b.close.iloc[i] > u else 1
        else:
            direction = 1 if b.close.iloc[i] < l else -1
        previous_st = l if direction == -1 else u
        out.append(direction < 0)
        pu, pl = (0. if pd.isna(u) else u), (0. if pd.isna(l) else l)
    return pd.Series(out, index=b.index)
