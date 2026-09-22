"""Historical confirmed-bar translation of hl gate.pine; TV parity pending."""
import numpy as np
import pandas as pd


def structure(bars, left=5, right=5):
    if left < 1 or right < 1:
        raise ValueError("HL pivot lengths must be positive")
    previous_high = previous_low = support = resistance = np.nan
    trend = latest = sequence = 0
    rows = []
    for i in range(len(bars)):
        ph = pl = np.nan
        if i >= left + right:
            j = i - right
            high = bars.high.iloc[j]
            low = bars.low.iloc[j]
            # The rightmost equal extreme is selected. Never publish at pivot j.
            if high >= bars.high.iloc[j-left:j].max() and high > bars.high.iloc[j+1:i+1].max():
                ph = high
            if low <= bars.low.iloc[j-left:j].min() and low < bars.low.iloc[j+1:i+1].min():
                pl = low
        hh, lh = ph > previous_high, ph < previous_high
        hl, ll = pl > previous_low, pl < previous_low
        if bars.close.iloc[i] > resistance:
            trend = 1
        elif bars.close.iloc[i] < support:
            trend = -1
        if hh or lh:
            resistance = ph
        if hl or ll:
            support = pl
        if not np.isnan(ph):
            previous_high = ph
        if not np.isnan(pl):
            previous_low = pl
        event = 1 if hh else 2 if hl else -2 if lh else -1 if ll else 0
        if event:
            sequence += 1
            latest = event
        rows.append((trend, support, resistance, event, latest, sequence))
    return pd.DataFrame(rows, index=bars.index,
        columns=["trend", "support", "resistance", "event", "latest", "sequence"])


def hl_gate(bars, *, left=5, right=5, mtf_minutes=15, htf_minutes=60,
            activation="HTF HL", confirmation="Moderate", close_source="HTF",
            close_mode="Any Next Signal"):
    if len(bars) < 2 or not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError("HL requires regular timezone-aware chart bars")
    step = bars.index[1] - bars.index[0]
    if not (bars.index.to_series().diff().dropna() == step).all():
        raise ValueError("HL chart bars must be contiguous")
    valid = step < pd.Timedelta(minutes=mtf_minutes) < pd.Timedelta(minutes=htf_minutes)
    if not valid:
        return pd.DataFrame({"gate_output": 0, "status": 4, "hierarchy_valid": False}, index=bars.index)
    if activation not in {"HTF HL", "MTF HL", "HTF + MTF HL Confluence"} or confirmation not in {"Strict", "Moderate", "HTF Only"}:
        raise ValueError("Invalid HL activation/confirmation")
    if close_source not in {"HTF", "MTF"} or close_mode not in {"Any Next Signal", "Bearish Structure", "LL Only", "HH Only"}:
        raise ValueError("Invalid HL close mode")
    def requested(minutes):
        interval = pd.Timedelta(minutes=minutes)
        if interval % step or not bars.index.equals(bars.index.floor(step)):
            raise ValueError("HL requested TF requires finer aligned market data")
        candles = bars.resample(f"{minutes}min", origin="start_day", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"})
        counts = bars.close.resample(f"{minutes}min", origin="start_day").count()
        # Incomplete leading aggregate must not seed pivots as a full HTF bar.
        if len(candles) and counts.iloc[0] != interval // step:
            candles = candles.iloc[1:]
        # Keep trailing partial bucket: shift(1) exposes the *preceding* full
        # bucket at its first chart bar. Dropping it would add a whole TF delay.
        return structure(candles, left, right).shift(1).reindex(bars.index, method="ffill")
    m, h = requested(mtf_minutes), requested(htf_minutes)
    m = m.fillna(0)
    h = h.fillna(0)
    seen_m = seen_h = None
    opened = False
    rows = []
    for i in range(len(bars)):
        ms, hs = m.sequence.iloc[i], h.sequence.iloc[i]
        me, he = m.event.iloc[i], h.event.iloc[i]
        new_m = seen_m is not None and ms > seen_m and me != 0
        new_h = seen_h is not None and hs > seen_h and he != 0
        seen_m, seen_h = ms, hs
        mtf_pass = m.trend.iloc[i] == 1 if confirmation == "Strict" else m.trend.iloc[i] != -1 if confirmation == "Moderate" else True
        activate = (new_h and he == 2 and mtf_pass) if activation == "HTF HL" else (new_m and me == 2) if activation == "MTF HL" else (new_m and me == 2 and new_h and he == 2)
        event, new_event = (he, new_h) if close_source == "HTF" else (me, new_m)
        matches = event != 0 if close_mode == "Any Next Signal" else event in {-2, -1} if close_mode == "Bearish Structure" else event == -1 if close_mode == "LL Only" else event == 1
        if not opened:
            opened = bool(activate)
            status = 2 if opened else 0
        elif new_event and matches:
            opened, status = False, 3
        else:
            status = 1
        rows.append((int(opened), status, True))
    return pd.DataFrame(rows, index=bars.index, columns=["gate_output", "status", "hierarchy_valid"])
