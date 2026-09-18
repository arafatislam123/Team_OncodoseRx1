"""Runs the interpretation chain for one request:

cache -> primary model -> repair -> backup model -> degraded fallback -> no_op

Every intent, whatever its source, goes through normalizer + guardrails before it
becomes a Directive. Notes are processed individually, so one bad note doesn't
throw away the good ones.
"""
import logging
import time
from typing import Dict, List, Tuple

from app.config import Settings
from app.interpret import fallback
from app.interpret.cache import IntentCache
from app.interpret.guardrails import check
from app.interpret.intent_parser import parse_intents
from app.interpret.llm_client import LLMClient, LLMError
from app.interpret.normalizer import to_directive
from app.interpret.prompt import build_messages
from app.schemas.directives import Directive, no_op

log = logging.getLogger("gridwise.interpret")

RESERVED_FOR_SOLVER_S = 2.0
MIN_CALL_S = 2.0


class Interpreter:
    def __init__(self, llm: LLMClient, settings: Settings, cache: IntentCache):
        self.llm = llm
        self.settings = settings
        self.cache = cache

    def _validate(self, idx: int, intent: dict, n_notes: int, capacity: float) -> Tuple[Directive, List[str]]:
        d, errs = to_directive(idx, intent, capacity)
        if d is not None and not errs:
            errs = check(d, n_notes, capacity)
        return d, errs

    def _accept(self, content: str, pending: Dict[int, str], results: Dict[int, Directive],
                n_notes: int, capacity: float, source: str) -> List[str]:
        intents, errors = parse_intents(content, list(pending))
        for idx, intent in intents.items():
            d, errs = self._validate(idx, intent, n_notes, capacity)
            if errs:
                errors.extend(errs)
                continue
            d.source = source
            results[idx] = d
            self.cache.put(pending.pop(idx), intent)
        return errors

    async def interpret(self, notes: List[str], capacity: float, deadline: float) -> List[Directive]:
        n = len(notes)
        results: Dict[int, Directive] = {}
        pending: Dict[int, str] = {}

        for i, text in enumerate(notes):
            cached = self.cache.get(text)
            if cached is not None:
                d, errs = self._validate(i, cached, n, capacity)
                if not errs:
                    d.source = "cache"
                    results[i] = d
                    continue
            pending[i] = text

        def time_left() -> float:
            return deadline - time.monotonic() - RESERVED_FOR_SOLVER_S

        for provider in (self.settings.primary, self.settings.backup):
            if not pending or not provider.enabled:
                continue
            budget = min(self.settings.llm_timeout_s, time_left())
            if budget < MIN_CALL_S:
                break
            try:
                content = await self.llm.chat(provider, build_messages(pending), budget)
            except LLMError as exc:
                log.warning("model call failed: %s", exc)
                continue
            errors = self._accept(content, pending, results, n, capacity, provider.name)
            if pending and errors:
                budget = min(self.settings.llm_timeout_s, time_left())
                if budget >= MIN_CALL_S:
                    log.info("repairing %d note(s): %s", len(pending), errors[:3])
                    try:
                        fixed = await self.llm.chat(
                            provider, build_messages(pending, errors=errors, previous=content), budget)
                        self._accept(fixed, pending, results, n, capacity, provider.name)
                    except LLMError as exc:
                        log.warning("repair call failed: %s", exc)

        if pending and self.settings.enable_degraded_fallback:
            for idx in list(pending):
                d, errs = self._validate(idx, fallback.extract(pending[idx]), n, capacity)
                if not errs:
                    d.source = "degraded"
                    results[idx] = d
                    pending.pop(idx)
            log.warning("degraded fallback used for %d note(s)", sum(d.source == "degraded" for d in results.values()))

        for idx in pending:
            results[idx] = no_op(idx, "This note could not be interpreted safely, so no constraint was applied.",
                                 source="none")

        return [results[i] for i in range(n)]
