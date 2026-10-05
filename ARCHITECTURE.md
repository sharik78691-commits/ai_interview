# Architecture — AI Interview Assistant (Web MVP)

Modular monolith. Angular SPA ↔ FastAPI over REST + WebSocket.
No DB, no auth, no queue — session state lives in memory/localStorage behind
interfaces so persistence can be added later.

## Runtime topology

```
┌────────────────────── Angular 20 SPA (:4200) ──────────────────────┐
│ landing → dashboard → interview (+ settings)                       │
│ core/services: AudioService (candidate mic),                       │
│   InterviewerAudioService (meeting/tab audio → WAV clips),         │
│   TranscriptionService, QuestionDetectorService,                   │
│   InterviewWsService, ResumeService, SettingsService, DemoService  │
│ speech/: SpeechToTextProvider iface → WebSpeechProvider            │
│   (swap for server-STT or desktop-audio later, UI untouched)       │
└──────────────┬───────────────────────────────┬─────────────────────┘
               │ REST /api/* (HttpClient)      │ WS /ws/interview
┌──────────────▼───────────────────────────────▼─────────────────────┐
│ FastAPI (uvicorn :8000), CORS → :4200                              │
│ api/: health, resume (upload), interview (prepare/context/analyze),│
│   websocket (accept → QuestionBuffer → AIService → ai_guidance;    │
│   binary audio → sniff_audio → STTProvider → QuestionBuffer)       │
│ services/: ResumeService, QuestionService(QuestionBuffer),         │
│   AIService (session ctx + LLM call), TranscriptionService         │
│   (per-connection transcript registry)                             │
│ providers/llm: LLMProvider → MockLLMProvider |                     │
│   OpenAICompatibleLLMProvider (strict-JSON, mock fallback)         │
│ providers/stt: STTProvider → GroqWhisperSTTProvider (Whisper via   │
│   OpenAI-compatible /audio/transcriptions) | MockSTTProvider       │
│ models/: ResumeData, AIInterviewResponse (validated), interview ctx │
│ core/: Settings (env), logging                                     │
└────────────────────────────────────────────────────────────────────┘
```

## Key flows

**Prepare:** `POST /api/interview/prepare {resumeText, jobDescription}`
validates length → stores in module `_context {resume, job}` → `{status:"ready"}`.
Angular also persists both strings + parsed resume in `localStorage`.

**Live loop:** mic → `AudioService.getUserMedia` → `WebSpeechProvider`
(interim + final) → `TranscriptionService.transcript$` → UI append +
`QuestionDetectorService` (debounce ~1200ms; `?`/wh-word/length heuristics)
→ `InterviewWsService.sendTranscript` → server `QuestionBuffer.push`
→ question? → `AIService.analyze(question)` with `_context` resume+job →
`LLMProvider.analyze_question` → validated `AIInterviewResponse` →
WS `{type:"ai_guidance", data}` → guidance card + history. WS down →
direct `POST /api/interview/analyze` fallback; backend down → `DemoService`
on-device guidance so the UI stays demonstrable.

**Question detection (both sides, same rules):** fragments accumulate until a
flush: text ending in `?`, or >120 pending chars, or debounce expiry; `is_question`
requires a `?` (≥3 words) or a leading wh-word/modal with >20 chars and ≥4 words.
Interviewer-audio clips are already cut at pauses, so `push_complete()` treats any
substantial utterance (≥4 words or ≥25 chars) as a finished prompt. This avoids an
LLM call per partial transcript.

## Logging & error tracking

Errors are logged on both sides so a production failure is diagnosable without
reproducing it locally.

**Backend** (`app/core/logging.py`):
- One line per request (method, path, status, duration, `origin`, request id) via
  `RequestLoggingMiddleware`; 4xx → WARNING, 5xx → ERROR, unhandled → traceback.
- `error.log` in `backend/logs/` **always** captures ERROR+ (rotating 5MB × 3),
  so errors survive restarts. `LOG_TO_FILE=1` additionally writes every line to
  `app.log`. `LOG_LEVEL`/`LOG_FORMAT` (text|json) control verbosity/shape.
- Endpoints log decision inputs (resume upload size/type, analyze question length,
  OAuth state host, cookie presence) so "works locally, fails in prod" issues are
  visible. Config is logged once at startup, redacting DB passwords.

