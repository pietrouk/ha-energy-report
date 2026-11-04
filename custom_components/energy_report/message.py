"""Render a Totals into the message that gets sent.

Pure formatting, no Home Assistant imports, so the tests can assert on the exact
text. Two rules hold the layout together, and the tests check both:

  * every money figure in the body traces to the earnings block: "worth" is the
    house-load term, the battery's "imported for" is the imported term, and the
    solar and battery "exported for" amounts add up to the export term. The
    house's own "imported for" is shown for information and is not a term:
    that energy would have been bought with or without solar and a battery.
    The Grid section's import is the house's and the battery's added together,
    and the Bill is that import, less the export, plus any standing charge -
    what the supplier actually charged, not what solar and the battery earned;
  * every kWh total is the sum of the parts printed beside it, and each flow
    reads the same wherever it appears - solar's "into the battery" is the
    battery's "from solar".

Both are kept by rounding each part once - kWh to 0.1, money to 0.01 - and
building every total from the rounded parts. A total can then differ from the
raw figure by 0.1 kWh or a penny; that is the price of never printing
"4.8 kWh: 4.6 + 0.1".

The price per kWh beside each import and export is the flow's own average,
worked out from the unrounded kWh and money, so it is the true average rather
than the rounded kWh divided into the rounded pounds.

render() is the report as sentences, which every messaging method can show.
render_tables() is the same figures as aligned tables for Telegram, where a
<pre> block is monospaced. Both take their numbers from _figures(), so a
figure reads the same in either.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .report import Totals

__all__ = ["charge_text", "peak_text", "render", "render_tables"]

# How a weekly or monthly report names its own period.
PERIOD_NAMES = {"daily": "today", "weekly": "this week", "monthly": "this month"}
# A monthly report asked for mid-month covers the last 30 days instead
ROLLING_HEADING = "Last 30 days"
ROLLING_NAME = "in the last 30 days"

HEADINGS = {
    "daily": "Last 24 hours",
    "weekly": "Last week",
    "monthly": "Last month",
}


def _r(value: float) -> float:
    """Round a flow once, to what gets printed."""
    return round(value + 0.0, 1)


def _kwh(value: float) -> str:
    return f"{value:.1f}"


def _money(currency: str, amount: float) -> str:
    """Two decimals always: £0.30 must not print as £0.3. A negative amount
    prints as -£0.12, not £-0.12."""
    sign = "-" if amount < -0.004 else ""
    return f"{sign}{currency}{abs(amount):.2f}"


def _p(value: float) -> float:
    """Round a money part once, to what gets printed."""
    return round(value + 0.0, 2)


# Hundredths of the currency, for the price per kWh: 16.5p rather than £0.165.
MINOR_UNITS = {"£": "p", "€": "c", "$": "¢"}


def _price(currency: str, value: float, kwh: float) -> str:
    """A flow's average price per kWh: '16.5p', or '€0.165/kWh' style for a
    currency without a known minor unit."""
    return _rate_text(currency, value / kwh)


def _rate_shown(currency: str, rate: float) -> float:
    """A price per kWh rounded to what gets printed, in currency units."""
    if MINOR_UNITS.get(currency):
        return round(rate * 100, 1) / 100
    return round(rate, 3)


def _rate_number(currency: str, rate: float) -> str:
    """A price per kWh as printed, without its unit: '16.5' (pence) or '0.165'."""
    if MINOR_UNITS.get(currency):
        return f"{round(rate * 100, 1) + 0.0:.1f}"
    return f"{round(rate, 3) + 0.0:.3f}"


def _rate_text(currency: str, rate: float) -> str:
    if minor := MINOR_UNITS.get(currency):
        return f"{_rate_number(currency, rate)}{minor}"
    return f"{currency}{_rate_number(currency, rate)}/kWh"


TAGS = r"</?(?:b|i|pre)>"


def _escape(message: str) -> str:
    """Escape &, < and > everywhere but the <b>, <i> and <pre> tags, as
    Telegram's HTML mode requires. The title and the earnings line both carry
    an "&"."""
    return "".join(part if re.fullmatch(TAGS, part) else html.escape(part, quote=False)
                   for part in re.split(f"({TAGS})", message))


def _duration(minutes: int) -> str:
    """'45 min', '7 h 45 min', '3 days 4 h'."""
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} day{'s' if days != 1 else ''}" + (f" {hours} h" if hours else "")
    if hours:
        return f"{hours} h" + (f" {mins} min" if mins else "")
    return f"{mins} min"


def peak_text(period: str, watts: float | None, at: datetime | None) -> str | None:
    """'3.8 kW at 12:40' for a day, '3.8 kW on Thu 17 Sep' for longer."""
    if not watts or watts <= 0 or at is None:
        return None
    when = f"at {at:%H:%M}" if period == "daily" else f"on {at:%a} {at.day} {at:%b}"
    return f"{watts / 1000:.1f} kW {when}"


def _percent(level: float) -> str:
    """'100%', or '87.3%' for a sensor that reports decimals."""
    return f"{round(level + 0.0, 1):g}%"


def charge_text(level: float | None, at: datetime | None, end: datetime) -> str | None:
    """'100%, first reached at 14:15', naming the day - 'at 23:30 on Sun' - when
    it was not the day the period ended. A level only carried over from before
    the period was not reached in it, and says so."""
    if level is None:
        return None
    shown = _percent(level)
    if at is None:
        return f"{shown}, already reached when the period began"
    when = f"{at:%H:%M}"
    # The period's last moment, so a window ending at midnight counts as the
    # day before rather than the day it ends on.
    if at.date() != (end - timedelta(microseconds=1)).date():
        when += f" on {at:%a}"
    return f"{shown}, first reached at {when}"


BUCKET_MINUTES = {"daily": 5}


@dataclass(frozen=True)
class _Figures:
    """Every number either layout prints, each rounded once."""

    s2h: float
    s2b: float
    s2g: float
    b2h: float
    b2g: float
    g2h: float
    g2b: float
    solar: float
    charged: float
    supplied: float
    house: float
    home_kwh: float
    export_kwh: float
    import_kwh: float
    net: float
    to_battery: bool
    # money
    home: float
    solar_sold: float
    battery_sold: float
    exported: float
    imported: float
    house_imported: float
    total: float
    bought: float
    # unrounded, for the average prices
    sold_kwh: float
    sold_value: float
    bought_kwh: float
    bought_value: float


def _figures(t: Totals, has_battery: bool) -> _Figures:
    s2h, s2b, s2g = _r(t.solar_to_house), _r(t.solar_to_battery), _r(t.solar_to_grid)
    b2h, b2g = _r(t.battery_to_house), _r(t.battery_to_grid)
    g2h, g2b = _r(t.grid_to_house), _r(t.grid_to_battery)
    home_kwh = _r(s2h + b2h)

    # A flow that prints as 0.0 kWh is worth £0.00 here too, so no total holds
    # a penny that no line below shows.
    home = _p(t.home_value) if home_kwh > 0 else 0.0
    solar_sold = _p(t.solar_export_value) if s2g > 0 else 0.0
    battery_sold = _p(t.battery_export_value) if has_battery and b2g > 0 else 0.0
    exported = _p(solar_sold + battery_sold)
    imported = _p(t.battery_cost) if has_battery and g2b > 0 else 0.0
    house_imported = _p(t.house_cost) if g2h > 0 else 0.0

    # The averages over what is shown: a flow printed as 0.0 kWh is left out
    # of the kWh as it is left out of the money.
    to_battery = has_battery and g2b > 0
    return _Figures(
        s2h=s2h, s2b=s2b, s2g=s2g, b2h=b2h, b2g=b2g, g2h=g2h, g2b=g2b,
        solar=_r(s2h + s2b + s2g),
        charged=_r(s2b + g2b),
        supplied=_r(b2h + b2g),
        house=_r(s2h + b2h + g2h),
        home_kwh=home_kwh,
        export_kwh=_r(s2g + b2g),
        import_kwh=_r(g2h + (g2b if to_battery else 0.0)),
        net=_r(_r(s2b + g2b) - _r(b2h + b2g)),
        to_battery=to_battery,
        home=home, solar_sold=solar_sold, battery_sold=battery_sold, exported=exported,
        imported=imported, house_imported=house_imported,
        total=_p(home + exported - imported),
        bought=_p(house_imported + imported),
        sold_kwh=((t.solar_to_grid if s2g > 0 else 0.0)
                  + (t.battery_to_grid if has_battery and b2g > 0 else 0.0)),
        sold_value=((t.solar_export_value if s2g > 0 else 0.0)
                    + (t.battery_export_value if has_battery and b2g > 0 else 0.0)),
        bought_kwh=((t.grid_to_house if g2h > 0 else 0.0)
                    + (t.grid_to_battery if to_battery else 0.0)),
        bought_value=((t.house_cost if g2h > 0 else 0.0)
                      + (t.battery_cost if to_battery else 0.0)),
    )


def _stored(f: _Figures, levels: tuple[float, float] | None,
            capacity: float | None) -> tuple[float, float] | None:
    """What the battery kept and what it lost, in kWh, from the level at both
    ends: None without both levels and the capacity.

    Losses only ever take away: the battery cannot gain more than went in and
    stayed in, nor fall by less than came out and was not put back. A level
    change saying otherwise is the gauge or the capacity being a little out,
    and the counters are taken at their word."""
    if levels is None or not capacity:
        return None
    start_level, end_level = levels
    banked = min(_r((end_level - start_level) / 100.0 * capacity), f.net)
    return banked, _r(f.net - banked)


def _points_lines(points, points_per_pound, money, period: str, rolling: bool) -> list[str]:
    """The Octopoints section: the balance at the end of the period and how it
    moved, from statistics that may have begun inside the period - in which
    case the change says since when."""
    if points is None:
        return []
    balance, change, since = points
    shown = f"{round(balance):,} points"
    if points_per_pound:
        shown += f", worth {money(balance / points_per_pound)}"
    moved = f"{'+' if round(change) >= 0 else '-'}{abs(round(change)):,}"
    when = (f"since {since.day} {since:%b}" if since
            else ROLLING_NAME if rolling else PERIOD_NAMES.get(period, ""))
    return ["", "<b>🐙 Octopoints</b>", f"{shown}, {moved} {when}"]


def _arbitrage_lines(arbitrage, arbitrage_note, money) -> list[str]:
    if arbitrage is None:
        return []
    lines = ["", "<b>📈 Arbitrage</b>"]
    if arbitrage_note:
        lines.append(f"{money(0)} - {arbitrage_note}")
    else:
        gained = "gained from" if arbitrage >= 0 else "lost to"
        lines.append(f"{money(abs(arbitrage))} {gained} energy arbitrage, "
                     "included in the total above")
    return lines


def _estimate_lines(t: Totals, period: str) -> list[str]:
    if not t.estimated:
        return []
    if t.fully_estimated:
        note = "No rates were recorded for this period, so all of it was priced at an estimated rate."
    else:
        unpriced = _duration(t.unpriced_buckets * BUCKET_MINUTES.get(period, 60))
        note = (f"{unpriced} of this period had no recorded rate, so that part was "
                "priced at an estimated rate.")
    return ["", f"<i>{note}</i>"]


def render(
    totals: Totals,
    period: str,
    span: str,
    currency: str = "£",
    arbitrage: float | None = None,
    arbitrage_note: str | None = None,
    has_battery: bool = True,
    peak: str | None = None,
    charge: str | None = None,
    standing: float | None = None,
    levels: tuple[float, float] | None = None,
    capacity: float | None = None,
    points: tuple[float, float, datetime | None] | None = None,
    points_per_pound: float | None = None,
    rolling: bool = False,
) -> str:
    """Build the report text. Telegram-flavoured HTML; delivery strips the tags
    and unescapes it for anything that would show them. `rolling` is a monthly
    report covering the last 30 days rather than a calendar month."""
    t = totals
    f = _figures(t, has_battery)
    s2h, s2b, s2g, b2h, b2g, g2h, g2b = f.s2h, f.s2b, f.s2g, f.b2h, f.b2g, f.g2h, f.g2b
    shown_solar, shown_charged, shown_supplied = f.solar, f.charged, f.supplied
    shown_house, shown_home, shown_export, shown_net = f.house, f.home_kwh, f.export_kwh, f.net
    home, solar_sold, battery_sold = f.home, f.solar_sold, f.battery_sold
    exported, imported, house_imported = f.exported, f.imported, f.house_imported
    total, bought = f.total, f.bought

    def money(amount: float) -> str:
        return _money(currency, amount)

    def at(value: float, kwh: float) -> str:
        """' at 16.5p': the flow's average price, from unrounded figures."""
        return f" at {_price(currency, value, kwh)}" if kwh > 0 else ""

    def term(amount: float, label: str) -> str:
        """'+£0.56 exported'. Signed by what it does to the total, so a
        negative price - paid to import, or charged to export - reads
        '+£0.05 imported' rather than '--£0.05 imported'."""
        return f"{'+' if amount >= 0 else '-'}{money(abs(amount))} {label}"

    source = "solar & battery" if has_battery else "solar"
    title = "SOLAR & BATTERY REPORT" if has_battery else "SOLAR REPORT"
    earnings = [f"{money(home)} house load saved by {source}", term(exported, "exported")]
    if imported != 0:
        earnings.append(term(-imported, "imported into the battery"))
    earnings.append(f"= {money(total)}")

    solar_parts = f"{_kwh(s2h)} to the house"
    if has_battery:
        solar_parts += f", {_kwh(s2b)} into the battery"
    solar_parts += f", {_kwh(s2g)} exported"
    if s2g > 0:
        solar_parts += f"{at(t.solar_export_value, t.solar_to_grid)} for {money(solar_sold)}"

    lines: list[str] = [
        f"<b>{title}</b>",
        f"{ROLLING_HEADING if rolling else HEADINGS.get(period, period)} ({span})",
        "",
        "<b>💷 Total earnings</b>",
        *earnings,
        "",
        "<b>☀️ Solar</b>",
        f"Generated {_kwh(shown_solar)} kWh" + (f", with a power peak of {peak}" if peak else ""),
        solar_parts,
    ]

    if has_battery:
        if g2b > 0:
            charged = (f"Charged {_kwh(shown_charged)} kWh: {_kwh(s2b)} from solar, "
                       f"{_kwh(g2b)} imported{at(t.battery_cost, t.grid_to_battery)} for {money(imported)}")
        elif shown_charged > 0:
            charged = f"Charged {_kwh(shown_charged)} kWh, all from solar"
        else:
            charged = "Charged nothing"
        if b2g > 0:
            supplied = (f"Supplied {_kwh(shown_supplied)} kWh: {_kwh(b2h)} to the house, "
                        f"{_kwh(b2g)} exported{at(t.battery_export_value, t.battery_to_grid)} "
                        f"for {money(battery_sold)}")
        elif shown_supplied > 0:
            supplied = f"Supplied {_kwh(shown_supplied)} kWh, all to the house"
        else:
            supplied = "Supplied nothing"
        # Battery energy is valued when it is used, never when it goes in, so a
        # report that fills the battery earns less than the one that empties it.
        # The last line says which this is. Charged minus supplied is not what
        # was stored: some of it is lost in the battery, and over a month - the
        # level ending near where it began - nearly all of it is. With the
        # level at both ends and the capacity, the line splits the two.
        if (stored := _stored(f, levels, capacity)) is not None:
            start_level, end_level = levels
            banked, lost = stored
            if banked > 0:
                kept = f"{_kwh(banked)} kWh stored for later, counted when it's used"
            elif banked < 0:
                kept = f"ran on {_kwh(-banked)} kWh stored earlier, counted now it's used"
            else:
                kept = "nothing stored"
            if lost > 0:
                kept += f"; {_kwh(lost)}{' kWh' if banked == 0 else ''} lost in the battery"
            net = f"Level {_percent(start_level)} → {_percent(end_level)}: {kept}"
        elif shown_net > 0:
            net = f"Net +{_kwh(shown_net)} kWh, stored for later or lost in the battery"
        elif shown_net < 0:
            net = f"Net -{_kwh(-shown_net)} kWh, ran on energy stored earlier, counted now it's used"
        else:
            net = "Net 0.0 kWh"
        lines += ["", "<b>🔋 Battery</b>", charged, supplied, net]
        if charge:
            lines.append(f"Peak charge {charge}")

    house_parts = f"{_kwh(s2h)} straight from solar"
    if has_battery:
        house_parts += f", {_kwh(b2h)} from the battery"
    if g2h > 0:
        house_parts += f", {_kwh(g2h)} imported{at(t.house_cost, t.grid_to_house)} for {money(house_imported)}"
    lines += [
        "",
        "<b>🏠 House</b>",
        f"Used {_kwh(shown_house)} kWh, {t.covered:.0f}% from {source}, "
        f"{_kwh(shown_home)} kWh worth {money(home)}",
        house_parts,
    ]

    export = f"{_kwh(shown_export)} kWh exported{at(f.sold_value, f.sold_kwh)} for {money(exported)}"
    if has_battery and b2g > 0:
        export += f": {_kwh(s2g)} from solar, {_kwh(b2g)} from the battery"
    elif shown_export > 0:
        export += ", all from solar"

    # The same flows as the House and Battery lines, so the same rule: a part
    # printed as 0.0 kWh adds nothing here either.
    imports = (f"{_kwh(f.import_kwh)} kWh imported{at(f.bought_value, f.bought_kwh)} "
               f"for {money(bought)}")
    if f.to_battery:
        imports += f": {_kwh(g2h)} to the house, {_kwh(g2b)} into the battery"
    elif f.import_kwh > 0:
        imports += ", all to the house"
    lines += ["", "<b>⚡ Grid</b>", export, imports]

    # What the supplier charged. The standing charge is here and never in the
    # earnings: it is paid with or without solar and a battery.
    bill = [f"{money(bought)} imported", f"{money(-exported)} exported"]
    standing_charge = 0.0
    if standing is not None:
        standing_charge = _p(standing)
        bill.append(f"{money(standing_charge)} standing charge")
    lines += ["", "<b>🧾 Bill</b>",
              f"Bill total {money(_p(bought - exported + standing_charge))}: " + ", ".join(bill)]

    # Not money in the bill, so its own section.
    lines += _points_lines(points, points_per_pound, money, period, rolling)
    lines += _arbitrage_lines(arbitrage, arbitrage_note, money)
    lines += _estimate_lines(t, period)
    return _escape("\n".join(lines))


