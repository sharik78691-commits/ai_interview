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
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.interview import VALID_LENGTHS, _normalize_length, get_context
from app.auth import security
from app.auth.service import get_user_by_id, is_session_token_revoked
from app.db.session import SessionLocal
from app.providers.stt.provider import (
    STTRequestError,
    STTUnavailableError,
    get_stt_provider,
)
from app.services.ai_service import AIService
from app.services.question_service import QuestionBuffer

logger = logging.getLogger(__name__)

router = APIRouter()

# Guard rails so a long meeting cannot exhaust memory.
MAX_CLIP_BYTES = 12 * 1024 * 1024
MAX_CLIP_SECONDS = 30
# Format assumed until a clip tells us its own (see audio_flush).
DEFAULT_AUDIO_MIME = "audio/wav"


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
    
    user_id = _authenticate_ws(websocket)
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

    async def _send_guidance(question: str) -> None:
        """Reuse the existing AI answer feature — same service, same UI payload."""
        try:
            guidance = await service.analyze(question)
            if service.last_warning:
                await websocket.send_json(
                    {"type": "notice", "message": service.last_warning}
                )
            await websocket.send_json(
                {"type": "ai_guidance", "data": guidance.model_dump()}
            )
        except Exception:
            logger.exception("analyze failed")
            await websocket.send_json(
                {"type": "error", "message": "AI response unavailable. Please try again."}
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

    async def _transcribe_and_detect() -> None:
        """Transcribe one interviewer audio clip, then run question detection.

        Question detection uses the same QuestionBuffer as microphone speech, so
        an interviewer question triggers exactly the same AI answer flow.
        """
        nonlocal audio_buffer, audio_mime
        clip = bytes(audio_buffer)
        audio_buffer = bytearray()
        if not clip:
            return

        # Diagnose before spending an API call: log what the bytes really are
        # versus what the client declared. A mismatch here is exactly what shows
        # up downstream as HTTP 400 "could not process file".
        detected = sniff_audio(clip)
        logger.info(
            "Interviewer clip: %d bytes, declared=%s, detected=%s",
            len(clip),
            audio_mime,
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
        real_mime = MIME_BY_FORMAT.get(detected)
        if real_mime and real_mime != audio_mime:
            logger.info("Correcting declared mime %s -> %s", audio_mime, real_mime)
            audio_mime = real_mime

        try:
            text = await stt.transcribe(clip, mime_type=audio_mime)
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
        # pause, so treat this segment as a finished utterance. With the new
        # push_complete, any substantial utterance fires the AI — the log line
        # below proves the LLM call started (if it is missing, detection gated).
        question = buffer.push_complete(text)
        if question:
            logger.info("Interviewer prompt (%d chars): %s", len(question), question[:100])
            await _handle_detected_question(question)
        else:
            # Too short to answer (backchannel like "okay", "thank you").
            # Report it so the UI leaves the "Question detected" state.
            logger.info("Interviewer backchannel ignored (%d chars): %s", len(text), text[:80])
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "listening",
                    "message": "listening... (waiting for a complete question)",
                }
            )

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
                import json

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
                    await _transcribe_and_detect()
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
                _apply_length(msg.get("responseLength"))
                await _transcribe_and_detect()

            elif mtype == "interviewer_question":
                # Manual/typed interviewer question -> existing AI flow.
                question = (msg.get("text") or "").strip()
                if question:
                    await websocket.send_json(
                        {"type": "stt_transcript", "text": question, "final": True}
                    )
                    _apply_length(msg.get("responseLength"))
                    await _handle_detected_question(question)

            elif mtype == "transcript":
                service.resume_text = context.get("resume", service.resume_text)
                service.job_description = context.get("job", service.job_description)
                _apply_length(msg.get("responseLength"))
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                question = buffer.push(text)
                # Force: long declarative that looks complete but lacks '?'.
                if question is None and (text.endswith("?") or len(buffer.pending) > 120):
                    question = buffer.force_flush()
                if question:
                    await _handle_detected_question(question)
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
                _apply_length(msg.get("responseLength"))
                if pending:
                    await _send_guidance(pending)

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