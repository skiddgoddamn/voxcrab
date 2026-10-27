"""Voice dialog state machine (pure logic, no audio/IO) used by bridge_voice.Bridge.

States: idle, working, awaiting_clarification, awaiting_confirmation, completed, failed.
Rules:
- "Что-нибудь ещё?" only on a real completion of a voice-started task, with nothing pending/queued,
  at most once per task and not more often than `more_gap` seconds.
- A pending clarification is answered by the next utterance and continues the SAME task (kind/sid/data kept).
- Failures speak a status (+ optional offer), never ask-more.
- The same question is not asked twice in a row while it is pending.
- Pending questions expire (clarify_timeout): state -> idle, the question is kept in `stale` for one re-ask ("повтори").
"""
import random
import re
import time
from dataclasses import dataclass, field

IDLE, WORKING, CLARIFY, CONFIRM, COMPLETED, FAILED = (
    'idle', 'working', 'awaiting_clarification', 'awaiting_confirmation', 'completed', 'failed')
MORE = ['Что-нибудь ещё?', 'Ещё что-то нужно?', 'Чем ещё помочь?', 'Нужно что-то ещё?']
NEG = re.compile(r'^(нет|не надо|не нужно|всё|все|ничего|спасибо|хватит|достаточно|пока|отбой)( |$)', re.I)
# short "nothing else" replies with the marker anywhere: "да нет ничего", "мне ничего не нужно", "не всё нормально"
DONE = re.compile(r'\b(ничего|не надо|не нужно|не нужен|хватит|достаточно|отбой|спасибо|всё|все|нормально|свободен|отдыхай|стоп)\b|^(нет|не|неа)\b', re.I)


def is_done_reply(low: str) -> bool:
    return bool(NEG.match(low) or (len(low.split()) <= 6 and DONE.search(low)))


_REPEAT = re.compile(r'^(?:(?:эй |ну )?(?:повтори(?:те)?|скажи (?:ещ[её] раз|снова)|ещ[её] раз|не расслышал\w*|не расслышала|не понял\w*|не услышал\w*|что ты (?:сказал|сказала|спросил|спросила)|что|чего)(?: ещ[её] раз| пожалуйста| последнее| вопрос)*)$')


def is_repeat(low: str) -> bool:
    """'повтори', 'повтори ещё раз', 'ещё раз', 'что?', 'не расслышал' -> ask to say the last assistant text again."""
    return bool(_REPEAT.match(re.sub(r'[^\w\s]', ' ', low.lower()).strip()))


class BargeDetector:
    """Detects the USER talking over our own TTS. The microphone also hears the speakers, so VAD alone is not enough:
    speech counts only when VAD is confident (vad_thr, higher than the normal 0.5), the level jumps above what the mic picked up
    from our own playback (echo_ratio x a slow average of the level while we speak) and this lasts min_s seconds."""
    def __init__(self, vad_thr=0.8, min_s=0.35, echo_ratio=1.8, floor=150.0, alpha=0.03):
        self.vad_thr, self.min_s, self.echo_ratio, self.floor, self.alpha = vad_thr, min_s, echo_ratio, floor, alpha
        self.base = None; self.t0 = None

    def reset(self): self.base = None; self.t0 = None

    def feed(self, p, rms, speaking, now):
        """One mic frame -> True once, when the user has been talking over the playback long enough."""
        if not speaking: self.reset(); return False
        if self.base is None: self.base = rms
        loud = p >= self.vad_thr and rms > self.echo_ratio * max(self.base, self.floor)
        if loud:
            if self.t0 is None: self.t0 = now
            if now - self.t0 >= self.min_s: self.t0 = None; return True
        else:
            self.t0 = None; self.base += self.alpha * (rms - self.base)      # learn the echo level only from frames that are not the user
        return False


def is_question(t: str) -> bool:
    return str(t or '').rstrip().endswith('?')


