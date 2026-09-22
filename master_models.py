"""Master Gate CORE/G3/G4/G8 calculations before external consumer delays."""
import numpy as np
import pandas as pd
from pine_math import atr, tr, ema, wma, linreg, supertrend
from gate_models import price_source


def most_line(close,length,percent):
    average=ema(close,length)
    previous=0.; previous_ema=np.nan; values=[]
    for value in average:
        stop=value*percent/100
        current=max(previous,value-stop) if value>previous and previous_ema>previous else min(previous,value+stop) if value<previous and previous_ema<previous else value-stop if value>previous else value+stop
        values.append(current)
        previous,previous_ema=current,value
    return pd.Series(values,index=close.index)


def t3_line(close,length,factor):
    e=close
    values=[]
    for _ in range(6):
        e=ema(e,length)
        values.append(e)
    return -factor**3*values[5]+(3*factor**2+3*factor**3)*values[4]+(-6*factor**2-3*factor-3*factor**3)*values[3]+(1+3*factor+factor**3+3*factor**2)*values[2]


def core_matrix(b, p):
    c, v = b.close, b.volume
    def vwma(s, n):
        return (s*v).rolling(n).sum()/v.rolling(n).sum().replace(0, np.nan)
    n = p["core_vwhma_length"]
    fast = vwma(2*vwma(c, n//2)-vwma(c, n), int(np.sqrt(n)))
    slope = (fast-fast.shift(1).fillna(fast)).rolling(3).mean()
    bull = supertrend(b, p["core_st_length"], p["core_st_mult"])
    kc_mult = p["core_kc_mult"] - (.2 if p["core_aaf_mode"] == "Volatile" else 0)
    upper = ema(c, p["core_kc_length"])+atr(b,14)*kc_mult
    vol_average = v.rolling(20).mean()
    ratio = (v/vol_average).where(vol_average>0, 0.)
    net = v*np.sign(c-b.open)
    volume_ok = (v > vol_average*(.65 if p["core_grind"] else 1)) & (net>0)
    recent = volume_ok.rolling(p["core_vector_length"]+1, min_periods=1).max().astype(bool)
    wr = ((b.high-c)/(b.high-b.low)).where(b.high!=b.low,0)
    thresholds = {"Stable":(.4,1.2), "Standard":(.5,1.5), "Volatile":(.6,2.)}
    wr_lim, vr_lim = thresholds.get(p["core_aaf_mode"], (p["core_aaf_wr"],p["core_aaf_vri"]))
    absorbed = p["core_aaf"] & (wr>wr_lim) & (ratio>vr_lim) & (ratio<=vr_lim*2)
    cvd = (net.rolling(5).sum()/(vol_average*5)).where(vol_average>0,0)
    def z(s):
        length = p["core_z_length"]
        denom = (s-s.shift(1).fillna(s)).abs().rolling(length).mean()*1.5
        return ((s-s.rolling(length).mean())/denom).where(denom>0,0)
    climax = p["core_zscore"] & (z(ratio)>p["core_z_vol"])
    trap = absorbed | (p["core_zscore"] & (z(cvd)>p["core_z_cvd"]) & (z((c-upper)/upper*100)<p["core_z_kc"]))
    if p["core_grind"]:
        trap = pd.Series(False,index=b.index)
    length = p["core_chop_length"]
    chop = 100*np.log10(tr(b).rolling(length).sum()/(b.high.rolling(length).max()-b.low.rolling(length).min()))/np.log10(length)
    setting = (~(p["core_chop"] & (chop>50)) & (fast>fast.shift(p["core_vector_length"]).fillna(fast)) & bull &
        ((c>upper) | (p["core_grind"] & (c>fast))) & recent & ~trap & ~climax)
    midpoint = c.rolling(p["bb_length"]).mean()
    smooth = ema(linreg(c,p["momentum_curve"]).diff(),p["momentum_slope"])
    hist = smooth-smooth.rolling(13).mean()
    momentum = (hist>hist.shift(1).fillna(hist)) & (hist.abs()>hist.abs().rolling(20).mean()*p["momentum_threshold"])
    return pd.DataFrame({"core_set":setting, "st_bull":bull, "slope":slope,
        "exit_threshold":fast-atr(b,14)*p["core_exit_atr"],
        "g3":(p["bb_above"] & (c>midpoint)) | (p["bb_below"] & (c<midpoint)),
        "g4":momentum},index=b.index)


def pmax_matrix(b,p):
    s = price_source(b,p["pmax_source"])
    n, kind = p["pmax_ma_length"],p["pmax_ma_type"]
    if kind == "SMA": ma = s.rolling(n).mean()
    elif kind == "EMA": ma = ema(s,n)
    elif kind in {"WMA","WWMA"}: ma = wma(s,n)  # source WWMA intentionally uses WMA
    elif kind == "TMA": ma = s.rolling(int(np.ceil(n/2))).mean().rolling(n//2+1).mean()
    elif kind == "ZLEMA": ma = ema(2*s-s.shift(n//2),n)
    elif kind == "TSF": ma = 2*linreg(s,n)-linreg(s,n,1)
    elif kind == "VAR":
        delta=s.diff().fillna(0)
        up=delta.clip(lower=0).rolling(9).sum()
        down=(-delta.clip(upper=0)).rolling(9).sum()
        weight=((up-down)/(up+down)).fillna(0).abs()*2/(n+1)
        previous=0.; values=[]
        for value,w in zip(s,weight):
            previous=w*value+(1-w)*previous
            values.append(previous)
        ma=pd.Series(values,index=b.index)
    else: raise ValueError(f"Unknown PMAX MA {kind}")
    av=atr(b,p["pmax_atr_length"]) if p["pmax_change_atr"] else tr(b,False).rolling(p["pmax_atr_length"]).mean()
    previous_long=previous_short=np.nan
    direction=1; lines=[]
    for m,a in zip(ma,av):
        lo,sh=m-p["pmax_atr_mult"]*a,m+p["pmax_atr_mult"]*a
        pl=lo if pd.isna(previous_long) else previous_long
        ps=sh if pd.isna(previous_short) else previous_short
        lo=max(lo,pl) if m>pl else lo
        sh=min(sh,ps) if m<ps else sh
        direction=1 if direction==-1 and m>ps else -1 if direction==1 and m<pl else direction
        lines.append((lo if direction==1 else sh)*p["pmax_height"])
        previous_long,previous_short=lo,sh
    line=pd.Series(lines,index=b.index)
    return pd.DataFrame({"line":line,"state":ma>line},index=b.index)