# --------------------------------------------------------------------------
# tables, for Telegram

# A dash where a flow cannot happen: the battery does not charge itself.
NONE_CELL = "–"


def _table(rows: list[list[str]], min_width: int = 5) -> str:
    """Columns padded to line up in monospace: the first to the left, the
    numbers to the right and at least `min_width` wide, so neighbouring
    figures never run together. Narrow on purpose - a phone shows about 32
    monospace characters before it wraps."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    widths[1:] = [max(width, min_width) for width in widths[1:]]
    lines = []
    for row in rows:
        # A space more after the labels, so the corner stands clear of "House".
        cells = [row[0].ljust(widths[0] + 1)] + [cell.rjust(widths[i]) for i, cell in enumerate(row) if i]
        lines.append(" ".join(cells).rstrip())
    return "\n".join(lines)


def _signed(currency: str, amount: float) -> str:
    """'+£2.08' or '-£1.88'."""
    return f"{'+' if amount >= 0 else '-'}{_money(currency, abs(amount))}"


def _article(figure: str) -> str:
    """'an 18.1p', 'an 8.0p', 'an 11p', but 'a 9.5p' and 'a 110p'."""
    return "an" if re.match(r"(8|1[18])(\D|$)", figure) else "a"


def _when(at: datetime, end: datetime | None) -> str:
    """'11:34', or '21:33 Mon' when it was not the day the period ended."""
    shown = f"{at:%H:%M}"
    if end is not None and at.date() != (end - timedelta(microseconds=1)).date():
        shown += f" {at:%a}"
    return shown


def render_tables(
    totals: Totals,
    period: str,
    span: str,
    currency: str = "£",
    arbitrage: float | None = None,
    arbitrage_note: str | None = None,
    has_battery: bool = True,
    peak: str | None = None,
    charge_peak: tuple[float, datetime | None] | None = None,
    low: tuple[float, datetime | None] | None = None,
    end: datetime | None = None,
    standing: float | None = None,
    levels: tuple[float, float] | None = None,
    capacity: float | None = None,
    points: tuple[float, float, datetime | None] | None = None,
    points_per_pound: float | None = None,
    rolling: bool = False,
) -> str:
    """The report as tables, for Telegram: earnings and the bill, where the
    energy went, what the grid charged and paid, and a few highlights.

    The same figures as render(), from the same rounding, so the two never
    disagree. `charge_peak` and `low` are (level, first reached), the time
    None when the level was only there as the period began; `end` is when the
    period ended, to name the day of a time that fell on an earlier one."""
    t = totals
    f = _figures(t, has_battery)

    def money(amount: float) -> str:
        return _money(currency, amount)

    source = "solar & battery" if has_battery else "solar"
    title = "SOLAR & BATTERY REPORT" if has_battery else "SOLAR REPORT"

    # Earnings and the bill share one label column, so the two blocks line up.
    earnings = [("House load saved", _signed(currency, f.home)),
                ("Exported", _signed(currency, f.exported))]
    if f.imported != 0:
        earnings.append(("Battery charging", _signed(currency, -f.imported)))
    earnings_total = ("Earnings", money(f.total))
    bill = [("Imported", money(f.bought)), ("Exported", money(-f.exported))]
    standing_charge = 0.0
    if standing is not None:
        standing_charge = _p(standing)
        bill.append(("Standing charge", money(standing_charge)))
    bill_total = ("Bill", money(_p(f.bought - f.exported + standing_charge)))

    every = [*earnings, earnings_total, *bill, bill_total]
    # One space more than the longest label, so the signs stand clear of it.
    label_width = max(len(label) for label, _ in every) + 1
    amount_width = max(len(amount) for _, amount in every)

    def sum_block(parts: list[tuple[str, str]], total_row: tuple[str, str]) -> str:
        rows = [f"{label.ljust(label_width)} {amount.rjust(amount_width)}" for label, amount in parts]
        rows.append(" " * (label_width + 1) + "─" * amount_width)
        rows.append(f"{total_row[0].ljust(label_width)} {total_row[1].rjust(amount_width)}")
        return "\n".join(rows)

    # Where the energy went: each row a source, each column where it went.
    # The bottom row is what each one took in, the right-hand column what
    # each one gave out.
    if has_battery:
        flows = [
            ["from\\to", "House", "Batt", "Grid", "Total"],
            ["Solar", _kwh(f.s2h), _kwh(f.s2b), _kwh(f.s2g), _kwh(f.solar)],
            ["Battery", _kwh(f.b2h), NONE_CELL, _kwh(f.b2g), _kwh(f.supplied)],
            ["Grid", _kwh(f.g2h), _kwh(f.g2b), NONE_CELL, _kwh(f.import_kwh)],
            ["Total", _kwh(f.house), _kwh(f.charged), _kwh(f.export_kwh), ""],
        ]
    else:
        flows = [
            ["from\\to", "House", "Grid", "Total"],
            ["Solar", _kwh(f.s2h), _kwh(f.s2g), _kwh(f.solar)],
            ["Grid", _kwh(f.g2h), NONE_CELL, _kwh(f.import_kwh)],
            ["Total", _kwh(f.house), _kwh(f.export_kwh), ""],
        ]

    # What the grid charged and paid, each flow at its own average price.
    minor = MINOR_UNITS.get(currency)

    def price(value: float, kwh: float) -> str:
        return _rate_number(currency, value / kwh) if kwh > 0 else NONE_CELL

    def cash(amount: float) -> str:
        return _money("", amount)

    grid = [["Grid", "kWh", f"{minor}/kWh" if minor else f"{currency}/kWh", currency],
            ["Import", _kwh(f.import_kwh), price(f.bought_value, f.bought_kwh), cash(f.bought)]]
    if has_battery:
        grid += [[" house", _kwh(f.g2h), price(t.house_cost, t.grid_to_house) if f.g2h > 0 else NONE_CELL,
                  cash(f.house_imported)],
                 [" battery", _kwh(f.g2b), price(t.battery_cost, t.grid_to_battery) if f.g2b > 0 else NONE_CELL,
                  cash(f.imported)]]
    grid.append(["Export", _kwh(f.export_kwh), price(f.sold_value, f.sold_kwh), cash(f.exported)])
    if has_battery:
        grid += [[" solar", _kwh(f.s2g), price(t.solar_export_value, t.solar_to_grid) if f.s2g > 0 else NONE_CELL,
                  cash(f.solar_sold)],
                 [" battery", _kwh(f.b2g),
                  price(t.battery_export_value, t.battery_to_grid) if f.b2g > 0 else NONE_CELL,
                  cash(f.battery_sold)]]

    lines: list[str] = [
        f"<b>{title}</b>",
        f"{ROLLING_HEADING if rolling else HEADINGS.get(period, period)} ({span})",
        "",
        "<b>💷 Total earnings</b>",
        f"<pre>{sum_block(earnings, earnings_total)}</pre>",
        "",
        "<b>🧾 Bill</b>",
        f"<pre>{sum_block(bill, bill_total)}</pre>",
        "",
        "<b>⚡ Energy (kWh)</b>",
        f"<pre>{_table(flows)}</pre>",
        "",
        "<b>💱 Grid prices</b>",
        f"<pre>{_table(grid, min_width=6)}</pre>",
    ]

    highlights = [f"☀️ Solar: generated {_kwh(f.solar)} kWh"
                  + (f" with a power peak of {peak}" if peak else "")]
    stored = _stored(f, levels, capacity) if has_battery else None
    if has_battery:
        battery = []
        if levels is not None:
            battery.append(f"{_percent(levels[0])} → {_percent(levels[1])}")
        # A low or a peak only there as the period began is the start level,
        # shown already.
        if low is not None and low[1] is not None:
            battery.append(f"lowest {_percent(low[0])} at {_when(low[1], end)}")
        if charge_peak is not None and charge_peak[1] is not None:
            reached = _when(charge_peak[1], end)
            battery.append(f"full at {reached}" if charge_peak[0] >= 100
                           else f"peak {_percent(charge_peak[0])} at {reached}")
        if battery:
            highlights.append("🔋 Battery " + ", ".join(battery))
        if capacity and f.supplied > 0:
            highlights.append(f"🔄 {f.supplied / capacity:.1f} cycles "
                              f"({_kwh(f.supplied)} kWh out of a {capacity:g} kWh battery)")
    if f.house > 0:
        highlights.append(f"🏠 {t.covered:.0f}% of the house ran on {source}")
    if has_battery and f.g2b > 0 and f.b2g > 0:
        # From the prices as printed, so the spread is their difference.
        bought_at = _rate_shown(currency, t.battery_cost / t.grid_to_battery)
        sold_at = _rate_shown(currency, t.battery_export_value / t.battery_to_grid)
        spread = sold_at - bought_at
        gap = _rate_text(currency, abs(spread))
        highlights.append(f"💹 Bought at {_rate_text(currency, bought_at)}, "
                          f"sold at {_rate_text(currency, sold_at)}: "
                          + (f"{_article(gap)} {gap} spread" if spread >= 0 else f"{gap} less than it cost"))
    if stored is not None and stored[1] > 0 and f.supplied > 0:
        lost = stored[1]
        trip = round(100 * f.supplied / (f.supplied + lost))
        highlights.append(f"🌡️ About {_kwh(lost)} kWh lost in charging and discharging "
                          f"(round trip about {trip}%)")
    lines += ["", "<b>📌 Highlights</b>", *highlights]

    lines += _points_lines(points, points_per_pound, money, period, rolling)
    lines += _arbitrage_lines(arbitrage, arbitrage_note, money)
    lines += _estimate_lines(t, period)
    return _escape("\n".join(lines))
