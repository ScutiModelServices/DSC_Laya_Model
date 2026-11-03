"""Decision strategies: how the two local Laya models are combined into one answer per question.

A caller names a strategy instead of a model. Every strategy returns, per question, the pick
(`choice`, `score` or `noul`), `answer_confidence`, `uncertain` and `answered_by`; `trace` adds the
`rounds` that led there. Only Laya's own models are used here; a cloud fallback is the caller's job.

  english-only        Laya English alone.
  typed-only          Laya typed-decisions alone.
  english-then-typed  English; if unsure, typed; if they still do not settle it, narrow the options and ask again.
  typed-then-english  The same, typed first.
  most-confident      Both answer; the more confident one wins.
  average             Both answer; their probabilities (or values) are averaged.
  consensus           Both answer; certain only when they agree and both are sure.
  auto                Today's default: english-then-typed, but noul questions skip English (its yes/no label bug).

Thresholds (confidence at or above, per model): 2 options English 0.90 / typed 0.80, 3+ options 0.75 / 0.55.
A request's `min_confidence` replaces them all."""

from __future__ import annotations

import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable

from answers import InputTooLong, normalize
from registry import Registry

ENGLISH, TYPED = "laya-english", "laya-typed"
CHECKPOINT = {ENGLISH: "convaiinnovations/laya", TYPED: "typed-decisions"}
_THRESHOLDS = {2: {ENGLISH: 0.90, TYPED: 0.80}, 3: {ENGLISH: 0.75, TYPED: 0.55}}
MAX_NARROWING = 3
PRUNE_RATIO = 0.25  # options under this share of the leader's averaged probability are dropped
PAIR_RATIO = 0.5    # a runner-up under this share of the leader's difference score leaves the leader standing


@dataclass(frozen=True)
class Tuning:
    """Per-request overrides of the defaults above. `min_confidence` replaces every threshold;
    `thresholds` replaces single ones, as {"2": {"laya-english": 0.9}, "3+": {...}}."""

    min_confidence: float | None = None
    thresholds: dict[str, dict[str, float]] = field(default_factory=dict)
    prune_ratio: float = PRUNE_RATIO
    pair_ratio: float = PAIR_RATIO


class Session:
    """One request: asks each model at most once for the full question set, and again only for narrowed ones."""

    def __init__(self, registry: Registry, text: str, questions: dict[str, dict], max_len: int,
                 tuning: Tuning, project: str | None = None) -> None:
        self._registry, self._text, self._max_len = registry, text, max_len
        self.questions, self.tuning = questions, tuning
        self._cache: dict[str, dict[str, dict] | None] = {}
        self.failures: list[str] = []
        self.too_long = False
        self.calls: list[dict[str, Any]] = []
        self.project = project

    def _call(self, name: str, questions: dict[str, dict]) -> dict[str, dict] | None:
        # Fine-tuning targets typed-decisions; English always retains its base checkpoint.
        selection = (self._registry.project_model(self.project) if name == TYPED
                     else nullcontext((CHECKPOINT[name], False)))
        with selection as (checkpoint, fallback):
            return self._predict(name, checkpoint, fallback, questions)

    def _predict(self, name: str, checkpoint: str, fallback: bool,
                 questions: dict[str, dict]) -> dict[str, dict] | None:
        started, tokens = time.monotonic(), None
        try:
            prediction = self._registry.predict(checkpoint, (self._text, questions, self._max_len))
            answers = normalize(questions, prediction)
            tokens = prediction["usage"].get("input_tokens")
            return answers
        except ValueError as error:
            self.too_long = self.too_long or isinstance(error, InputTooLong)
            self.failures.append(f"{name}: {error}")
            return None
        finally:
            self.calls.append({"model": checkpoint, "fallback": fallback,
                               "input_tokens": tokens if isinstance(tokens, int) else None,
                               "duration_ms": round((time.monotonic() - started) * 1000), "ok": tokens is not None})

    def ask(self, name: str, qid: str, spec: dict, narrowed: bool = False) -> dict | None:
        if narrowed:
            answers = self._call(name, {qid: spec})
        else:
            if name not in self._cache:
                self._cache[name] = self._call(name, self.questions)
            answers = self._cache[name]
        return None if answers is None else answers[qid]

    def need(self, name: str, count: int) -> float:
        if self.tuning.min_confidence is not None:
            return self.tuning.min_confidence
        row = 2 if count == 2 else 3
        return self.tuning.thresholds.get("2" if row == 2 else "3+", {}).get(name, _THRESHOLDS[row][name])


