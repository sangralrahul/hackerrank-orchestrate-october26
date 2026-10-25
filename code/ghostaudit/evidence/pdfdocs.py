"""PDF evidence: text is extracted with ``pdftotext`` (poppler) when available,
with a pure-Python fallback that pulls the literal strings out of the PDF
content streams.  An optional model extractor can add structured records."""

from __future__ import annotations

import re
import shutil
import subprocess
import zlib
from pathlib import Path
from typing import List, Optional

from ..observations import Observation
from .docparse import observations_from_text, observations_from_structured


def pdf_text(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if exe:
        try:
            res = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, text=True, timeout=60)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout
        except (subprocess.SubprocessError, OSError):
            pass
    return _fallback_pdf_text(path)


def _fallback_pdf_text(path: Path) -> str:
    """Minimal content-stream string extraction (handles Flate streams)."""
    data = path.read_bytes()
    chunks: List[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        for sm in re.finditer(rb"\((.*?)(?<!\\)\)\s*Tj|\[(.*?)\]\s*TJ", raw, re.S):
            if sm.group(1) is not None:
                chunks.append(sm.group(1).decode("latin-1", "replace"))
            else:
                parts = re.findall(rb"\((.*?)(?<!\\)\)", sm.group(2), re.S)
                chunks.append("".join(p.decode("latin-1", "replace") for p in parts))
        chunks.append("\n")
    return "\n".join(chunks)


def parse(ctx, doc_extractor=None) -> List[Observation]:
    from .images import correct_ocr_emails
    text = correct_ocr_emails(pdf_text(ctx.path), getattr(ctx, "known_emails", None))
    src = f"pdf:{ctx.path.name}"
    aliases = getattr(ctx, "aliases", {}) or {}
    out = observations_from_text(ctx, text, aliases, src)
    if doc_extractor is not None:
        try:
            structured = doc_extractor(ctx.path, "pdf", text)
        except Exception:  # pragma: no cover - model failures must not break the run
            structured = None
        if structured:
            extra = observations_from_structured(ctx, structured, src + ":model")
            known = {o.resource_id for o in out}
            out.extend(o for o in extra if o.resource_id not in known)
    return out
