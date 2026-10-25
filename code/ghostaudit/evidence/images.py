"""Screenshot evidence.

Deterministic path: Tesseract OCR (two page-segmentation modes merged) and
the regex-based fact extractor in ``docparse``.  Optional path: a vision
model that returns structured records, merged with the OCR facts.  OCR
output is cached next to the code so repeated runs are fast and stable.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from ..observations import Observation
from .docparse import observations_from_text, observations_from_structured

CACHE_DIR = Path(__file__).resolve().parents[2] / "cache" / "ocr"


def _cache_key(path: Path) -> str:
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:24]
    return f"{path.stem}_{h}.txt"


def ocr_text(path: Path) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / _cache_key(path)
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    exe = shutil.which("tesseract")
    text_parts: List[str] = []
    if exe:
        for psm in ("4", "6", "11"):
            try:
                res = subprocess.run([exe, str(path), "-", "--psm", psm], capture_output=True, text=True, timeout=120)
                if res.returncode == 0:
                    text_parts.append(f"### psm {psm}\n" + res.stdout)
            except (subprocess.SubprocessError, OSError):
                continue
    text = "\n".join(text_parts)
    try:
        cache_file.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return text


def correct_ocr_emails(text: str, known) -> str:
    """Snap OCR-damaged e-mail addresses (gmai.com, outiook.com, ...) to the closest known address."""
    if not known:
        return text
    import difflib
    from ..identity import EMAIL_RE
    known_list = sorted(known)
    known_set = set(known_list)
    cache = {}

    def fix(m):
        e = m.group(0)
        low = e.lower()
        if low in known_set or low.endswith(("@northstar.shop", "@company.com")):
            return e
        if low not in cache:
            close = difflib.get_close_matches(low, known_list, n=1, cutoff=0.92)
            cache[low] = close[0] if close else e
        return cache[low]
    return EMAIL_RE.sub(fix, text)


def parse(ctx, doc_extractor=None) -> List[Observation]:
    src = f"img:{ctx.path.name}"
    aliases = getattr(ctx, "aliases", {}) or {}
    text = correct_ocr_emails(ocr_text(ctx.path), getattr(ctx, "known_emails", None))
    out = observations_from_text(ctx, text, aliases, src) if text.strip() else []
    if doc_extractor is not None:
        try:
            structured = doc_extractor(ctx.path, "image", text)
        except Exception:  # pragma: no cover
            structured = None
        if structured:
            extra = observations_from_structured(ctx, structured, src + ":model")
            known = {o.resource_id for o in out}
            out.extend(o for o in extra if o.resource_id not in known)
    return out
