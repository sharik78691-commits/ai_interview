"""WebSocket live-interview endpoint.

Message types from the browser:
  {type: "transcript", text, responseLength?}      -> microphone/manual question
  {type: "force", text, responseLength?}            -> client says "this is a question"
  <binary frame>                                    -> interviewer audio chunk
  {type: "audio_flush", mimeType?}                   -> end of one audio clip
                                                        (mimeType wins: it is
                                                        what that clip actually is)
  {type: "audio_preview", mimeType?}                 -> the preceding binary frame
                                                        is a SNAPSHOT of the clip
                                                        still being spoken (live
                                                        transcript, display only)
  <binary frame "LPCM" + 16 kHz PCM16>               -> live transcript stream
                                                        (see live_stream.py)
  {type: "speech_pause", responseLength?}            -> the interviewer just went
                                                        quiet (~0.45 s); sent only
                                                        when the handshake says
                                                        liveAnswers
  {type: "interviewer_audio_start"|"interviewer_audio_stop"}
  {type: "reset"} | {type: "ping"}

Messages to the browser:
  {type: "status"}     {type: "ai_guidance"}   {type: "stt_transcript"}
  {type: "notice"}     {type: "error"}

  stt_transcript carries `final` and `seq` (the clip it belongs to). A
  `final: false` transcript is PROVISIONAL live text for the clip still being
  spoken: it is never question-detected or answered, and the final transcript
  of the same `seq` replaces it.

  {type: "stt_live", text, final} is the word-by-word streamed transcript of
  the current utterance (cumulative per turn; `final` closes the turn). An
  interim frame is never answered. With liveAnswers, the stream's text is
  answered once the turn is over (a pause after a finished sentence, or the
  turn close) — see LiveTurnTracker; the clip pipeline then only answers
  speech the stream did not hear.
"""
import asyncio
import json
import logging
import re
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.auth import security
from app.auth.service import get_user_by_id, is_session_token_revoked
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.interview import MAX_QUESTION_CHARS
from app.providers.llm.registry import get_model, validate_model_key
from app.providers.stt.provider import (
    STTRequestError,
    STTUnavailableError,
    get_stt_provider,
    is_stt_hallucination,
    live_stream_selected,
    prewarm_stt,
    strip_sound_tags,
)
from app.providers.stt.live_stream import LIVE_PCM_TAG, LiveTranscriptStream
from app.services import free_trial_service as free_trial
from app.services.activity import log_llm_usage
from app.services.ai_service import AIService
from app.services.interview_context import VALID_LENGTHS, context_store, normalize_length
from app.services.question_service import LiveTurnTracker, QuestionBuffer, same_utterance

logger = logging.getLogger(__name__)

router = APIRouter()

