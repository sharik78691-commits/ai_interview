"""Question detection tests."""
from app.services.question_service import (
    ANSWER,
    NOISE,
    QuestionBuffer,
    QuestionDetector,
    classify,
    is_question,
)


def test_is_question_true() -> None:
    assert is_question("Can you explain event delegation in JavaScript?")
    assert is_question("Tell me about a time you resolved a team conflict at work?")
    assert is_question("How do you optimize a slow SQL query in production systems?")
    assert is_question("What is Java?")


def test_short_question_needs_force_path() -> None:
    # "what is Java" (no "?") is intentionally NOT auto-fired from fragments —
    # the client detects it and triggers analysis via the force path instead.
    assert not is_question("what is Java")
    buf = QuestionBuffer()
    assert buf.push("what is Java") is None  # buffered, not auto-fired
    assert buf.force_flush() == "what is Java"  # force analyzes it anyway


def test_is_question_false() -> None:
    assert not is_question("")
    assert not is_question("yeah okay")
    assert not is_question("The weather is nice today and we shipped the release")


def test_buffer_join() -> None:
    buf = QuestionBuffer()
    assert buf.push("Can you explain") is None
    result = buf.push("event delegation in JavaScript?")
    assert result is not None
    assert "event delegation" in result


def test_push_waits_for_question_mark() -> None:
    """Human pattern: a long question dictated in pieces must NOT fire
    halfway — only the "?"-terminated completion fires, with full text."""
    buf = QuestionBuffer()
    assert buf.push("Can you explain the saga pattern in") is None
    assert buf.push("microservices with compensation") is None
    result = buf.push("and a concrete example?")
    assert result is not None
    assert result.startswith("Can you explain")
    assert "concrete example?" in result


def test_long_question_without_mark_accumulates() -> None:
    """A 200+ char question with no "?" stays buffered for the pause
    detector (force), never auto-fires partial."""
    buf = QuestionBuffer()
    part1 = "Can you walk me through how you would design a rate limiter"
    part2 = "for a high throughput API gateway handling millions of requests per day"
    assert buf.push(part1) is None
    assert buf.push(part2) is None
    full = buf.force_flush()
    assert full is not None
    assert full.startswith("Can you walk me")
    assert full.endswith("per day")


def test_detector() -> None:
    det = QuestionDetector()
    assert det.detect(["Can you explain", "closures in JavaScript?"]) is not None
    assert det.detect("just a statement about work") is None


# ---------------------------------------------------------------------------
# Finished-utterance classification (interviewer STT clips).
#
# Regression guard for the "answers random stuff" bug: the old rule fired on
# ANY utterance of 4+ words, so backchannels, filler and half-asked questions
# each burned an LLM call — and because answers queue one-at-a-time, every
# false trigger delayed the real question.
# ---------------------------------------------------------------------------

def test_noise_is_never_answered() -> None:
    """Small talk, filler and plain statements must not fire the AI."""
    noise = [
        "Okay, great, thanks for that.",
        "Let me pull up your resume here.",
        "So we're going to start with a couple of backend questions.",
        "That's really interesting, thank you for sharing.",
        "Alright, so, um, give me one moment.",
        "I'll be right back in two minutes.",
        "Yeah no that makes sense.",
        "Moving on to the next section now.",
        "Hold on a second while I check my notes.",
    ]
    for text in noise:
        assert classify(text) == NOISE, text
        assert QuestionBuffer().push_complete(text) is None, text


def test_audio_and_session_checks_are_not_for_the_model() -> None:
    """These are questions, some even end in "?", but they address the HUMAN."""
    checks = [
        "Can you hear me okay?",
        "Are you there?",
        "Can you see my screen?",
        "Is my audio working?",
        "Am I audible?",
        "Can we start?",
        "Are we recording?",
    ]
    for text in checks:
        assert classify(text) == NOISE, text
        assert QuestionBuffer().push_complete(text) is None, text


def test_real_questions_are_answered_immediately() -> None:
    """Complete prompts — short or long, with or without a question mark."""
    questions = [
        "What is a closure?",
        "Can you explain the Saga pattern",
        "Design a rate limiter for a high throughput API gateway",
        "Tell me about your last project and the hardest part of it",
        "How do you handle a disagreement with a teammate on an approach?",
        "Give me a brief about the Spring API and its architecture",
        "Walk me through how you would debug a memory leak in production",
        "Explain the difference between a thread and a process",
        "What do you know about Kafka consumer lag?",
    ]
    for text in questions:
        assert classify(text) == ANSWER, text


