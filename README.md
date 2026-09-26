# Solar & Battery reports

A daily, weekly and monthly summary of where your solar, battery and grid energy
actually went, and what it was worth, delivered through any Home Assistant
notify service.

```
SOLAR & BATTERY REPORT
Last 24 hours (Fri 25 Sep 15:10 - Sat 26 Sep 15:10)

💷 Total earnings
£1.86 house load saved by solar & battery
+£0.55 exported
-£0.14 imported
= £2.27

☀️ Solar
Generated 13.5 kWh, with a power peak of 3.0 kW at 11:45
4.0 to the house, 7.7 into the battery, 1.8 exported for £0.21

🔋 Battery
Charged 8.5 kWh: 7.7 from solar, 0.8 imported for £0.14
Supplied 6.2 kWh: 3.3 to the house, 2.9 exported for £0.34
Net +2.3 kWh, stored for later

🏠 House
Used 7.7 kWh, 95% from solar and battery, 7.3 kWh worth £1.86
4.0 straight from solar, 3.3 from the battery, 0.4 imported for £0.07

⚡ Export
4.7 kWh exported for £0.55: 1.8 from solar, 2.9 from the battery
```

That is a real day: solar filling the battery, a cheap overnight top-up from the
grid, and the battery selling some back in the evening. Headings are bold in
Telegram; other messaging methods get plain text.

It works with any inverter that exposes lifetime kWh counters. There are no
helper entities or utility meters to create and keep in step: everything is read
from the long-term statistics Home Assistant already keeps.

## What "total earnings" means

What your house's energy would have cost at the rates of the time, minus what
you actually paid the grid, net of export income:

| Term | What it is |
|---|---|
| **house load saved by solar & battery** | everything the house got from solar *and* from the battery, each kWh at the import rate in force when it was used |
| **exported** | everything sold to the grid, solar or battery, at the export rate in force when it went. The solar and battery lines show their share |
| **imported** | what was paid to charge the battery from the grid |

The house's own grid import ("imported for" on the House line) is shown but is
not a term: that energy would have been bought with or without solar and a
battery, so it neither adds to nor takes from what they earned.

Negative prices, such as those on Octopus Agile, keep each term's meaning and
flip its sign: being paid to import reads "+£0.10 imported".

Every £ adds up the same way the kWh do. Each part is rounded to the penny once
and every total is the sum of the rounded parts, so the solar and battery
"exported for" amounts always make the export figure exactly.

**Battery energy is valued once, when it is used or sold, never when it goes
in.** Valuing it on the way in as well would count the same kWh twice. So a day
that fills the battery earns less than one that empties it, and the value shows
up in the report for the day the energy comes back out. The Net line on the
battery says which way it went.

**A trade needs no term of its own.** Charge at 15p overnight and sell at 30p
in the evening: the sale lands in *exported*, the purchase in *imported*,
and the difference is the gain. The round-trip loss is already accounted for,
because you pay for what went in and earn on what came out. If the two halves
fall either side of a daily report's cut-off, the weekly and monthly reports
still see the whole trade.

**A planner's savings figure is shown but not added.** Predbat, for example,
measures its savings against the same solar and load with the battery doing
plain self-consumption. That figure is the part of the total its decisions
made, and those decisions are already in the flows above. Adding it on top
would count them twice.

## How the energy is split

Every figure is worked out per statistics bucket (five minutes for a daily
report), not over the whole period. Over a day the house might use 7.4 kWh
while the panels make 9.4 kWh, and those two totals alone can't tell you the
battery supplied 4.0 kWh overnight.

In each bucket, house load is what the counters say it must have been: solar +
import + discharge − export − charge. Solar serves the house first, then fills
the battery, then goes to the grid. The battery covers what solar couldn't, and
anything it gave beyond that was exported. Whatever the house still needed came
from the grid, and so did any charge solar didn't explain.

Two kinds of noise within a single bucket are kept apart, so they can't pass for
real flows:

