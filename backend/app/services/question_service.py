"""Debounced question detection over streaming transcript fragments.

Two very different callers feed this buffer:

* ``push`` — live MICROPHONE fragments, arriving continuously. A fragment that
  merely LOOKS like a question ("Can you explain the saga pattern in…") must
  not fire, because the rest of the sentence is still being spoken.

* ``push_complete`` — one finished utterance, e.g. a server-side STT clip that
  was already cut at a pause. That clip boundary is only a guess: a human
  interviewer pauses mid-question, so a single clip may be the WHOLE question,
  the FIRST HALF of one, or just "okay, great".

Answering every "substantial" utterance (the previous rule) produced exactly
the noise it was meant to avoid: backchannels, "let me pull up your resume",
and half-asked questions each burned an answer — and because answers run
one-at-a-time on a queue, every false trigger delayed the real question. So a
finished utterance is CLASSIFIED instead:

* ``ANSWER`` — a genuine, complete interviewer prompt → answer at once.
* ``PARTIAL`` — the clip boundary landed mid-question → hold it, then stitch it
  back together with the next clip rather than answering a fragment.
* ``NOISE``  — backchannels, small talk, audio checks, plain statements →
  never worth an LLM call.
"""
import re
import time

ANSWER = "answer"
PARTIAL = "partial"
NOISE = "noise"

#: Kept for the microphone path and ``is_question`` (public API).
QUESTION_WORDS = (
    "who", "what", "when", "where", "why", "how",
    "can", "could", "would", "should", "is", "are", "do", "does",
    "did", "have", "has", "will", "explain", "describe", "tell",
)

#: If an utterance ENDS on one of these — with NO sentence-final punctuation —
#: the clip boundary almost certainly landed mid-phrase ("…the difference
#: between") and the rest of the question is still coming. Answering it now
#: would answer a fragment, so it is held and stitched with the next clip.
#:
#: Deliberately HIGH-CONFIDENCE only: these words require a complement and
#: cannot naturally finish a clause. Pronouns ("it", "that", "you") and
#: question words ("how", "why") are excluded on purpose — "I loved it" and
#: "I know why" are finished sentences, and holding a finished question only
#: adds latency for nothing.
DANGLING_TAIL_WORDS = frozenset({
    # articles
    "a", "an", "the",
    # prepositions
    "of", "to", "in", "on", "at", "as", "for", "with", "about", "between",
    "among", "from", "by", "into", "onto", "over", "under", "during",
    "without", "within", "through", "across", "against", "around",
    "before", "after", "above", "below", "until", "upon",
    # coordinating / subordinating conjunctions
    "and", "or", "but", "nor", "because", "since", "while", "whereas",
    "if", "unless", "whether", "than", "so", "also", "plus", "then",
    # auxiliaries — "…that you can", "…that we should"
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "can", "could", "would", "should", "will",
    "shall", "may", "might",
})

#: Leading filler the interviewer says before getting to the point.
_FILLER = r"(?:so|well|um|uh|erm|okay|ok|alright|all\s+right|now|right|yeah|yep)?\s*"

