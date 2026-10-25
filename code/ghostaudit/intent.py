"""Request intent: does ``request_text`` authorise deletion?

Two layers:

* ``RuleIntentClassifier`` - deterministic multilingual keyword rules.  It
  recognises deletion / erasure vocabulary in English, Spanish, Portuguese,
  French, German, Italian, Dutch, Japanese, Korean, Chinese, Russian, Arabic
  and Hindi, and explicit "do not delete" style overrides (access, export,
  correction, marketing opt-out, password help ...).
* ``LLMIntentClassifier`` - optional model call (see ``llm.py``) that returns
  a JSON verdict; it is consulted only when enabled and its answer is
  reconciled with the rule verdict (the rules win when the two disagree on an
  explicit non-deletion override, which keeps the pipeline deterministic).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

DELETION_PATTERNS = [
    # English
    r"\bdelet", r"\beras", r"\bremov(e|al|ed|ing)\b.*\b(account|profile|data|record|details|information|copy)",
    r"\b(account|profile|data|records?|details|information|copy|name)\b.*\bremov(e|ed|al)\b",
    r"\b(close|closed|closing|closure)\b.*\baccount", r"\baccount[- ]?(deletion|closure)", r"\bpermanently\b",
    r"\bprofile is gone\b", r"\bis gone\b", r"\bno longer (exist|needed|need)", r"\bcannot identify me\b",
    r"\bfully removed\b", r"\bwas deleted\b", r"\bwipe\b", r"\bpurge\b", r"\bforget me\b", r"\bright to be forgotten\b",
    # Spanish / Portuguese
    r"\belimin", r"\bborr", r"\bexclus[aã]o\b", r"\bexcluir\b", r"\bapagar\b", r"\bsupres", r"\bsuprim",
    # French
    r"\bsuppression\b", r"\bsupprim", r"\beffac",
    # German / Dutch
    r"\bl[öo]sch", r"\bverwijder", r"\bentfern",
    # Italian
    r"\bcancell",
    # Japanese / Chinese / Korean
    r"削除", r"消去", r"抹消", r"删除", r"刪除", r"삭제", r"지워",
    # Russian / Arabic / Hindi
    r"удал", r"стер", r"حذف", r"امسح", r"हटा", r"मिटा",
]

NON_DELETION_OVERRIDES = [
    r"\bnot a request to (erase|delete)", r"\bnot asking for deletion", r"\bdo not (close|delete|remove)",
    r"\bdon'?t (close|delete|remove|change)", r"\bplease don'?t\b", r"\bkeep (the|my) account\b", r"\bleave (my|the) (profile|account)",
    r"\bwithout removing\b", r"\bcontinue using\b", r"\bstill use\b", r"\bstill need\b", r"\bstill want\b",
    r"\bleave the account active\b", r"\bkeep the account as it is\b", r"\bdo not treat this as a deletion\b",
    r"\binformation request\b", r"\baccess request\b", r"\b(send|need|want|like|request)\b[^.]*\ba copy of (the|my|last)",
    r"\bportable format\b",
    r"\bmarketing lists only\b", r"\bstop the promotional\b", r"\bunsubscribe\b", r"\bopt[- ]?out\b",
    r"\breset(ting)? (the|my) (login|password)", r"\bmisspelled\b", r"\bcorrect it\b", r"\bchange the email\b",
    r"\breplace it with\b", r"\bupdate (my|the) (phone|mobile|number|address)",
    # other languages
    r"\bno (borr|elimin)", r"\bn[aã]o (apag|exclu)", r"\bne (pas )?supprim", r"\bnichts? (zu )?l[öo]sch",
    r"\bkeine l[öo]schung", r"\bniet verwijder", r"\bkopie (meiner|der|van)", r"\bcopia de (mis|los) datos",
    r"\bc[óo]pia dos (meus )?dados", r"\bcopie de mes donn[ée]es", r"\bweiter (nutzen|verwenden)",
    r"コピー", r"사본",
    r"削除しないで", r"削除は不要", r"삭제하지", r"не удаля", r"لا تحذف",
]

_DEL_RE = [re.compile(p, re.I) for p in DELETION_PATTERNS]
_NEG_RE = [re.compile(p, re.I) for p in NON_DELETION_OVERRIDES]


@dataclass
class IntentResult:
    authorizes_deletion: bool
    confidence: float
    reason: str
    source: str  # "rules", "llm", "rules+llm"


class RuleIntentClassifier:
    def classify(self, text: str) -> IntentResult:
        t = (text or "").strip()
        neg = [p.pattern for p in _NEG_RE if p.search(t)]
        pos = [p.pattern for p in _DEL_RE if p.search(t)]
        if neg:
            return IntentResult(False, 0.95 if not pos else 0.85,
                                f"non-deletion override matched: {neg[:2]}", "rules")
        if pos:
            return IntentResult(True, 0.95, f"deletion vocabulary matched: {pos[:2]}", "rules")
        # No vocabulary at all: a privacy request that mentions nothing about deletion
        return IntentResult(False, 0.6, "no deletion vocabulary found", "rules")


class LLMIntentClassifier:
    """Model-backed classifier; falls back to rules when the model is unavailable."""

    def __init__(self, llm_client, prompt_template: str):
        self.llm = llm_client
        self.prompt_template = prompt_template
        self.rules = RuleIntentClassifier()

    def classify(self, text: str) -> IntentResult:
        base = self.rules.classify(text)
        if self.llm is None or not self.llm.enabled:
            return base
        prompt = self.prompt_template.replace("{{REQUEST_TEXT}}", text)
        raw = self.llm.complete(prompt, purpose="intent", max_tokens=200)
        verdict = _parse_verdict(raw)
        if verdict is None:
            return base
        authorizes = bool(verdict.get("authorizes_deletion"))
        reason = str(verdict.get("reason", ""))[:200]
        if authorizes != base.authorizes_deletion:
            # keep the explicit override semantics deterministic
            if base.reason.startswith("non-deletion override"):
                return IntentResult(False, 0.9, f"rules override kept; model said {authorizes}: {reason}", "rules+llm")
            return IntentResult(authorizes, 0.7, f"model: {reason}", "rules+llm")
        return IntentResult(authorizes, 0.98, f"rules and model agree: {reason}", "rules+llm")


def _parse_verdict(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None
