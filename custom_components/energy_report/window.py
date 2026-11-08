"""Which slice of time each period covers.

Separated out and free of Home Assistant imports so the boundary arithmetic -
the part that is easy to get wrong around month ends, week starts and daylight
saving - can be tested directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .const import PERIOD_DAILY, PERIOD_MONTHLY, PERIOD_WEEKLY

__all__ = ["days_between", "is_rolling", "previous_window", "window_for", "span_label", "ROLLING_DAYS",
           "COMPARE_DAYS"]

# How far back a monthly report asked for on any day but the 1st looks.
ROLLING_DAYS = 30
# How many days before it a daily report is compared with, as an average day.
COMPARE_DAYS = 7


def window_for(period: str, now: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) for a period, as local naive-or-aware datetimes.

    daily    the previous 24 hours, ending on the last five-minute boundary
    weekly   the Monday-to-Sunday week that just finished
    monthly  on the 1st (which is when the schedule runs it), the calendar
             month that just finished; on any other day - a button pressed or
             the service called mid-month - the 30 full days up to midnight

    The daily window is snapped to a five-minute boundary so consecutive
    reports tile exactly. Ending it at the moment of the run asked for a bucket
    the recorder had not compiled yet, so the five minutes before each run
    belonged to neither that report nor the next.

    Weekly and the scheduled monthly report are anchored to calendar
    boundaries rather than counted back from now, so running one late - or
    twice - on the same day still reports the same period. The 30 days end at
    midnight too, so pressing the button twice in a day gives the same report.
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
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now.day != 1:
            # Whole local days, so across a clock change it is still 30 days
            # from midnight to midnight rather than 30 x 24 hours.
            return midnight - timedelta(days=ROLLING_DAYS), midnight
        # Step back a day from the 1st to land in the previous month whatever its
        # length, then take that month's 1st. Subtracting 30 days would skip
        # February and double-count March.
        first_of_last = (midnight - timedelta(days=1)).replace(day=1)
        return first_of_last, midnight

    raise ValueError(f"unknown period: {period}")


def previous_window(period: str, start: datetime, end: datetime) -> tuple[datetime, datetime, str]:
    """The stretch a report is compared with, and how the comparison names it:
    (start, end, label), the label read after the figure - "vs £3.91, 7-day
    average".

    daily    the 7 days before it, which the caller averages to a day
    weekly   the week before
    monthly  the calendar month before; for a run of 30 days, the 30 before
             ("prior", to fit a phone beside a four-figure sum)

    Each ends where the report begins, stepped back in whole local days so a
    clock change does not shift it by an hour."""
    if period == PERIOD_DAILY:
        return start - timedelta(days=COMPARE_DAYS), start, f", {COMPARE_DAYS}-day average"
    if period == PERIOD_WEEKLY:
        return start - timedelta(days=7), start, " previous week"
    if period == PERIOD_MONTHLY:
        if is_rolling(period, start, end):
            return start - timedelta(days=ROLLING_DAYS), start, f" prior {ROLLING_DAYS} days"
        first = (start - timedelta(days=1)).replace(day=1)
        return first, start, f" in {first.strftime('%B')}"
    raise ValueError(f"unknown period: {period}")


def span_label(period: str, start: datetime, end: datetime) -> str:
    """Human-readable description of the window, for the second line."""
    if period == PERIOD_DAILY:
        return f"{_stamp(start)} - {_stamp(end)}"
    if period == PERIOD_WEEKLY:
        # The window ends at midnight on the Monday after; name the Sunday.
        last_day = end - timedelta(days=1)
        return f"{_day(start)} - {_day(last_day)}"
    if is_rolling(period, start, end):
        # The window ends at midnight after its last day; name that day.
        return f"{_day(start)} - {_day(end - timedelta(days=1))}"
    return start.strftime("%B %Y")


def is_rolling(period: str, start: datetime, end: datetime) -> bool:
    """Whether a monthly window is a run of days rather than a calendar month:
    a calendar month runs from one 1st to the next."""
    return period == PERIOD_MONTHLY and not (start.day == 1 and end.day == 1)


def days_between(start: datetime, end: datetime) -> float:
    """Calendar days from start to end, for charging by the day: 1 for every
    daily report, 7 for a week, the month's length for a month - 31 for a
    March that loses an hour to the clocks, which elapsed time would make
    30.96. Part days count by the clock, so 10:00 to 22:00 is half a day."""
    def seconds(moment: datetime) -> int:
        return moment.hour * 3600 + moment.minute * 60 + moment.second

    return (end.date() - start.date()).days + (seconds(end) - seconds(start)) / 86400


def _stamp(moment: datetime) -> str:
    return f"{moment.strftime('%a')} {moment.day} {moment.strftime('%b %H:%M')}"


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment.strftime('%b')}"
