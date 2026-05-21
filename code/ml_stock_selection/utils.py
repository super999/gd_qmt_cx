from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

import numpy as np
import pandas as pd


def print_title(title: str) -> None:
    print("")
    print("=" * 80)
    print(title)
    print("=" * 80)


def normalize_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y%m%d")
        except Exception:
            pass
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def date_int(value: Any, open_date_special_values: set[str]) -> Optional[int]:
    date = normalize_date(value)
    if not date or date in open_date_special_values:
        return None
    try:
        datetime.strptime(date, "%Y%m%d")
        number = int(date)
    except Exception:
        return None
    if number < 19000101:
        return None
    return number


def chunked(items: Sequence[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def is_st_name(name: str) -> bool:
    upper = str(name or "").upper()
    return "ST" in upper or "退" in upper


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)