#: Request forms an interviewer uses WITHOUT a question mark. Real interviewers
#: say "tell me about your project", not "what is your project?" — so a prompt
#: verb at the START of the utterance must count as a question even when the
#: sentence ends in a period or in nothing at all.
#: Anchored on purpose: matching anywhere in the sentence would fire on
#: "that was a brief summary of the meeting".
PROMPT_PATTERNS = (
    r"tell\s+me", r"tell\s+us",
    r"explain", r"describe", r"define", r"discuss",
    r"walk\s+me", r"walk\s+us", r"walk\s+through", r"go\s+through",
    r"give\s+me", r"give\s+us", r"brief",
    r"let'?s\s+talk", r"let\s+us\s+talk", r"let'?s\s+discuss",
    r"let\s+us\s+discuss", r"let'?s\s+go\s+through",
    r"talk\s+about", r"speak\s+about",
    r"compare", r"contrast", r"distinguish", r"summar[iz]e", r"review",
    r"write", r"implement", r"design", r"optimi[sz]e", r"debug", r"refactor",
    r"estimate", r"scale",
    r"i\s+want\s+you", r"i'?d\s+like\s+you", r"i\s+would\s+like\s+you",
    r"your\s+task", r"your\s+first",
    r"do\s+you\s+know", r"are\s+you\s+familiar",
    r"can\s+you", r"could\s+you", r"would\s+you", r"should\s+you",
    r"will\s+you", r"shall\s+we",
)
_PROMPT_RE = re.compile(
    r"^" + _FILLER + r"(?:" + "|".join(PROMPT_PATTERNS) + r")\b", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Hindi / Hinglish support.
#
# This is an Indian interview product: the interviewer speaks Hinglish or
# Hindi far more often than clean English, so an English-only word list makes
# the detector answer NOTHING for them.
#
# The linguistically important part: Hindi is a WH-IN-SITU language — the
# question word does NOT move to the front.
#
#     English : "What is a deadlock?"          -> "what" leads the sentence
#     Hindi   : "deadlock kya hota hai?"       -> "kya" stays IN PLACE
#
# So a first-word rule can never find a Hindi question. These interrogatives
# are matched ANYWHERE in the utterance instead. Romanised and Devanagari
# forms are both listed because speech-to-text may emit either.
# ---------------------------------------------------------------------------
HINDI_QUESTION_WORDS = (
    "kya", "kyaa", "kya?",                 # what
    "kaise", "kaisa", "kaisi",             # how / what kind
    "kaun", "kaunsa", "kaunsi",            # who / which
    "kab",                                 # when
    "kyu", "kyun", "kyunki?",              # why
    "kis", "kiska", "kiski", "kisme",      # which / whose / in what
    "kitna", "kitne", "kitni",             # how much / how many
    "kahan",                               # where
    # Devanagari
    "क्या", "कैसे", "कैसा", "कैसी", "कौन", "कौनसा", "कौनसी",
    "कब", "क्यों", "किस", "किसका", "किसकी", "कितना", "कितने",
    "कितनी", "कहाँ",
)
_HINDI_Q_RE = re.compile(
    r"(?:^|[\s,।?])(" + "|".join(re.escape(w) for w in HINDI_QUESTION_WORDS) + r")(?:[\s,।?]|$)",
    re.IGNORECASE,
)

#: Request / imperative forms interviewers use in Hinglish WITHOUT any question
#: mark — "spring boot ke baare mein batao", "explain karo", "difference batao".
#: Matched ANYWHERE, because in Hinglish the request verb routinely lands at the
#: end ("synchronized aur volatile mein difference batao").
HINDI_PROMPT_PATTERNS = (
    r"batao", r"bataiye", r"bataiye\s+ga", r"bata\s+do", r"bata", r"batayein",
    r"bata\s+sakte", r"bata\s+dena", r"bata\s+dijiye",     # tell / can you tell
    r"samjhao", r"samjhaiye", r"samjha\s+do", r"samjha",   # explain
    r"samjha\s+sakte", r"samjh\s+do",
    r"dikhao", r"dikhaiye", r"dikha",                       # show
    r"sunao", r"suniye", r"sun",                            # tell / listen
    r"bolo", r"boliye",                                     # speak
    r"chalo\s+dekhte", r"dekho", r"dekhiye",               # let's see
    r"explain\s+karo", r"explain\s+kijiye", r"explain\s+karke",
    r"batado", r"btao", r"bta", r"btana",                   # common STT spellings
    r"mujhe", r"mere\s+ko",                                 # "to me …"
    r"ka\s+kya\s+difference", r"mein\s+kya\s+difference",
    r"kya\s+hoti\s+hai", r"kya\s+hai", r"kya\s+hota\s+hai",
    r"kaise\s+kaam\s+karta", r"kaise\s+hot hai", r"kaise\s+handle",
    r"baare\s+mein",                                        # "about …"
)
_HINDI_PROMPT_RE = re.compile(
    "(?:" + "|".join(HINDI_PROMPT_PATTERNS) + ")", re.IGNORECASE
)

#: Interviewer housekeeping — never a prompt for the candidate. These are
#: checked BEFORE the question signals so that "let me pull up your resume"
#: cannot pass as a question just because it is a full sentence.
HOUSEKEEPING_OPENERS = (
    "let me", "let us", "hold on", "hold on a second", "one moment",
    "one second", "give me a moment", "give me a second",
    "bear with me", "be right back", "i'll be right back", "i will be right back",
    "moving on", "let's move on", "we are going to start", "we're going to start",
    "we are going to begin", "we're going to begin", "going to start with",
    "going to begin with", "let's start with", "let us start with",
    "i'll just", "i will just", "i'm going to", "i am going to",
    # Hinglish
    "acha chalo", "accha chalo", "chalo shuru", "chalo start",
    "to chalo", "acha to", "accha to", "theek hai chalo", "haan chalo",
    "ab hum", "to hum", "next question", "last question", "final question",
    "ek second", "ruko", "wait wait",
)

#: Gratitude / acknowledgement. A candidate is owed an answer to their
#: QUESTION, not to "thanks for sharing" — but these must never swallow a real
#: question, so each is only noise when no question signal is present.
ACKNOWLEDGEMENT_PATTERNS = (
    "thank you", "thanks for", "thanks a lot", "thanks so much",
    "thankyou", "shukriya", "dhanyawad", "dhanyavaad",
    "makes sense", "that makes sense", "got it", "i see", "i agree",
    "agreed", "fair enough", "good point", "nice point", "well said",
    "that's interesting", "that is interesting", "really interesting",
    "great point", "interesting point",
    # Hinglish
    "acha hai", "accha hai", "theek hai", "theek hai na", "shi hai",
    "sahi hai", "bilkul", "haan ji", "hanji", "acha", "accha",
    "bahut badhiya", "badhiya", "chalo theek", "samajh gaya",
    "samajh gayi", "got it bhai", "nice nice",
)

#: Small talk / backchannels — the interviewer is reacting or talking to the
#: room, not asking the candidate anything.
BACKCHANNELS = frozenset({
    "okay", "ok", "yeah", "yes", "no", "nope", "yep", "yup", "right",
    "sure", "good", "great", "nice", "cool", "alright", "all right",
    "hmm", "hm", "huh", "uh", "um", "er", "ah", "wow", "oh",
    "thanks", "thankyou", "thank you", "got it", "makes sense", "i see",
    "perfect", "exactly", "done", "fine", "absolutely", "certainly",
    "indeed", "brilliant", "fantastic", "wonderful", "interesting",
    "noted", "understood", "clear", "sounds good", "no worries", "welcome",
    "let's see", "let me see", "hold on", "one moment", "bear with me",
    "moving on", "next", "lastly", "finally", "anyway",
    # Hinglish / Hindi backchannels
    "haan", "haanji", "han", "acha", "accha", "achha", "theek hai", "thik hai",
    "shi hai", "sahi hai", "bilkul", "ji", "shukriya", "dhanyawad",
    "bahut badhiya", "badhiya", "chalo", "ab", "phir", "to", "ye", "wo",
    "haina", "hai na", "samajh gaya", "samajh gayi", "batao na",
})

#: Session housekeeping said OUT LOUD. These really are questions — some even
#: end in "?" — but they are addressed to the human. Answering "can you hear
#: me?" with an explanation of audio latency is precisely the "random" reply
#: the classifier exists to prevent.
TECHNICAL_CHECK_PATTERNS = (
    "can you hear me", "can you hear", "are you there", "can you see me",
    "can you see my", "can you see the", "is my audio", "is my mic",
    "is the audio", "am i audible", "is my screen", "screen share",
    "you are on mute", "you're on mute", "is the connection",
    "is my connection", "can we start", "shall we start", "can we begin",
    "are we recording", "is this recording", "are you ready",
    "shall we begin", "is it audible", "is it visible",
    # Hinglish / Hindi audio & session checks
    "awaaz aa rahi", "awaaj aa rahi", "sunai de raha", "sunayi de raha",
    "sunai de rahi", "sunayi de rahi", "voice aa rahi", "awaz aa rahi",
    "video dikh raha", "video dikh rahi", "screen dikh raha", "screen dikh rahi",
    "sab theek hai na", "sab thik hai na", "start karein", "shuru karein",
    "ready hai aap", "ready ho aap", "record ho raha", "recording ho raha",
    "echo aa raha", "distortion", "clear sunai", "clear awaaz",
)


def is_question(text: str) -> bool:
    """Microphone path: is this a QUESTION-TERMINATED question?

    Deliberately strict and UNCHANGED — a fragment that starts with a question
    word is not enough, because the speaker may still be talking. Short
    questions without a "?" are handled by the client's force path instead.
    """
    t = (text or "").strip()
    if not t:
        return False
    words = t.split()
    if t.endswith("?"):
        return len(words) >= 3
    first = words[0].lower().strip("',\"")
    if first in QUESTION_WORDS and len(t) > 20 and len(words) >= 4:
        return True
    return False


def _clean(word: str) -> str:
    return word.lower().strip("'\",.;:!?…")


def _sentence_terminated(text: str) -> bool:
    """True when the transcript itself says the sentence is finished.

    Speech-to-text emits final punctuation only when the utterance looks
    complete, so its ABSENCE at a clip boundary is the signal that the
    speaker was cut off mid-sentence.
    """
    t = text.rstrip()
    if t.endswith(("...", "…", ",", ";", ":")):
        return False
    return t.endswith((".", "?", "!"))


def _ends_dangling(text: str, words: list[str]) -> bool:
    """True when the sentence was cut off mid-phrase by the clip boundary."""
    if not words:
        return False
    t = text.rstrip()
    if t.endswith(("...", "…")):
        return True
    if t.endswith((",", ";", ":")):
        return True
    if _sentence_terminated(t):
        return False
    return _clean(words[-1]) in DANGLING_TAIL_WORDS


def _starts_new_question(text: str) -> bool:
    """True when the utterance opens a question of its own.

    Used before stitching. After an abandoned "Can you explain the difference
    between", a fresh "What is a closure?" must NOT be glued onto it.

    Deliberately ASYMMETRIC, because a held fragment always ends on a dangling
    function word ("between", "the", "for", "can"):
      * an interrogative opener ("what", "how", "can you …") never continues a
        dangling phrase — it is a new question, so it always wins;
      * an IMPERATIVE opener ("design a rate limiter …") DOES continue one
        ("…how you would" + "design a rate limiter for a gateway"), so it only
        counts as new once the sentence is terminated. Otherwise a normal
        mid-question split would be mistaken for a fresh prompt.
    """
    t = (text or "").strip()
    if not t:
        return False
    if _clean(t.split()[0]) in QUESTION_WORDS:
        return True
    return _sentence_terminated(t) and bool(_PROMPT_RE.match(t))


def _has_question_signal(text: str) -> bool:
    """Does the utterance carry ANY question/prompt cue, in any supported form?

    Used to protect a real question from being thrown away by the noise rules.
    "Thanks for that, what is a closure?" is gratitude AND a question — the
    question must win.
    """
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("?"):
        return True
    words = t.split()
    if _clean(words[0]) in QUESTION_WORDS:
        return True
    # Hindi is wh-in-situ: the question word sits in place, so it is searched
    # across the WHOLE utterance rather than at the front.
    if _HINDI_Q_RE.search(t):
        return True
    if _PROMPT_RE.match(t):
        return True
    return bool(_HINDI_PROMPT_RE.search(t))


def classify(text: str) -> str:
    """Decide what one finished utterance actually is: ANSWER / PARTIAL / NOISE.

    Order matters, and the bias is deliberate: a false NOISE (a real question
    dropped, mid-interview, in the candidate's own language) is far worse than
    a false ANSWER. So the noise rules are a tight, bilingual deny-list of
    things that are genuinely never prompts, and anything substantial that is
    NOT recognisably noise is answered — an interviewer utterance that survives
    every deny-list rule is, in an interview, a prompt.

    Language coverage matters as much as precision here: the interviewer is far
    more likely to speak Hinglish or Hindi than clean English, and Hindi is
    wh-in-situ ("java mein kya hota hai"), so its interrogatives are matched
    anywhere in the utterance rather than only at the front.
    """
    t = (text or "").strip()
    if not t:
        return NOISE
    words = t.split()
    if len(words) < 2:
        # "why", "batao", "okay" — too short to carry a prompt, and too short
        # to stitch onto anything.
        return NOISE

    low = " ".join(_clean(w) for w in words)
    signal = _has_question_signal(t)

    # --- never a prompt, question mark or not ------------------------------
    if any(pat in low for pat in TECHNICAL_CHECK_PATTERNS):
        return NOISE
    if len(words) <= 4 and all(_clean(w) in BACKCHANNELS for w in words):
        return NOISE

    # --- noise ONLY when no question signal is present --------------------
    # ("Thanks for that, what is a closure?" must still be answered.)
    if not signal:
        if any(pat in low for pat in ACKNOWLEDGEMENT_PATTERNS):
            return NOISE
        if any(low.startswith(op) or f" {op}" in low for op in HOUSEKEEPING_OPENERS):
            return NOISE

    # The clip was cut mid-question — hold it for its second half.
    if _ends_dangling(t, words):
        return PARTIAL

    # --- question signals -------------------------------------------------
    if t.endswith("?"):
        return ANSWER
    if _clean(words[0]) in QUESTION_WORDS:
        return ANSWER
    if _HINDI_Q_RE.search(t):
        return ANSWER
    if _PROMPT_RE.match(t):
        return ANSWER
    if _HINDI_PROMPT_RE.search(t):
        return ANSWER

    # Safety net. Every recognisable noise class above has already been
    # removed, so a substantial utterance left over is an interviewer asking
    # something in a phrasing this list does not cover yet — a Hinglish or
    # regional phrasing, most likely. Dropping it silently strands the
    # candidate mid-interview, which is exactly the failure users complain
    # about far more loudly than an occasional stray answer.
    return ANSWER if len(words) >= 6 else NOISE


#: Verbs that still need their object. An UNTERMINATED utterance ending on one
#: ("can you explain", "tell me") is a question a pause away from its subject:
#: the classifier already sees a prompt in it, but answering now answers a stub.
#: Nouns that look like verbs ("design", "name", "list") are left out on
#: purpose — "tell me about the design" is a finished question.
OBJECT_VERBS = frozenset({
    "explain", "describe", "define", "discuss", "compare", "contrast",
    "tell", "show", "give", "walk", "mention",
})


def _wants_object(text: str) -> bool:
    """Unterminated and ending on a verb that still needs its object."""
    t = (text or "").strip()
    words = t.split()
    if not words or _sentence_terminated(t):
        return False
    last = _clean(words[-1])
    prev = _clean(words[-2]) if len(words) > 1 else ""
    return last in OBJECT_VERBS or (last in {"me", "us"} and prev in OBJECT_VERBS)


def awaits_continuation(text: str) -> bool:
    """True when an UNTERMINATED utterance cannot grammatically end here yet."""
    t = (text or "").strip()
    words = t.split()
    if not words or _sentence_terminated(t):
        return False
    return _ends_dangling(t, words) or _wants_object(t)


_TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
#: Words too common to tell two utterances apart ("what is java" vs "what is
#: python" share 2 of 3 words but are different questions).
_COMMON_WORDS = (
    frozenset(QUESTION_WORDS)
    | DANGLING_TAIL_WORDS
    | frozenset({
        "i", "me", "my", "you", "your", "we", "us", "our", "it", "its", "this",
        "that", "these", "those", "there", "here", "what's", "how's", "let's",
        "please", "okay", "ok", "um", "uh", "like", "just", "not", "no", "yes",
        "kya", "hai", "ka", "ki", "ke", "ko", "mein", "aap", "hota", "hoti",
    })
)


def _content_words(text: str) -> set[str]:
    return {w for w in _TOKEN_RE.findall((text or "").lower()) if w not in _COMMON_WORDS}


def same_utterance(answered: str, text: str, ratio: float = 0.6) -> bool:
    """True when ``text`` is (part of) the utterance ``answered`` already covers.

    Two speech-to-text engines hearing the same audio never agree word for
    word, and a clip may hold only half of the question the other engine
    answered — so this is containment of ``text``'s CONTENT words, not
    equality. Text with no content words never matches (judge it normally).
    """
    words = _content_words(text)
    if not words:
        return False
    return len(words & _content_words(answered)) / len(words) >= ratio


class QuestionBuffer:
    """Accumulate fragments; emit a question once it looks complete."""

    #: How long a question cut in half waits for its continuation. The next
    #: clip only arrives after its speech + the ~1s pause gate + upload +
    #: transcription, so this has to cover a few seconds. It only ever delays
    #: a PARTIAL — a complete question still answers instantly.
    HOLD_MS = 5000

    def __init__(self, silence_ms: int = 1200) -> None:
        self.silence_ms = silence_ms
        self._buffer: str = ""
        self._last_push: float = 0.0
        # A question whose clip boundary landed mid-phrase (see PARTIAL).
        self._held: str = ""
        self._held_at: float = 0.0

    # ---------------------------------------------------------------- held
    @property
    def holding_partial(self) -> bool:
        """True while a half-asked question is waiting to be stitched."""
        return bool(self._held)

    def flush_expired(self, now: float | None = None) -> str | None:
        """Return the held question once its hold window has closed.

        Deadline-checked, so a stale flush timer that fires early is harmless,
        and a question that was already stitched away is simply gone — the same
        question can never be answered twice.
        """
        if not self._held:
            return None
        now = time.monotonic() if now is None else now
        if (now - self._held_at) * 1000 < self.HOLD_MS:
            return None
        held, self._held, self._held_at = self._held, "", 0.0
        return held

    def _hold(self, text: str, now: float) -> None:
        self._held = text
        self._held_at = now

    def _clear_held(self) -> None:
        self._held = ""
        self._held_at = 0.0

    # ------------------------------------------------------------- streaming
    def push(self, fragment: str) -> str | None:
        """Accumulate one live-transcript fragment.

        Human pattern: people pause mid-question, so a fragment that merely
        LOOKS like a question ("Can you explain the saga pattern in…")
        must NOT fire yet — the rest is still coming. Fire immediately only
        on a hard completion signal: text ending in "?" (a finished
        question). Everything else accumulates until the client's pause
        detector asks for it via ``force_flush``.
        """
        fragment = (fragment or "").strip()
        if not fragment:
            return None
        if self._buffer and not self._buffer.endswith((" ", "\n")):
            self._buffer += " "
        self._buffer += fragment
        self._last_push = time.monotonic()
        if self._buffer.endswith("?") and is_question(self._buffer):
            return self.force_flush()
        return None

    def force_flush(self) -> str | None:
        """Microphone/stop path: hand over whatever is buffered."""
        text = self._buffer.strip()
        self._buffer = ""
        self._last_push = 0.0
        # A held interviewer half-question is dropped rather than merged with
        # microphone text — the freshest prompt wins, and merging the two
        # speakers' words would produce nonsense.
        self._clear_held()
        return text or None

    # ---------------------------------------------------------- utterances
    def push_complete(self, text: str, now: float | None = None) -> str | None:
        """Handle one finished utterance (e.g. a speech-to-text clip).

        Speech-to-text providers cut clips at pauses, so a segment is a natural
        "the speaker stopped talking" boundary — but that boundary can land in
        the middle of a question. A completed question is returned at once; a
        cut-in-half one is held for stitching; everything else is dropped.
        """
        now = time.monotonic() if now is None else now
        incoming = (text or "").strip()
        held = self._held
        expired = bool(held) and (now - self._held_at) * 1000 >= self.HOLD_MS

        # 1. A previous clip was cut mid-question and this one arrived inside
        #    the hold window. It is usually its second half — but only if it
        #    does not open a question of its own. (After an abandoned "Can you
        #    explain the difference between", a fresh "What is a closure?" must
        #    NOT be glued onto it.)
        if held and incoming and not expired and not _starts_new_question(incoming):
            self._clear_held()
            stitched = f"{held} {incoming}".strip()
            # Once stitching was needed the clip boundary is proven unreliable,
            # so only explicit sentence-final punctuation counts as finished.
            # Without it the interviewer may still be talking.
            if _sentence_terminated(stitched):
                return stitched
            self._hold(stitched, now)
            return None

        # 2. Nothing to stitch onto (or the new segment is a fresh question,
        #    or the hold window already closed): judge it on its own.
        self._clear_held()
        verdict = classify(incoming) if incoming else NOISE
        if verdict == ANSWER:
            return incoming
        if verdict == PARTIAL and incoming:
            self._hold(incoming, now)
            return None
        # 3. The new segment was not a prompt. Never lose a half-asked
        #    question: flush what we held.
        if held and classify(held) == PARTIAL:
            return held
        return None

    @property
    def pending(self) -> str:
        return self._buffer


class LiveTurnTracker:
    """Turn-taking over the streamed (word-by-word) interviewer transcript.

    A human candidate knows the question is over when the interviewer stops
    talking AND what they said sounds finished — not after a fixed timeout.
    Phrase-internal pauses run ~200–500 ms, so a short quiet only ends a turn
    whose sentence is closed ("…what is a deadlock?"); an unpunctuated prompt
    gets one more beat; a sentence left hanging ("…the difference between")
    waits for its second half however long the pause.

    Signals, in the order they normally arrive after the last word:
      * ``update(final=False)`` — words still coming. Provisional: never
        answered.
      * ``pause()`` (~0.45 s quiet, client-side) — answer a CLOSED sentence at
        once; a complete but unpunctuated prompt sets ``recheck``.
      * ``settle(gen)`` — that re-check: no new word since → answer.
      * ``update(final=True)`` (~1.3 s quiet, provider VAD) — answer any
        complete prompt; carry a dangling one into the next turn.

    Every spoken word is answered at most once: ``_used`` counts the words of
    the current turn already consumed, so the final transcript of a turn that
    was answered at the pause never fires again.
    """

    #: How long a dangling turn waits for its continuation (cf. HOLD_MS).
    CARRY_S = QuestionBuffer.HOLD_MS / 1000.0
    #: An open turn with no update for this long belongs to a dead stream.
    STALE_S = 3.0
    #: New words after an answered question must be a question of their own
    #: and at least this long — "…in Java?" is the same question, finished.
    MIN_FOLLOW_ON_WORDS = 4

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        #: Bumped on every transcript change; a settle check for an older gen
        #: is stale (the interviewer kept talking).
        self.gen = 0
        #: Set by the last call when a settle re-check is worth scheduling.
        self.recheck = False
        self._turn = ""
        self._open = False
        self._used = 0
        self._fired = ""
        self._carry = ""
        self._carry_at = 0.0
        self._last_update = 0.0

    @property
    def carrying(self) -> bool:
        return bool(self._carry)

    def deciding(self, now: float | None = None) -> bool:
        """True while this lane still owes a verdict on speech it has heard."""
        now = time.monotonic() if now is None else now
        if self._carry and now - self._carry_at < self.CARRY_S:
            return True
        fresh = len(self._turn.split()) > self._used
        return self._open and fresh and now - self._last_update < self.STALE_S

    def update(self, text: str, final: bool, now: float | None = None) -> str | None:
        """One streamed transcript (cumulative per turn). Returns a question to
        answer, only ever on a turn close."""
        now = time.monotonic() if now is None else now
        self.recheck = False
        self._last_update = now
        if not self._open:
            self._open, self._turn, self._used, self._fired = True, "", 0, ""
        text = (text or "").strip()
        if text and text != self._turn:
            self._turn = text
            self.gen += 1
        if not final:
            return None
        self._open = False
        return self._evaluate(now, closed=True)

    def pause(self, now: float | None = None) -> str | None:
        """The interviewer went quiet mid-turn."""
        self.recheck = False
        if not self._open:
            return None  # the closed turn was already judged
        return self._evaluate(time.monotonic() if now is None else now, closed=False)

    def settle(self, gen: int, now: float | None = None) -> str | None:
        """The beat after a pause: still nothing new → the prompt is finished."""
        self.recheck = False
        if gen != self.gen or not self._open:
            return None
        return self._evaluate(time.monotonic() if now is None else now, closed=False, settled=True)

    def close(self, now: float | None = None) -> str | None:
        """Capture stopped mid-turn: judge what was heard as a closed turn."""
        if not self._open:
            return None
        return self.update(self._turn, True, now)

    def flush_carry(self, now: float | None = None) -> str | None:
        """Answer a dangling question whose continuation never came."""
        now = time.monotonic() if now is None else now
        if not self._carry or self._open or now - self._carry_at < self.CARRY_S:
            return None
        carry, self._carry = self._carry, ""
        return carry

    def _evaluate(self, now: float, closed: bool, settled: bool = False) -> str | None:
        words = self._turn.split()
        fresh = " ".join(words[self._used:])
        if not fresh:
            return None
        # After "can you explain" / "tell me", a wh-clause ("how a hash map
        # works") is the missing object, not a new question.
        new_question = _starts_new_question(fresh) and not _wants_object(
            self._fired or self._carry
        )
        if self._fired:
            # Still talking after an answered question: the new words must
            # ask something on their own; then answer them WITH that context.
            base = "" if new_question else self._fired
            follow_on = len(fresh.split()) >= self.MIN_FOLLOW_ON_WORDS
            verdict = classify(fresh) if follow_on else NOISE
        else:
            base = "" if new_question or not self._carry else self._carry
            verdict = None
        candidate = f"{base} {fresh}".strip()
        if verdict is None:
            verdict = classify(candidate)
        complete = verdict == ANSWER and not awaits_continuation(candidate)

        if complete and (closed or settled or _sentence_terminated(candidate)):
            self._used, self._fired, self._carry = len(words), candidate, ""
            return candidate
        if complete:
            self.recheck = True
            return None
        if closed:
            if verdict in (ANSWER, PARTIAL):
                self._carry, self._carry_at = candidate, now  # hangs: wait for the rest
            else:
                self._carry = ""
            self._used = len(words)
        return None


class QuestionDetector:
    """Join transcript fragments and detect a trailing question."""

    def detect(self, transcript: str | list[str]) -> str | None:
        if isinstance(transcript, list):
            text = " ".join(t.strip() for t in transcript if t and t.strip())
        else:
            text = (transcript or "").strip()
        if is_question(text):
            return text
        return None
