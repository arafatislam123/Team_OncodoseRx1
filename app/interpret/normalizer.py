"""Deterministic conversion of a model intent into a directive.

- windows [start, end) on a 24-hour clock -> sorted unique hour list (wraps past midnight)
- value + unit -> factor / kWh
The result still goes through guardrails.check before it is used.
"""
import math
from typing import Any, List, Optional, Tuple

from app.schemas.directives import (DIRECTIVE_TYPES, MAX_GRID, MIN_RESERVE, NO_CHARGE,
                                    NO_DISCHARGE, NO_OP, SOLAR_REDUCTION, Directive)

ALL_HOURS = list(range(24))
MAX_EXPLANATION = 300


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str):
        try:
            f = float(v.strip().rstrip("%").replace("kWh", "").replace("kwh", "").strip())
            return f if math.isfinite(f) else None
        except ValueError:
            return None
    return None


def expand_windows(windows: Any, hours: Any = None) -> Tuple[List[int], List[str]]:
    errors: List[str] = []
    result = set()
    if not windows and isinstance(hours, list) and hours:
        # Model gave explicit hours instead of windows; accept them if they are clean.
        for h in hours:
            hi = _as_int(h)
            if hi is None or not 0 <= hi <= 23:
                errors.append(f"invalid hour {h!r}")
            else:
                result.add(hi)
        return sorted(result), errors
    if not windows:
        return list(ALL_HOURS), errors
    if not isinstance(windows, list):
        return [], ["windows must be a list"]
    for w in windows:
        if not isinstance(w, dict):
            errors.append("each window must be an object")
            continue
        s, e = _as_int(w.get("start_hour")), _as_int(w.get("end_hour"))
        if s is None or e is None or not 0 <= s <= 24 or not 0 <= e <= 24:
            errors.append(f"invalid window {w!r} (hours must be integers 0-24)")
            continue
        s %= 24
        if e == s:
            # "from 6 PM to 6 PM" is meaningless; a zero-length window is most likely a single hour.
            result.add(s)
        elif e > s:
            result.update(range(s, e))
        else:  # crosses midnight
            result.update(range(s, 24))
            result.update(range(0, e))
    return sorted(result), errors


def _clean_explanation(text: Any, fallback: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return fallback
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return text[:MAX_EXPLANATION]


def to_directive(note_index: int, intent: dict, capacity: float) -> Tuple[Optional[Directive], List[str]]:
    errors: List[str] = []
    dtype = intent.get("directive_type")
    if isinstance(dtype, str):
        dtype = dtype.strip().lower()
    relevant = intent.get("relevant")

    if dtype not in DIRECTIVE_TYPES:
        return None, [f"note {note_index}: unsupported directive_type {dtype!r}"]

    # relevant=false with a real type is also treated as no_op: the model said it does not apply.
    if dtype == NO_OP or relevant is False:
        expl = _clean_explanation(intent.get("explanation"),
                                  "This note does not affect today's energy schedule.")
        return Directive(note_index=note_index, directive_type=NO_OP, explanation=expl), []

    hours, herr = expand_windows(intent.get("windows"), intent.get("hours"))
    errors += [f"note {note_index}: {e}" for e in herr]

    value: Optional[float] = None
    raw = intent.get("value")
    if dtype in (SOLAR_REDUCTION, MIN_RESERVE, MAX_GRID):
        if not isinstance(raw, dict):
            errors.append(f"note {note_index}: {dtype} needs value {{amount, unit}}")
        else:
            amount = _as_float(raw.get("amount"))
            unit = str(raw.get("unit", "")).strip().lower()
            if amount is None:
                errors.append(f"note {note_index}: value.amount must be a finite number")
            elif dtype == SOLAR_REDUCTION:
                conv = {
                    "percent_remaining": lambda a: a / 100.0,
                    "fraction_remaining": lambda a: a,
                    "percent_reduction": lambda a: 1.0 - a / 100.0,
                    "fraction_reduction": lambda a: 1.0 - a,
                }
                if unit not in conv:
                    errors.append(f"note {note_index}: solar unit must be one of {sorted(conv)}")
                else:
                    value = conv[unit](amount)
            elif dtype == MIN_RESERVE:
                if unit == "kwh":
                    value = amount
                elif unit == "percent_of_capacity":
                    value = amount / 100.0 * capacity
                elif unit == "fraction_of_capacity":
                    value = amount * capacity
                else:
                    errors.append(f"note {note_index}: reserve unit must be kwh or percent_of_capacity")
            elif dtype == MAX_GRID:
                if unit in ("kwh", "kwh_per_hour", ""):
                    value = amount
                else:
                    errors.append(f"note {note_index}: grid cap unit must be kwh")
        if value is not None:
            value = round(value, 4)

    if errors:
        return None, errors
    fallback_expl = {
        SOLAR_REDUCTION: "Usable solar is reduced during the stated hours.",
        MIN_RESERVE: "A minimum battery reserve is required during the stated hours.",
        NO_CHARGE: "Battery charging is unavailable during the stated hours.",
        NO_DISCHARGE: "Battery discharging is unavailable during the stated hours.",
        MAX_GRID: "Grid import is capped during the stated hours.",
    }[dtype]
    return Directive(note_index=note_index, directive_type=dtype, hours=hours, value=value,
                     explanation=_clean_explanation(intent.get("explanation"), fallback_expl)), []
