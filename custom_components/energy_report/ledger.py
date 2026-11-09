"""A permanent hour-by-hour record of the flows and the money, priced at five
minutes.

Home Assistant keeps five-minute statistics for ten days (purge_keep_days) and
hourly ones for good. An hourly bucket only has the hour's average price, which
on a half-hourly tariff is wrong for exactly the hours a battery is busiest:
the cheap rate starting at 00:30 makes 00:00-01:00 average halfway between the
two, and the battery charges in the cheap half. So every hour, once its
five-minute statistics are in, the integration splits and prices them the way a
daily report does and adds the hour's totals to statistics of its own, one per
Totals field. Those last, so a week or a month of any age adds up the way its
days did.

They are external statistics - "energy_report:<entry>_<field>" - so there are no
entities to clutter the device page, and they are cleared when the entry is
removed, or rebuilt when the sensors or prices they came from change. They
begin as far back as the five-minute statistics go when the integration first
runs, and anything older is read from hourly statistics as before.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import fields
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .report import Bucket, Totals, add_totals, summarise
from .statistics import collect

_LOGGER = logging.getLogger(__name__)

__all__ = ["FIELDS", "Ledger", "LedgerRead", "statistic_id"]

FIELDS = tuple(f.name for f in fields(Totals))
COUNTS = {"priced_buckets", "unpriced_buckets"}
MONEY = {"home_value", "export_value", "solar_export_value", "battery_export_value", "house_cost",
         "battery_cost"}
HOUR = timedelta(hours=1)
# How long after the hour its last five-minute bucket is safely compiled.
COMPILE_GRACE = timedelta(seconds=90)
# Short-term statistics are kept ten days by default; never look back further.
FURTHEST_BACK = timedelta(days=10)
STORE_VERSION = 1


def statistic_id(entry_id: str, name: str) -> str:
    return f"{DOMAIN}:{entry_id.lower()}_{name}"


def _metadata(entry_id: str, name: str) -> dict:
    energy = name not in COUNTS and name not in MONEY
    return {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": f"Energy report {name.replace('_', ' ')}",
        "source": DOMAIN,
        "statistic_id": statistic_id(entry_id, name),
        "unit_class": "energy" if energy else None,
        "unit_of_measurement": "kWh" if energy else None,
    }


def _key(row: dict) -> float | None:
    start = row.get("start")
    if isinstance(start, datetime):
        return start.timestamp()
    try:
        return float(start)
    except (TypeError, ValueError):
        return None


class LedgerRead:
    """What the ledger holds for a period: the totals over its whole hours,
    which hours those are, and the battery's export hour by hour."""

    def __init__(self) -> None:
        self.totals = Totals()
        self.hours: set[float] = set()
        self.sales: list[tuple[datetime, float, float]] = []


