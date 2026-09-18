"""Degraded-mode extractor.

Used ONLY when every configured language-model provider is unreachable or keeps
returning unusable output. It recognises the common, explicit phrasings (clock
times, %, kWh, obvious keywords) and returns an intent in the same format as the
model, so it goes through the same normalizer and guardrails. Anything it is not
confident about becomes no_op. Can be disabled with ENABLE_DEGRADED_FALLBACK=false.
"""
import re
from typing import List, Optional, Tuple

from app.schemas.directives import (MAX_GRID, MIN_RESERVE, NO_CHARGE, NO_DISCHARGE, NO_OP,
                                    SOLAR_REDUCTION)

_MER = r"(a\.?m\.?|p\.?m\.?)"
_RANGE = re.compile(rf"\b(\d{{1,2}})(?::(\d{{2}}))?\s*(?:-|–|to|until|till|and)\s*(\d{{1,2}})(?::(\d{{2}}))?\s*{_MER}", re.I)
_TOKEN = re.compile(rf"\b(\d{{1,2}}):(\d{{2}})\s*{_MER}?|\b(\d{{1,2}})\s*{_MER}|\b(noon|midday|midnight)\b", re.I)

_OTHER_DAY = re.compile(r"\b(tomorrow|yesterday|next\s+(week|month|year|semester|term|\w+day)|last\s+(week|month|year)"
                        r"|previous\s+(week|month))\b", re.I)
_NEGATIVE = re.compile(r"\b(not|no|disabled?|unavailable|prohibited|forbidden|locked|cannot|can't|blocked|suspend\w*"
                       r"|paus\w*|isolat\w*|offline|avoid|halt\w*|stop\w*|out of service|down)\b", re.I)
_RESERVE = re.compile(r"\b(reserve|at least|minimum|keep|remain|hold|backup|stay above|no less than"
                      r"|(go|drop|fall|dip) below)\b", re.I)
_DISCHARGE_WORDS = re.compile(r"discharg|battery (output|supply)|supply (the )?(campus )?load", re.I)
# A time is mentioned in a form we can't parse (spelled-out numbers, day parts...): don't guess a window.
_UNPARSED_TIME = re.compile(r"\b(from|until|till|between|after|before|through)\b|o'clock|morning|afternoon|evening"
                            r"|night|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", re.I)
_GRID = re.compile(r"\b(grid|import|feeder|transformer|substation|intake|utility|purchase)\b", re.I)
_SOLAR = re.compile(r"\b(solar|pv|photovoltaic|panels?|rooftop)\b", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
_KWH = re.compile(r"(\d+(?:\.\d+)?)\s*kwh", re.I)
_PCT_REDUCTION = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:reduction|drop|decrease|less|lower|cut|loss)"
                            r"|(?:reduc\w*|drop\w*|cut|decreas\w*|down|lower\w*)\s+by\s+(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
_WORD_FRACTIONS = [("three-quarters", 75), ("three quarters", 75), ("half", 50), ("quarter", 25),
                   ("one-fifth", 20), ("one fifth", 20), ("a fifth", 20), ("one-third", 100 / 3),
                   ("a third", 100 / 3), ("one-tenth", 10), ("a tenth", 10)]


def _to24(h: int, mer: Optional[str]) -> int:
    if mer:
        pm = mer.lower().startswith("p")
        if pm and h < 12:
            return h + 12
        if not pm and h == 12:
            return 0
    return h


def _times(text: str) -> List[Tuple[int, int]]:
    """(position, hour) for each time mention, hour on 0-24 scale."""
    out = []
    for m in _TOKEN.finditer(text):
        if m.group(1):
            h = _to24(int(m.group(1)), m.group(3))
        elif m.group(4):
            h = _to24(int(m.group(4)), m.group(5))
        else:
            word = m.group(6).lower()
            h = 0 if word == "midnight" else 12
        if 0 <= h <= 24:
            out.append((m.start(), h))
    return out


def _windows(text: str) -> Optional[list]:
    m = _RANGE.search(text)
    if m:
        mer = m.group(5)
        end = _to24(int(m.group(3)), mer)
        start = _to24(int(m.group(1)), mer)
        if start > end:  # "11-2 PM" means 11 AM to 2 PM
            start = int(m.group(1)) % 12
        return [{"start_hour": start, "end_hour": end}]
    times = _times(text)
    if len(times) >= 2:
        start, end = times[0][1], times[1][1]
        if end == 0:
            end = 24
        return [{"start_hour": start % 24, "end_hour": end}]
    if len(times) == 1:
        pos, h = times[0]
        before = text[max(0, pos - 25):pos].lower()
        if re.search(r"\b(before|until|till|up to)\s*$", before):
            return [{"start_hour": 0, "end_hour": h if h else 24}]
        if re.search(r"\b(at|during)\s*(the)?\s*$", before):
            return [{"start_hour": h % 24, "end_hour": h % 24 + 1}]
        return [{"start_hour": h % 24, "end_hour": 24}]
    return []


def _solar_value(text: str) -> Optional[dict]:
    m = _PCT_REDUCTION.search(text)
    if m:
        return {"amount": float(m.group(1) or m.group(2)), "unit": "percent_reduction"}
    m = _PCT.search(text)
    if m:
        return {"amount": float(m.group(1)), "unit": "percent_remaining"}
    low = text.lower()
    for word, pct in _WORD_FRACTIONS:
        if word in low:
            return {"amount": pct, "unit": "percent_remaining"}
    if re.search(r"\b(no solar|zero solar|completely (shaded|offline)|fully (shaded|offline))\b", low):
        return {"amount": 0, "unit": "percent_remaining"}
    return None


def extract(text: str) -> dict:
    t = text.strip()
    low = t.lower()
    noop = {"relevant": False, "directive_type": NO_OP, "windows": [], "value": None,
            "explanation": "No supported directive for today's schedule was recognised (fallback interpreter)."}
    if _OTHER_DAY.search(t) and "today" not in low:
        return noop

    dtype, value = None, None
    if "discharg" in low and _NEGATIVE.search(t):
        dtype = NO_DISCHARGE
    elif re.search(r"\bcharg", low) and _NEGATIVE.search(t) and not _RESERVE.search(t):
        dtype = NO_CHARGE
    elif (_RESERVE.search(t) and re.search(r"\b(battery|reserve|stored|storage)\b", low)
          and (_KWH.search(t) or _PCT.search(t) or "half" in low)):
        dtype = MIN_RESERVE
        if _KWH.search(t):
            value = {"amount": float(_KWH.search(t).group(1)), "unit": "kwh"}
        elif _PCT.search(t):
            value = {"amount": float(_PCT.search(t).group(1)), "unit": "percent_of_capacity"}
        else:
            value = {"amount": 50, "unit": "percent_of_capacity"}
    elif _GRID.search(t) and _KWH.search(t):
        dtype = MAX_GRID
        value = {"amount": float(_KWH.search(t).group(1)), "unit": "kwh"}
    elif _SOLAR.search(t):
        value = _solar_value(t)
        if value is not None:
            dtype = SOLAR_REDUCTION

    if dtype is None:
        return noop
    return {"relevant": True, "directive_type": dtype, "windows": _windows(t), "value": value,
            "explanation": f"Recognised as {dtype} (fallback interpreter)."}
