"""Which slice of time each period covers.

Separated out and free of Home Assistant imports so the boundary arithmetic -
the part that is easy to get wrong around month ends, week starts and daylight
saving - can be tested directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .const import PERIOD_DAILY, PERIOD_MONTHLY, PERIOD_WEEKLY

__all__ = ["window_for", "span_label"]


def window_for(period: str, now: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) for a period, as local naive-or-aware datetimes.

    daily    the previous 24 hours, ending on the last five-minute boundary
    weekly   the Monday-to-Sunday week that just finished
    monthly  the calendar month that just finished

    The daily window is snapped to a five-minute boundary so consecutive
    reports tile exactly. Ending it at the moment of the run asked for a bucket
    the recorder had not compiled yet, so the five minutes before each run
    belonged to neither that report nor the next.

    Weekly and monthly are anchored to calendar boundaries rather than counted
    back from now, so running one late - or twice - still reports the same
    period rather than a window that slides with the clock.
    """
    if period == PERIOD_DAILY:
        end = now.replace(second=0, microsecond=0)
        end -= timedelta(minutes=end.minute % 5)
        return end - timedelta(hours=24), end

    if period == PERIOD_WEEKLY:
        this_monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return this_monday - timedelta(days=7), this_monday

    if period == PERIOD_MONTHLY:
        first_of_this = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        # Step back a day from the 1st to land in the previous month whatever its
        # length, then take that month's 1st. Subtracting 30 days would skip
        # February and double-count March.
        first_of_last = (first_of_this - timedelta(days=1)).replace(day=1)
        return first_of_last, first_of_this

    raise ValueError(f"unknown period: {period}")


def span_label(period: str, start: datetime, end: datetime) -> str:
    """Human-readable description of the window, for the second line."""
    if period == PERIOD_DAILY:
        return f"{_stamp(start)} - {_stamp(end)}"
    if period == PERIOD_WEEKLY:
        # The window ends at midnight on the Monday after; name the Sunday.
        last_day = end - timedelta(days=1)
        return f"{_day(start)} - {_day(last_day)}"
    return start.strftime("%B %Y")


def _stamp(moment: datetime) -> str:
    return f"{moment.strftime('%a')} {moment.day} {moment.strftime('%b %H:%M')}"


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment.strftime('%b')}"
