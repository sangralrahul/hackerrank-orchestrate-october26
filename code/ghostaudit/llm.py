"""Optional model client (Anthropic Messages API over plain HTTPS, no SDK needed).

* Disabled unless ``ANTHROPIC_API_KEY`` is set and ``--llm`` / ``GHOSTAUDIT_LLM=1`` is used.
* Every call is cached on disk (``code/cache/llm``) keyed by model + prompt hash, so
  re-runs are free and deterministic.
* Token usage is accounted per purpose and written to the usage report.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from . import config as C

CACHE_DIR = Path(__file__).resolve().parents[1] / "cache" / "llm"
API_URL = os.environ.get("GHOSTAUDIT_LLM_URL", "https://api.anthropic.com/v1/messages")

# USD per million tokens (input, output); used only for the cost estimate.
PRICING = {
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-5": (5.0, 25.0),
}


class UsageMeter:
    def __init__(self):
        self.calls = defaultdict(int)
        self.cached = defaultdict(int)
        self.input_tokens = defaultdict(int)
        self.output_tokens = defaultdict(int)

    def add(self, model: str, purpose: str, inp: int, out: int, cached: bool):
        key = (model, purpose)
        if cached:
            self.cached[key] += 1
            return
        self.calls[key] += 1
        self.input_tokens[key] += inp
        self.output_tokens[key] += out

    def summary(self) -> List[dict]:
        keys = sorted(set(self.calls) | set(self.cached))
        rows = []
        for k in keys:
            pin, pout = PRICING.get(k[0], (3.0, 15.0))
            cost = self.input_tokens[k] / 1e6 * pin + self.output_tokens[k] / 1e6 * pout
            rows.append({"model": k[0], "purpose": k[1], "calls": self.calls[k], "cache_hits": self.cached[k],
                         "input_tokens": self.input_tokens[k], "output_tokens": self.output_tokens[k],
                         "cost_usd": round(cost, 6)})
        return rows


class LLMClient:
    def __init__(self, model: Optional[str] = None, enabled: Optional[bool] = None, meter: Optional[UsageMeter] = None):
        self.model = model or os.environ.get("GHOSTAUDIT_MODEL", C.DEFAULT_LLM_MODEL)
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        want = enabled if enabled is not None else os.environ.get("GHOSTAUDIT_LLM", "0") == "1"
        self.enabled = bool(want and self.api_key)
        self.meter = meter or UsageMeter()
        self.errors = 0

    def _cache_path(self, payload: dict) -> Path:
        h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]
        return CACHE_DIR / f"{h}.json"

    def _call(self, content, purpose: str, max_tokens: int) -> Optional[str]:
        payload = {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
                   "messages": [{"role": "user", "content": content}]}
        cp = self._cache_path(payload)
        if cp.exists():
            data = json.loads(cp.read_text())
            self.meter.add(self.model, purpose, 0, 0, cached=True)
            return data.get("text")
        if not self.enabled:
            return None
        req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), method="POST",
                                     headers={"content-type": "application/json", "x-api-key": self.api_key,
                                              "anthropic-version": "2023-06-01"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
                text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                usage = data.get("usage", {})
                self.meter.add(self.model, purpose, usage.get("input_tokens", 0), usage.get("output_tokens", 0), False)
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cp.write_text(json.dumps({"text": text, "usage": usage}))
                return text
            except Exception:  # network / rate limit
                self.errors += 1
                time.sleep(2 ** attempt)
        return None

    def complete(self, prompt: str, purpose: str = "general", max_tokens: int = 512) -> Optional[str]:
        return self._call(prompt, purpose, max_tokens)

    def vision(self, image_path: Path, prompt: str, purpose: str = "vision", max_tokens: int = 1500) -> Optional[str]:
        media = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        content = [{"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                   {"type": "text", "text": prompt}]
        return self._call(content, purpose, max_tokens)


def make_doc_extractor(client: LLMClient, prompt_template: str):
    """Returns a callable(path, kind, text) -> list of operational records, or None."""
    def extract(path: Path, kind: str, text: str):
        if not client.enabled and not CACHE_DIR.exists():
            return None
        prompt = prompt_template.replace("{{DOCUMENT_TEXT}}", (text or "")[:12000])
        raw = client.vision(path, prompt, purpose="document_extraction") if kind == "image" \
            else client.complete(prompt, purpose="document_extraction", max_tokens=1500)
        if not raw:
            return None
        start, end = raw.find("["), raw.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            recs = json.loads(raw[start:end + 1])
        except ValueError:
            return None
        return [r for r in recs if isinstance(r, dict) and r.get("resource_id")]
    return extract
