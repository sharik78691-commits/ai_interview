# AI Interview Assistant — Web MVP

Real-time, resume-aware interview guidance in the browser.
Upload a resume, paste the job description, then get concise AI answer points
as interview questions are detected from live microphone transcription.

> Web MVP only. Microphone/audio input depends on browser permissions and
> capabilities. No system-audio capture, no Zoom/Teams/Webex integrations,
> no desktop wrapper in this phase.

## Tech stack

- Frontend: Angular 20 (standalone), TypeScript, RxJS, hand-written CSS
- Backend: Python 3.10+, FastAPI, WebSocket, Pydantic v2
- Speech: browser Web Speech API behind a `SpeechToTextProvider` abstraction
- AI: `LLMProvider` abstraction — mock/demo by default, OpenAI-compatible HTTP provider when `LLM_API_KEY` is set

## Quick start (Demo mode, no keys)

**Backend**

```powershell
cd backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Health: `GET http://localhost:8000/api/health` → `{"status":"ok","demo_mode":true,...}`
Docs: `http://localhost:8000/docs`

**Frontend**

```powershell
cd frontend
npm install
npm run start   # http://localhost:4200 (proxied /api + /ws -> :8000)
```

Flow: Landing → Get Started → Dashboard (upload resume + paste JD + Prepare)
→ Interview (Start, then Demo or speak/type). `Try Demo` skips the backend.

## Real AI mode

1. Copy `.env.example` to `backend/.env`, set `LLM_API_KEY` (+ optional `LLM_MODEL`, `LLM_BASE_URL`).
2. Restart uvicorn. `demo_mode` becomes `false`; `/api/interview/analyze` and the
   WebSocket path call the configured OpenAI-compatible `chat/completions` endpoint
   with a strict-JSON system prompt, falling back to the mock provider on failure.
3. Keys never leave the backend — Angular only talks to `/api/*` and `/ws/interview`.

## Interviewer audio detection (meeting / browser tab)

The app is used by the **interviewee**, so questions must come from the
interviewer, not from the candidate's own microphone.

Two separate audio sources exist and never mix:

| Source | Service | Used for |
|---|---|---|
| Your microphone | `AudioService` + Web Speech API | your own voice (notes, practice) |
| Meeting / browser tab | `InterviewerAudioService` → server STT | **interviewer question detection** |

How it works:

1. Click **Select meeting audio** and choose the meeting tab/window. The browser
   asks you to allow audio — tick **"Also share tab audio"** (Chrome/Edge).
2. Short clips are recorded and cut automatically when the interviewer pauses
   (silence detection via `AudioContext` energy check).
3. Each clip is streamed to the backend over the existing WebSocket and
   transcribed by the STT provider (Whisper).
4. The recognised text goes through the **same** `QuestionBuffer` detection and
   the **same** `AIService.analyze()` used by every other path, so the answer
   appears in the existing AI Guidance card. No second AI system exists.

Requirement: browser tab audio + speech-to-text. Browsers cannot transcribe tab
audio locally, so transcription is server-side:

```env
STT_API_KEY=          # optional: falls back to LLM_API_KEY
STT_MODEL=whisper-large-v3-turbo   # Groq; use whisper-1 for OpenAI
STT_BASE_URL=         # optional: defaults to LLM_BASE_URL
```

Check `GET /api/health` → `stt_configured: true`.

Manual fallback (always available): paste or type the question and press
**Generate Answer** — same AI function, same answer card. Also **Clear** and
**Copy Answer** (copies points, takeaways, example, STAR and follow-ups).

Status messages shown in the UI: `Select meeting audio` → `Listening for the
interviewer's question…` → `Question detected` → `Generating answer…` →
`Answer ready`, plus `Audio permission was denied` and `Browser audio capture is
unavailable. Please paste the question manually.`

> Chrome/Edge on `localhost` or HTTPS. Safari/Firefox support for
> `getDisplayMedia` audio varies; the manual fallback always works.

