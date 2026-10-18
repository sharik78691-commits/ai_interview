"""WebSocket live-interview endpoint.

Message types from the browser:
  {type: "transcript", text, responseLength?}      -> microphone/manual question
  {type: "force", text, responseLength?}            -> client says "this is a question"
  <binary frame>                                    -> interviewer audio chunk
  {type: "audio_flush", mimeType?}                   -> end of one audio clip
                                                        (mimeType wins: it is
                                                        what that clip actually is)
  {type: "interviewer_audio_start"|"interviewer_audio_stop"}
  {type: "reset"} | {type: "ping"}

Messages to the browser:
  {type: "status"}     {type: "ai_guidance"}   {type: "stt_transcript"}
  {type: "notice"}     {type: "error"}
"""
import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.interview import VALID_LENGTHS, _normalize_length, get_context
from app.auth import security
from app.auth.service import get_user_by_id, is_session_token_revoked
from app.db.session import SessionLocal
from app.providers.llm.registry import get_model, validate_model_key
from app.providers.stt.provider import (
    STTRequestError,
    STTUnavailableError,
    get_stt_provider,
    is_stt_hallucination,
)
from app.services.activity import log_llm_usage
from app.services.ai_service import AIService
from app.services.question_service import QuestionBuffer

logger = logging.getLogger(__name__)

router = APIRouter()

# Guard rails so a long meeting cannot exhaust memory.
MAX_CLIP_BYTES = 12 * 1024 * 1024
MAX_CLIP_SECONDS = 30
# Format assumed until a clip tells us its own (see audio_flush).
DEFAULT_AUDIO_MIME = "audio/wav"
# How many audio clips may transcribe at the same time. Clips used to queue
# BEHIND the LLM answer (the receive loop awaited every STT call inline), so
# the next question waited minutes; now they run in parallel, bounded so a
# fast-talking interviewer cannot hammer the transcription rate limit.
MAX_CONCURRENT_TRANSCRIPTIONS = 2


def sniff_audio(data: bytes) -> str:
    """Identify an audio container from its magic bytes.

    The browser may or may not have converted a clip to WAV; this tells us what
    actually arrived, which is the first thing to check when a transcription
    request is rejected as an invalid file.
    """
    if len(data) < 8:
        return "unknown"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"fLaC":
        return "flac"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    return "unknown"


# Container name -> what we declare to the speech-to-text provider.
MIME_BY_FORMAT = {
    "wav": "audio/wav",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "mp4": "audio/mp4",
    "ogg": "audio/ogg",
    "mp3": "audio/mpeg",
}


def _substantial(text: str) -> bool:
    """Worth answering (same threshold as push_complete): 3+ words or 25+ chars."""
    t = (text or "").strip()
    return len(t.split()) >= 3 or len(t) >= 25


