"""Bar-close wiring, separate from indicator mathematics.

Inputs are plot values at the current chart bar, never pre-shifted signals.
References: master.pine 208-219; master gate.pine 392-525.
"""
from dataclasses import dataclass
import pandas as pd


def g6_pass(source: pd.Series, mode: str = "Pozitif") -> pd.Series:
    previous = source.shift(1)
    if mode == "Pozitif":
        result = previous.gt(0)
    elif mode == "Negatif":
        result = previous.lt(0)
    elif mode == "Sıfır Dışı":
        result = previous.ne(0) & previous.notna()
    else:
        raise ValueError(f"Unknown G6 mode: {mode}")
    return result.fillna(False).astype(bool)


def master_latch(index, auxiliary: dict[str, pd.Series], *,
                 core_set: pd.Series | None = None,
                 core_reset: pd.Series | None = None,
                 macro_reset: pd.Series | None = None,
                 g7_sma: pd.Series | None = None, g7_level: float = 92.,
                 g7_mode: str = "Latch On Cross", g7_reset_on_fail: bool = True,
                 g7_reset_each_day: bool = False) -> pd.DataFrame:
    """SET then RESET, with CORE retaining state when only its SET disappears.

    Each auxiliary member is an enabled gate. Disabled gates must be omitted.
    The caller supplies CORE's macro-qualified SET and explicit reset condition.
    """
    if core_set is not None and core_reset is None:
        raise ValueError("CORE requires its reset signal")
    signals = list(auxiliary.values()) + [s for s in (core_set, core_reset, macro_reset) if s is not None]
    if any(not signal.index.equals(index) or signal.isna().any() for signal in signals):
        raise ValueError("Gate state inputs must be aligned and non-missing")
    if g7_sma is not None and not g7_sma.index.equals(index):
        raise ValueError("G7 SMA must be aligned")
    if g7_mode not in {"Latch On Cross", "SMA > Level"}:
        raise ValueError("Unknown G7 mode")
    count = len(auxiliary) + int(core_set is not None) + int(g7_sma is not None)
    state = False
    g7_latched = False
    rows = []
    for i in range(len(index)):
        aux_ok = all(bool(s.iloc[i]) for s in auxiliary.values())
        passed = sum(bool(s.iloc[i]) for s in auxiliary.values())
        if g7_sma is not None:
            if g7_reset_each_day and i > 0 and index[i].date() != index[i-1].date():
                g7_latched = False
            if i > 0 and g7_sma.iloc[i] > g7_level and g7_sma.iloc[i-1] <= g7_level:
                g7_latched = True
            g7_ok = bool(g7_sma.iloc[i] > g7_level) if g7_mode == "SMA > Level" else g7_latched
            aux_ok = aux_ok and g7_ok
            passed += int(g7_ok)
        passed += int(core_set is not None and bool(core_set.iloc[i]))
        triggered = count > 0 and passed == count
        if triggered:
            state = True
        reset = (not aux_ok or
                 (core_set is None and passed < count) or
                 (core_reset is not None and bool(core_reset.iloc[i])) or
                 (macro_reset is not None and bool(macro_reset.iloc[i])))
        if reset:
            state = False
            if g7_reset_on_fail:
                g7_latched = False
        rows.append((count, passed, triggered, reset, int(state)))
    return pd.DataFrame(rows, index=index, columns=[
        "active_count", "passed_count", "set", "reset", "master_output"])


@dataclass(frozen=True)
class ExternalRouting:
    mode: str = "AND"
    use_ext1: bool = True
    use_ext2: bool = False
    master_enabled: bool = True


def strategy_external(index, ext1: pd.Series | None, ext2: pd.Series | None,
                      config: ExternalRouting = ExternalRouting()) -> pd.DataFrame:
    if config.mode not in {"AND", "OR", "ONLY_1", "ONLY_2"}:
        raise ValueError(f"Unknown EXT mode: {config.mode}")
    def consume(source, enabled):
        if not enabled:
            return pd.Series(True, index=index)
        if source is None or not source.index.equals(index):
            raise ValueError("Enabled EXT source is missing or misaligned")
        return source.shift(1).gt(0).fillna(False)
    one = consume(ext1, config.use_ext1)
    two = consume(ext2, config.use_ext2)
    combined = {"AND": one & two, "OR": one | two,
                "ONLY_1": one, "ONLY_2": two}[config.mode]
    opened = combined if config.master_enabled else pd.Series(True, index=index)
    # Source's master toggle disables entries; it does NOT mean unrestricted entry.
    return pd.DataFrame({"ext1_pass": one, "ext2_pass": two,
                         "is_ext_open": opened,
                         "entry_permission": opened & config.master_enabled,
                         "close_request": ~opened & config.master_enabled}, index=index)