def _count(spec: dict) -> int:
    return len(spec["criteria"]) if spec["type"] == "choice" else (2 if spec["type"] == "noul" else 3)


def _done(answer: dict, by: str, uncertain: bool, **extra: Any) -> dict:
    return {**answer, "answered_by": by, "uncertain": uncertain, **extra}


def _verdict(answer: dict) -> Any:
    """What a model decided, comparable across models."""
    if answer["type"] == "choice":
        return answer["choice"]
    return round(answer["score"]) if answer["type"] == "score" else answer["noul"] >= 0.5


# -- single model -----------------------------------------------------------------------------------
def _single(name: str) -> Callable[[Session, str, dict], dict]:
    def settle(s: Session, qid: str, spec: dict) -> dict:
        answer = s.ask(name, qid, spec)
        if answer is None:
            raise _no_answer(s, qid)
        return _done(answer, name, answer["answer_confidence"] < s.need(name, _count(spec)) - 1e-9)
    return settle


def _no_answer(s: Session, qid: str) -> ValueError:
    """InputTooLong when a model could not read the text, so the caller can tell it from a model failure."""
    message = f"no model could answer {qid!r}" + (f" ({'; '.join(s.failures)})" if s.failures else "")
    return InputTooLong(message) if s.too_long else ValueError(message)


# -- sequential, with narrowing for choice questions -------------------------------------------------
def _sequential(order: tuple[str, ...], skip_english_for_noul: bool = False) -> Callable[[Session, str, dict], dict]:
    def settle(s: Session, qid: str, spec: dict) -> dict:
        voters = tuple(v for v in order if not (skip_english_for_noul and spec["type"] == "noul" and v == ENGLISH))
        return _settle_choice(s, qid, spec, voters) if spec["type"] == "choice" else _settle_other(s, qid, spec, voters)
    return settle


def _settle_other(s: Session, qid: str, spec: dict, voters: tuple[str, ...]) -> dict:
    """First model that meets its threshold answers; else the most confident one, marked uncertain."""
    best: tuple[str, dict] | None = None
    for name in voters:
        answer = s.ask(name, qid, spec)
        if answer is None:
            continue
        if answer["answer_confidence"] >= s.need(name, _count(spec)) - 1e-9:
            return _done(answer, name, False, resolved_by="local")
        if best is None or answer["answer_confidence"] > best[1]["answer_confidence"]:
            best = (name, answer)
    if best is None:
        raise _no_answer(s, qid)
    return _done(best[1], best[0], True)


def _mean(answers: dict[str, dict]) -> dict[str, float]:
    options = list(next(iter(answers.values()))["probabilities"])
    return {o: sum(a["probabilities"][o] for a in answers.values()) / len(answers) for o in options}


def _restrict(answer: dict, options: list[str]) -> dict:
    kept = {o: answer["probabilities"].get(o, 0.0) for o in options}
    total = sum(kept.values())
    choice = answer["choice"] if answer["choice"] in kept else max(kept, key=kept.get)
    return {**answer, "choice": choice, "probabilities": {o: (v / total if total else 1 / len(options)) for o, v in kept.items()}}


def _with_eliminated(spec: dict, answer: dict, eliminated: list[str]) -> dict:
    if not eliminated:
        return answer
    return {**answer, "probabilities": {o: answer["probabilities"].get(o, 0.0) for o in spec["criteria"]},
            "eliminated": eliminated}


def kept_options(average: dict[str, float], ratio: float) -> list[str]:
    """The options worth asking about again, in the question's order: those at least `ratio` of the leader's
    averaged probability, never fewer than two. A ratio of 0 keeps them all."""
    if ratio <= 0:
        return list(average)
    top = max(average.values())
    kept = [o for o in average if average[o] >= ratio * top]
    if len(kept) < 2:
        kept = sorted(average, key=average.__getitem__, reverse=True)[:2]
    return [o for o in average if o in kept]


def combine(voters: dict[str, dict], options: list[str] | None, prune_ratio: float, pair_ratio: float) -> dict:
    """Pure helpers for a caller that adds its own voter (Garrison's cloud fallback) to the local ones: the
    voters over only `options` (renormalised), their averaged probabilities, the options still worth asking
    about and, with two or more voters, the pairwise-comparison answer."""
    options = options or list(next(iter(voters.values()))["probabilities"])
    restricted = {n: _restrict(a, options) for n, a in voters.items()}
    average = _mean(restricted)
    out: dict[str, Any] = {"voters": restricted, "average": average, "kept": kept_options(average, prune_ratio)}
    if len(restricted) > 1:
        out["final"] = _final(restricted, pair_ratio)
    return out