def _authenticate_ws(websocket: WebSocket) -> int | None:
    """Resolve the authenticated user id from the session cookie.

    Uses the SAME server-managed session as the REST API — no separate
    WebSocket auth system. Returns None when the session is missing/invalid.
    """
    # The live-interview socket is opened directly against the backend host,
    # because a static host (Vercel) cannot proxy a WebSocket upgrade. That makes
    # it a CROSS-ORIGIN request, and browsers do not attach cookies to
    # cross-origin WebSocket handshakes — so cookie auth alone cannot work in
    # production. A short-lived ticket in the query string is the primary
    # credential, with the cookie kept as a fallback for same-origin/local dev.
    
    # 1) Preferred: short-lived, single-purpose ticket in the query string.
    ticket = websocket.query_params.get("ticket")
    if ticket:
        user_id = security.read_ws_ticket(ticket)
        if user_id is None:
            logger.warning("WebSocket ticket supplied but invalid or expired")
            return None
        logger.info("WebSocket authenticated via ticket (user_id=%s)", user_id)
        return _ws_user_is_active(user_id)

    # 2) Fallback: the server-managed session cookie. Works when the SPA is
    # same-origin with the API (local dev via the Angular proxy, or a deployment
    # that serves the frontend and backend from one host).
    cookie_names = list(websocket.cookies.keys())
    logger.info(
        "No ticket supplied; falling back to the session cookie. Cookies received: %s",
        cookie_names or "none",
    )
    token = websocket.cookies.get(security.SESSION_COOKIE)
    if not token:
        logger.warning(
            "Session cookie %r absent from the WebSocket handshake (received: %s). "
            "Expected for a cross-origin socket — the client must request a ticket "
            "from /api/auth/ws-ticket and pass it as ?ticket=.",
            security.SESSION_COOKIE,
            cookie_names or "none",
        )
        return None
    user_id = security.read_session_token(token)
    if user_id is None:
        return None
    db = SessionLocal()
    try:
        if is_session_token_revoked(db, token):
            logger.warning("WebSocket auth failed: session cookie was logged out")
            return None
    finally:
        db.close()
    logger.info("WebSocket authenticated via session cookie (user_id=%s)", user_id)
    return _ws_user_is_active(user_id)


def _ws_user_is_active(user_id: int) -> int | None:
    """Re-validate the user against the database.

    Sessions are server-managed, so a deactivated user must be rejected even
    with a cryptographically valid ticket or cookie.
    """
    db = SessionLocal()
    try:
        user = get_user_by_id(db, user_id)
        if user is None:
            logger.warning("WebSocket auth failed: user %s no longer exists", user_id)
            return None
        if not user.is_active:
            logger.warning("WebSocket auth failed: user %s is deactivated", user_id)
            return None
        return user.id
    finally:
        db.close()