def test_short_question_answers_without_holding() -> None:
    """The fast path: a finished short question must not be delayed."""
    buf = QuestionBuffer()
    assert buf.push_complete("What is a closure?") == "What is a closure?"
    assert buf.holding_partial is False
    assert buf.flush_expired() is None


def test_question_cut_in_half_is_stitched_not_answered_as_a_fragment() -> None:
    """A pause mid-question splits the clip; the halves must rejoin into ONE
    answer instead of answering a fragment and then its tail separately."""
    buf = QuestionBuffer()
    first = "Can you explain the difference between"
    second = "a saga and two phase commit?"

    assert buf.push_complete(first, now=100.0) is None
    assert buf.holding_partial is True

    stitched = buf.push_complete(second, now=101.5)
    assert stitched == "Can you explain the difference between a saga and two phase commit?"
    assert buf.holding_partial is False
    # The held half is consumed, so the flush timer has nothing left to fire.
    assert buf.flush_expired(now=120.0) is None


def test_unfinished_question_is_flushed_when_no_continuation_arrives() -> None:
    """Abandoning a sentence mid-way must not LOSE the question."""
    buf = QuestionBuffer()
    assert buf.push_complete("How would you design a rate limiter for", now=50.0) is None
    # Inside the hold window nothing is released yet.
    assert buf.flush_expired(now=52.0) is None
    # Past the window the half-question is handed over rather than dropped.
    assert buf.flush_expired(now=50.0 + QuestionBuffer.HOLD_MS / 1000.0 + 0.1) == (
        "How would you design a rate limiter for"
    )
    # Idempotent: already handed over, so it can never be answered twice.
    assert buf.flush_expired(now=999.0) is None
    assert buf.holding_partial is False


def test_stale_half_question_does_not_swallow_a_new_complete_one() -> None:
    """If the interviewer gives up on a sentence and asks a fresh question, the
    fresh one wins — the abandoned half is stale noise."""
    buf = QuestionBuffer()
    assert buf.push_complete("Can you explain the difference between", now=10.0) is None
    fresh = buf.push_complete("What is a closure?", now=10.0 + QuestionBuffer.HOLD_MS / 1000.0 + 1)
    assert fresh == "What is a closure?"
    assert buf.holding_partial is False


def test_two_halves_still_incomplete_keep_holding() -> None:
    """A three-clip question keeps accumulating until it finally terminates."""
    buf = QuestionBuffer()
    assert buf.push_complete("Can you walk me through how you would", now=1.0) is None
    assert buf.push_complete("design a rate limiter for a gateway", now=3.0) is None
    full = buf.push_complete("that handles a million requests per second?", now=5.0)
    assert full is not None
    assert full.startswith("Can you walk me through")
    assert full.endswith("per second?")
    assert buf.holding_partial is False


def test_backchannel_between_two_questions_does_not_lose_either() -> None:
    """Noise clears the hold only when the hold is already expired; a real
    question before or after is still delivered."""
    buf = QuestionBuffer()
    assert buf.push_complete("What is your biggest strength?", now=1.0) == (
        "What is your biggest strength?"
    )
    assert buf.push_complete("Okay, thanks.", now=2.0) is None
    assert buf.push_complete("What is a weakness you are working on?", now=3.0) == (
        "What is a weakness you are working on?"
    )
    assert buf.holding_partial is False


# ---------------------------------------------------------------------------
# Multilingual detection (English / Hinglish / Hindi).
#
# Regression guard for the outage this classifier once caused: the first
# version kept an ENGLISH-ONLY question-word list, so Hindi and Hinglish
# questions were classified NOISE and never answered at all — while English
# worked fine, which is exactly why it went unnoticed in a quick English test.
#
# The root cause is structural, not a missing translation: Hindi is a
# WH-IN-SITU language, so the question word stays in place
# ("java mein kya hota hai", never "kya java mein hota hai"). A first-word
# rule therefore CANNOT find a Hindi question — the interrogative has to be
# searched across the whole utterance.
# ---------------------------------------------------------------------------

HINDISH_QUESTIONS = [
    # The exact shape a real OyeInterview user speaks.
    "bhai hashmap aur concurrent hashmap ka kya difference hai matlab java mein",
    "kya hota hai deadlock aur isse kaise prevent karte hain",
    "exception handling kaise handle karte hain java mein",
    "spring boot ke baare mein batao",
    "ye batao ki dependency injection kya hota hai",
    "microservices aur monolith mein kya farak hai",
    "kya aap bata sakte hain sql query optimize kaise karte hain",
    "multithreading kaise kaam karta hai",
    "explain karo ki deadlock kaise prevent karte hain",
    "synchronized aur volatile mein difference batao",
    "kya hashmap thread safe hai",
    # Request verb at the END — common in Hinglish word order.
    "java mein streams aur collection ka difference batao",
    "deadlock prevent karne ke tarike bataiye",
    # Question mark present but no English word at all.
    "array aur linked list mein kya difference hai?",
    "kya polymorphism ke types hote hain?",
]