def _final(voters: dict[str, dict], pair_ratio: float = PAIR_RATIO) -> dict:
    """No model was sure: compare their confidence-weighted values by pairwise difference per option."""
    options = list(next(iter(voters.values()))["probabilities"])
    scaled = {n: {o: a["answer_confidence"] * a["probabilities"][o] for o in options} for n, a in voters.items()}
    names = list(scaled)
    pairs = [(x, y) for i, x in enumerate(names) for y in names[i + 1:]]
    score = {o: sum(abs(scaled[x][o] - scaled[y][o]) for x, y in pairs) / len(pairs) for o in options}
    leader = max(voters.values(), key=lambda a: a["answer_confidence"])["choice"]
    ranked = sorted(options, key=lambda o: (round(score[o], 6), o == leader), reverse=True)
    top, runner_up = ranked[0], ranked[1]
    decided = score[top] > 0 and score[runner_up] < pair_ratio * score[top]
    total = sum(score.values())
    share = {o: (score[o] / total if total else 1 / len(options)) for o in options}
    out: dict[str, Any] = {
        "type": "choice", "choice": top, "probabilities": {o: round(v, 4) for o, v in share.items()},
        "answer_confidence": round(share[top], 4), "uncertain": True, "answered_by": "+".join(names),
        "resolved_by": "final", "disagreement": {n: a["choice"] for n, a in voters.items()}, "also_check": ranked[1:3],
    }
    if not decided:
        out.update(unknown=True, candidates=ranked[:2])
    return out


def _settle_choice(s: Session, qid: str, spec: dict, voters: tuple[str, ...]) -> dict:
    options, eliminated, rounds = list(spec["criteria"]), [], []
    current, latest = spec, {}

    def ask(name: str) -> dict | None:
        answer = s.ask(name, qid, current, narrowed=bool(eliminated))
        rounds.append({"step": name, "options": list(options), **(
            {"choice": answer["choice"], "confidence": answer["answer_confidence"],
             "probabilities": {o: round(p, 4) for o, p in answer["probabilities"].items()},
             "threshold": s.need(name, len(options))} if answer else {"failed": "could not answer"})})
        if answer is not None:
            latest[name] = answer
        return answer

    def sure(name: str, answer: dict | None) -> bool:
        return answer is not None and answer["answer_confidence"] >= s.need(name, len(options)) - 1e-9

    def decided(answer: dict, by: str, resolved_by: str) -> dict:
        return _with_eliminated(spec, _done(answer, by, False, resolved_by=resolved_by, rounds=rounds), eliminated)

    before: tuple[str, dict] | None = None
    for name in voters:
        answer = ask(name)
        if sure(name, answer):
            if before is None:
                return decided(answer, name, "local")
            if answer["choice"] == before[1]["choice"]:
                return decided(answer, f"{before[0]}+{name}", "local")
        if answer is not None:
            before = (name, answer)

    for _ in range(MAX_NARROWING):
        known = dict(latest)
        if len(options) <= 2 or not known:
            break
        kept = kept_options(_mean(known), s.tuning.prune_ratio)
        if len(kept) == len(options):
            break
        eliminated += [o for o in options if o not in kept]
        options = kept
        current = {**spec, "criteria": {o: spec["criteria"][o] for o in kept}}
        latest.clear()
        for name in voters:
            answer = ask(name)
            if sure(name, answer):
                return decided(answer, name, "narrowed")

    known = {n: _restrict(a, options) for n, a in latest.items()}
    if not known:
        raise _no_answer(s, qid)
    if len(known) == 1:
        name, answer = next(iter(known.items()))
        undecided = _done(answer, name, True, rounds=rounds)
    else:
        undecided = {**_final(known, s.tuning.pair_ratio), "rounds": rounds}
    # `options` and `voters` let a caller with another voter carry on from here (see `combine`).
    return _with_eliminated(spec, {**undecided, "options": options, "voters": known}, eliminated)


# -- both models answer every question -------------------------------------------------------------------
def _both(combine: Callable[[Session, dict, dict[str, dict]], dict]) -> Callable[[Session, str, dict], dict]:
    def settle(s: Session, qid: str, spec: dict) -> dict:
        answers = {n: a for n in (ENGLISH, TYPED) if (a := s.ask(n, qid, spec)) is not None}
        if not answers:
            raise _no_answer(s, qid)
        if len(answers) == 1:
            name, answer = next(iter(answers.items()))
            return _done(answer, name, answer["answer_confidence"] < s.need(name, _count(spec)) - 1e-9)
        return combine(s, spec, answers)
    return settle


