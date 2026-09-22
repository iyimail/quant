"""Computational settings shared by runner and tabs (visual-only Pine inputs omitted)."""
MASTER_DEFAULTS = {
    "core_enabled":False,"g3_enabled":False,"g4_enabled":False,"g5_enabled":False,"pmax_enabled":False,
    "master_tf_minutes":24,"g1_tf_minutes":240,"g5_tf_minutes":60,"g2_tf_minutes":60,
    "most2_length":14,"most2_percent":2.,"tillson_mode":"Price vs T3",
    "line_timing":"CAUSAL_CONFIRMED", "tillson_execution":"Confirmed HTF",
    "core_vwhma_length":14,"core_vector_length":5,"core_st_length":10,"core_st_mult":3.,
    "core_grind":True,"core_aaf":True,"core_kc_length":20,"core_kc_mult":1.5,
    "core_aaf_mode":"Standard","core_aaf_wr":.5,"core_aaf_vri":1.5,
    "core_chop":False,"core_chop_length":14,"core_zscore":True,"core_z_length":100,
    "core_z_cvd":1.2,"core_z_kc":1.9,"core_z_vol":2.5,
    "core_hysteresis":True,"core_exit_atr":1.5,
    "bb_length":12,"bb_above":True,"bb_below":False,
    "momentum_curve":50,"momentum_slope":5,"momentum_threshold":1.5,
    "pmax_tf_minutes":0,"pmax_source":"hl2","pmax_atr_length":11,"pmax_atr_mult":1.9,
    "pmax_ma_type":"VAR","pmax_ma_length":9,"pmax_change_atr":True,"pmax_height":1.,
}
BIAS_DEFAULTS = {
    "bias_market":"Spot","bias_main":"BINANCE","bias_ex2":"BYBIT","bias_ex3":"OKX","bias_ex4":"NONE",
    "bias_liquidity":True,"bias_min_liquidity":5000000.,"bias_valid_ex":False,"bias_min_ex":1,
    "bias_participation":True,"bias_min_participation":15.,"bias_today":True,"bias_min_today":2.,
    "bias_hold_high":True,"bias_max_pullback":1.5,"bias_breakout":True,
    "bias_day":True,"bias_min_day":1.,"bias_week":False,"bias_min_week":2.,
    "bias_month":True,"bias_min_month":3.,"bias_btc_zone":False,"bias_btc_symbol":"BINANCE:BTCUSDT",
    "bias_btc_minutes":240,"bias_zone_low":0.,"bias_zone_high":0.,"bias_zone_close":True,
    "bias_pair":False,"bias_pair_a":"BINANCE:BTCUSDT","bias_pair_chart_b":True,"bias_pair_b":"BINANCE:ETHUSDT",
    "bias_pair_minutes":240,"bias_pair_direction":"B / A","bias_pair_measure":"Percentage Change",
    "bias_pair_pct":.5,"bias_pair_atr":1.,"bias_pair_atr_length":14,"bias_pair_lookback":1,
}
STRATEGY_DEFAULTS = {
    "strategy_master_enabled":True,"daily_enabled":False,"daily_max_entries":3,
    "strategy_sl_enabled":True,"strategy_tp_enabled":False,"strategy_tp_pct":5.,"strategy_trail_enabled":True,
    "exit_model":"LEGACY_REF00","exit_most_minutes":60,"exit_most_length":14,"exit_most_percent":2.,
    "exit_most_mode":"Candle Close Under","exit_t3_minutes":60,"exit_t3_length":8,"exit_t3_factor":.7,"exit_t3_mode":"Candle Close Under",
    "exit_rsi_minutes":1440,"exit_rsi_length":40,"exit_rsi_sma_length":50,"exit_lookahead":"OFF",
    "wr_source":"FOLLOW_COMBO","wr_mode":"FOLLOW_COMBO",
    "exit_use_most":False,"exit_use_rsi":False,"exit_use_t3":False,
}
DEFAULTS = {**MASTER_DEFAULTS,**BIAS_DEFAULTS,**STRATEGY_DEFAULTS}
INTS = {k for k,v in DEFAULTS.items() if type(v) is int}
FLOATS = {k for k,v in DEFAULTS.items() if type(v) is float}
CHOICES = {
    "exit_model":("LEGACY_REF00","SOURCE_MTF"),"exit_lookahead":("OFF","ON"),
    "exit_most_mode":("Cross Under","Candle Close Under"),"exit_t3_mode":("Cross Under","Candle Close Under"),
    "wr_source":("FOLLOW_COMBO","SHADOW","REAL_CLOSEDTRADES"),"wr_mode":("FOLLOW_COMBO","OVERALL","ROLLING","WEIGHTED"),
    "tillson_mode":("Price vs T3","T3 Slope"),"tillson_execution":("Confirmed HTF","Live Cross"),
    "line_timing":("CAUSAL_CONFIRMED","SOURCE_HISTORICAL_LOOKAHEAD"),
    "core_aaf_mode":("Stable","Standard","Volatile","Custom"),
    "pmax_source":("open","high","low","close","hl2","hlc3","ohlc4"),
    "pmax_ma_type":("SMA","EMA","WMA","TMA","VAR","WWMA","ZLEMA","TSF"),
    "bias_market":("Spot","Perp"),
    "bias_main":("BINANCE","BYBIT","OKX","BITGET","GATEIO","GATE"),
    "bias_pair_direction":("A / B","B / A"),"bias_pair_measure":("Percentage Change","ATR Normalized"),
}
for key in ("bias_ex2","bias_ex3","bias_ex4"):
    CHOICES[key] = ("NONE",)+CHOICES["bias_main"]
TEXT_FIELDS = {"bias_btc_symbol","bias_pair_a","bias_pair_b"}


def validate_component(key,value):
    """Source bounds plus explicit mathematical-domain guards."""
    import math
    if key in INTS and (type(value) not in (int,float) or not math.isfinite(value) or int(value)!=value or value<(0 if key=="pmax_tf_minutes" else 1)):
        raise ValueError(f"{key}: positive integer required")
    if key in FLOATS and (type(value) not in (int,float) or not math.isfinite(value)):
        raise ValueError(f"{key}: finite number required")
    if key in {"core_vwhma_length","core_chop_length","momentum_curve"} and value<2:
        raise ValueError(f"{key}: minimum 2 required")
    if key=="bb_length" and not 5<=value<=50:
        raise ValueError("bb_length: source range is 5..50")
    if key=="bias_min_ex" and not 1<=value<=4:
        raise ValueError("bias_min_ex: source range is 1..4")
    if key in {"bias_zone_low","bias_zone_high"} and value<0:
        raise ValueError(f"{key}: nonnegative required")
    if key=="most2_percent" and not 0<=value<100:
        raise ValueError("most2_percent: range is 0..100 exclusive")
    if key in {"strategy_tp_pct","exit_t3_factor"} and value<=0:
        raise ValueError(f"{key}: positive value required")
