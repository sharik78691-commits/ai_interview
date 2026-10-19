"""Screenshot analysis for the desktop app (server-side Gemini call).

The desktop .exe ships NO AI key: it sends the user's screenshots here with
the normal session cookie, and this service calls Gemini with GEMINI_API_KEY
from the server environment. The key never leaves the backend — it is sent in
a header (never the URL) and redacted from anything logged or returned.
"""
import base64
import binascii
import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Requests reach Render through the Vercel /api rewrite, which rejects bodies
# over ~4.5 MB. Base64 adds ~33%, so ~3 MB of image bytes keeps the JSON body
# safely under that. The desktop app enforces the same numbers before sending.
MAX_IMAGES = 10
MAX_TOTAL_BYTES = 3 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
_MAGIC = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}

REQUEST_TIMEOUT = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=15.0)

SYSTEM_PROMPT = """You are an interview-preparation coach. The user shares screenshots of interview questions (mock interviews, practice material, or content they own) and wants clear answer guidance to learn from.
Read ALL screenshots in order. Respond ONLY with JSON matching this shape:
{
  "questions": [string],            // every distinct question found, verbatim where possible
  "topic": string,                  // e.g. "System design - caching"
  "seniority": string,              // expected level: "Junior" | "Mid" | "Senior" | "Staff+" with a short reason
  "directAnswer": string,           // concise, direct answer (2-4 sentences)
  "interviewAnswer": string,        // polished, spoken interview-style answer
  "keyPoints": [string],            // key points to mention
  "followUps": [{"question": string, "suggestedResponse": string}],
  "code": null | {                  // only when code is visible
    "explanation": string,
    "complexity": string,           // time and space
    "edgeCases": [string],
    "improvedAnswer": string        // improved solution/answer, code in a fenced block if useful
  }
}
If no question is visible, return an empty "questions" array and explain in "directAnswer". Do not invent content that is not in the screenshots."""


