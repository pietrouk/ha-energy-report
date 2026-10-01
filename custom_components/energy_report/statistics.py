"""Read long-term statistics and shape them into buckets.

This is the only place that talks to the recorder. Everything downstream works
on what this returns, which is what lets the arithmetic be tested without Home
Assistant. The battery level is the one thing read from state history rather
than statistics, for the minute it peaked; see level_history().

House load is not read. It is worked out per bucket from the counters, in
report.split(). A house-load sensor built from the same counters agrees with that
to within 0.02 kWh over a day, but it updates on its own schedule, so inside one
five-minute bucket it lags them - enough to move 0.4 kWh a day from "from the
battery" to "from the grid" when the two are compared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import state_changes_during_period
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .report import Bucket

_LOGGER = logging.getLogger(__name__)

__all__ = ["Collected", "collect", "level_at", "level_history"]


@dataclass
class Collected:
    buckets: list[Bucket] = field(default_factory=list)
    extras: dict[str, float] = field(default_factory=dict)
    peak_watts: float | None = None
    peak_at: datetime | None = None


def _bucket_key(row: dict) -> float | None:
    """Statistics rows are keyed by start, which has changed type across
    releases - a float timestamp now, a datetime in older ones. Normalise to a
    float so rows from different series line up whatever the version."""
    start = row.get("start")
    if start is None:
        return None
    if isinstance(start, datetime):
        return start.timestamp()
    try:
        return float(start)
    except (TypeError, ValueError):
        return None


async def collect(
    hass: HomeAssistant,
    start: datetime,
    end: datetime,
    resolution: str,
    energy_ids: dict[str, str | None],
    import_rate_id: str | None,
    export_rate_id: str | None,
    rate_divisor: float = 1.0,
    extra_ids: dict[str, str | None] | None = None,
    power_id: str | None = None,
) -> Collected:
    """Fetch the period and return one Bucket per time slot.

    energy_ids maps Bucket field names to entity ids; a None entry is simply
    absent and reads as zero. extra_ids are summed over the whole period and
    returned alongside - for counters that are not part of the energy flows,
    such as a planner's running savings total. power_id, if given, supplies the
    highest power seen in the period and when.
    """
    extra_ids = {k: v for k, v in (extra_ids or {}).items() if v}
    energy = {eid for eid in energy_ids.values() if eid}
    rates = {eid for eid in (import_rate_id, export_rate_id) if eid}
    others = set(extra_ids.values()) | ({power_id} if power_id else set())
    wanted = energy | rates | others
    if not wanted:
        return Collected()

    # One call for everything: energy counters answer "change", rate sensors
    # "mean", the power sensor "max", each with a null for what does not apply.
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        wanted,
        resolution,
        {"energy": "kWh"},
        {"change", "mean", "max"},
    )

    missing = wanted - set(stats)
    if missing:
        _LOGGER.debug("energy_report: no statistics in %s - %s for %s",
                      start, end, ", ".join(sorted(missing)))

    def series(entity_id: str | None, key: str) -> dict[float, float | None]:
        if not entity_id:
            return {}
        out: dict[float, float | None] = {}
        for row in stats.get(entity_id) or []:
            slot = _bucket_key(row)
            if slot is not None:
                out[slot] = row.get(key)
        return out

    flows = {name: series(entity_id, "change") for name, entity_id in energy_ids.items()}
    import_rate = series(import_rate_id, "mean")
    export_rate = series(export_rate_id, "mean")

    # A series with no data for a slot is absent, not zero, so the slots are
    # unioned rather than taken from any one of them. Rate slots are left out on
    # purpose: a bucket with a price and no energy contributes nothing and would
    # only dilute the priced/unpriced count.
    slots = sorted({slot for values in flows.values() for slot in values})

    def rate(source: dict[float, float | None], slot: float) -> float | None:
        value = source.get(slot)
        if value is None:
            return None
        try:
            return float(value) / rate_divisor
        except (TypeError, ValueError):
            return None

    result = Collected(
        buckets=[
            Bucket(
                **{name: (values.get(slot) or 0.0) for name, values in flows.items()},
                import_rate=rate(import_rate, slot),
                export_rate=rate(export_rate, slot),
            )
            for slot in slots
        ],
        extras={
            name: sum(float(row.get("change") or 0.0) for row in (stats.get(entity_id) or []))
            for name, entity_id in extra_ids.items()
        },
    )

    for slot, watts in series(power_id, "max").items():
        try:
            watts = float(watts)
        except (TypeError, ValueError):
            continue
        if watts > 0 and (result.peak_watts is None or watts > result.peak_watts):
            result.peak_watts = watts
            result.peak_at = dt_util.as_local(dt_util.utc_from_timestamp(slot))

    return result


async def level_history(hass: HomeAssistant, entity_id: str, start: datetime,
                        end: datetime) -> list[tuple[datetime, float]]:
    """A level sensor's readings over the period, oldest first, in local time.

    The first is the level in force as the period began, timed at start; then
    every change. Readings that are not numbers - unavailable, unknown - are
    left out. Five-minute statistics would only say which bucket a peak fell
    in; the states say the minute it was reached, and a daily report never
    looks back further than the recorder keeps them.
    """
    states = await get_instance(hass).async_add_executor_job(
        partial(state_changes_during_period, hass, start, end, entity_id,
                no_attributes=True, include_start_time_state=True))
    readings: list[tuple[datetime, float]] = []
    for state in states.get(entity_id) or []:
        try:
            level = float(state.state)
        except (TypeError, ValueError):
            continue
        if level == level:  # not NaN
            readings.append((dt_util.as_local(state.last_updated), level))
    if not readings:
        _LOGGER.debug("energy_report: no readings for %s in %s - %s", entity_id, start, end)
    return readings


def _number(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if result != result else result


async def level_at(hass: HomeAssistant, entity_id: str, moment: datetime,
                   inside_before: bool) -> float | None:
    """A level sensor's reading at one moment, or None if nothing says.

    State history first, exact while the recorder keeps it (purge_keep_days,
    ten days by default). After that, statistics, which last: the mean of the
    bucket beside the moment and inside the period - the one starting at its
    start, or the one ending at its end, as inside_before says - five-minute if
    still kept, else hourly. Reports turn over at midnight or in the evening,
    when a battery is mostly idle, so an hour's mean is close.
    """
    def read() -> float | None:
        states = state_changes_during_period(
            hass, moment, moment + timedelta(seconds=1), entity_id,
            no_attributes=True, include_start_time_state=True)
        for state in states.get(entity_id) or []:
            # The state in force at the moment is the one timed at it exactly.
            if abs(state.last_updated.timestamp() - moment.timestamp()) < 0.001:
                if (level := _number(state.state)) is not None:
                    return level
            break
        for period, span in (("5minute", timedelta(minutes=5)), ("hour", timedelta(hours=1))):
            first, last = (moment - span, moment) if inside_before else (moment, moment + span)
            rows = statistics_during_period(hass, first, last, {entity_id}, period, None,
                                            {"mean"}).get(entity_id) or []
            if rows and (level := _number(rows[-1 if inside_before else 0].get("mean"))) is not None:
                return level
        return None

    level = await get_instance(hass).async_add_executor_job(read)
    if level is None:
        _LOGGER.debug("energy_report: no reading for %s at %s", entity_id, moment)
    return level