# Free-trial metering (see free_trial_service). Every TRIAL_TICK_SECONDS the
# session is charged one tick IF it is active: interviewer audio is being
# captured, or an answer was requested within TRIAL_IDLE_GRACE_SECONDS. An
# open but idle page costs nothing. Close code 4402 = trial used up (the
# client shows the upgrade prompt and does not reconnect).
TRIAL_TICK_SECONDS = 15
TRIAL_IDLE_GRACE_SECONDS = 90
TRIAL_EXHAUSTED_CLOSE_CODE = 4402

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
# Live-transcript previews are disposable: a slow one is abandoned (the next
# snapshot supersedes it) and a failing provider pauses previews for a while so
# they never compete with the FINAL clip for the rate limit.
PREVIEW_TIMEOUT_SECONDS = 6.0
#: After a FINAL clip failed (rate limit / provider trouble): finals first.
PREVIEW_COOLDOWN_SECONDS = 20.0
#: After a preview's OWN failure (one slow/failed snapshot). It used to share
#: the 20 s cooldown, so a single network blip froze the Transcript line for
#: 20 s while the interviewer kept talking; the next snapshot now retries soon.
PREVIEW_RETRY_SECONDS = 3.0
# Live answering (LiveTurnTracker). A complete but unpunctuated prompt is
# answered this long after the pause if no new word arrived meanwhile.
LIVE_SETTLE_SECONDS = 0.6
# A clip's own question detection waits until this long after its cut, so the
# live stream's turn close (~0.45 s after the cut) has landed and it is known
# whether the live lane already owns that speech.
LIVE_GRACE_SECONDS = 1.2
# A live answer this soon after a clip cut is about the speech in that clip.
LIVE_FLUSH_WINDOW_SECONDS = 1.6
# Cross-lane duplicate guard: the live and clip lanes transcribe the same
# audio with different engines, so a repeat is matched by content words.
AUDIO_COVER_WINDOW_SECONDS = 20.0
# Longest question accepted by "Show follow-ups" (questions are short text).
MAX_FOLLOWUP_QUESTION_CHARS = 2000
# Longest question sent to the LLM (shared with the REST route): real
# questions are a few hundred chars; the cap only bounds token spend when a
# client streams a huge text frame. Here the TAIL is kept (the actual question
# ends a long monologue); REST rejects oversize input instead.


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
    # Cross-Site WebSocket Hijacking guard: browsers attach cookies to a
    # cross-site WebSocket handshake (SameSite=None in production) and CORS
    # does not apply to WebSockets. A cookie-authenticated socket must
    # therefore come from one of OUR origins. (The ticket path above is safe:
    # a foreign site cannot read a ticket from /api/auth/ws-ticket.)
    origin = websocket.headers.get("origin")
    if origin and not get_settings().cors_allows(origin):
        logger.warning(
            "WebSocket cookie auth refused: origin %s is not in CORS_ORIGINS", origin
        )
        return None
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
    payload = security.read_session_payload(token)
    if payload is None:
        return None
    user_id, issued_at = payload
    db = SessionLocal()
    try:
        if is_session_token_revoked(db, token):
            logger.warning("WebSocket auth failed: session cookie was logged out")
            return None
        user = get_user_by_id(db, user_id)
        if user is not None and security.session_issued_before_revocation(
            issued_at, user.sessions_revoked_at
        ):
            logger.warning("WebSocket auth failed: session predates a password reset")
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


def _trial_access(user_id: int, client: str) -> free_trial.LiveAccess:
    db = SessionLocal()
    try:
        user = get_user_by_id(db, user_id)
        return free_trial.live_access(db, user, client)
    finally:
        db.close()


def _trial_tick(user_id: int, client: str, seconds: int) -> free_trial.LiveAccess:
    """Charge one active tick (unless a plan was bought meanwhile)."""
    db = SessionLocal()
    try:
        user = get_user_by_id(db, user_id)
        access = free_trial.live_access(db, user, client)
        if access.mode != "trial":
            return access
        left = free_trial.consume_seconds(db, user, client, seconds)
        return free_trial.LiveAccess("trial" if left > 0 else "denied", left, access.limit_seconds)
    finally:
        db.close()


def _access_frame(access: free_trial.LiveAccess, client: str) -> dict:
    frame = {
        "type": "access",
        "mode": access.mode,
        "client": client,
        "remainingSeconds": access.remaining_seconds,
        "limitSeconds": access.limit_seconds,
    }
    if access.mode == "denied":
        frame["code"] = "TRIAL_EXHAUSTED"
        frame["message"] = (
            "Your free live-interview minutes are used up. Choose a plan to keep practising."
        )
    return frame