def _sure(s: Session, spec: dict, answers: dict[str, dict]) -> bool:
    return all(a["answer_confidence"] >= s.need(n, _count(spec)) - 1e-9 for n, a in answers.items())


def _most_confident(s: Session, spec: dict, answers: dict[str, dict]) -> dict:
    name = max(answers, key=lambda n: answers[n]["answer_confidence"])
    answer = answers[name]
    return _done(answer, name, answer["answer_confidence"] < s.need(name, _count(spec)) - 1e-9,
                 disagreement={n: _verdict(a) for n, a in answers.items()} if len({_verdict(a) for a in answers.values()}) > 1 else None)


def _average(s: Session, spec: dict, answers: dict[str, dict]) -> dict:
    confidence = sum(a["answer_confidence"] for a in answers.values()) / len(answers)
    out: dict[str, Any] = {"type": spec["type"]}
    if spec["type"] == "choice":
        probabilities = _mean(answers)
        out["choice"] = max(probabilities, key=probabilities.get)
        out["probabilities"] = {o: round(p, 4) for o, p in probabilities.items()}
        confidence = probabilities[out["choice"]]
    elif spec["type"] == "score":
        out["score"] = round(sum(a["score"] for a in answers.values()) / len(answers), 4)
        out["legend"] = next(iter(answers.values()))["legend"]
    else:
        out["noul"] = round(sum(a["noul"] for a in answers.values()) / len(answers), 4)
    needed = sum(s.need(n, _count(spec)) for n in answers) / len(answers)
    return _done({**out, "answer_confidence": round(confidence, 4)}, "+".join(answers), confidence < needed - 1e-9)


def _consensus(s: Session, spec: dict, answers: dict[str, dict]) -> dict:
    verdicts = {n: _verdict(a) for n, a in answers.items()}
    best = max(answers, key=lambda n: answers[n]["answer_confidence"])
    if len(set(verdicts.values())) == 1:
        return _done(answers[best], "+".join(answers), not _sure(s, spec, answers))
    out = _done(answers[best], "+".join(answers), True, disagreement=verdicts, unknown=True)
    if spec["type"] == "choice":
        out["candidates"] = list(dict.fromkeys(verdicts.values()))
    return out


STRATEGIES: dict[str, Callable[[Session, str, dict], dict]] = {
    "english-only": _single(ENGLISH),
    "typed-only": _single(TYPED),
    "english-then-typed": _sequential((ENGLISH, TYPED)),
    "typed-then-english": _sequential((TYPED, ENGLISH)),
    "most-confident": _both(_most_confident),
    "average": _both(_average),
    "consensus": _both(_consensus),
}
STRATEGIES["auto"] = _sequential((ENGLISH, TYPED), skip_english_for_noul=True)
DEFAULT_STRATEGY = "auto"

_COMPACT = ("type", "choice", "score", "noul", "answer_confidence", "uncertain", "answered_by", "probabilities",
            "also_check", "unknown", "candidates", "disagreement")


def _compact(answer: dict) -> dict:
    out = {k: answer[k] for k in _COMPACT if answer.get(k) is not None}
    for key in ("answer_confidence", "score", "noul"):
        if key in out:
            out[key] = round(out[key], 2)
    if "probabilities" in out:
        out["probabilities"] = {o: round(p, 2) for o, p in out["probabilities"].items()}
    return out


def run(registry: Registry, strategy: str, text: str, questions: dict[str, dict], max_len: int,
        tuning: Tuning = Tuning(), trace: bool = False, project: str | None = None) -> dict[str, Any]:
    """Answer every question with the named strategy. Raises ValueError when no model could answer one.
    `calls` lists each model call made (checkpoint, tokens read, duration) for the caller's usage log."""
    settle = STRATEGIES[strategy]
    session = Session(registry, text, questions, max_len, tuning, project)
    try:
        answers = {qid: settle(session, qid, spec) for qid, spec in questions.items()}
    except ValueError as error:
        error.calls = session.calls  # type: ignore[attr-defined]
        raise
    bases = set(CHECKPOINT.values())
    return {
        "strategy": strategy,
        "model": "+".join(dict.fromkeys(call["model"] for call in session.calls if call["ok"])),
        "fallback": any(call["fallback"] for call in session.calls),
        # False when the strategy never asked the typed model (english-only, or english was sure): the project went unused.
        "project_used": any(call["ok"] and call["model"] not in bases for call in session.calls),
        "answers": answers if trace else {qid: _compact(a) for qid, a in answers.items()},
        "uncertain": any(a["uncertain"] for a in answers.values()),
        "calls": session.calls,
    }
