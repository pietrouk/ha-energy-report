# Energy Report

A daily, weekly and monthly summary of where your solar, battery and grid energy
actually went — and what it was worth — delivered through any Home Assistant
notify service.

```
SOLAR SUMMARY - Last 24 hours
Thu 17 Sep 19:00 - Fri 18 Sep 19:00

💷 Total earnings
£2.03 home used + £0.40 battery + £0.38 exported + £0.32 arbitraged = £3.13

☀️ Solar
Generated 12.4 kWh
Exported 3.2 kWh, earning £0.38

🔋 Battery
Supplied 2.5 kWh
Charged 4.6 kWh, 0.6 of it from the grid, costing £0.16
Net +2.1 kWh stored, worth +£0.40 once the grid charging is paid for

🏠 House
Used 8.1 kWh
95% of it from solar and battery (7.7 kWh, worth £2.03)
Bought 0.4 kWh straight from the grid, costing £0.11
```

It works with any inverter that exposes lifetime kWh counters. No helper
entities, no utility meters to create or keep in step — everything is read from
the long-term statistics Home Assistant already keeps.

## Why not just subtract

Two things most energy summaries get wrong, and the reason this one works bucket
by bucket rather than over the whole period.

**Grid import is not all house load.** If your battery charges from the grid
overnight, subtracting the day's import from the day's house load charges that
charge to the house. On one real day 2.91 kWh arrived in the midnight hour while
the house used 0.30 kWh and the battery took 2.65. The whole-period sum reported
59% self-supply; the true figure was 95%.

Here the house takes solar first, then the battery, and only the shortfall is
grid. The battery's share is worked out first and the house takes the remainder,
so the two always add back to the import — anything that reached neither, like
round-trip losses, lands on the house rather than quietly counting as free.

**A kilowatt-hour is worth what it cost at the time.** An Octopus saving session
lifts the import rate for one hour. A half-hourly tariff moves it all day.
Valuing a whole period at whatever the price happens to be when the report runs
misprices it either way, so each bucket is priced at the rate recorded for that
bucket.

That needs a recorded rate, which is what the mirror sensors below are for.

## Install

### HACS

Add this repository as a custom repository (category: Integration), then install
**Energy Report** and restart Home Assistant.

### Manually

Copy `custom_components/energy_report` into your `config/custom_components/`
folder and restart.

Then: **Settings → Devices & services → Add integration → Energy Report**.

## Setting it up

### Energy entities

Pick your lifetime kWh counters. **Each must be a sensor whose `state_class` is
`total` or `total_increasing`** — that is what long-term statistics are built
from. A power sensor in watts has no "change" to sum; the setup form rejects one
rather than letting the report silently return zeros.

| Field | Required | Notes |
|---|---|---|
| Solar generated | yes | lifetime PV kWh |
| Grid imported | yes | |
| Grid exported | yes | |
| Battery charged | no | omit and the battery section disappears |
| Battery discharged | no | |
| House load | no | leave empty and one is calculated for you |

If you leave House load empty, a `House load energy` sensor is created from the
others:

```
house = solar + import + discharge − export − charge
```

It accumulates the step between readings, so a counter that resets daily is
handled, and a jump larger than 25 kWh between readings is treated as an
integration restart rather than energy that flowed.

### Prices

Point these at whatever holds your current price. It does **not** have to be a
`sensor` — Predbat's `predbat.rates`, for example, is in its own domain and gets
no statistics at all. A mirror sensor is created for whatever you pick, which is
what makes historical per-bucket pricing possible.

Choose whether your source reports whole units (`0.2635`) or hundredths
(`26.35p`).

The fallback price is used only for buckets from before the mirror existed. A
report covering any of those says so:

> *Partly valued at the rate showing now — recorded rates do not cover this whole
> period yet.*

So expect that footnote on weekly reports for a week and monthly for a month
after install, then never again.

### Delivery

Give it a notify service — `notify.mobile_app_phone`,
`telegram_bot.send_message`, anything — or leave it empty and call
`energy_report.generate` yourself.

The daily report can wait for sunset when that falls after your chosen time.
This is compared as a **time**, never by asking the sun entity what state it is
in: `sun.sun` flips to `below_horizon` several minutes after the astronomical
sunset, which opens a window where neither a clock condition nor a sun condition
is true and the report is silently skipped.

### Optional: a planner's savings figure

If you run something that reports its own arbitrage savings as a running total,
point the last three fields at it. The gate exists because a planner that is
only watching — or stopped — has not saved you anything, and reporting its
figure anyway would claim savings that were never made. Anything other than the
expected gate state reports zero and says why.

## Services

### `energy_report.generate`

Returns every figure without sending anything.

```yaml
action: energy_report.generate
data:
  period: daily
response_variable: report
```

The response carries `message` plus `solar`, `house`, `imported`, `exported`,
`charged`, `discharged`, `grid_to_house`, `grid_to_battery`, `home_supplied`,
`covered_percent`, `avoided`, `house_cost`, `battery_cost`, `export_income`,
`battery_value`, `total_earnings`, `estimated`, `buckets` and
`unpriced_buckets` — so you can build your own card or message instead.

### `energy_report.send`

The same, then delivers it through the configured notify service. Both accept
`start` and `end` to override the window.

## Resolution

| Period | Buckets | Why |
|---|---|---|
| Daily | 5 minute | short-term statistics, kept for `purge_keep_days` |
| Weekly | hourly | long-term statistics, kept forever |
| Monthly | hourly | |

Totals are identical at any bucket size. The split and the pricing are not. On a
flat tariff all resolutions agree exactly. On a half-hourly tariff the daily
report is exact — every five-minute bucket falls inside one price — while weekly
and monthly average within the hour, so a 30-minute grid charge in the cheap half
of an hour is priced at that hour's mean. Nothing longer-lived than hourly
statistics exists to do better.

## Testing

```bash
python3 tests/test_report.py
```

No Home Assistant install needed: the arithmetic, the window boundaries and the
message layout are all plain Python.

## Licence

MIT.
