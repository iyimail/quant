"""Shared UI/runner contract. LEGACY preserves previously saved research jobs."""
ROUTING_CHOICES = {
    "g7_mode": ("Latch On Cross", "SMA > Level"),
    "g7_source": ("open", "high", "low", "close", "hl2", "hlc3", "ohlc4"),
    "hl_activation": ("HTF HL", "MTF HL", "HTF + MTF HL Confluence"),
    "hl_confirmation": ("Strict", "Moderate", "HTF Only"),
    "hl_close_source": ("HTF", "MTF"),
    "hl_close_mode": ("Any Next Signal", "Bearish Structure", "LL Only", "HH Only"),
    "routing_mode": ("LEGACY", "EXPLICIT"),
    "ext_mode": ("AND", "OR", "ONLY_1", "ONLY_2"),
    "ext1_source": ("MASTER_GATE", "VWAP", "BIAS", "HL", "close"),
    "ext2_source": ("MASTER_GATE", "VWAP", "BIAS", "HL", "close"),
    "g6_source": ("VWAP", "BIAS", "HL", "close"),
    "g6_mode": ("Pozitif", "Negatif", "Sıfır Dışı"),
    "vwap_mode": ("Daily Only", "Weekly Only", "D+W Confirm"),
    "vwap_daily_source": ("open", "high", "low", "close", "hl2", "hlc3", "ohlc4"),
    "vwap_weekly_source": ("open", "high", "low", "close", "hl2", "hlc3", "ohlc4"),
}
ROUTING_DEFAULTS = {
    "g7_enabled": False, "g7_mode": "Latch On Cross", "g7_source": "close",
    "g7_level": 92.0, "g7_length": 5, "g7_reset_on_fail": True, "g7_reset_each_day": False,
    "macro_enabled": False, "macro_minutes": 240, "macro_length": 200,
    # The local decision chart is 30m.  The Pine default 15m/60m is valid
    # only on a chart finer than 15m, so use the first valid hierarchy here.
    "hl_left": 5, "hl_right": 5, "hl_mtf_minutes": 60, "hl_htf_minutes": 240,
    "hl_activation": "HTF HL", "hl_confirmation": "Moderate",
    "hl_close_source": "HTF", "hl_close_mode": "Any Next Signal",
    "routing_mode": "LEGACY", "ext_mode": "AND",
    "ext1_source": "MASTER_GATE", "ext2_source": "close",
    "ext1_enabled": True, "ext2_enabled": False,
    "g6_enabled": False, "g6_source": "VWAP", "g6_mode": "Pozitif",
    "vwap_use_daily": True, "vwap_use_weekly": True,
    "vwap_mode": "D+W Confirm", "vwap_daily_source": "hlc3", "vwap_weekly_source": "hlc3",
}
ROUTING_INTS = {"hl_left", "hl_right", "hl_mtf_minutes", "hl_htf_minutes", "g7_length", "macro_minutes", "macro_length"}
from component_settings import DEFAULTS, CHOICES, INTS
ROUTING_DEFAULTS.update(DEFAULTS)
ROUTING_CHOICES.update(CHOICES)
ROUTING_INTS.update(INTS)