### Why the app cannot read Teams/Meet audio by itself

Products such as Final Round AI are **desktop apps**: they hook the operating
system audio mixer (WASAPI loopback on Windows, ScreenCaptureKit on macOS) and
therefore hear every meeting app with zero user action. A web page has no such
access — the only audio a website may receive is what the user explicitly shares
through the browser. That is why this web build uses **tab/window sharing with
audio** instead of silent system capture.

| Capability | Desktop app | This web build |
|---|---|---|
| Hears Teams/Meet/Zoom with no picker | Yes (OS mixer) | No |
| Hears the selected tab with audio | Yes | **Yes — user picks the tab** |
| Separate candidate mic mute | Yes | Yes |

If OS-level capture is wanted later, wrap this frontend in a thin Electron
shell: add a `SystemAudioProvider` and forward the stream into the existing
`/ws/interview` binary frame. No AI, API or UI change needed.

### Troubleshooting

| Message | Cause | Fix |
|---|---|---|
| `Browser audio capture is unavailable. Please paste the question manually.` | Backend has no STT provider | Start the server from `backend/` **or** check `backend/.env` has `STT_MODEL=whisper-large-v3-turbo`. `GET /api/health` must show `stt_configured: true` |
| `Tab audio sharing needs a secure address…` | Opened over a LAN IP (`http://192.168.x.x`) | Use `http://localhost:4200` or HTTPS |
| `This browser cannot share tab audio…` | Safari/Firefox, or no `getDisplayMedia` | Use Chrome or Edge on desktop |
| `No audio track was shared…` | "Also share tab audio" not ticked | Re-pick the tab and tick the checkbox |
| `Audio permission was denied` | User cancelled / blocked the picker | Allow the tab, then press the button again |
| `Speech-to-text model '…' is not available on …` | Wrong `STT_MODEL` for the provider | Groq → `whisper-large-v3-turbo`, OpenAI → `whisper-1` |
| `The recorded audio could not be decoded` | Broken or partial clip | Fixed in code — clips are re-encoded as 16 kHz WAV before upload. If it persists, re-share the tab |
| `Speech-to-text rate limit reached` | Too many clips in a burst | Handled automatically: 3 attempts with backoff (honours `Retry-After`), one transcription in flight at a time |

`backend/.env` is loaded by absolute path, so the server can be started from any
working directory without losing the API keys.

### Why clips are converted to WAV

Three separate things were breaking transcription:

| Symptom | Cause | Fix |
|---|---|---|
| `HTTP 400 could not process file` | Sending a partial container (a stopped/restarted `MediaRecorder` emits headerless fragments) | Clips are now encoded straight from **raw PCM** — no container at all |
| `HTTP 400 could not process file` | WAV bytes labelled `audio/webm` (or the reverse) | The backend **sniffs the magic bytes** and corrects the label before uploading |
| `rate limit reached` | Two requests per question (the recorder stop/restart race) | One recorder, one clip per sentence, one request in flight, 3 retries with backoff |

How capture works now:

1. `getDisplayMedia({audio:true})` shares the meeting/tab. Permission is requested
   only from a user click.
2. A `ScriptProcessor` on that stream hands us raw PCM. The audio graph ends in a
   **muted gain**, so the meeting is not played back a second time.
3. Silence detection (RMS on the same samples) ends the clip when the interviewer
   pauses; a 15 s cap handles uninterrupted talking. Silence-only clips are never
   uploaded — they only burned quota.
4. `wav-encoder.ts` downmixes, resamples to **16 kHz mono PCM** and writes a RIFF
   WAV. Seekable, universally accepted, exactly Whisper's native rate.
5. The backend reads `RIFF…WAVE` (or EBML/ftyp/OggS…) and, if the declared mime
   disagrees, overwrites it with the correct one. Unreadable bytes are rejected
   locally with a clear message instead of being sent to the provider.
