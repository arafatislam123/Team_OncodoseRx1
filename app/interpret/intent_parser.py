"""Parses raw model text into per-note intent dicts. Nothing here is trusted yet."""
import json
import re
from typing import Any, Dict, List, Tuple

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _extract_json(text: str) -> Any:
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except ValueError:
        pass
    # Fall back to the outermost {...} block (models sometimes add a sentence around it).
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("no JSON object found")


def parse_intents(text: str, expected: List[int]) -> Tuple[Dict[int, dict], List[str]]:
    """Returns ({note_index: intent}, errors). Missing notes are reported as errors."""
    errors: List[str] = []
    try:
        data = _extract_json(text)
    except ValueError:
        return {}, ["response was not valid JSON"]

    items = data.get("notes") if isinstance(data, dict) else data
    if isinstance(data, dict) and items is None:
        # tolerate {"interpretations": [...]} or a single-key wrapper
        lists = [v for v in data.values() if isinstance(v, list)]
        items = lists[0] if len(lists) == 1 else None
    if not isinstance(items, list):
        return {}, ['expected an object with a "notes" list']

    out: Dict[int, dict] = {}
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"notes[{pos}] is not an object")
            continue
        idx = item.get("note_index")
        if isinstance(idx, float) and idx.is_integer():
            idx = int(idx)
        if not isinstance(idx, int) or isinstance(idx, bool) or idx not in expected:
            errors.append(f"notes[{pos}] has invalid note_index {idx!r}")
            continue
        if idx in out:
            errors.append(f"note_index {idx} appears more than once")
            continue
        out[idx] = item
    for idx in expected:
        if idx not in out:
            errors.append(f"note_index {idx} is missing")
    return out, errors