- **churn**: the battery charging and discharging within the same five minutes
- **hunting**: the grid meter importing and exporting a few watt-hours as it settles on zero

Left in, churn would show as a grid charge *and* an export, and hunting would
look like the battery selling. In practice each amounts to a few hundredths of a
kWh up to about 0.2 kWh a day.

Every line adds up: each total is the sum of the parts printed beside it, and
each flow reads the same wherever it appears. Solar's "into the battery" is the
battery's "from solar". To guarantee that, each flow is rounded once and every
total is built from the rounded parts, so a total can differ from your dashboard
by 0.1 kWh.

## Install

### HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pietrouk&repository=ha-energy-report&category=integration)

Or add this repository as a custom repository (category: Integration). Then
download **Solar & Battery reports** and restart Home Assistant.

### Manually

Copy `custom_components/energy_report` into your `config/custom_components/`
folder and restart.

Then go to **Settings → Devices & services → Add integration → Solar & Battery reports**.

## Setting it up

### Energy entities

Pick your lifetime kWh counters. **Each must be a sensor whose `state_class` is
`total` or `total_increasing`**, because that's what long-term statistics are
built from. A power sensor in watts has no "change" to sum, so the setup form
rejects one rather than let the report silently return zeros.

| Field | Required | Notes |
|---|---|---|
| Solar generated | yes | lifetime PV kWh |
| Grid imported | yes | |
| Grid exported | yes | |
| Battery charged | no | leave both battery fields empty and the battery section disappears |
| Battery discharged | no | |
| Solar power | no | watts, `state_class: measurement`; adds the peak to the solar line |

There's no house-load field. House load is calculated from the counters above,
which keeps it in step with them inside every five-minute bucket.

### Prices

Point these at whatever holds your live rate: Predbat's `predbat.rates` and
`predbat.rates_export` (pence), an Octopus rate sensor (pounds), or any tariff
sensor. It **doesn't** have to be a `sensor`: `predbat.rates` is in its own
domain and gets no statistics at all.

The integration records whatever you pick as **Import rate (recorded)** and
**Export rate (recorded)**. Those are copies of your price entity, and what
every report is priced from, five minutes at a time. They are not the fallback.

Choose whether your source reports whole units (`0.2635`) or hundredths
(`26.35p`).

Periods from before recording started need a stand-in price. It is chosen in
this order:

1. the price entity's `average` attribute, if it has one (Predbat does)
2. the fallback price you configure, if it isn't zero
3. the average of the prices recorded elsewhere in the same period
4. the price showing now, only as a last resort

The order matters. A daily report runs in the evening, which on a time-of-use
tariff is the peak and can have a saving session on top. On the day that
prompted this, the live price read 44p, and three-quarters of a day that really
cost 26p was valued at it.

A report that used a stand-in price says so, and for how long:

> *7 h 45 min of this period had no recorded rate, so that part was priced at
> an estimated rate.*

It appears only when some of the period is from before the rate sensors started
recording: daily reports for the first day after installing, weekly ones for
the first week, monthly ones for the first month. It also appears if a rate
entity was unavailable for a while. Otherwise it doesn't appear at all.

### Messaging

| Method | What you pick | Format |
|---|---|---|
| **Telegram** | the chat entities your Telegram bot created, and/or chat IDs (group chats start with a minus sign) | HTML, bold headings |
| **Notify entity** | one or more `notify.*` entities, such as your phone's | plain text |
| **Notify service** | a service by name, such as `notify.mobile_app_my_phone` | plain text |
| **Don't send** | nothing; call `energy_report.generate` and deliver the message yourself | |

### Schedule

Daily, weekly (Mondays) and monthly (the 1st), each on or off with its own
time. The daily report can wait for sunset when sunset falls after your chosen
time.

The comparison uses the sunset **time**, worked out from your home location,
rather than the sun entity's state. `sun.sun` changes to `below_horizon` several
minutes after the astronomical sunset. That leaves a gap where neither the clock
condition nor the sun condition is true, and the report is silently skipped.

