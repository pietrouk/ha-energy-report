"""Read long-term statistics and shape them into buckets.

This is the only place that talks to the recorder. Everything downstream works
on the Bucket list this produces, which is what lets the arithmetic be tested
without Home Assistant.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant

from .report import Bucket

_LOGGER = logging.getLogger(__name__)

__all__ = ["collect"]


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
) -> tuple[list[Bucket], dict[str, float]]:
    """Fetch the period and return one Bucket per time slot.

    energy_ids maps Bucket field names to entity ids; a None entry is simply
    absent from the result and reads as zero. extra_ids are summed over the
    whole period and returned alongside - for counters that are not part of the
    energy balance, such as a planner's running savings total.
    """
    extra_ids = {k: v for k, v in (extra_ids or {}).items() if v}
    wanted = {eid for eid in energy_ids.values() if eid}
    extras_wanted = set(extra_ids.values())
    rate_ids = {eid for eid in (import_rate_id, export_rate_id) if eid}
    if not wanted and not rate_ids and not extras_wanted:
        return [], {}

    # One call for both: energy counters answer "change", rate sensors answer
    # "mean", and each row carries a null for whichever does not apply.
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        wanted | rate_ids | extras_wanted,
        resolution,
        {"energy": "kWh"},
        {"change", "mean"},
    )

    missing = (wanted | rate_ids | extras_wanted) - set(stats)
    if missing:
        _LOGGER.debug(
            "energy_report: no statistics in %s - %s for %s",
            start, end, ", ".join(sorted(missing)),
        )

    def series(entity_id: str | None, key: str) -> dict[float, float | None]:
        if not entity_id:
            return {}
        out: dict[float, float | None] = {}
        for row in stats.get(entity_id) or []:
            slot = _bucket_key(row)
            if slot is not None:
                out[slot] = row.get(key)
        return out

    energy = {
        field: series(entity_id, "change") for field, entity_id in energy_ids.items()
    }
    import_rate = series(import_rate_id, "mean")
    export_rate = series(export_rate_id, "mean")

    # A series with no data for a slot is absent, not zero, so the slots are
    # unioned rather than taken from any one of them. Rate slots are left out
    # deliberately: a bucket with a price but no energy contributes nothing and
    # would only dilute the priced/unpriced count.
    slots = sorted({slot for values in energy.values() for slot in values})

    def rate(source: dict[float, float | None], slot: float) -> float | None:
        value = source.get(slot)
        if value is None:
            return None
        try:
            return float(value) / rate_divisor
        except (TypeError, ValueError):
            return None

    extras = {
        name: sum(
            float(row.get("change") or 0.0) for row in (stats.get(entity_id) or [])
        )
        for name, entity_id in extra_ids.items()
    }

    buckets = [
        Bucket(
            **{
                field: (values.get(slot) or 0.0)
                for field, values in energy.items()
            },
            import_rate=rate(import_rate, slot),
            export_rate=rate(export_rate, slot),
        )
        for slot in slots
    ]
    return buckets, extras