class ScreenshotAnalysisError(Exception):
    """A failure with a stable ``code`` the desktop app maps to a message."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class DecodedImage:
    mime_type: str
    data: str  # base64, as sent to Gemini
    size: int  # decoded bytes


def is_configured() -> bool:
    return bool(get_settings().gemini_api_key)


def model_name() -> str:
    return get_settings().screenshot_model


def redact(text: str, api_key: str = "") -> str:
    out = str(text or "")
    if api_key:
        out = out.replace(api_key, "[redacted]")
    return re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[redacted]", out)


def decode_images(images: list[tuple[str, str]]) -> list[DecodedImage]:
    """Validate (mime, base64) pairs: count, type, encoding, content, size."""
    if not images:
        raise ScreenshotAnalysisError("VALIDATION", "Add at least one screenshot.", 422)
    if len(images) > MAX_IMAGES:
        raise ScreenshotAnalysisError(
            "VALIDATION", f"Too many screenshots — at most {MAX_IMAGES} per analysis.", 422
        )
    decoded: list[DecodedImage] = []
    total = 0
    for index, (mime, data) in enumerate(images, start=1):
        if mime not in ALLOWED_MIME:
            raise ScreenshotAnalysisError(
                "VALIDATION", f"Screenshot {index} is not a JPEG, PNG or WebP image.", 422
            )
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise ScreenshotAnalysisError(
                "VALIDATION", f"Screenshot {index} is not valid image data.", 422
            ) from None
        if not raw.startswith(_MAGIC[mime]):
            raise ScreenshotAnalysisError(
                "VALIDATION", f"Screenshot {index} does not match its image type.", 422
            )
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ScreenshotAnalysisError(
                "TOO_LARGE",
                f"Screenshots are too large together (max {MAX_TOTAL_BYTES // (1024 * 1024)} MB). "
                "Remove some and try again.",
                413,
            )
        decoded.append(DecodedImage(mime_type=mime, data=data, size=len(raw)))
    return decoded


def _as_str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for s in (_as_str(v) for v in value) if s]


def normalize_result(raw) -> dict:
    """Coerce arbitrary model JSON into the strict shape the desktop UI renders."""
    if not isinstance(raw, dict):
        raise ScreenshotAnalysisError(
            "BAD_RESPONSE", "The model returned an unreadable answer. Try again.", 502
        )
    code = None
    raw_code = raw.get("code")
    if isinstance(raw_code, dict):
        code = {
            "explanation": _as_str(raw_code.get("explanation")),
            "complexity": _as_str(raw_code.get("complexity")),
            "edgeCases": _as_str_list(raw_code.get("edgeCases")),
            "improvedAnswer": _as_str(raw_code.get("improvedAnswer")),
        }
        if not any(code.values()):
            code = None
    follow_ups = []
    for item in raw.get("followUps") if isinstance(raw.get("followUps"), list) else []:
        if isinstance(item, dict) and _as_str(item.get("question")):
            follow_ups.append(
                {
                    "question": _as_str(item.get("question")),
                    "suggestedResponse": _as_str(item.get("suggestedResponse")),
                }
            )
    return {
        "questions": _as_str_list(raw.get("questions")),
        "topic": _as_str(raw.get("topic")),
        "seniority": _as_str(raw.get("seniority")),
        "directAnswer": _as_str(raw.get("directAnswer")),
        "interviewAnswer": _as_str(raw.get("interviewAnswer")),
        "keyPoints": _as_str_list(raw.get("keyPoints")),
        "followUps": follow_ups,
        "code": code,
    }


def _parse_model_json(text: str):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except ValueError:
        raise ScreenshotAnalysisError(
            "BAD_RESPONSE", "The model returned an unreadable answer. Try again.", 502
        ) from None


def _extract_text(body: dict) -> str:
    block = (body.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise ScreenshotAnalysisError(
            "BLOCKED", f"The request was blocked by the model's safety filter ({block}).", 422
        )
    candidates = body.get("candidates") or []
    cand = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text:
        reason = cand.get("finishReason")
        if reason and reason != "STOP":
            raise ScreenshotAnalysisError(
                "BLOCKED", f"The model stopped without an answer ({reason}).", 422
            )
        raise ScreenshotAnalysisError(
            "BAD_RESPONSE", "The model returned an empty answer. Try again.", 502
        )
    return text


def _http_error(status: int) -> ScreenshotAnalysisError:
    if status in (401, 403):
        # The SERVER key is wrong — not something the desktop user can fix.
        return ScreenshotAnalysisError(
            "SERVER_KEY_INVALID", "Screenshot analysis is not available right now.", 503
        )
    if status == 429:
        return ScreenshotAnalysisError(
            "RATE_LIMITED", "AI quota reached. Wait a moment and try again.", 429
        )
    if status == 413:
        return ScreenshotAnalysisError(
            "TOO_LARGE", "The request was too large. Remove some screenshots.", 413
        )
    return ScreenshotAnalysisError(
        "PROVIDER_ERROR", "The AI provider is having trouble. Try again shortly.", 502
    )


def build_request_body(images: list[DecodedImage]) -> dict:
    parts: list[dict] = []
    for i, img in enumerate(images, start=1):
        parts.append({"text": f"Screenshot {i} of {len(images)}:"})
        parts.append({"inline_data": {"mime_type": img.mime_type, "data": img.data}})
    parts.append({"text": "Analyze these screenshots and respond with the JSON described."})
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }


async def analyze(images: list[DecodedImage], client: httpx.AsyncClient | None = None) -> dict:
    settings = get_settings()
    api_key = settings.gemini_api_key
    if not api_key:
        raise ScreenshotAnalysisError(
            "NOT_CONFIGURED", "Screenshot analysis is not set up on the server yet.", 503
        )
    model = settings.screenshot_model
    url = f"{GEMINI_BASE}/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    try:
        res = await client.post(url, headers=headers, json=build_request_body(images))
    except httpx.TimeoutException:
        raise ScreenshotAnalysisError(
            "TIMEOUT", "The AI request timed out. Try again with fewer screenshots.", 504
        ) from None
    except httpx.RequestError:
        raise ScreenshotAnalysisError(
            "PROVIDER_ERROR", "Could not reach the AI provider. Try again shortly.", 502
        ) from None
    finally:
        if owns_client:
            await client.aclose()

    if res.status_code >= 400:
        logger.warning(
            "Screenshot analysis provider error %s: %s",
            res.status_code,
            redact(res.text, api_key)[:300],
        )
        raise _http_error(res.status_code)
    try:
        body = res.json()
    except ValueError:
        raise ScreenshotAnalysisError(
            "BAD_RESPONSE", "The AI provider returned an unreadable response.", 502
        ) from None
    return normalize_result(_parse_model_json(_extract_text(body if isinstance(body, dict) else {})))