The daily window ends on the last five-minute boundary, and the report waits a
minute before reading. The recorder takes a few seconds to write each
five-minute bucket, and without that pause the last five minutes before each
report belonged to neither that report nor the next.

### Optional: a planner's savings figure

If you run something that keeps its own arbitrage savings as a running total,
point the last three fields of the Prices step at it. The gate is there because
a planner that is only watching, or is stopped, hasn't saved you anything. In
any state other than the one you set, the report shows zero and says why.

## Changing settings later

**Settings → Devices & services → Solar & Battery reports → Configure** opens a menu
that shows what is set now, with an entry for each part: messaging (Telegram,
phone, notify), schedule, energy entities, and prices and planner savings. Each
one saves on its own. The device page links there too (**Visit**).

Change an energy entity when you replace a device or picked the wrong one, not
routinely. Reports reach back through history, so a weekly report covering a
change reads the new counter for the whole week.

The device page has the everyday controls:

| Entity | What it does |
|---|---|
| Messaging method | Telegram, notify entity, notify service, or don't send |
| Messaging send to | where it goes, comma separated: Telegram chat entities and/or chat IDs, notify entities, or one service name. A bad value is refused with the reason |
| Daily / Weekly / Monthly report | switches that turn each report on or off |
| Daily / Weekly / Monthly report time | when each one goes out |
| Daily report waits for sunset | whether the daily one waits for a later sunset |
| Send daily / weekly / monthly report now | buttons that build and send one straight away |
| Next daily / weekly / monthly report | when each one next goes out, sunset included; unknown while that report is off |
| Import / Export rate (recorded) | the rate the reports are priced from, recorded from your price entity (its `recorded_from` attribute says which). Not the fallback. Keeps its last price across a restart |

## Services

### `energy_report.generate`

Returns every figure without sending anything.

```yaml
action: energy_report.generate
data:
  period: daily
response_variable: report
```

The response includes `message`; the totals `solar`, `house`, `imported`,
`exported`, `charged` and `discharged`; each flow (`solar_to_house`,
`solar_to_battery`, `solar_to_grid`, `battery_to_house`, `battery_to_grid`,
`grid_to_house`, `grid_to_battery`) and the noise terms (`battery_churn`,
`grid_hunting`); the money (`home_value`, `export_value`, `house_cost`,
`battery_cost`, `total_earnings`, `arbitrage`); and `covered_percent`,
`peak_watts`, `peak_at`, `estimated`, `buckets` and `unpriced_buckets`. That's
enough to build your own card or message instead.

### `energy_report.send`

Does the same, then sends the message through the configured notify service.
Both services accept `start` and `end` to override the window.

## Resolution

| Period | Buckets | Why |
|---|---|---|
| Daily | 5 minutes | short-term statistics, kept for `purge_keep_days` |
| Weekly | hourly | long-term statistics, kept forever |
| Monthly | hourly | |

Totals come out the same at any bucket size; the split and the pricing don't.
On a flat tariff every resolution agrees. On a half-hourly tariff the daily
report is exact, because every five-minute bucket falls within a single price.
Weekly and monthly reports average within each hour, so a 30-minute grid charge
in the cheap half of an hour is priced at that hour's average. Hourly is the
finest resolution Home Assistant keeps long term, so they can't do better.

## Testing

```bash
python3 tests/test_report.py
```

No Home Assistant install needed. The tests cover the split and the pricing,
check that every counter balances across 5,000 random buckets, and parse every
kWh out of rendered messages to confirm each line adds up.

```bash
pip install pytest-homeassistant-custom-component voluptuous-serialize
python3 tests/test_config_flow.py
python3 -m pytest
```

These need Home Assistant. The first builds every setup and Configure form with
Home Assistant's own selector code. A selector setting Home Assistant rejects
only fails when the form is built, which the user sees as "Unknown error
occurred". The second runs setup, the Configure menu, the device-page entities
and each delivery method against a real Home Assistant instance.

## Licence

MIT.
