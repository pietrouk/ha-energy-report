"""Read long-term statistics and shape them into buckets.

This is the only place that talks to the recorder. Everything downstream works
on what this returns, which is what lets the arithmetic be tested without Home
Assistant.

House load is not read. It is worked out per bucket from the counters, in
report.split(). A house-load sensor built from the same counters agrees with that
to within 0.02 kWh over a day, but it updates on its own schedule, so inside one
five-minute bucket it lags them - enough to move 0.4 kWh a day from "from the
battery" to "from the grid" when the two are compared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .report import Bucket

_LOGGER = logging.getLogger(__name__)

__all__ = ["Collected", "collect"]


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