**Frontend**:
- `errorLoggingInterceptor` logs every failed HTTP call (`[http] 502 POST /api/…`)
  so it pairs with the backend request-id line.
- `GlobalErrorHandler` (`ErrorHandler`) captures uncaught Angular errors.
- `InterviewWsService` logs socket close codes/reasons, server `error` frames and
  malformed messages.

## Decisions

- **No DB:** MVP session only. `InterviewRepository`-style seam = `_context` dict
  server-side + `history$` client-side; add Postgres later without touching WS schema.
- **Browser STT for the candidate mic, server STT for meeting audio:** the Web
  Speech API only accepts microphone input, so the candidate's own voice is
  transcribed in-browser via `SpeechToTextProvider`. Meeting/tab audio cannot be
  transcribed locally, so it is streamed to the backend and transcribed by
  `STTProvider` (Whisper). Both interfaces mean desktop system-audio is a new
  provider, not a rewrite.
- **Strict-JSON Pydantic validation:** every AI reply must parse as
  `AIInterviewResponse{question, questionType, answerPoints[1..], star?, codeHint?, example?, keyTakeaways[], followUpQuestions[], responseLength}`; OpenAI path strips code fences and falls back to mock.
- **Demo-first:** mock LLM + scripted `DemoService` sequences keep the whole UI
  testable with zero keys; `demo_mode = !LLM_API_KEY` unless overridden.

## Interviewer audio (meeting / tab) vs. candidate mic

Two deliberately independent audio pipelines, because the app is used by the
interviewee and must never feed the candidate's own voice to the AI:

```
Candidate mic ──> AudioService.getUserMedia ──> WebSpeechProvider (browser STT)
                     └── setMuted()/toggleMute() (tracks.enabled = false)
                                  │
                                  └──> NEVER used for interviewer questions

Meeting/tab ────> InterviewerAudioService.getDisplayMedia({audio})
                     AudioContext graph
                       ├─ AnalyserNode      (level meter, fallback path)
                       └─ ScriptProcessor   ──> raw Float32 PCM
                                                   │
                     silence detection ends clip ──┤
                                                   ▼
                                        wav-encoder.ts  (downmix + 16 kHz mono)
                                                   │
                                                   ▼
                                  16 kHz mono PCM WAV bytes  (no container)
                                                   │
FastAPI /ws/interview ──> sniff magic bytes ─> correct the declared mime
                                                   │
                                            STTProvider (Whisper)
                                                   │
                                            QuestionBuffer.push_complete()
                                                   └─> AIService.analyze()  [same AI path]
                                                        └─> {ai_guidance} ──> existing card
```

Why raw PCM instead of a container: `MediaRecorder` emits a live WebM with no
duration, and fragments it while recording. Stitched partially, the result is an
invalid file (`HTTP 400 could not process file`); its stop/restart lifecycle also
produced two uploads per question (`429`). Encoding samples ourselves removes the
container entirely, and the backend still sniffs the magic bytes so any client
that does send a container gets labelled correctly.

Why server-side STT: the Web Speech API accepts microphone input only, so tab /
meeting audio cannot be recognised in the browser. Clips are cut at pauses
(silence detection) so STT gets short clean snippets rather than a whole
meeting recording.

Extension seams for the desktop phase:
- New `STTProvider` implementation (Deepgram, local Whisper, desktop audio) —
  the WebSocket contract (`audio_flush{mimeType}` → `stt_transcript`) does not change.
- New `SpeechToTextProvider` for system audio — untouched UI.
- Meeting integrations can simply post `{type:"interviewer_question"}` or stream
  their own audio through the same messages; no AI or layout changes.
- `sniff_audio()` and `MIME_BY_FORMAT` cover wav/webm/mp4/ogg/flac/mp3, so a new
  container only needs an entry there.

## Extension seams (desktop phase)

1. New `SpeechToTextProvider` (e.g. `DesktopAudioProvider`) + optional server
   `/api/stt/chunk` — transcript pipeline unchanged.
2. Meeting integrations post `{type:"transcript"}` to the same WS endpoint.
3. Add storage: implement a session store behind `AIService`/history, keep message shapes.
4. Auth/payments: new FastAPI dependencies + route guards; no core flow changes.
