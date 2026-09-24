"""Turn a period's energy statistics into a priced summary.

Nothing in this module imports Home Assistant. Everything here is arithmetic over
a list of buckets, so it can be exercised directly by the tests.

Two things make this different from summing a period and multiplying by a rate,
and both are the reason the work is done per bucket rather than per period:

1. Grid import has to be split between the house and the battery. A whole-period
   subtraction charges an overnight battery charge to the house: on one real day
   2.91 kWh arrived in the midnight hour while the house used 0.30 kWh and the
   battery took 2.65, and subtracting the day's 3.17 kWh import from its 7.76 kWh
   house load reported 59% self-supply where the true figure was 95%.

2. Every kWh has to be priced at the rate in force when it moved. An Octopus
   saving session lifts the import rate for one hour; a half-hourly tariff moves
   it all day. One snapshot rate misprices the whole period either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Bucket", "Totals", "summarise"]


@dataclass(frozen=True)
class Bucket:
    """One statistics bucket. Energies in kWh, rates in currency units per kWh.

    A rate of None means no rate was recorded for this bucket - normally a period
    that predates the rate sensors. summarise() falls back to the supplied
    current rate and counts the bucket as unpriced so the caller can say so.
    """

    pv: float = 0.0
    house: float = 0.0
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
    house: float = 0.0
    imported: float = 0.0
    exported: float = 0.0
    charged: float = 0.0
    discharged: float = 0.0

    grid_to_house: float = 0.0
    grid_to_battery: float = 0.0

    avoided: float = 0.0
    house_cost: float = 0.0
    battery_cost: float = 0.0
    export_income: float = 0.0
    battery_value: float = 0.0

    priced_buckets: int = 0
    unpriced_buckets: int = 0
    extras: dict[str, float] = field(default_factory=dict)

    @property
    def home_supplied(self) -> float:
        """What solar and the battery gave the house.

        Not solar minus export (some is still in the battery, some went to the
        round trip) and not house minus total import (some of that import went
        into the battery).
        """
        return max(self.house - self.grid_to_house, 0.0)

    @property
    def covered(self) -> float:
        """Percentage of house load met without buying it."""
        if self.house <= 0:
            return 0.0
        return min(self.home_supplied / self.house * 100.0, 100.0)

    @property
    def battery_net(self) -> float:
        """Change in stored energy. Negative means the period spent an asset."""
        return self.charged - self.discharged

    @property
    def import_cost(self) -> float:
        return self.house_cost + self.battery_cost

    @property
    def estimated(self) -> bool:
        return self.unpriced_buckets > 0

    @property
    def fully_estimated(self) -> bool:
        return self.unpriced_buckets > 0 and self.priced_buckets == 0

    def total_earnings(self, arbitrage: float = 0.0) -> float:
        return self.avoided + self.battery_value + self.export_income + arbitrage


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


def summarise(
    buckets: list[Bucket],
    fallback_import_rate: float = 0.0,
    fallback_export_rate: float = 0.0,
) -> Totals:
    """Add up and price a period, bucket by bucket."""
    totals = Totals()

    for bucket in buckets:
        pv = _number(bucket.pv)
        house = _number(bucket.house)
        imported = _number(bucket.imported)
        exported = _number(bucket.exported)
        charged = _number(bucket.charged)
        discharged = _number(bucket.discharged)

        priced = bucket.import_rate is not None
        import_rate = _number(bucket.import_rate) if priced else fallback_import_rate
        export_rate = (
            _number(bucket.export_rate)
            if bucket.export_rate is not None
            else fallback_export_rate
        )

        # The house takes solar first, then the battery; only the shortfall is
        # grid. The battery's share of the import is worked out first and the
        # house takes the remainder, so the two always add back to the import.
        # Anything that reached neither - the round trip, an export leak - lands
        # on the house rather than vanishing. Letting it vanish would quietly
        # count energy that was bought and wasted as though it were free.
        shortfall = max(house - pv - discharged, 0.0)
        to_battery = min(max(imported - shortfall, 0.0), charged)
        to_battery = max(to_battery, 0.0)
        to_house = max(imported - to_battery, 0.0)

        totals.pv += pv
        totals.house += house
        totals.imported += imported
        totals.exported += exported
        totals.charged += charged
        totals.discharged += discharged

        totals.grid_to_house += to_house
        totals.grid_to_battery += to_battery

        totals.avoided += max(house - to_house, 0.0) * import_rate
        totals.house_cost += to_house * import_rate
        totals.battery_cost += to_battery * import_rate
        totals.export_income += exported * export_rate
        # What the battery banked that was not paid for. Energy charged from the
        # grid is worth the import rate when it comes back out, but it cost the
        # import rate to put in, so it nets to nothing; only free charging is a
        # gain. Priced per bucket, so a battery charged cheap and emptied dear
        # shows the spread instead of netting out at one average rate.
        totals.battery_value += (charged - discharged - to_battery) * import_rate

        if priced:
            totals.priced_buckets += 1
        else:
            totals.unpriced_buckets += 1

    return totals
