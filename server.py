# MHT assistant server (runs on the DGX Spark)
#
#   Unreal --(text or voice + context)--> this server --(reply + actions)--> Unreal runs the actions
#
# The contract is docs/PROTOCOL.md. To run:
#   ./run.sh
# or the long way:
#   source .venv/bin/activate
#   uvicorn server:app --host 0.0.0.0 --port 8010

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartParser

from assistant import NAME, PROTOCOL, VERSION, llm, log, pipeline, stt
from assistant.config import settings

# Our own lines only: other libraries keep their default logging.
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

NOT_HEARD = "I did not catch that. Hold the logo while you speak."

# Keep uploads in memory while they are parsed (Starlette would otherwise spool anything over 1 MB to
# a temporary file). The size is capped by GateMiddleware before it gets here.
MultiPartParser.spool_max_size = settings.max_audio_bytes + 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the models in the background so the server is reachable right away.
    threading.Thread(target=llm.warm_up, daemon=True).start()
    if stt.installed():
        threading.Thread(target=stt.warm_up, daemon=True).start()
    else:
        log.warning("faster-whisper is not installed: /v1/voice will answer 503, text still works.")
    if not settings.api_key:
        log.warning("ASSISTANT_API_KEY is not set: anyone who can reach this port can use the server.")
    yield


app = FastAPI(title=NAME, version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------------------
# Authentication and size limits, checked before the request body is read
# ---------------------------------------------------------------------------

def key_is_valid(authorization: str) -> bool:
    """True if no key is configured, or the header is 'Bearer <the key>' (constant-time comparison)."""
    expected = settings.api_key
    if not expected:
        return True
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(supplied.strip().encode("utf-8"), expected.encode("utf-8"))


class GateMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        headers = {name.lower(): value for name, value in scope.get("headers", [])}

        if not (path == "/health" and method == "GET"):
            if not key_is_valid(headers.get(b"authorization", b"").decode("latin-1")):
                response = JSONResponse(
                    {"detail": "Missing or wrong API key."}, status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return

        limit = settings.max_audio_bytes + 1024 * 1024 if path == "/v1/voice" else settings.max_json_bytes
        too_large = f"The request is too large (the limit is {limit // (1024 * 1024)} MB)."
        length = headers.get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            await JSONResponse({"detail": too_large}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:                    # no Content-Length, or a wrong one
                    raise HTTPException(status_code=413, detail=too_large)
            return message

        await self.app(scope, limited_receive, send)


app.add_middleware(GateMiddleware)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, error: StarletteHTTPException):
    detail = error.detail if isinstance(error.detail, str) else "Request failed."
    return JSONResponse({"detail": detail}, status_code=error.status_code, headers=error.headers)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error: RequestValidationError):
    return JSONResponse(
        {"detail": "The request is not in the form docs/PROTOCOL.md describes."}, status_code=422,
    )


@app.exception_handler(Exception)
async def server_error(request, error: Exception):
    log.exception("Unhandled error")
    return JSONResponse({"detail": "The server had an internal error."}, status_code=500)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """The body of /v1/chat. Every field is optional and loosely typed: a missing or odd field must
    never fail the request (PROTOCOL.md, Context)."""

    model_config = ConfigDict(extra="ignore")

    protocol: Any = None
    session_id: Any = ""
    text: Any = ""
    context: Any = None
    catalog_id: Any = ""
    catalog: Any = None


def check_protocol(version: Any) -> None:
    if version is None or version == PROTOCOL:
        return
    raise HTTPException(
        status_code=400,
        detail=f"This server speaks protocol {PROTOCOL}, the request says protocol {str(version)[:20]}.",
    )


def short(value: object, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def finish(endpoint: str, answer: dict, started: float) -> dict:
    """Adds the timing and writes the one log line of this request."""
    answer["latency_ms"] = round((time.perf_counter() - started) * 1000)
    what = "need_catalog" if answer["need_catalog"] else short(answer["actions"])
    log.info(
        '[%s %dms] %s "%s" -> %s | %s',
        answer["source"], answer["latency_ms"], endpoint, short(answer["transcript"], 200), what,
        short(answer["reply"], 200),
    )
    return answer


def not_heard() -> dict:
    return {"transcript": "", "reply": NOT_HEARD, "actions": [], "source": "rules", "need_catalog": False}


# ---------------------------------------------------------------------------
# Endpoints. Plain "def" (not async) so FastAPI runs them in worker threads: a "pause" sent while
# Whisper or the LLM is busy with another request is still answered immediately.
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "name": NAME,
        "version": VERSION,
        "protocol": PROTOCOL,
        "auth_required": bool(settings.api_key),
        "llm_model": llm.model_name(),
        "llm_ready": llm.ready(),
        "stt_model": settings.stt_model,
        "stt_ready": stt.ready(),
    }


@app.post("/v1/chat")
def chat(body: ChatRequest):
    started = time.perf_counter()
    check_protocol(body.protocol)
    answer = pipeline.handle(body.text, body.context, body.catalog_id, body.catalog, body.session_id)
    return finish("chat", answer, started)


@app.post("/v1/voice")
def voice(audio: UploadFile = File(...), request: str = Form("")):
    started = time.perf_counter()

    try:
        body = json.loads(request) if request.strip() else {}
    except ValueError:
        raise HTTPException(status_code=400, detail="The request part is not valid JSON.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="The request part must be a JSON object.")
    check_protocol(body.get("protocol"))

    if not stt.installed():
        raise HTTPException(
            status_code=503,
            detail="Speech recognition is not installed on this server (faster-whisper is missing).",
        )

    audio_bytes = audio.file.read(settings.max_audio_bytes + 1)
    if len(audio_bytes) > settings.max_audio_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"The audio is too large (the limit is {settings.max_audio_bytes // (1024 * 1024)} MB).",
        )
    if not audio_bytes:
        return finish("voice", not_heard(), started)

    if settings.save_last_audio:
        try:
            settings.last_audio_path.write_bytes(audio_bytes)
        except OSError as error:
            log.warning("could not save the last audio clip: %r", error)

    try:
        transcript = stt.transcribe(audio_bytes)
    except stt.STTUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:            # a file that is not audio, a decoder error, ...
        log.warning("transcription failed (%d bytes): %r", len(audio_bytes), error)
        return finish("voice", not_heard(), started)

    if not transcript.strip():
        return finish("voice", not_heard(), started)

    answer = pipeline.handle(
        transcript, body.get("context"), body.get("catalog_id"), body.get("catalog"), body.get("session_id"),
    )
    return finish("voice", answer, started)
