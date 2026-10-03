import re


def clock_to_seconds(clock: str) -> float:
    """
    Convert H:MM:SS.s into seconds.
    """

    parts = clock.split(":")

    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])

    return hours * 3600 + minutes * 60 + seconds


def extract_clock(comment: str):
    """
    Extract [%clk H:MM:SS.s] from a PGN comment.
    """

    match = re.search(
        r"\[%clk\s+([0-9:.]+)\]",
        comment
    )

    if not match:
        return None

    return clock_to_seconds(match.group(1))