import re
from typing import Optional


def clock_to_seconds(clock: str) -> Optional[float]:
    """
    Convert H:MM:SS.s, MM:SS.s, or SS.s into seconds.
    Returns None if the format is invalid.
    """
    try:
        parts = clock.split(":")
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        elif len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60 + seconds
        elif len(parts) == 1:
            return float(parts[0])
        return None
    except (ValueError, IndexError):
        return None


def extract_clock(comment: str) -> Optional[float]:
    """
    Extract [%clk H:MM:SS.s] from a PGN comment safely.
    """
    if not comment:
        return None

    match = re.search(
        r"\[%clk\s+([0-9:.]+)\]",
        comment
    )

    if not match:
        return None

    return clock_to_seconds(match.group(1))