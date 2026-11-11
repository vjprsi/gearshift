"""Task text -> task class, flags and the evidence for them.

No model call here on purpose: this runs inside a hook on every subagent
spawn, so it has to be fast and deterministic.

Two kinds of evidence:
  - lead verbs: the imperative that opens a sentence ("Find ...", "Please
    refactor ..."). First one counts most.
  - signals: phrase patterns from the policy file ("race condition", "into
    JSON", "entire codebase"). Some add weight to a class, some only set a flag.

Then a few rules for the cases plain keyword scoring gets wrong. Each of
those rules is there because a test case failed without it.

English, Spanish and Portuguese verbs and patterns are in the policy file.
Anything else (or odd phrasing) comes out "unsure" and goes to the judge if
one is configured (see judge.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .policy import Policy, tier_rank

WORK_CLASSES = ("lookup", "extract", "edit", "write", "review", "build", "design", "research", "frontier")

_SENT_SPLIT = re.compile(r"(?:\n+|(?<=[.!?;:])\s+)")
_BULLET = re.compile(r"^(?:[\s>*•\-]+|\d+[.)]\s+|\(?[a-z0-9]\)\s+|#+\s+)*", re.IGNORECASE)
_POLITE = re.compile(
    r"^(?:"
    r"(?:please|kindly|now|then|first|next|finally|also|just|ok|okay|so|and|carefully|quickly|thoroughly|briefly|simply)[,:]?\s+"
    r"|(?:can|could|would|will) you (?:please )?"
    r"|i (?:need|want|would like|'d like) (?:you )?to\s+"
    r"|you (?:should|must|need to|will|are to|have to)\s+"
    r"|your (?:task|job|goal|mission|role) is to\s+"
    r"|the (?:task|goal|job) is to\s+"
    r"|we (?:need|want|have) to\s+"
    r"|let'?s\s+"
    r"|go (?:and )?"
    r"|help me (?:to )?"
    r"|try to\s+"
    r"|(?:task|goal|todo|objective|tarea|objetivo|tarefa)\s*:\s*"
    # Spanish / Portuguese openers
    r"|(?:por favor|primero|luego|despu[eé]s|ahora|tambi[eé]n|finalmente|primeiro|depois|agora)[,:]?\s+"
    r"|(?:necesito|quiero|preciso|quero) que\s+"
    r"|(?:puedes|podr[ií]as|pod[eé]s|voc[eê] pode|pode)\s+"
    r"|(?:hay que|debes|tienes que|tu tarea es|sua tarefa [eé])\s+"
    r"|(?:for now|right now|before that|after that|once (?:that is |that's )?done|meanwhile|in the meantime|instead|today)[,:]?\s+"
    r")+",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W\d_][\w\-']*", re.IGNORECASE)   # unicode letters: "diseña", "evalúa"
_CONJ = re.compile(r"(?:\band\b|\bthen\b|\by\b|\be\b|\bluego\b|\bdepois\b|,)\s+(?:then\s+|also\s+|luego\s+|tambi[eé]n\s+)?([^\W\d_][\w\-]+)\b", re.IGNORECASE)
_GERUND = re.compile(r"\b(?:after|by|while|before|when|without)\s+([a-z]+ing)\b", re.IGNORECASE)
_BULLET_ES = re.compile(r"^[¿¡]+")
_FILE = re.compile(
    r"(?<![\w/])[\w./\-]*\w\.(?:py|js|jsx|ts|tsx|go|rs|java|rb|php|c|cc|cpp|h|cs|kt|swift|sql|sh|yaml|yml|toml|json|md|html|css|vue|scala)\b",
    re.IGNORECASE,
)
_QUESTION = "conversational question"


@dataclass
class Classification:
    task_class: str
    confidence: float
    scores: Dict[str, float]
    flags: Set[str] = field(default_factory=set)
    evidence: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    runner_up: Optional[str] = None
    judged: bool = False     # True when the LLM judge decided the class

    def to_dict(self) -> dict:
        return {
            "task_class": self.task_class,
            "confidence": self.confidence,
            "judged": self.judged,
            "runner_up": self.runner_up,
            "scores": {k: round(v, 2) for k, v in sorted(self.scores.items(), key=lambda kv: -kv[1]) if v > 0},
            "flags": sorted(self.flags),
            "evidence": list(self.evidence),
            "notes": list(self.notes),
        }


def _lemma(word: str, known) -> Optional[str]:
    w = word.lower()
    if w in known:
        return w
    if w.endswith("ing") and len(w) > 5:
        stem = w[:-3]
        cands = [stem, stem + "e"]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            cands.append(stem[:-1])
        for c in cands:
            if c in known:
                return c
    return None


def _lead_verbs(text: str, known) -> List[str]:
    """Imperative verbs that open a sentence, in order of appearance."""
    out: List[str] = []
    for sent in _SENT_SPLIT.split(text):
        s = _BULLET.sub("", sent.strip())
        s = _BULLET_ES.sub("", s)
        s = _POLITE.sub("", s)
        m = _WORD.match(s)
        if not m:
            continue
        verb = _lemma(m.group(0), known)
        if verb:
            out.append(verb)
    return out


def _conj_verbs(text: str, known) -> List[str]:
    """Verbs that follow "and"/"then", or sit in "after renaming ..." clauses."""
    out: List[str] = []
    for m in _CONJ.finditer(text):
        # base form only. "billing, login, shipping" was being read as the
        # verb "ship" before this.
        w = m.group(1).lower()
        if w in known:
            out.append(w)
    for m in _GERUND.finditer(text):
        verb = _lemma(m.group(1), known)
        if verb:
            out.append(verb)
    return out


def _snip(match: "re.Match[str]") -> str:
    s = " ".join(match.group(0).split())
    return s if len(s) <= 40 else s[:37] + "..."


def classify(
    text: str,
    *,
    policy: Policy,
    description: Optional[str] = None,
    agent_type: Optional[str] = None,
    platform: str = "api",
) -> Classification:
    """Classify a task. description is a short summary that, when present,
    counts 1.5x (in Claude Code it is the Agent tool's description field)."""
    text = text or ""
    desc = (description or "").strip()
    scores: Dict[str, float] = {c: 0.0 for c in policy.classes if c != "unsure"}
    flags: Set[str] = set()
    evidence: List[str] = []
    notes: List[str] = []
    known = policy.lead_verbs
    mutating = False

    def add(weights: Dict[str, float], factor: float) -> None:
        for cls, w in weights.items():
            if cls in scores:
                scores[cls] += w * factor

    # 1. lead verbs. description counts more (it's the summary of the job).
    # same verb again counts half, third time nothing - "Add.. Add.. Add.."
    # is not three pieces of evidence.
    seen: Dict[str, int] = {}

    def verb(v: str, factor: float, label: str, note: bool = True) -> None:
        nonlocal mutating
        n = seen.get(v, 0)
        seen[v] = n + 1
        if n >= 2:
            return
        add(known[v], factor * (0.5 if n else 1.0))
        if note and not n:
            evidence.append(label % v)
        if v in policy.mutating_verbs:
            mutating = True

    if desc:
        for i, v in enumerate(_lead_verbs(desc, known)[:2]):
            verb(v, 1.5 if i == 0 else 0.6, 'verb "%s" in the task summary')
        for v in _conj_verbs(desc, known)[:2]:
            verb(v, 1.0, 'follow-on verb "%s" in the task summary')
    for i, v in enumerate(_lead_verbs(text, known)[:5]):
        verb(v, (1.0, 0.6, 0.3, 0.3, 0.3)[i],
             'verb "%s" opens the task' if i == 0 else 'verb "%s" opens a later step', note=i < 2)
    for v in _conj_verbs(text, known)[:3]:
        verb(v, 0.6, 'follow-on verb "%s"', note=v in policy.mutating_verbs)

    # 2. phrase signals. mask file paths first, otherwise "profile.py"
    # matches the performance patterns (yes, that happened).
    blob = (desc + "\n" + text) if desc else text
    masked = _FILE.sub(lambda m: "f" * len(m.group(0)), blob)
    fired_names: Set[str] = set()
    for sig in policy.signals:
        hits = []
        in_desc = False
        haystack = blob if sig.raw else masked
        for pat in sig.patterns:
            m = pat.search(haystack)
            if m:
                hits.append(m)
                if desc and m.start() < len(desc):
                    in_desc = True
        if not hits:
            continue
        fired_names.add(sig.name)
        factor = min(1.5, 1.0 + 0.25 * (len(hits) - 1)) * (1.5 if in_desc else 1.0)
        add(sig.adds, factor)
        if sig.flag:
            flags.add(sig.flag)
        if not sig.quiet:
            evidence.append('%s ("%s")' % (sig.name, _snip(hits[0])))

    # 3. agent type prior (Claude Code subagent_type)
    if agent_type:
        for pat, spec in policy.priors:
            if pat.search(agent_type):
                add(spec.get("adds", {}), 1.0)
                if spec.get("flag"):
                    flags.add(spec["flag"])
                evidence.append('agent type "%s"' % agent_type)
                break

    if "mutating" in flags:      # a signal can mark the task as a code change
        mutating = True
    files = {m.group(0).lower() for m in _FILE.finditer(blob)}
    if len(files) >= 3 and mutating:
        flags.add("multi_file")
        scores["build"] += 1.5
        evidence.append("%d files named" % len(files))
    if mutating and "layer_backend" in flags and "layer_frontend" in flags:
        flags.add("multi_file")
        scores["build"] += 2.0
        evidence.append("touches both back end and UI")
    if "deliverables" in flags and mutating:
        # "Add X: a table, an endpoint, a page and tests" = feature sized.
        # only with a change verb though, an email brief has the same shape.
        flags.add("multi_file")
        scores["build"] += 1.5
    if mutating:
        flags.add("mutating")

    # 4. conflict rules
    read_only = "read_only" in flags
    if read_only:
        scores["edit"] *= 0.3
        scores["build"] *= 0.3
        flags.discard("mutating")
        mutating = False
        notes.append("read-only instruction: edit/build verbs are context, not the job")
    if mutating:
        # if it changes code it is at least an edit. I'd rather pay Sonnet
        # for a lookup-plus-fix than let Haiku do the fix.
        if scores["lookup"] > 0:
            scores["lookup"] *= 0.25
            notes.append("the task changes something, so it is not a lookup")
        scores["chat"] = 0.0
    if "bug_hunt" in flags:
        # "Find the bug" starts with a lookup verb but it isn't a lookup
        scores["lookup"] *= 0.3
        if mutating:
            scores["review"] *= 0.4   # find-and-fix is a fix
        notes.append("hunting for a bug is judgment work, not a lookup")
    if "contained" in flags:
        # "your part is small, only touch X" inside a big migration briefing
        scores["design"] *= 0.5
        scores["build"] *= 0.7
        notes.append("scope is explicitly contained; project-level context is not the task")
    else:
        if "multi_file" in flags and mutating:
            scores["edit"] *= 0.6
        if "major" in flags and (mutating or scores["design"] >= 2.0):
            scores["edit"] *= 0.5
            scores["build"] *= 0.8
    if platform == "claude-code":
        # a question handed to a subagent is a lookup, not small talk
        if _QUESTION in fired_names:
            scores["lookup"] += 1.5
        scores["chat"] = 0.0
    elif scores["chat"] > 0:
        if any(scores[c] >= 2.5 for c in WORK_CLASSES):
            scores["chat"] = 0.0   # "Can you review this?" is a task, not chat
        elif "code_context" in flags:
            scores["lookup"] += 2.0
            scores["chat"] = 0.0
            notes.append("a question about this codebase is a lookup")
    # Fable is expensive. one stray word must not select it.
    gate = policy.thresholds.get("frontier_min", 2.0)
    if 0 < scores["frontier"] < gate:
        notes.append("evidence for the top tier is too thin (%.1f < %.1f)" % (scores["frontier"], gate))
        scores["frontier"] = 0.0

    # 5. pick
    ranked: List[Tuple[str, float]] = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top, top_score = ranked[0]
    second, second_score = ranked[1]
    th = policy.thresholds
    if top_score < th.get("min_signal", 1.5):
        return Classification("unsure", 0.2, scores, flags, evidence,
                              notes + ["no strong signal for any task class"], None)

    margin = (top_score - second_score) / top_score
    if second_score > 0:
        # when torn, go up. the bigger the tier gap between the two
        # candidates, the easier the higher one wins (a wrong cheap pick
        # costs more the further apart they are).
        t_top = tier_rank(policy.classes[top]["tier"])
        t_second = tier_rank(policy.classes[second]["tier"])
        gaps = th.get("torn_margin_per_tier_gap") or [th.get("torn_margin", 0.2)]
        gap = t_second - t_top
        if gap > 0 and margin < gaps[min(gap, len(gaps)) - 1]:
            notes.append("torn between %s and %s; taking the more capable route" % (top, second))
            top, second = second, top
            top_score, second_score = second_score, top_score
            margin = 0.0
    strength = min(1.0, top_score / 4.5)
    confidence = round(0.25 + 0.75 * strength * (0.4 + 0.6 * max(0.0, margin)), 2)
    return Classification(top, confidence, scores, flags, evidence, notes,
                          second if second_score > 0 else None)
