"""HTTP entry point.

GET  /health           -> {"status": "ok"}
POST /optimize-energy  -> interpretation + 24-hour plan

400 = malformed JSON / structurally invalid request
422 = well-formed but impossible values
500 = controlled internal error (no stack traces or config in the body)
"""
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.interpret.cache import IntentCache
from app.interpret.interpreter import Interpreter
from app.interpret.llm_client import LLMClient
from app.schemas.request import OptimizeRequest, semantic_errors
from app.service import optimize

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gridwise")


@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(limits=httpx.Limits(max_connections=50, max_keepalive_connections=20))
    app.state.interpreter = Interpreter(LLMClient(http), settings, IntentCache(settings.cache_size))
    if not settings.primary.enabled:
        log.warning("LLM_BASE_URL / LLM_MODEL not set: running without a language model (degraded mode only)")
    else:
        log.info("primary model: %s", settings.primary.model)
    if settings.backup.enabled:
        log.info("backup model: %s", settings.backup.model)
    yield
    await http.aclose()


app = FastAPI(title="GridWise Energy Optimizer", version="1.0.0", lifespan=lifespan)


def _error(status: int, message: str, details=None) -> JSONResponse:
    body = {"error": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status, content=body)


def _reject_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    return _error(400, "invalid request")


@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    return _error(exc.status_code, "not found" if exc.status_code == 404 else str(exc.detail))


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    ref = uuid.uuid4().hex[:8]
    log.error("unhandled error ref=%s type=%s", ref, type(exc).__name__)
    return _error(500, "internal error", {"ref": ref})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"service": "gridwise", "endpoints": ["GET /health", "POST /optimize-energy"], "docs": "/docs"}


def _example_request() -> dict:
    """Sample body shown in the /docs page so the endpoint can be tried from a browser."""
    path = Path(__file__).resolve().parent.parent / "samples" / "request_sample01.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# The body is parsed by hand (to control 400 vs 422), so describe it for the docs page explicitly.
_OPENAPI_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}, "example": _example_request()}},
    }
}


@app.post("/optimize-energy", openapi_extra=_OPENAPI_BODY)
async def optimize_energy(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError):
        return _error(400, "malformed JSON")
    if not isinstance(data, dict):
        return _error(400, "request body must be a JSON object")

    try:
        req = OptimizeRequest.model_validate(data)
    except ValidationError as exc:
        details = [{"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                   for e in exc.errors(include_input=False, include_url=False)][:20]
        return _error(400, "invalid request", details)

    problems = semantic_errors(req)
    if problems:
        return _error(422, "request values are not feasible", problems[:20])

    try:
        result = await optimize(req, request.app.state.interpreter, settings.request_deadline_s)
    except Exception as exc:  # noqa: BLE001 - controlled 500, never leak internals
        ref = uuid.uuid4().hex[:8]
        log.error("optimize failed ref=%s scenario=%s type=%s", ref, req.scenario_id[:64], type(exc).__name__)
        return _error(500, "internal error", {"ref": ref})
    return JSONResponse(content=result)