6. Fallbacks: if `ScriptProcessor` is unavailable we decode `MediaRecorder` output
   with `decodeAudioData`; if that fails too, the original bytes are sent rather
   than losing the question.

## Browser microphone permissions

- Use Chrome/Edge on `http://localhost` or HTTPS; grant mic access on first Start.
- If blocked: the app shows `Microphone permission denied` and you can still type
  questions manually or use Demo mode.
- `Speech recognition unavailable` appears where the Web Speech API is missing.

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | — | `{status, demo_mode, llm_configured, stt_configured, stt_model}` |
| POST | `/api/resume/upload` | multipart `file` (.pdf/.docx/.txt ≤10MB) | `{filename, chars, resume: ResumeData}` |
| POST | `/api/interview/prepare` | `{resumeText, jobDescription, responseLength?}` (≥50 chars each) | `{status:"ready", message, resumeChars, jobChars, responseLength}` |
| GET | `/api/interview/context` | — | truncated context summary |
| POST | `/api/interview/analyze` | `{question, resumeText?, jobDescription?, responseLength?}` | `AIInterviewResponse` |
| WS | `/ws/interview` | `{type:"transcript",text,responseLength?}` / `{type:"force",text,responseLength?}` / `{type:"interviewer_question",text,responseLength?}` / `{type:"interviewer_audio_start"}` / `{type:"interviewer_audio_stop"}` / binary audio frame + `{type:"audio_flush"}` / `{type:"reset"}` / `{type:"ping"}` | `{type:"ai_guidance",data}` / `{type:"stt_transcript",text}` / `{type:"status"}` / `{type:"notice"}` / `{type:"error"}` |

### WebSocket message flow for interviewer audio

```
browser                                   backend
{interviewer_audio_start}          ───►   mark capturing
<binary webm/opus chunk>          ───►   append to clip buffer
{audio_flush}                     ───►   Whisper STT ─► text
                                           │
        ◄── {stt_transcript, text}         │
        ◄── {status: Question detected}    │ QuestionBuffer.push_complete()
        ◄── {ai_guidance, data}            │ AIService.analyze()  ← existing AI feature
```

## Answer detail (short / medium / long)

The user picks how much the AI writes — from the Interview screen's
**Answer detail** selector or Settings. The level travels to the backend with
every question and changes the system prompt:

| Level | Answer points | Also included |
|---|---|---|
| `short` | 3–4 crisp bullets (≤12 words) | fast mental cue for quick answers |
| `medium` (default) | 5–7 bullets, 1–2 sentences each | 1 short example, 2–3 key takeaways |
| `long` | 8–12 detailed bullets (20–45 words) | senior-engineer framing: definition → mechanism → failure modes → alternatives → production relevance; detailed real-world example; 3–5 takeaways; 3–5 deeper follow-ups |

Changing the selector mid-interview re-asks the current question at the new
depth so you can compare instantly. Backend responses also include
`responseLength`, `keyTakeaways` and `example` fields, validated by Pydantic.
Token budgets scale per level (700 / 1600 / 3200) so long answers are never truncated.

## Testing

```powershell
cd backend; python -m pytest -q        # 16 tests: resume, question buffer, mock AI, API
cd ../frontend; npm run build          # production build (Karma specs: npm test)
```

External AI/STT calls are mocked/fallback in all tests.

## Privacy

Resume and interview context are processed only to provide interview assistance.
The MVP keeps context in server memory (per-process `_context`) and browser
`localStorage`; nothing is written to a permanent database. Full resume text and
transcripts are not logged — only lengths and error summaries.

## Limits → roadmap

```
Web MVP (this repo)
  → Desktop app wrapper
  → System-audio capture (STT provider swap, no UI rewrite)
  → Zoom/Meet/Teams/Webex integrations
  → Persistent history, auth, advanced AI
```
