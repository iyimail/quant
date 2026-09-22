"""Bias historical confirmed-bar engine. Requires explicitly identified venue feeds."""
import numpy as np
import pandas as pd
from pine_math import aggregate, atr, confirmed


def bias_gate(chart, p, feeds, chart_symbol):
    """feeds keys are exact TradingView ids, e.g. BINANCE:WCTUSDT or ...USDT.P.

    Missing requested datasets are errors. Present NaN observations retain Pine's
    valid-exchange semantics rather than inventing price/volume observations.
    """
    index=chart.index
    def feed(symbol):
        if symbol not in feeds:
            raise ValueError(f"BIAS missing market feed: {symbol}")
        b=feeds[symbol]
        if not isinstance(b.index,pd.DatetimeIndex) or b.index.tz is None or not b.index.is_unique or not b.index.is_monotonic_increasing:
            raise ValueError(f"Invalid BIAS feed index: {symbol}")
        return b
    def calendar(b,kind):
        idx=b.index
        if kind=="day": labels=idx.floor("D")
        elif kind=="week": labels=(idx-pd.to_timedelta(idx.weekday,unit="D")).floor("D")
        else: labels=pd.to_datetime(idx.strftime("%Y-%m-01"),utc=True)
        out=b.groupby(labels).agg({"close":"last","volume":"sum"})
        # A partial leading period is not a known complete prior period.
        if len(out) and b.index[0]!=out.index[0]: out=out.iloc[1:]
        return out
    pair=chart_symbol.split(":")[-1].removesuffix(".P")+(".P" if p["bias_market"]=="Perp" else "")
    main=f"{p['bias_main']}:{pair}"
    main_bars=feed(main)
    day=pd.Series(index.floor("D"),index=index)
    day_open=chart.open.groupby(day).transform("first")
    day_high=chart.high.groupby(day).cummax()
    prior_high=chart.high.groupby(day).cummax().groupby(day).shift(1)
    today=(chart.close/day_open-1)*100
    pullback=(day_high-chart.close)/day_high*100
    previous_total=pd.Series(0.,index=index)
    today_total=pd.Series(0.,index=index)
    valid_count=pd.Series(0,index=index)
    for exchange in (p["bias_main"],p["bias_ex2"],p["bias_ex3"],p["bias_ex4"]):
        if exchange=="NONE": continue
        raw=feed(f"{exchange}:{pair}")
        # Exactly chart-sized venue bars; never forward-fill intraday volume.
        chart_minutes=int((index[1]-index[0])/pd.Timedelta(minutes=1))
        aligned=aggregate(raw,chart_minutes)
        b=aligned.reindex(index)
        missing=index.difference(aligned.index)
        if len(missing): raise ValueError(f"BIAS {exchange}:{pair}: missing {len(missing)} chart timestamps")
        d=calendar(raw,"day")
        prev=(d.volume*d.close).shift(1).reindex(index,method="ffill")
        notional=b.volume*b.close
        valid=notional.notna() & prev.notna()
        cumulative=notional.fillna(0).groupby(day).cumsum()
        today_total+=cumulative.where(valid,0)
        previous_total+=prev.where(valid,0)
        valid_count+=valid.astype(int)
    checks={
        "liquidity": (~pd.Series(p["bias_liquidity"],index=index)) | (previous_total>=p["bias_min_liquidity"]),
        "valid_ex": (not p["bias_valid_ex"]) | (valid_count>=p["bias_min_ex"]),
        "participation":(not p["bias_participation"]) | (today_total/previous_total.replace(0,np.nan)*100>=p["bias_min_participation"]),
        "today":(not p["bias_today"]) | (today>=p["bias_min_today"]),
        "hold":(not p["bias_hold_high"]) | (pullback<=p["bias_max_pullback"]),
        "breakout":(not p["bias_breakout"]) | ((chart.high>prior_high)&(chart.close>=prior_high)),
    }
    for kind in ("day","week","month"):
        c=calendar(main_bars,kind).close
        change=(c.shift(1)/c.shift(2)-1)*100
        checks[kind]=(not p[f"bias_{kind}"]) | (change.reindex(index,method="ffill")>=p[f"bias_min_{kind}"])
    if p["bias_btc_zone"]:
        n=p["bias_btc_minutes"]
        btc=confirmed(aggregate(feed(p["bias_btc_symbol"]),n),index,n)
        valid=p["bias_zone_low"]>0 and p["bias_zone_high"]>p["bias_zone_low"]
        inside=(btc.close>=p["bias_zone_low"]) & (btc.close<=p["bias_zone_high"])
        touches=(btc.high>=p["bias_zone_low"]) & (btc.low<=p["bias_zone_high"])
        checks["btc_zone"]=~(valid & (inside if p["bias_zone_close"] else touches))
    if p["bias_pair"]:
        n=p["bias_pair_minutes"]
        a=aggregate(feed(p["bias_pair_a"]),n)
        b=aggregate(chart if p["bias_pair_chart_b"] else feed(p["bias_pair_b"]),n)
        def values(frame):
            current=confirmed(frame.close,index,n)
            prior=frame.close.shift(1+p["bias_pair_lookback"]).reindex(index,method="ffill")
            av=confirmed(atr(frame,p["bias_pair_atr_length"])/frame.close*100,index,n)
            return current,prior,av
        a1,a2,aa=values(a); b1,b2,ba=values(b)
        if p["bias_pair_direction"]=="A / B":
            ratio,old=a1/b1,a2/b2
            move=(a1/a2-b1/b2)*100
        else:
            ratio,old=b1/a1,b2/a2
            move=(b1/b2-a1/a2)*100
        pct=(ratio/old-1)*100
        score=move/np.sqrt(aa**2+ba**2).replace(0,np.nan)
        checks["pair"]=(pct>=p["bias_pair_pct"]) if p["bias_pair_measure"]=="Percentage Change" else (score>=p["bias_pair_atr"])
    trace=pd.DataFrame(checks,index=index).fillna(False)
    trace["gate_output"]=trace.all(axis=1).astype(int)
    trace["valid_exchanges"]=valid_count
    trace["previous_day_notional"]=previous_total
    trace["today_notional"]=today_total
    return trace