@dataclass
class Pending:
    kind: str                   # project | confirm_send | session | chat | pick_session | resume_offer | what_to_send
    text: str                   # the question as spoken
    sid: str | None = None      # bound session (answers go there)
    data: dict = field(default_factory=dict)
    t: float = field(default_factory=time.time)


class Dialog:
    def __init__(self, ask_more: bool = True, clarify_timeout: float = 60.0, more_gap: float = 120.0, more_window: float = 60.0):
        self.state = IDLE
        self.pending: Pending | None = None
        self.stale: Pending | None = None      # expired question, may be re-asked once
        self.ask_more, self.clarify_timeout, self.more_gap, self.more_window = ask_more, clarify_timeout, more_gap, more_window
        self.more_t = 0.0          # when we last said "anything else?"
        self.done_t = 0.0          # when the state became completed
        self.task_sid: str | None = None
        self.notes: list[str] = []

    # ---- transitions
    def start(self, sid: str | None = None) -> None:
        self.state, self.pending, self.task_sid = WORKING, None, sid

    def ask(self, kind: str, text: str, sid: str | None = None, data: dict | None = None, confirm: bool = False) -> bool:
        """Open a pending question. False -> identical question already pending (do not speak it again)."""
        if self.pending and self.pending.kind == kind and self.pending.text == text and self.pending.sid == sid:
            self.pending.t = time.time()
            return False
        self.pending = Pending(kind, text, sid, dict(data or {}))
        self.state, self.stale = (CONFIRM if confirm else CLARIFY), None
        return True

    def take(self) -> Pending | None:
        """The next utterance answers the pending question: consume it, keep working on the same task."""
        p, self.pending = self.pending, None
        if p:
            self.state = WORKING
            if p.sid: self.task_sid = p.sid
        return p

    def complete(self, text: str, voice: bool = True, queued: int = 0, now: float | None = None) -> str:
        """Final result. Returns the text to speak (+ ask-more when allowed)."""
        now = time.time() if now is None else now
        if is_question(text):  # a result that asks something is a clarification, not a completion
            self.ask('session' if self.task_sid else 'chat', text, self.task_sid)
            return text
        self.state, self.done_t = COMPLETED, now
        if (voice and self.ask_more and not self.pending and not queued and now - self.more_t >= self.more_gap):
            self.more_t = now
            return f'{text} {random.choice(MORE)}'
        return text

    def fail(self, text: str, offer: Pending | None = None) -> str:
        self.state = FAILED
        self.pending = offer
        if offer: self.state = CONFIRM if offer.kind in ('resume_offer', 'confirm_send') else CLARIFY
        return text

    def session_stop(self, sid: str, text: str, voice: bool, queued: int = 0) -> str:
        """Stop hook of a session: question -> clarification bound to it, else completion."""
        if is_question(text):
            if self.ask('session', text, sid): return text
            return ''  # duplicate question
        if voice: self.task_sid = sid
        return self.complete(text, voice=voice, queued=queued)

    def reset(self) -> None:
        self.state, self.pending = IDLE, None

    # ---- queries
    def answering_more(self, now: float | None = None) -> bool:
        """The utterance is a reply to our "anything else?"."""
        now = time.time() if now is None else now
        return self.state == COMPLETED and self.more_t >= self.done_t - 1 and now - self.more_t < self.more_window

    def tick(self, now: float | None = None) -> str | None:
        """Expire a pending question. Returns a note to show (not a question), or None."""
        now = time.time() if now is None else now
        p = self.pending
        if p and now - p.t > self.clarify_timeout:
            self.stale, self.pending, self.state = p, None, IDLE
            return 'Вопрос остался без ответа. Скажи «повтори», чтобы вернуться к нему.'
        if self.state in (COMPLETED, FAILED) and now - max(self.done_t, self.more_t) > self.more_window:
            self.state = IDLE
        return None

    def reask(self) -> Pending | None:
        """One re-ask of an expired question."""
        p, self.stale = self.stale, None
        if p:
            p.t = time.time(); self.pending = p
            self.state = CONFIRM if p.kind in ('resume_offer', 'confirm_send') else CLARIFY
        return p