DEVANAGARI_QUESTIONS = [
    "क्या है डेडलॉक और इसे कैसे रोक सकते हैं",
    "हैशमैप और कॉनकरेंट हैशमैप में क्या अंतर है",
    "स्प्रिंग बूट के बारे में बताइए",
    "मल्टीथ्रेडिंग कैसे काम करती है",
]


def test_hinglish_questions_are_answered() -> None:
    """A Hinglish question must never be dropped as noise.

    Dropping one strands the candidate mid-interview with no help at all,
    which is far worse than the occasional stray answer this classifier was
    tightened to prevent.
    """
    for q in HINDISH_QUESTIONS:
        assert classify(q) == ANSWER, q
        assert QuestionBuffer().push_complete(q) == q, q


def test_devanagari_hindi_questions_are_answered() -> None:
    """Speech-to-text may emit Devanagari instead of romanised Hindi."""
    for q in DEVANAGARI_QUESTIONS:
        assert classify(q) == ANSWER, q


def test_english_questions_are_answered() -> None:
    english = [
        "What is a deadlock and how can we prevent it",
        "Can you explain the difference between HashMap and ConcurrentHashMap",
        "Tell me about your last project",
        "Explain dependency injection",
        "What is Java?",
    ]
    for q in english:
        assert classify(q) == ANSWER, q


def test_hindi_small_talk_is_still_rejected() -> None:
    """Noise rejection must survive the bilingual extension — this is what the
    first (English-only) version got right and must not regress."""
    noise = [
        "acha chalo shuru karte hain",
        "haan theek hai",
        "shi hai",
        "ok done",
        "bilkul",
        "acha accha",
        "bahut badhiya",
        "samajh gaya",
        "theek hai chalo",
    ]
    for text in noise:
        assert classify(text) == NOISE, text
        assert QuestionBuffer().push_complete(text) is None, text


def test_hindi_audio_and_session_checks_are_rejected() -> None:
    checks = [
        "awaaz aa rahi hai na aapki",
        "sunai de raha hai aapko",
        "video dikh raha hai aapko",
        "sab theek hai na",
        "start karein ab",
    ]
    for text in checks:
        assert classify(text) == NOISE, text


def test_gratitude_or_housekeeping_never_swallows_a_real_question() -> None:
    """"Thanks for that" is noise, but not when the SAME utterance asks
    something — the question must win in every language."""
    mixed = [
        "Thanks for that, what is a closure?",
        "Okay thanks. Can you explain the saga pattern?",
        "acha hai but kya hashmap thread safe hai",
        "got it, kaise handle karte hain java mein exception",
    ]
    for text in mixed:
        assert classify(text) == ANSWER, text


def test_unrecognised_but_substantial_utterance_is_not_silently_dropped() -> None:
    """Safety net: after every deny-list rule, a long leftover utterance from
    an interviewer is a prompt in a phrasing not covered yet (typically a
    regional language). It must be answered rather than discarded."""
    assert classify("Aaj hum log system design round start karenge pehle") == ANSWER
    assert classify("Chaliye shuru karte hain aaj ka first topic") == ANSWER


def test_short_unrecognised_fragments_stay_noise() -> None:
    """The safety net must not fire on 2-5 word fragments with no signal at
    all — that would bring back the original random-trigger complaint."""
    assert classify("ok done") == NOISE
    assert classify("bilkul theek") == NOISE
    assert classify("yes haan") == NOISE


def test_unpunctuated_question_after_a_filler_lead_in_is_not_dropped() -> None:
    """Seen locally 2026-10-29: batch STT returned a lower-case run-on with no
    punctuation, and the real question after "let me start" was dropped as
    housekeeping."""
    for text in [
        "yeah hi so let me start with that question so what is the difference between hashmap and hashtable",
        "so let me ask you this can you walk me through your last project",
        "okay so tell me about a time you led a team",
    ]:
        assert classify(text) == ANSWER, text
    # Housekeeping without any question clause is still noise.
    for text in ["let me pull up your resume", "okay let me see", "let me share my screen", "can you hear me"]:
        assert classify(text) == NOISE, text
