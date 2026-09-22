"""Explicit local data bindings; no spot/perpetual or venue substitution."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd


def load_feed_manifest(path):
    if not path:
        return {}, []
    manifest=Path(path).resolve()
    spec=json.loads(manifest.read_text(encoding="utf-8-sig"))
    feeds={}; evidence=[]
    for identity,entry in spec.get("feeds",{}).items():
        if ":" not in identity:
            raise ValueError("Feed keys must be EXCHANGE:SYMBOL (perpetual suffix .P)")
        file=(manifest.parent/entry["path"]).resolve()
        digest=hashlib.sha256(file.read_bytes()).hexdigest()
        if entry.get("sha256") and entry["sha256"]!=digest:
            raise ValueError(f"Feed changed: {identity}")
        raw=pd.read_csv(file)
        if "time" not in raw:
            raise ValueError(f"{file}: ISO UTC time column required")
        raw.index=pd.to_datetime(raw.pop("time"),utc=True)
        columns=["open","high","low","close","volume"]
        raw[columns]=raw[columns].apply(pd.to_numeric,errors="raise")
        if not raw.index.is_unique or not raw.index.is_monotonic_increasing or len(raw)<2:
            raise ValueError(f"Invalid timestamp order: {identity}")
        if not np.isfinite(raw[columns].to_numpy()).all() or (raw[columns[:4]]<=0).any().any() or (raw.volume<0).any():
            raise ValueError(f"Invalid OHLCV: {identity}")
        if ((raw.high<raw[["open","close","low"]].max(axis=1)) | (raw.low>raw[["open","close","high"]].min(axis=1))).any():
            raise ValueError(f"Invalid OHLC bounds: {identity}")
        feeds[identity]=raw
        evidence.append({"symbol":identity,"path":str(file),"sha256":digest,"rows":len(raw)})
    return feeds,evidence