@router.websocket("/ws/interview")
async def ws_interview(websocket: WebSocket) -> None:
    # Authentication is checked BEFORE accepting the connection. An
    # unauthenticated client is rejected with 1008 (policy violation).
    client_host = websocket.client.host if websocket.client else "unknown"
    origin = websocket.headers.get("origin")
    ticket = websocket.query_params.get("ticket")
    logger.info(
        "WebSocket connection attempt: client=%s origin=%s host=%s ticket=%s",
        client_host,
        origin or "-",
        websocket.headers.get("host", "-"),
        "yes" if ticket else "no",
    )
    
    # Auth does synchronous DB reads; keep them OFF the event loop so a slow
    # SQLite lock can never stall the socket's audio/message pump.
    user_id = await asyncio.to_thread(_authenticate_ws, websocket)
    if user_id is None:
        # 1008 = policy violation; the browser reports this as an opaque 403 on
        # the handshake, so log the cause and the remediation here.
        logger.warning(
            "WebSocket authentication failed (client=%s origin=%s ticket=%s). "
            "Expected ?ticket=<from POST /api/auth/ws-ticket> or a session cookie.",
            client_host,
            origin or "-",
            "yes" if ticket else "no",
        )
        await websocket.close(code=1008, reason="unauthenticated")
        return
    
    logger.info(f"WebSocket authenticated successfully for user_id={user_id}")

    await websocket.accept()

    # Per-user interview context (never another user's resume/JD).
    context = get_context(user_id)

    # Existing question/AI pipeline (unchanged behaviour).
    buffer = QuestionBuffer()
    service = AIService(
        resume_text=context.get("resume", ""),
        job_description=context.get("job", ""),
        response_length=context.get("length", "medium"),
    )

    # Interviewer audio capture (tab / meeting audio) -> server-side STT.
    stt = get_stt_provider()
    audio_buffer = bytearray()
    audio_mime = "audio/webm"
    interviewer_capturing = False

    def _apply_length(raw: object) -> None:
        """Client may override the answer depth for the next answer."""
        length = _normalize_length(raw if isinstance(raw, str) else None)
        if length in VALID_LENGTHS:
            service.response_length = length

    # Per-connection AI model. Defaults to the backend default; the client may
    # switch it mid-session via `model` on any question-bearing message.
    model_key: str | None = None

    async def _apply_model(raw: object) -> bool:
        """Adopt a client-selected model. Returns False for an unknown key.

        The user's explicit choice is respected: an invalid key is reported,
        never silently replaced with another model.
        """
        nonlocal model_key
        if raw is None:
            return True
        key = str(raw).strip()
        if not key:
            return True
        try:
            model_key = validate_model_key(key)
            return True
        except ValueError as exc:
            await websocket.send_json(
                {"type": "error", "message": f"Unknown AI model: {exc}"}
            )
            return False

    # Duplicate-answer guard: the mic path (sendTranscript) and the debounced
    # client detector (sendForce) can report the same question twice, and
    # WebSpeech sometimes delivers the same final transcript twice.
    last_answered: str | None = None
    last_answered_length: str = ""
    last_answered_at: float = 0.0
    DEDUP_WINDOW_S = 30.0

    def _norm_question(q: str) -> str:
        return re.sub(r"\s+", " ", (q or "").strip().lower())

    async def _send_guidance(question: str) -> None:
        """Reuse the existing AI answer feature — same service, same UI payload."""
        # Duplicate guard: mic fragments (sendTranscript) and the debounced
        # client detector (sendForce) can report the SAME question twice, and
        # WebSpeech sometimes delivers the same final transcript twice. Answer
        # once per (question, depth) — a repeat at the SAME depth reuses the
        # previous guidance, but a length change is a DELIBERATE re-ask and is
        # never suppressed (the user asked for a longer/shorter answer).
        # Recorded ONLY on success, so Retry-after-failure always goes through.
        nonlocal last_answered, last_answered_at, last_answered_length
        now = time.monotonic()
        same_depth = service.response_length == last_answered_length
        if (
            last_answered is not None
            and same_depth
            and _norm_question(question) == _norm_question(last_answered)
            and (now - last_answered_at) < DEDUP_WINDOW_S
        ):
            logger.info("Duplicate question suppressed (%d chars)", len(question))
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "ready",
                    "message": "Already answered — showing the previous guidance.",
                }
            )
            return
        # Tell the UI which model is generating BEFORE the first token, then
        # forward one provisional bullet per streamed delta. The final
        # ai_guidance below stays strictly validated (provisional != final).
        try:
            spec = await asyncio.to_thread(get_model, model_key)
            await websocket.send_json(
                {
                    "type": "ai_stream_start",
                    "model": spec.key,
                    "provider": spec.provider,
                }
            )
        except ValueError:
            pass  # non-fatal: the validated answer below is what matters

        async def _on_delta(point: str) -> None:
            await websocket.send_json({"type": "ai_stream_delta", "point": point})

        try:
            guidance = await service.analyze(
                question, model_key=model_key, on_delta=_on_delta
            )
            if service.last_warning:
                await websocket.send_json(
                    {"type": "notice", "message": str(service.last_warning)}
                )
            provider = service.last_provider
            if not isinstance(provider, str):
                provider = str(provider) if provider else None
            model = model_key if isinstance(model_key, str) else None
            await websocket.send_json(
                {
                    "type": "ai_guidance",
                    "data": guidance.model_dump(),
                    "model": model,
                    "provider": provider,
                }
            )
            last_answered = question
            last_answered_length = service.response_length
            last_answered_at = time.monotonic()
        except Exception:
            logger.exception("analyze failed")
            await websocket.send_json(
                {"type": "error", "message": "AI response unavailable. Please try again."}
            )
            return
        # Perceived latency: how long until the candidate saw the FIRST bullet.
        # The DB column stores the full-call latency, which hides the model's
        # silent thinking phase — this is the number to compare models by.
        first_ms = service.last_usage.get("first_delta_ms")
        if first_ms is not None:
            logger.info(
                "Answer latency: first_bullet=%.2fs total=%.2fs key=%s model=%s",
                first_ms / 1000,
                (service.last_usage.get("latency_ms") or 0.0) / 1000,
                model_key,
                service.last_model,
            )
        # Usage tracking: provider-reported tokens only, never estimated.
        # Off the event loop: a SQLite write must not delay the next question.
        await asyncio.to_thread(
            log_llm_usage,
            user_id=user_id,
            request_type="interview",
            provider=service.last_provider,
            model=service.last_model,
            latency_ms=service.last_usage.get("latency_ms"),
            input_tokens=service.last_usage.get("input_tokens"),
            output_tokens=service.last_usage.get("output_tokens"),
            error_category=service.last_usage.get("error_category"),
        )

    async def _handle_detected_question(question: str) -> None:
        """Common path: a question was detected -> ask the existing AI service."""
        await websocket.send_json(
            {
                "type": "status",
                "status": "processing",
                "message": f"Question detected: {question[:80]}",
            }
        )
        await _send_guidance(question)

    async def _transcribe_and_detect(
        clip: bytes, clip_mime: str, length_raw: object, model_raw: object
    ) -> None:
        """Transcribe one interviewer audio clip, then run question detection.

        Question detection uses the same QuestionBuffer as microphone speech, so
        an interviewer question triggers exactly the same AI answer flow.

        Runs as a BACKGROUND task: the receive loop keeps reading (pings, the
        next clip, model switches) while this awaits the STT provider, and at
        most MAX_CONCURRENT_TRANSCRIPTIONS clips transcribe at once.
        """
        nonlocal audio_mime
        if not clip:
            return

        # Diagnose before spending an API call: log what the bytes really are
        # versus what the client declared. A mismatch here is exactly what shows
        # up downstream as HTTP 400 "could not process file".
        detected = sniff_audio(clip)
        logger.info(
            "Interviewer clip: %d bytes, declared=%s, detected=%s",
            len(clip),
            clip_mime,
            detected,
        )
        if detected == "unknown":
            # Sending an undecodable payload only earns an opaque provider error.
            logger.warning("Unreadable clip, header bytes: %s", clip[:16].hex())
            await websocket.send_json(
                {
                    "type": "notice",
                    "scope": "stt",
                    "message": "Received an unreadable audio clip. Please paste the question manually.",
                }
            )
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Transcription failed. Please paste the question manually.",
                }
            )
            return

        # The backend is the authority on format. If the label disagrees with the
        # magic bytes, trust the bytes - a WAV payload sent as audio/webm (or vice
        # versa) is rejected by strict providers as an invalid file.
        real_mime = MIME_BY_FORMAT.get(detected) or clip_mime
        if real_mime != audio_mime:
            logger.info("Correcting declared mime %s -> %s", audio_mime, real_mime)
            audio_mime = real_mime

        async with stt_slots:
            t0 = time.perf_counter()
            try:
                text = await stt.transcribe(clip, mime_type=real_mime)
            except STTUnavailableError:
                # No transcription backend configured at all.
                logger.warning("Interviewer audio received but no STT provider is configured")
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Browser audio capture is unavailable. Please paste the question manually.",
                    }
                )
                return
            except STTRequestError as exc:
                # Provider is configured but the call failed - tell the real reason.
                # scope="stt" so the UI does not claim it is showing template guidance.
                logger.warning("Interviewer audio transcription failed: %s", exc)
                await websocket.send_json(
                    {"type": "notice", "scope": "stt", "message": str(exc)}
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Transcription failed. Please paste the question manually.",
                    }
                )
                return
            except Exception:
                logger.exception("transcription failed")
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Transcription failed. Please paste the question manually.",
                    }
                )
                return
            # Latency telemetry: the single number that tells us whether STT is
            # slow (provider) or the clip simply sat waiting (pipeline).
            logger.info(
                "STT latency: %.2fs for %d bytes (%s)",
                time.perf_counter() - t0,
                len(clip),
                real_mime,
            )

        if text and is_stt_hallucination(text):
            logger.info("STT hallucination dropped: %r", text[:40])
            text = ""

        if not text:
            # Empty transcription (silence/music). Tell the UI so it does not
            # sit on "Question detected" until the 25s safety valve fires.
            logger.info("STT empty: no speech in this clip")
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "listening",
                    "message": "listening... (no speech in last clip)",
                }
            )
            return

        await websocket.send_json(
            {"type": "stt_transcript", "text": text, "final": True}
        )

        # Question detection for interviewer audio: the clip already ends at a
        # pause, so treat this segment as a finished utterance — but only a
        # GENUINE, complete question is answered. Answering every substantial
        # utterance used to burn an LLM call on "let me pull up your resume"
        # and on half-asked questions, and because answers run one-at-a-time on
        # the queue, each of those delayed the real question.
        question = buffer.push_complete(text)
        if question:
            logger.info("Interviewer prompt (%d chars): %s", len(question), question[:100])
            await _enqueue_answer(question, length_raw, model_raw, announce=True)
            return

        if buffer.holding_partial:
            # The clip boundary landed mid-question. Wait out the hold window
            # so the next clip can be stitched onto this one and the WHOLE
            # question answered; if the interviewer truly abandoned the
            # sentence, flush what we have instead of dropping it.
            # flush_expired() is deadline-checked, so a stale timer is
            # harmless, and a question already stitched away is gone from the
            # buffer — so it can never be answered twice.
            logger.info(
                "Holding %d chars of an unfinished question for its second half",
                len(text),
            )
            _spawn(_flush_held_question(buffer.HOLD_MS / 1000.0, length_raw, model_raw))
            return

        # Backchannel / small talk ("okay", "thank you", "let me see").
        # Report it so the UI leaves the "Question detected" state.
        logger.info("Interviewer backchannel ignored (%d chars): %s", len(text), text[:80])
        await websocket.send_json(
            {
                "type": "status",
                "status": "listening",
                "message": "listening... (waiting for a complete question)",
            }
        )

    async def _flush_held_question(
        delay_s: float, length_raw: object, model_raw: object
    ) -> None:
        """Answer a question that was cut in half if its continuation never comes.

        The hold window covers a normal mid-question pause plus the next clip's
        upload and transcription, so in practice this only fires when the
        interviewer genuinely abandoned the sentence mid-way.
        """
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        held = buffer.flush_expired()
        if held:
            logger.info(
                "Held question flushed after %.1fs (%d chars): %s",
                delay_s,
                len(held),
                held[:100],
            )
            await _enqueue_answer(held, length_raw, model_raw, announce=True)

    # ------------------------------------------------------------------
    # Background pipeline (latency-critical).
    #
    # The receive loop used to AWAIT every STT and LLM call inline, so pings,
    # model switches and the NEXT audio clip queued behind up to minutes of
    # provider I/O — the single biggest cause of "the AI/STT is slow".
    # Now: clips transcribe as bounded background tasks, and answers are
    # generated by ONE worker, so two streamed answers can never interleave on
    # the socket and a duplicate can never spend a second AI call.
    # ------------------------------------------------------------------
    answer_queue: asyncio.Queue = asyncio.Queue()
    #: Normalized questions currently queued/being answered (duplicate guard).
    answering: set[str] = set()
    #: Everything spawned for this connection; cancelled on disconnect.
    background: set[asyncio.Task] = set()
    #: Caps concurrent transcriptions (rate-limit protection).
    stt_slots = asyncio.Semaphore(MAX_CONCURRENT_TRANSCRIPTIONS)

    def _spawn(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)
        return task

    async def _answer_worker() -> None:
        """Answer queued questions sequentially — one streamed answer at a time."""
        while True:
            question, length_raw, model_raw, announce = await answer_queue.get()
            try:
                # Only a real client value overrides the depth; `None` keeps the
                # session's current depth (a stop-flush has no length field).
                if isinstance(length_raw, str):
                    _apply_length(length_raw)
                await _apply_model(model_raw)
                if announce:
                    await _handle_detected_question(question)
                else:
                    await _send_guidance(question)
            finally:
                answering.discard(_norm_question(question))
                answer_queue.task_done()

    async def _enqueue_answer(
        question: str, length_raw: object, model_raw: object, announce: bool = False
    ) -> bool:
        """Queue one question for the answer worker.

        Returns False when the SAME question is already queued or generating —
        a repeat (mic fragment + debounced force detector, or WebSpeech
        delivering the same final twice) must never spend a second AI call, and
        the UI gets a status so its spinner does not hang.
        """
        key = _norm_question(question)
        if key in answering:
            logger.info(
                "Duplicate question suppressed (in flight, %d chars)", len(question)
            )
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "ready",
                    "message": "Already answered — showing the previous guidance.",
                }
            )
            return False
        answering.add(key)
        answer_queue.put_nowait((question, length_raw, model_raw, announce))
        return True

    _spawn(_answer_worker())

    # Initial handshake keeps the existing client contract intact.
    logger.info("[ws user=%s] accepted; sending handshake", user_id)
    await websocket.send_json(
        {
            "type": "status",
            "status": "listening",
            "message": "connected. Send {type:'transcript', text}.",
        }
    )

    try:
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                logger.info("[ws user=%s] client disconnected", user_id)
                break
            except Exception:
                logger.exception("[ws user=%s] receive() failed", user_id)
                await websocket.send_json(
                    {"type": "error", "message": "Expected JSON message."}
                )
                continue

            # ---- Binary frame: interviewer audio chunk ----
            if message.get("type") == "websocket.receive" and message.get("bytes"):
                chunk = message["bytes"]
                if interviewer_capturing and len(audio_buffer) < MAX_CLIP_BYTES:
                    audio_buffer.extend(chunk)
                continue

            text_frame = message.get("text")
            if text_frame is None:
                continue
            try:
                msg = json.loads(text_frame)
            except Exception:
                await websocket.send_json(
                    {"type": "error", "message": "Expected JSON message."}
                )
                continue

            mtype = (msg.get("type") or "").lower() if isinstance(msg, dict) else ""
            # Skip the high-frequency keepalive so real traffic stays readable.
            if mtype and mtype != "ping":
                logger.info("[ws user=%s] recv type=%s", user_id, mtype)

            if mtype == "ping":
                await websocket.send_json({"type": "pong"})

            elif mtype == "set_model":
                # Switch the AI model for subsequent answers (mid-session).
                await _apply_model(msg.get("model"))
                await websocket.send_json(
                    {"type": "status", "status": "listening", "message": "model updated"}
                )

            elif mtype == "reset":
                buffer = QuestionBuffer()
                audio_buffer = bytearray()
                await websocket.send_json(
                    {"type": "status", "status": "listening", "message": "buffer reset"}
                )

            # ---- Interviewer audio lifecycle ----
            elif mtype == "interviewer_audio_start":
                interviewer_capturing = True
                audio_buffer = bytearray()
                # The client may not know the final format yet (it depends on
                # how MediaRecorder output is decoded). Each clip announces its
                # own mimeType in audio_flush, which always wins.
                audio_mime = msg.get("mimeType") or DEFAULT_AUDIO_MIME
                await websocket.send_json(
                    {
                        "type": "status",
                        "status": "listening",
                        "message": "Listening for the interviewer's question...",
                    }
                )

            elif mtype == "interviewer_audio_stop":
                interviewer_capturing = False
                if audio_buffer:
                    # Snapshot synchronously, then transcribe in the background
                    # so the "stopped" status (and everything else) stays fast.
                    clip = bytes(audio_buffer)
                    audio_buffer = bytearray()
                    _spawn(_transcribe_and_detect(clip, audio_mime, None, None))
                await websocket.send_json(
                    {
                        "type": "status",
                        "status": "ready",
                        "message": "Interviewer audio capture stopped.",
                    }
                )

            elif mtype == "audio_flush":
                # One clip finished -> transcribe and detect a question.
                # The client converts clips to 16 kHz mono WAV, so adopt the
                # mimeType that shipped with THIS clip. Sending WAV bytes under
                # an "audio/webm" label makes the provider answer
                # HTTP 400 "could not process file".
                clip_mime = msg.get("mimeType")
                if isinstance(clip_mime, str) and clip_mime.strip():
                    audio_mime = clip_mime.strip()
                # Snapshot NOW (the loop keeps appending the NEXT clip right
                # after this handler returns) and transcribe in the background.
                clip = bytes(audio_buffer)
                audio_buffer = bytearray()
                _spawn(
                    _transcribe_and_detect(
                        clip, audio_mime, msg.get("responseLength"), msg.get("model")
                    )
                )

            elif mtype == "interviewer_question":
                # Manual/typed interviewer question -> existing AI flow.
                question = (msg.get("text") or "").strip()
                if question:
                    await websocket.send_json(
                        {"type": "stt_transcript", "text": question, "final": True}
                    )
                    await _enqueue_answer(
                        question,
                        msg.get("responseLength"),
                        msg.get("model"),
                        announce=True,
                    )

            elif mtype == "transcript":
                service.resume_text = context.get("resume", service.resume_text)
                service.job_description = context.get("job", service.job_description)
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                question = buffer.push(text)
                # push() fires only on "?"-terminated questions. The rest is
                # decided here once per fragment:
                # - short "?" final ("What is X?") with substance -> fire now;
                # - "?"-terminated noise ("Okay?") -> drop, keep listening;
                # - tiny fragments WITHOUT "?" ("Can", "tell me") -> keep
                #   accumulating slow word-by-word speech, never drop;
                # - very long monologue ending at a sentence boundary ->
                #   fire (safety valve if the client detector never fires).
                # Anything else accumulates for the client's ~600ms pause
                # detector, which reaps the COMPLETE question via force.
                if question is None:
                    pending = buffer.pending
                    if not _substantial(pending):
                        if text.endswith("?"):
                            buffer.force_flush()  # drop noise; keep listening
                    elif text.endswith("?"):
                        question = buffer.force_flush()
                    elif len(pending) > 500 and pending.rstrip().endswith(
                        (".", "?", "!")
                    ):
                        question = buffer.force_flush()
                if question:
                    await _enqueue_answer(
                        question, msg.get("responseLength"), msg.get("model")
                    )
                else:
                    await websocket.send_json(
                        {"type": "status", "status": "listening", "message": "listening..."}
                    )

            elif mtype == "force" or (isinstance(msg, dict) and msg.get("force")):
                pending = buffer.force_flush()
                # Client-detected question arrived but server buffer is empty
                # (e.g. reconnect in between): fall back to the supplied text.
                if not pending and isinstance(msg.get("text"), str) and msg["text"].strip():
                    pending = msg["text"].strip()
                if pending:
                    await _enqueue_answer(
                        pending, msg.get("responseLength"), msg.get("model")
                    )

            else:
                await websocket.send_json(
                    {"type": "error", "message": f"unknown message type: {mtype}"}
                )
    except WebSocketDisconnect:
        logger.info("[ws user=%s] disconnected", user_id)
    except Exception as exc:
        logger.exception("[ws user=%s] unhandled websocket error", user_id)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close()
        except Exception:
            logger.debug("[ws user=%s] client already gone; could not send error", user_id)
    finally:
        # Background work (answer worker, in-flight transcriptions) belongs to
        # THIS socket — stop it so a departed client never keeps calling APIs.
        for task in list(background):
            task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)