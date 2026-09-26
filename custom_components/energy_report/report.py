"""Turn a period's energy statistics into a priced summary.

Nothing in this module imports Home Assistant. Everything here is arithmetic over
a list of buckets, so the tests can exercise it directly.

WHAT "TOTAL EARNINGS" MEANS

What the house's energy would have cost at the rates of the time, minus what was
actually paid to the grid, net of export income:

    house load     everything the house got from solar and from the battery,
                   each kWh at the import rate in force when it was used
    exported       everything sold to the grid, solar or battery, at the export
                   rate in force when it went
    imported       what was paid to charge the battery from the grid

Battery energy is valued once, when it is used or sold - never when it goes in.
Valuing it on the way in as well would count the same kWh twice. A day that
fills the battery therefore earns less than one that empties it; the value
arrives in whichever report covers the day the energy comes back out.

A trade - charging cheap, selling or using dear - needs no term of its own: the
sale lands in exported (or house load), the purchase in imported, and the
difference is the gain, with the round-trip loss already paid for.

WHY BUCKET BY BUCKET

Two things are wrong if worked out over a whole period:

1. Where the energy went. Over a day the house used 7.4 kWh and solar made 9.4,
   which says nothing about the 4.0 kWh the battery supplied overnight. In each
   bucket, house load is what the counters say it must have been - solar +
   import + discharge - export - charge - and the flows are split from there.

2. What it was worth. An Octopus saving session lifts the import rate for one
   hour; a half-hourly tariff moves it all day. Each kWh is priced at the rate
   recorded for its own bucket.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Bucket", "Totals", "choose_fallback", "summarise"]


@dataclass(frozen=True)
class Bucket:
    """One statistics bucket. Energies in kWh, rates in currency units per kWh.

    A rate of None means none was recorded for this bucket - normally a period
    that predates the rate sensors. summarise() uses the supplied fallback and
    counts the bucket as unpriced so the caller can say so.
    """

    pv: float = 0.0
    imported: float = 0.0
    exported: float = 0.0
    charged: float = 0.0
    discharged: float = 0.0
    import_rate: float | None = None
    export_rate: float | None = None


@dataclass
class Totals:
    """Energy and money over the whole period."""

    pv: float = 0.0
    imported: float = 0.0
    exported: float = 0.0
    charged: float = 0.0
    discharged: float = 0.0
    house: float = 0.0

    solar_to_house: float = 0.0
    solar_to_battery: float = 0.0
    solar_to_grid: float = 0.0
    battery_to_house: float = 0.0
    battery_to_grid: float = 0.0
    grid_to_house: float = 0.0
    grid_to_battery: float = 0.0
    # Noise inside a single bucket, kept apart so it cannot pose as a real flow:
    # the battery charging and discharging in the same five minutes, and the
    # meter importing and exporting a few watt-hours as it hunts around zero.
    battery_churn: float = 0.0
    grid_hunting: float = 0.0

    home_value: float = 0.0
    export_value: float = 0.0
    # export_value split by where the energy came from.
    solar_export_value: float = 0.0
    battery_export_value: float = 0.0
    house_cost: float = 0.0
    battery_cost: float = 0.0

    priced_buckets: int = 0
    unpriced_buckets: int = 0

    @property
    def home_supplied(self) -> float:
        """What the house got without buying it: solar directly, plus the battery."""
        return self.solar_to_house + self.battery_to_house

    @property
    def covered(self) -> float:
        """Percentage of house load met without buying it."""
        if self.house <= 0:
            return 0.0
        return min(self.home_supplied / self.house * 100.0, 100.0)

    @property
    def battery_net(self) -> float:
        """Stored energy gained (positive) or spent (negative) over the period."""
        return self.charged - self.discharged

    @property
    def total_earnings(self) -> float:
        """House load saved + exported - imported.

        A planner's savings figure is deliberately not added. It is measured
        against the same solar and load with the battery doing plain
        self-consumption, so it is the part of this total that its decisions
        made. The flows above already contain those decisions.
        """
        return self.home_value + self.export_value - self.battery_cost

    @property
    def estimated(self) -> bool:
        return self.unpriced_buckets > 0

    @property
    def fully_estimated(self) -> bool:
        return self.unpriced_buckets > 0 and self.priced_buckets == 0


def _number(value: float | None) -> float:
    """Coerce a possibly-missing statistic to a float. None means no data."""
    if value is None:
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    # A NaN would poison every total it touches and never compare equal to
    # itself, so it is treated as missing rather than propagated.
    return 0.0 if result != result else result


def split(pv: float, imported: float, exported: float, charged: float,
          discharged: float) -> dict[str, float]:
    """Where one bucket's energy went.

    Every counter closes exactly: solar = to house + to battery + to grid,
    battery out = to house + to grid + churn, battery in = from solar + from
    grid + churn, export = from solar + from battery + hunting, and the house
    load is solar + battery + grid.
    """
    house = max(pv + imported + discharged - exported - charged, 0.0)
    s2h = min(pv, house)
    s2b = min(pv - s2h, charged)
    s2g = max(min(pv - s2h - s2b, exported), 0.0)
    b2h = min(discharged, house - s2h)
    g2h = max(house - s2h - b2h, 0.0)
    # Battery output the house did not take goes to the grid first. Anything
    # beyond what the export explains went straight back into the battery
    # inside the same bucket - churn, which is neither an export nor a grid
    # charge, and which inflated both before it had a name.
    rest = discharged - b2h
    b2g = max(min(rest, exported - s2g), 0.0)
    b2b = max(rest - b2g, 0.0)
    g2b = max(charged - s2b - b2b, 0.0)
    # Export neither solar nor the battery explains is the meter hunting
    # around zero, a few watt-hours in and straight back out.
    g2g = max(exported - s2g - b2g, 0.0)
    return {"house": house, "s2h": s2h, "s2b": s2b, "s2g": s2g, "b2h": b2h,
            "b2g": b2g, "b2b": b2b, "g2h": g2h, "g2b": g2b, "g2g": g2g}


def summarise(
    buckets: list[Bucket],
    fallback_import_rate: float = 0.0,
    fallback_export_rate: float = 0.0,
) -> Totals:
    """Add up and price a period, bucket by bucket."""
    t = Totals()

    for bucket in buckets:
        pv = _number(bucket.pv)
        imported = _number(bucket.imported)
        exported = _number(bucket.exported)
        charged = _number(bucket.charged)
        discharged = _number(bucket.discharged)

        priced = bucket.import_rate is not None
        ir = _number(bucket.import_rate) if priced else fallback_import_rate
        er = (_number(bucket.export_rate) if bucket.export_rate is not None
              else fallback_export_rate)

        f = split(pv, imported, exported, charged, discharged)

        t.pv += pv
        t.imported += imported
        t.exported += exported
        t.charged += charged
        t.discharged += discharged
        t.house += f["house"]

        t.solar_to_house += f["s2h"]
        t.solar_to_battery += f["s2b"]
        t.solar_to_grid += f["s2g"]
        t.battery_to_house += f["b2h"]
        t.battery_to_grid += f["b2g"]
        t.grid_to_house += f["g2h"]
        t.grid_to_battery += f["g2b"]
        t.battery_churn += f["b2b"]
        t.grid_hunting += f["g2g"]

        t.home_value += (f["s2h"] + f["b2h"]) * ir
        t.export_value += (f["s2g"] + f["b2g"]) * er
        t.solar_export_value += f["s2g"] * er
        t.battery_export_value += f["b2g"] * er
        t.house_cost += f["g2h"] * ir
        t.battery_cost += f["g2b"] * ir

        if priced:
            t.priced_buckets += 1
        else:
            t.unpriced_buckets += 1

    return t


def choose_fallback(average: object, configured: object, recorded: list[float | None],
                    live: object, divisor: float = 1.0) -> float:
    """The rate for buckets with no recorded price, in currency units per kWh.

    Never the price showing right now, unless there is nothing else. A daily
    report fires in the evening, which on a time-of-use tariff is the peak and
    can be a saving session on top: on one real day the live rate read ~44p,
    and three-quarters of a day that really cost 26p was valued at it. In order:

      1. the price entity's "average" attribute, if it has one (Predbat does)
      2. the fallback price configured for this entry, if it is not zero
      3. the mean of the rates recorded elsewhere in the same period
      4. the live price, as a last resort

    average, configured and live are in the source's own units and divided by
    divisor; recorded rates have already been normalised.
    """
    divisor = divisor or 1.0

    def number(value: object) -> float | None:
        try:
            result = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return None if result != result else result

    if (value := number(average)) is not None:
        return value / divisor
    if (value := number(configured)) is not None and value > 0:
        return value / divisor
    seen = [r for r in (number(x) for x in recorded) if r is not None]
    if seen:
        return sum(seen) / len(seen)
    if (value := number(live)) is not None:
        return value / divisor
    return 0.0
