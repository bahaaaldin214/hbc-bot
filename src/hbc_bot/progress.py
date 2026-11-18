"""Optional progress protocol tools can print on stdout:

    ::progress <percent 0-100> [message]

Lines that match are shown as a progress bar instead of log output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    """Parsed ``::progress`` line from tool stdout."""

    percent: float
    message: str = ""


def parse(line: str) -> Progress | None:
    """Parse one stdout line; return Progress or None if not a progress line."""
    match = re.fullmatch(
        r"::progress\s+(\d{1,3}(?:\.\d+)?)%?(?:\s+(.*))?",
        line.strip(),
    )
    if not match:
        return None
    # Clamp >100 from noisy tools so the bar never overflows.
    return Progress(min(100.0, float(match.group(1))), (match.group(2) or "").strip())
