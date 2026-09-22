"""Source MTF indicator exits, each using its own Pine timeframe input."""
import pandas as pd
from pine_math import aggregate,rma
from master_models import most_line,t3_line


def exit_conditions(chart,calculation,p):
    index=chart.index
    step=index[1]-index[0]
    def mapped(series,minutes):
        copy=series.copy()
        if p["exit_lookahead"]=="OFF":
            copy.index=copy.index+pd.Timedelta(minutes=minutes)-step
        return copy.reindex(index,method="ffill")
    most_minutes=p["exit_most_minutes"]
    most_bars=aggregate(calculation,most_minutes)
    most=mapped(most_line(most_bars.close,p["exit_most_length"],p["exit_most_percent"]),most_minutes)
    t3_minutes=p["exit_t3_minutes"]
    t3_bars=aggregate(calculation,t3_minutes)
    t3=mapped(t3_line(t3_bars.close,p["exit_t3_length"],p["exit_t3_factor"]),t3_minutes)
    def under(line,mode):
        result=chart.close<line
        if mode=="Cross Under": result &= chart.close.shift(1)>=line.shift(1)
        return result.fillna(False)
    n=p["exit_rsi_minutes"]
    c=aggregate(calculation,n).close
    delta=c.diff()
    gain=rma(delta.clip(lower=0),p["exit_rsi_length"])
    loss=rma(-delta.clip(upper=0),p["exit_rsi_length"])
    rsi=(100-100/(1+gain/loss)).mask(loss==0,100).mask((loss!=0)&(gain==0),0)
    avg=rsi.rolling(p["exit_rsi_sma_length"]).mean()
    return {"NONE":None,"MOST":under(most,p["exit_most_mode"]),"TILLSON":under(t3,p["exit_t3_mode"]),
        "RSI_SMA":(mapped(rsi.shift(1),n)<mapped(avg.shift(1),n)).fillna(False)}