class Ledger:
    """One entry's hourly record."""

    def __init__(self, hass: HomeAssistant, entry_id: str, energy_ids: dict[str, str | None],
                 import_rate_id: str | None, export_rate_id: str | None,
                 fallbacks: Callable[[list[Bucket]], tuple[float, float]], fingerprint: dict) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.energy_ids = energy_ids
        self.import_rate_id = import_rate_id
        self.export_rate_id = export_rate_id
        self.fallbacks = fallbacks
        self.fingerprint = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, default=str)
                                          .encode()).hexdigest()[:16]
        self.store = Store(hass, STORE_VERSION, f"{DOMAIN}.ledger.{entry_id}")
        self.lock = asyncio.Lock()

    @property
    def ids(self) -> list[str]:
        return [statistic_id(self.entry_id, name) for name in FIELDS]

    async def clear(self) -> None:
        """Forget every hour, to start again."""
        done = asyncio.Event()
        get_instance(self.hass).async_clear_statistics(
            self.ids, on_done=lambda: self.hass.loop.call_soon_threadsafe(done.set))
        await done.wait()

    async def remove(self) -> None:
        await self.clear()
        await self.store.async_remove()

    async def update(self, now: datetime | None = None) -> int:
        """Add every finished hour not yet recorded. Returns how many."""
        if not any(self.energy_ids.values()):
            return 0
        async with self.lock:
            if self.hass.is_stopping:
                return 0
            stored = await self.store.async_load() or {}
            if stored.get("fingerprint") != self.fingerprint:
                # A first run has nothing to clear: a removed entry took its
                # record with it.
                if stored:
                    _LOGGER.info("energy_report: the energy sensors changed; rebuilding the hourly record")
                    await self.clear()
                await self.store.async_save({"fingerprint": self.fingerprint})
            return await self._extend(now or dt_util.utcnow())

    async def _last(self) -> tuple[float | None, dict[str, float]]:
        """When the last recorded hour began, and every running sum there."""
        instance = get_instance(self.hass)
        probe = statistic_id(self.entry_id, "house")
        last = await instance.async_add_executor_job(
            get_last_statistics, self.hass, 1, probe, False, {"sum"})
        rows = last.get(probe) or []
        if not rows or (start := _key(rows[0])) is None:
            return None, {}
        at = dt_util.utc_from_timestamp(start)
        sums = await instance.async_add_executor_job(
            statistics_during_period, self.hass, at, at + HOUR, set(self.ids), "hour", None, {"sum"})
        return start, {sid: float((r[0].get("sum") or 0.0)) for sid, r in sums.items() if r}

    async def _extend(self, now: datetime) -> int:
        last, sums = await self._last()
        until = (now - COMPILE_GRACE).replace(minute=0, second=0, microsecond=0)
        oldest = (now - FURTHEST_BACK).replace(minute=0, second=0, microsecond=0)
        follows = dt_util.utc_from_timestamp(last) + HOUR if last is not None else None
        since = max(follows or oldest, oldest)
        if since >= until:
            return 0

        collected = await collect(self.hass, since, until, "5minute", self.energy_ids,
                                  self.import_rate_id, self.export_rate_id, rate_divisor=1.0)
        starts = [b.start.timestamp() for b in collected.buckets if b.start is not None]
        if not starts:
            return 0
        # Only hours whose last five minutes are compiled: just after a
        # restart the recorder may not have caught up, and an hour written
        # empty would stay empty.
        until = min(until, dt_util.utc_from_timestamp(max(starts) + 300).replace(
            minute=0, second=0, microsecond=0))
        # The first five-minute bucket the recorder still has can not be
        # trusted - its change is reckoned from nothing - so a record that
        # starts where the statistics do starts at the next full hour. One
        # that carries on from its last hour can: the bucket before is there.
        if since != follows and min(starts) > since.timestamp():
            since = dt_util.utc_from_timestamp(min(starts)).replace(minute=0, second=0, microsecond=0) + HOUR
        if since >= until:
            return 0
        hours: dict[float, list[Bucket]] = {}
        for bucket in collected.buckets:
            if bucket.start is None:
                continue
            hour = dt_util.as_utc(bucket.start).replace(minute=0, second=0, microsecond=0)
            if since <= hour < until:
                hours.setdefault(hour.timestamp(), []).append(bucket)
        fallbacks = self.fallbacks(collected.buckets)

        rows: dict[str, list[dict]] = {sid: [] for sid in self.ids}
        running = {sid: sums.get(sid, 0.0) for sid in self.ids}
        hour = since
        count = 0
        while hour < until:
            totals = summarise(hours.get(hour.timestamp(), []), *fallbacks)
            for name, sid in zip(FIELDS, self.ids):
                running[sid] += getattr(totals, name)
                rows[sid].append({"start": hour, "sum": running[sid]})
            hour += HOUR
            count += 1
        for name, sid in zip(FIELDS, self.ids):
            async_add_external_statistics(self.hass, _metadata(self.entry_id, name), rows[sid])
        _LOGGER.debug("energy_report: recorded %s hour(s) from %s", count, since)
        return count

    async def read(self, start: datetime, end: datetime) -> LedgerRead:
        """The recorded hours wholly inside [start, end)."""
        out = LedgerRead()
        first = dt_util.as_utc(start)
        if first.minute or first.second or first.microsecond:
            first = first.replace(minute=0, second=0, microsecond=0) + HOUR
        last = dt_util.as_utc(end).replace(minute=0, second=0, microsecond=0)
        if first >= last:
            return out
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, first, last, set(self.ids), "hour", None, {"change"})
        by_hour: dict[float, dict[str, float]] = {}
        for name, sid in zip(FIELDS, self.ids):
            for row in stats.get(sid) or []:
                if (key := _key(row)) is not None and row.get("change") is not None:
                    by_hour.setdefault(key, {})[name] = float(row["change"])
        parts = []
        for key, values in by_hour.items():
            # An hour is only whole with every field written for it.
            if len(values) != len(FIELDS):
                continue
            hour = Totals(**{name: (int(round(v)) if name in COUNTS else v) for name, v in values.items()})
            parts.append(hour)
            out.hours.add(key)
            if hour.battery_to_grid > 0:
                out.sales.append((dt_util.as_local(dt_util.utc_from_timestamp(key)),
                                  hour.battery_to_grid, hour.battery_export_value))
        out.totals = add_totals(*parts)
        return out