def _ws_user_is_admin(user_id: int) -> bool:
    """Only admins may pick the answering model; everyone else gets the default."""
    db = SessionLocal()
    try:
        user = get_user_by_id(db, user_id)
        return bool(user and user.is_admin)
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

    # Free trial gate: no plan and no trial minutes left for this client →
    # tell the client why, then close (it shows the upgrade prompt).
    client_kind = free_trial.normalize_client(websocket.query_params.get("client"))
    access = await asyncio.to_thread(_trial_access, user_id, client_kind)
    if access.mode == "denied":
        logger.info("[ws user=%s] live trial used up (%s); closing", user_id, client_kind)
        await websocket.send_json(_access_frame(access, client_kind))
        await websocket.close(code=TRIAL_EXHAUSTED_CLOSE_CODE, reason="trial_exhausted")
        return
    #: Last time an answer was requested (monotonic); drives trial metering.
    last_activity = 0.0

    # Per-user interview context (never another user's resume/JD).
    context = context_store.get(user_id)

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

    # Live-transcript lane: provisional text while the interviewer is still
    # talking. Its own provider instance makes ONE attempt per snapshot, so a
    # preview never retries into the rate limit the final clip needs.
    live_preview = get_settings().stt_live_preview
    preview_stt = get_stt_provider()
    if preview_stt is not stt:
        preview_stt.max_attempts = 1
    #: Index of the clip currently being spoken; bumped when a clip is flushed,
    #: so a preview that finishes after its clip's final is dropped as stale.
    clip_seq = 0
    #: Newest unsent snapshot (clip, seq) — latest wins, older ones drop.
    preview_pending: tuple[bytes, int] | None = None
    preview_running = False
    preview_paused_until = 0.0

    # Word-by-word stream: ONLY when the admin-selected STT is Gemini Transcribe
    # Live (the one streaming model). With any other STT (e.g. Whisper on
    # DeepInfra) the live transcript comes from the snapshot previews above,
    # through that same admin-selected STT. When on, the client streams PCM
    # instead of sending snapshots, so the preview lane stays idle.
    settings = get_settings()
    live_stream: LiveTranscriptStream | None = None
    # Live answering: the streamed transcript also decides WHEN a question is
    # finished, so the answer starts at a human-like pause instead of after the
    # clip's silence cut + batch STT round-trip. Clips stay the fallback.
    live_turns = LiveTurnTracker()
    #: Clip seqs whose speech the live lane answered (their clip must not).
    live_owned_seqs: set[int] = set()
    live_settle_gen = -1
    #: Depth / model from the latest audio control frame (the live lane has
    #: no frame of its own to carry them).
    live_length_raw: object = None
    live_model_raw: object = None
    last_flush_at = 0.0
    if live_preview and live_stream_selected() and settings.gemini_api_key and settings.stt_live_model:

        async def _on_live_text(text: str, final: bool) -> None:
            # Track the words BEFORE showing them: a pause the client reports
            # after seeing this text must find it already in the tracker.
            track = live_answers and interviewer_capturing
            question = live_turns.update(text, final) if track else None
            try:
                await websocket.send_json({"type": "stt_live", "text": text, "final": final})
            except Exception:
                logger.debug("[ws user=%s] live text dropped: socket closing", user_id)
            if track:
                await _on_live_decision(question)

        live_stream = LiveTranscriptStream(
            settings.gemini_api_key, settings.stt_live_model, _on_live_text
        )
    live_answers = live_stream is not None and settings.stt_live_answers

    def _apply_length(raw: object) -> None:
        """Client may override the answer depth for the next answer."""
        length = normalize_length(raw if isinstance(raw, str) else None)
        if length in VALID_LENGTHS:
            service.response_length = length

    # Per-connection AI model. Defaults to the backend default (the admin's
    # pick, resolved per answer). Only admins may switch it via `model`;
    # a regular user's `model` field is ignored.
    model_key: str | None = None
    can_pick_model = await asyncio.to_thread(_ws_user_is_admin, user_id)

    async def _apply_model(raw: object) -> bool:
        """Adopt an admin-selected model. Returns False for an unknown key.

        The admin's explicit choice is respected: an invalid key is reported,
        never silently replaced with another model.
        """
        nonlocal model_key
        if raw is None or not can_pick_model:
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

    def _repeat_of_last(question: str, length: str) -> bool:
        """Same question at the same depth answered moments ago (see below)."""
        return (
            last_answered is not None
            and length == last_answered_length
            and _norm_question(question) == _norm_question(last_answered)
            and (time.monotonic() - last_answered_at) < DEDUP_WINDOW_S
        )

    #: The answer currently being generated (cancellable by a newer question).
    generating: dict[str, asyncio.Task | None] = {"task": None}

    async def _generate(question: str, on_delta) -> object | None:
        """service.analyze as a task a NEWER question may cancel.

        Answers run one at a time, so a new question used to wait for the
        previous answer to stream to its end (seconds, or a whole retry +
        fallback cycle) before it even started. Returns None when superseded.
        """
        task = asyncio.ensure_future(
            service.analyze(question, model_key=model_key, on_delta=on_delta)
        )
        generating["task"] = task
        try:
            return await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # the socket is closing, not a newer question
            return None
        finally:
            generating["task"] = None

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
        if _repeat_of_last(question, service.response_length):
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
            guidance = await _generate(question, _on_delta)
            if guidance is None:
                # A newer question took over; its ai_stream_start (sent next by
                # the worker) clears these provisional bullets.
                logger.info("Answer superseded by a newer question (%d chars)", len(question))
                return
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
        fb = getattr(service, "last_fallback", None)
        if isinstance(fb, dict):
            # The first model failed (rate limit / quota / network) and another
            # one answered: record the failure too, so it stays measurable.
            logger.warning(
                "Answer came from fallback model %s after %s failed (%s)",
                service.last_model, fb["model"], fb["usage"].get("error_category"),
            )
            await asyncio.to_thread(
                log_llm_usage,
                user_id=user_id,
                request_type="interview",
                provider=fb["provider"],
                model=fb["model"],
                latency_ms=fb["usage"].get("latency_ms"),
                input_tokens=fb["usage"].get("input_tokens"),
                output_tokens=fb["usage"].get("output_tokens"),
                error_category=fb["usage"].get("error_category"),
            )
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

    #: Questions whose on-demand follow-ups are being generated (dedup clicks).
    followups_inflight: set[str] = set()

    async def _send_follow_ups(question: str) -> None:
        """Generate + send on-demand follow-ups; always answers the client."""
        try:
            items = await service.follow_ups(question, model_key=model_key)
            frame: dict = {
                "type": "ai_followups",
                "question": question,
                "followUps": [f.model_dump() for f in items],
            }
            if not items:
                frame["error"] = "No follow-ups were suggested. Try again."
            await websocket.send_json(frame)
            if service.last_usage:
                await asyncio.to_thread(
                    log_llm_usage,
                    user_id=user_id,
                    request_type="followups",
                    provider=service.last_provider,
                    model=service.last_model,
                    latency_ms=service.last_usage.get("latency_ms"),
                    input_tokens=service.last_usage.get("input_tokens"),
                    output_tokens=service.last_usage.get("output_tokens"),
                    error_category=service.last_usage.get("error_category"),
                )
        except Exception:
            logger.exception("follow-ups failed")
            try:
                await websocket.send_json(
                    {
                        "type": "ai_followups",
                        "question": question,
                        "followUps": [],
                        "error": "Could not load follow-ups. Try again.",
                    }
                )
            except Exception:
                pass  # socket already gone
        finally:
            followups_inflight.discard(_norm_question(question))

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
        clip: bytes,
        clip_mime: str,
        length_raw: object,
        model_raw: object,
        seq: int | None = None,
        flushed_at: float | None = None,
    ) -> None:
        """Transcribe one clip; always end it with exactly one frame for the client.

        The client keeps ONE clip in flight and frees the slot on that clip's
        final transcript. A clip that yields no transcript (silence, a cough,
        an STT error) used to send nothing that freed it, so the next clips
        waited ~10 s behind it and newer ones replaced older ones in the
        client's queue — losing half a question. ``clip_done`` closes those.
        """
        outcome = {"final": False}
        try:
            await _transcribe_clip(clip, clip_mime, length_raw, model_raw, seq, flushed_at, outcome)
        finally:
            if not outcome["final"]:
                try:
                    await websocket.send_json({"type": "clip_done", "seq": seq})
                except Exception:
                    logger.debug("[ws user=%s] clip_done dropped: socket closing", user_id)

    async def _transcribe_clip(
        clip: bytes,
        clip_mime: str,
        length_raw: object,
        model_raw: object,
        seq: int | None,
        flushed_at: float | None,
        outcome: dict,
    ) -> None:
        """Transcribe one interviewer audio clip, then run question detection.

        Question detection uses the same QuestionBuffer as microphone speech, so
        an interviewer question triggers exactly the same AI answer flow.

        Runs as a BACKGROUND task: the receive loop keeps reading (pings, the
        next clip, model switches) while this awaits the STT provider, and at
        most MAX_CONCURRENT_TRANSCRIPTIONS clips transcribe at once.
        """
        nonlocal audio_mime, preview_paused_until
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
                # Rate limit / provider trouble: the final clip has priority.
                preview_paused_until = time.monotonic() + PREVIEW_COOLDOWN_SECONDS
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

        text = strip_sound_tags(text)
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

        final_frame: dict = {"type": "stt_transcript", "text": text, "final": True}
        if seq is not None:
            final_frame["seq"] = seq
        await websocket.send_json(final_frame)
        outcome["final"] = True

        # The live stream heard this speech and answers (or holds) it itself:
        # judging the clip too would answer the same question twice.
        if live_answers and seq is not None and await _live_owns_clip(seq, flushed_at):
            logger.info("Clip %d left to the live transcript (%d chars)", seq, len(text))
            if not answering:
                await websocket.send_json(
                    {
                        "type": "status",
                        "status": "listening",
                        "message": "listening... (waiting for a complete question)",
                    }
                )
            return

        # Question detection for interviewer audio: the clip already ends at a
        # pause, so treat this segment as a finished utterance — but only a
        # GENUINE, complete question is answered. Answering every substantial
        # utterance used to burn an LLM call on "let me pull up your resume"
        # and on half-asked questions, and because answers run one-at-a-time on
        # the queue, each of those delayed the real question.
        question = buffer.push_complete(text)
        if question:
            logger.info("Interviewer prompt (%d chars): %s", len(question), question[:100])
            await _enqueue_answer(question, length_raw, model_raw, announce=True, source="clip")
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

    async def _preview_worker() -> None:
        """Transcribe live snapshots of the clip still being spoken.

        DISPLAY ONLY: the result is sent as `final: false` and never touches
        the QuestionBuffer or the answer queue, so a provisional transcript can
        never be answered (or answered twice). One preview runs at a time and
        only the newest snapshot is kept, which bounds the extra STT calls to
        the provider's own latency.
        """
        nonlocal preview_pending, preview_running, preview_paused_until
        try:
            while preview_pending is not None:
                clip, seq = preview_pending
                preview_pending = None
                # Stale (its clip was already flushed), paused after a provider
                # failure, or every final slot busy (finals have priority).
                if seq != clip_seq or time.monotonic() < preview_paused_until:
                    continue
                if stt_slots.locked():
                    continue
                real_mime = MIME_BY_FORMAT.get(sniff_audio(clip))
                if not real_mime:
                    continue
                t0 = time.perf_counter()
                try:
                    text = await asyncio.wait_for(
                        preview_stt.transcribe(clip, mime_type=real_mime),
                        timeout=PREVIEW_TIMEOUT_SECONDS,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    preview_paused_until = max(
                        preview_paused_until, time.monotonic() + PREVIEW_RETRY_SECONDS
                    )
                    logger.info(
                        "Live preview paused %.0fs after STT failure: %s",
                        PREVIEW_RETRY_SECONDS,
                        str(exc)[:120] or type(exc).__name__,
                    )
                    continue
                logger.info(
                    "STT preview latency: %.2fs for %d bytes",
                    time.perf_counter() - t0,
                    len(clip),
                )
                text = strip_sound_tags(text)
                if seq != clip_seq or not text or is_stt_hallucination(text):
                    continue
                await websocket.send_json(
                    {"type": "stt_transcript", "text": text, "final": False, "seq": seq}
                )
        finally:
            preview_running = False

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
            await _enqueue_answer(held, length_raw, model_raw, announce=True, source="clip")

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
    # In-flight questions: normalised text -> requested depth (None = the
    # session's current depth). Depth-aware so a deliberate re-ask at another
    # length is never mistaken for a duplicate, even if it arrives in the gap
    # between the answer frame and the worker clearing the entry.
    answering: dict[str, str | None] = {}
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
                answering.pop(_norm_question(question), None)
                answer_queue.task_done()

    #: Questions answered from interviewer AUDIO: (lane, question, monotonic).
    audio_answered: list[tuple[str, str, float]] = []

    async def _enqueue_answer(
        question: str,
        length_raw: object,
        model_raw: object,
        announce: bool = False,
        source: str | None = None,
    ) -> bool:
        """Queue one question for the answer worker.

        Returns False when the SAME question is already queued or generating —
        a repeat (mic fragment + debounced force detector, or WebSpeech
        delivering the same final twice) must never spend a second AI call, and
        the UI gets a status so its spinner does not hang.

        ``source`` ("live" / "clip") marks interviewer-audio questions: the two
        lanes hear the same speech, so one lane's question that the OTHER lane
        already answered is a duplicate even though the wording differs.
        """
        nonlocal last_activity
        last_activity = time.monotonic()
        if len(question) > MAX_QUESTION_CHARS:
            question = question[-MAX_QUESTION_CHARS:]
        if source is not None:
            now = time.monotonic()
            audio_answered[:] = [
                e for e in audio_answered if now - e[2] < AUDIO_COVER_WINDOW_SECONDS
            ]
            if any(
                lane != source and same_utterance(q, question) for lane, q, _ in audio_answered
            ):
                logger.info(
                    "Duplicate question suppressed (%s lane, answered by the other lane, %d chars)",
                    source,
                    len(question),
                )
                if not answering:
                    await websocket.send_json(
                        {
                            "type": "status",
                            "status": "ready",
                            "message": "Already answered — showing the previous guidance.",
                        }
                    )
                return False
        key = _norm_question(question)
        requested = normalize_length(length_raw) if isinstance(length_raw, str) else None
        if key in answering and (requested is None or answering[key] == requested):
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
        answering[key] = requested
        if source is not None:
            audio_answered.append((source, question, time.monotonic()))
        # The newest question wins: the candidate needs THIS answer now, not
        # the end of the previous one. Never for a repeat the worker would
        # only suppress — that would cancel an answer and replace it with
        # nothing. An answer already delivered is past this point (no task).
        running = generating["task"]
        if (
            running is not None
            and not running.done()
            and not _repeat_of_last(question, requested or service.response_length)
        ):
            logger.info("New question supersedes the answer in progress (%d chars)", len(question))
            running.cancel()
        answer_queue.put_nowait((question, length_raw, model_raw, announce))
        return True

    # ---- Live lane: answer from the streamed transcript (LiveTurnTracker) ----

    def _mark_live_owned(after_cut: bool) -> None:
        """Record which clip holds the speech the live lane just answered."""
        # A pause is heard before the cut, so that speech is still in the clip
        # being recorded. A turn close / settle normally lands just AFTER the
        # cut (speech in the clip just flushed) — but not always, and a missed
        # mark would answer the question twice, so both are claimed then.
        live_owned_seqs.add(clip_seq)
        if after_cut and time.monotonic() - last_flush_at < LIVE_FLUSH_WINDOW_SECONDS:
            live_owned_seqs.add(clip_seq - 1)
        for old in [s for s in live_owned_seqs if s < clip_seq - 8]:
            live_owned_seqs.discard(old)

    async def _answer_live(question: str | None, after_cut: bool = True) -> None:
        if not question:
            return
        _mark_live_owned(after_cut)
        logger.info("Live prompt (%d chars): %s", len(question), question[:100])
        await _enqueue_answer(
            question, live_length_raw, live_model_raw, announce=True, source="live"
        )

    async def _after_live_decision(question: str | None, after_cut: bool = True) -> None:
        """Answer, or schedule the re-check / carry flush the tracker asked for."""
        nonlocal live_settle_gen
        if question:
            await _answer_live(question, after_cut)
        elif live_turns.recheck and live_turns.gen != live_settle_gen:
            live_settle_gen = live_turns.gen
            _spawn(_live_settle(live_turns.gen))
        elif live_turns.carrying:
            _spawn(_live_flush_carry())

    async def _on_live_decision(question: str | None) -> None:
        # Runs inside the live stream's reader: a failure here must never
        # take the word-by-word transcript down with it.
        try:
            await _after_live_decision(question)
        except Exception:
            logger.exception("[ws user=%s] live answering failed", user_id)

    async def _live_pause(after_cut: bool) -> None:
        await _after_live_decision(live_turns.pause(), after_cut)

    async def _live_settle(gen: int) -> None:
        await asyncio.sleep(LIVE_SETTLE_SECONDS)
        await _answer_live(live_turns.settle(gen))

    async def _live_flush_carry() -> None:
        await asyncio.sleep(live_turns.carry_window)
        await _answer_live(live_turns.flush_carry())

    async def _live_owns_clip(seq: int, flushed_at: float | None) -> bool:
        """Should the live lane, not this clip, decide about this speech?"""
        if seq in live_owned_seqs:
            return True
        if live_stream is None or not live_stream.available:
            return False  # the stream is not hearing anything: no reason to wait
        if flushed_at is not None:
            wait = flushed_at + LIVE_GRACE_SECONDS - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        return seq in live_owned_seqs or live_turns.deciding()

    _spawn(_answer_worker())

    # Initial handshake keeps the existing client contract intact.
    logger.info("[ws user=%s] accepted; sending handshake", user_id)
    await websocket.send_json(
        {
            "type": "status",
            "status": "listening",
            "message": "connected. Send {type:'transcript', text}.",
            # Feature flags: the client sends audio_preview snapshots only when
            # livePreview is set, and streams LPCM frames only when liveStream is.
            "livePreview": live_preview,
            "liveStream": live_stream is not None,
            # The client sends speech_pause frames only when this is set.
            "liveAnswers": live_answers,
            # Trial / plan state (additive field; later updates arrive as
            # {type:"access"} frames from the trial meter).
            "access": _access_frame(access, client_kind),
        }
    )

    async def _trial_meter() -> None:
        while True:
            await asyncio.sleep(TRIAL_TICK_SECONDS)
            active = interviewer_capturing or (
                last_activity and time.monotonic() - last_activity < TRIAL_IDLE_GRACE_SECONDS
            )
            if not active:
                continue
            state = await asyncio.to_thread(
                _trial_tick, user_id, client_kind, max(1, round(TRIAL_TICK_SECONDS))
            )
            try:
                await websocket.send_json(_access_frame(state, client_kind))
                if state.mode == "denied":
                    logger.info("[ws user=%s] live trial ran out (%s); closing", user_id, client_kind)
                    await websocket.close(code=TRIAL_EXHAUSTED_CLOSE_CODE, reason="trial_exhausted")
            except Exception:
                return  # client already gone
            if state.mode != "trial":
                return

    if access.mode == "trial":
        _spawn(_trial_meter())

    try:
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                logger.info("[ws user=%s] client disconnected", user_id)
                break
            except Exception:
                # Newer Starlette raises a different error type when receive()
                # runs after the disconnect frame; that is a normal close, not
                # a server error (and the socket can no longer be written to).
                if websocket.client_state == WebSocketState.DISCONNECTED:
                    logger.info("[ws user=%s] client disconnected", user_id)
                    break
                logger.exception("[ws user=%s] receive() failed", user_id)
                await websocket.send_json(
                    {"type": "error", "message": "Expected JSON message."}
                )
                continue

            # ---- Binary frame: interviewer audio chunk ----
            if message.get("type") == "websocket.receive" and message.get("bytes"):
                chunk = message["bytes"]
                if chunk[:4] == LIVE_PCM_TAG:
                    # Live transcript audio: never part of a clip.
                    if interviewer_capturing and live_stream is not None:
                        live_stream.feed(chunk[4:])
                    continue
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

            elif mtype == "request_followups":
                # "Show follow-ups" button: generate "If they ask further" for
                # an answered question, off the receive loop and outside the
                # answer queue so it never delays a live answer.
                q = msg.get("question")
                if isinstance(q, str) and q.strip() and len(q) <= MAX_FOLLOWUP_QUESTION_CHARS:
                    q = q.strip()
                    if _norm_question(q) not in followups_inflight:
                        followups_inflight.add(_norm_question(q))
                        _spawn(_send_follow_ups(q))
                else:
                    await websocket.send_json(
                        {"type": "ai_followups", "question": q if isinstance(q, str) else "",
                         "followUps": [], "error": "Invalid question."}
                    )

            elif mtype == "reset":
                buffer = QuestionBuffer()
                live_turns.reset()
                audio_buffer = bytearray()
                clip_seq += 1  # in-flight previews belong to the old session
                await websocket.send_json(
                    {"type": "status", "status": "listening", "message": "buffer reset"}
                )

            # ---- Interviewer audio lifecycle ----
            elif mtype == "interviewer_audio_start":
                interviewer_capturing = True
                audio_buffer = bytearray()
                live_turns.reset()
                last_flush_at = time.monotonic()
                # The client may not know the final format yet (it depends on
                # how MediaRecorder output is decoded). Each clip announces its
                # own mimeType in audio_flush, which always wins.
                audio_mime = msg.get("mimeType") or DEFAULT_AUDIO_MIME
                # Open the STT connection now, while the interviewer is still
                # talking, so the first clip skips the TCP+TLS setup.
                _spawn(prewarm_stt())
                if live_stream is not None:
                    live_stream.warm()
                await websocket.send_json(
                    {
                        "type": "status",
                        "status": "listening",
                        "message": "Listening for the interviewer's question...",
                    }
                )

            elif mtype == "interviewer_audio_stop":
                interviewer_capturing = False
                stop_at = time.monotonic()
                if audio_buffer:
                    # Snapshot synchronously, then transcribe in the background
                    # so the "stopped" status (and everything else) stays fast.
                    clip = bytes(audio_buffer)
                    audio_buffer = bytearray()
                    _spawn(
                        _transcribe_and_detect(
                            clip, audio_mime, None, None, clip_seq, stop_at
                        )
                    )
                if live_answers:
                    # No more audio reaches the stream, so its turn never
                    # closes by itself: judge what it heard now.
                    await _answer_live(live_turns.close(), after_cut=False)
                    live_turns.reset()
                clip_seq += 1
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
                seq = clip_seq
                clip_seq += 1  # previews of this clip are stale from now on
                last_flush_at = time.monotonic()
                _spawn(
                    _transcribe_and_detect(
                        clip,
                        audio_mime,
                        msg.get("responseLength"),
                        msg.get("model"),
                        seq,
                        last_flush_at,
                    )
                )
                if live_answers and interviewer_capturing:
                    # The cut means ~0.85 s of quiet: also a pause for the
                    # live lane (the only one an older client sends).
                    live_length_raw = msg.get("responseLength")
                    live_model_raw = msg.get("model")
                    await _live_pause(after_cut=True)

            elif mtype == "speech_pause":
                # The interviewer went quiet for ~0.45 s: a finished sentence
                # in the live transcript is answered now, before the clip cut.
                if live_answers and interviewer_capturing:
                    if isinstance(msg.get("responseLength"), str):
                        live_length_raw = msg["responseLength"]
                    await _live_pause(after_cut=False)

            elif mtype == "audio_preview":
                # Live transcript: the binary frame just before this is a
                # snapshot of the clip STILL being spoken. Take it out of the
                # buffer (it is not part of the final clip) and hand it to the
                # preview lane; the newest snapshot replaces an unsent one.
                snapshot = bytes(audio_buffer)
                audio_buffer = bytearray()
                if snapshot and interviewer_capturing and live_preview:
                    preview_pending = (snapshot, clip_seq)
                    if not preview_running:
                        preview_running = True
                        _spawn(_preview_worker())

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
    except Exception:
        logger.exception("[ws user=%s] unhandled websocket error", user_id)
        try:
            # Never echo exception text to the client (it can carry internal
            # paths, provider responses or SQL); the traceback is in the log.
            await websocket.send_json(
                {"type": "error", "message": "Unexpected server error. Please reconnect."}
            )
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
        if live_stream is not None:
            await live_stream.close()