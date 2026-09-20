"""Source-trust / prompt-injection signal detection (#727).

Extraction can mine a rule from a trajectory whose tool output was itself an
adversarial payload. Replay + linter cannot tell such a rule apart from one
mined from a real failure, so the production promotion gate needs a *source*
signal, not a content signal. This module detects injection signatures in a
trajectory's tool I/O.
"""

from __future__ import annotations

import re
import unicodedata

from cauterule.models.trajectory import Trajectory

# Known prompt-injection / instruction-override markers (case-insensitive).
_INJECTION_MARKERS: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all previous",
    "ignore the above",
    "disregard previous",
    "disregard the above",
    "disregard all prior",
    "system prompt",
    "you are now",
    "developer mode",
    "jailbreak",
    "<|im_start|>",
    "<|system|>",
    "[inst]",
    "### instruction",
    "override your",
    "do not follow the",
)

# #779: common confusable Cyrillic/Greek characters folded to Latin before
# marker matching, so homoglyph-obfuscated injections are still detected.
# Keys are written as escapes to keep this file ASCII (RUF003).
_HOMOGLYPHS = str.maketrans(
    {
        "\u0430": "a",
        "\u0435": "e",
        "\u043e": "o",
        "\u0440": "p",
        "\u0441": "c",
        "\u0443": "y",
        "\u0445": "x",
        "\u0456": "i",
        "\u0455": "s",
        "\u0458": "j",
        "\u0501": "d",
        "\u051b": "q",
        "\u04bb": "h",
        "\u051d": "w",
        "\u0391": "A",
        "\u0392": "B",
        "\u0395": "E",
        "\u0396": "Z",
        "\u0397": "H",
        "\u0399": "I",
        "\u039a": "K",
        "\u039c": "M",
        "\u039d": "N",
        "\u039f": "O",
        "\u03a1": "P",
        "\u03a4": "T",
        "\u03a5": "Y",
        "\u03a7": "X",
    }
)


def _fold(text: str) -> str:
    """NFKC-normalize and fold confusable homoglyphs before matching."""
    return unicodedata.normalize("NFKC", text).translate(_HOMOGLYPHS)


# #779: match each marker with ``\\s+`` between words so whitespace/newline
# perturbations do not evade detection.
_INJECTION_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"\s+".join(re.escape(word) for word in marker.split()), re.IGNORECASE)
    for marker in _INJECTION_MARKERS
)

# Long base64 blobs are a common injection-obfuscation vector. Detection
# requires base64-specific punctuation (``+`` / ``/`` / padding ``=``) so that
# ordinary 40-char git SHAs and hex digests — which are ubiquitous in tool
# output — are NOT flagged as payloads.
_B64_RE = re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")


def _has_encoded_blob(text: str) -> bool:
    for match in _B64_RE.finditer(text):
        blob = match.group(0)
        if "+" in blob or "/" in blob or blob.endswith("="):
            return True
    return False


def detect_injection_signal(trajectory: Trajectory) -> bool:
    """Return True if any step's tool I/O carries an injection signature."""
    for step in trajectory.steps:
        text = f"{step.input or ''}\n{step.output or ''}"
        if not text.strip():
            continue
        folded = _fold(text)
        if any(pattern.search(folded) for pattern in _INJECTION_RES):
            return True
        if _has_encoded_blob(text):
            return True
    return False


def is_source_tainted(trajectory: Trajectory) -> bool:
    """Return True if the trajectory is flagged tainted or carries a signal."""
    return bool(trajectory.injection_signal) or detect_injection_signal(trajectory)
