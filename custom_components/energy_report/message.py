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

__all__ = ["Comparison", "charge_text", "comparison", "peak_text", "render", "render_tables"]

# How a weekly or monthly report names its own period.
PERIOD_NAMES = {"daily": "in the last 24 hours", "weekly": "this week", "monthly": "this month"}
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


@dataclass(frozen=True)
class Comparison:
    """The headline figures over the stretch before the report, to set beside
    its own: the average day of the 7 before a daily report, the week before a
    weekly one, the month (or the 30 days) before a monthly one.

    `label` follows the figure: "vs £3.91, 7-day average", "vs £27.10
    previous week", "vs £110.20 in October"."""

    label: str
    earnings: float
    bill: float
    solar: float
    house: float
    # The battery's sell-minus-buy price per kWh, None when it did not both
    # charge from the grid and export.
    spread: float | None = None


def _spread(currency: str, t: Totals) -> tuple[float, float, float] | None:
    """(bought at, sold at, spread) per kWh for the battery, from the prices as
    printed so the spread is their difference; None unless it both charged
    from the grid and sold to it."""
    if _r(t.grid_to_battery) <= 0 or _r(t.battery_to_grid) <= 0:
        return None
    bought_at = _rate_shown(currency, t.battery_cost / t.grid_to_battery)
    sold_at = _rate_shown(currency, t.battery_export_value / t.battery_to_grid)
    return bought_at, sold_at, sold_at - bought_at


def comparison(totals: Totals, has_battery: bool, label: str, per: float = 1.0,
               standing: float | None = None, currency: str = "£") -> Comparison:
    """The Comparison for a stretch's Totals. Energy and money are divided by
    `per` - the days covered, for a daily report's average day - and
    `standing` is the standing charge for what is compared, already divided."""
    f = _figures(totals, has_battery)
    spread = _spread(currency, totals) if has_battery else None
    return Comparison(
        label=label,
        earnings=f.total / per,
        bill=(f.bought - f.exported) / per + (standing or 0.0),
        solar=f.solar / per,
        house=f.house / per,
        spread=spread[2] if spread else None,
    )


def _points_text(points, points_per_pound, money, period: str, rolling: bool) -> str:
    """'1,396 points, worth £1.75, +352 this week': the balance at the end of
    the period and how it moved, from statistics that may have begun inside
    the period - in which case the change says since when."""
    balance, change, since = points
    shown = f"{round(balance):,} points"
    if points_per_pound:
        shown += f", worth {money(balance / points_per_pound)}"
    moved = f"{'+' if round(change) >= 0 else '-'}{abs(round(change)):,}"
    when = (f"since {since.day} {since:%b}" if since
            else ROLLING_NAME if rolling else PERIOD_NAMES.get(period, ""))
    return f"{shown}, {moved} {when}"


def _points_lines(points, points_per_pound, money, period: str, rolling: bool) -> list[str]:
    """The Octopoints section of the sentences."""
    if points is None:
        return []
    return ["", "<b>🐙 Octopoints</b>", _points_text(points, points_per_pound, money, period, rolling)]


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


# The widest line a phone shows in portrait before it wraps: 30 monospace
# characters on Android Telegram.
TABLE_WIDTH = 30


def _table(rows: list[list[str]], min_width: int = 5, label_gap: int = 1) -> str:
    """Columns padded to line up in monospace: the first to the left, the
    numbers to the right and at least `min_width` wide, so neighbouring
    figures never run together. `label_gap` is extra space after the labels.

    Narrow on purpose, to fit TABLE_WIDTH, and widened to it when narrower, so every table's block is the same width in Telegram. Only
    a month of big numbers makes one wider."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    widths[1:] = [max(width, min_width) for width in widths[1:]]
    # Widened a space at a time, number columns from the left, so the gaps
    # stay even.
    spare = TABLE_WIDTH - (widths[0] + label_gap + sum(widths[1:]) + len(widths) - 1)
    for i in range(max(spare, 0)):
        widths[1 + i % (len(widths) - 1)] += 1
    lines = []
    for row in rows:
        cells = [row[0].ljust(widths[0] + label_gap)] + [cell.rjust(widths[i]) for i, cell in enumerate(row) if i]
        lines.append(" ".join(cells).rstrip())
    return "\n".join(lines)


def _signed(currency: str, amount: float) -> str:
    """'+£2.08' or '-£1.88'."""
    return f"{'+' if amount >= 0 else '-'}{_money(currency, abs(amount))}"


def _article(figure: str) -> str:
    """'an 18.1p', 'an 8.0p', 'an 11p', but 'a 9.5p' and 'a 110p'."""
    return "an" if re.match(r"(8|1[18])(\D|$)", figure) else "a"


# A time of day is named only if it carried at least this share of the
# battery's export: a few minutes either side of the evening says nothing.
SALES_SHARE = 0.1


def _sold_when(currency: str, sales: list[tuple[str, float, float]] | None, t: Totals) -> str:
    """' on average (30.2p in the evening, 17.9p overnight)', dearest first,
    when the battery sold at two or more times of day at different prices;
    otherwise nothing, the average saying it all."""
    if not sales or t.battery_to_grid <= 0:
        return ""
    parts = sorted(((_rate_shown(currency, value / kwh), when) for when, kwh, value in sales
                    if kwh >= SALES_SHARE * t.battery_to_grid), reverse=True)
    if len(parts) < 2 or len({rate for rate, _ in parts}) < 2:
        return ""
    return " on average (" + ", ".join(f"{_rate_text(currency, rate)} {when}" for rate, when in parts) + ")"


def _when(at: datetime, end: datetime | None) -> str:
    """'11:34', or '21:33 Mon' when it was not the day the period ended."""
    shown = f"{at:%H:%M}"
    if end is not None and at.date() != (end - timedelta(microseconds=1)).date():
        shown += f" {at:%a}"
    return shown


def _vs(shown: str, previous: Comparison | None) -> str:
    """'vs £3.91, 7-day average', or nothing without a comparison."""
    return f"vs {shown}{previous.label}" if previous is not None else ""


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
    sales: list[tuple[str, float, float]] | None = None,
    previous: Comparison | None = None,
) -> str:
    """The report as tables, for Telegram: earnings and the bill, where the
    energy went with the solar, battery and house lines under it, then what
    the grid charged and paid with the battery's spread under that.

    The same figures as render(), from the same rounding, so the two never
    disagree. `charge_peak` is (level, first reached), the time None when the
    level was only there as the period began; `end` is when the period ended,
    to name the day of a time that fell on an earlier one. `sales` is the
    battery's export by time of day, (when, kWh, value), for what it sold at in
    the evening as against overnight. `previous` puts the stretch before
    beside the earnings, the bill, solar, the house load and the spread.

    `low` is accepted for the service response's sake and no longer shown."""
    t = totals
    f = _figures(t, has_battery)

    def money(amount: float) -> str:
        return _money(currency, amount)

    def at(kwh: float, value: float, raw_kwh: float) -> str | None:
        """'6.8 kWh at 32.9p': a line's energy and its average price."""
        return f"{_kwh(kwh)} kWh at {_price(currency, value, raw_kwh)}" if kwh > 0 and raw_kwh > 0 else None

    source = "solar & battery" if has_battery else "solar"
    title = "SOLAR & BATTERY REPORT" if has_battery else "SOLAR REPORT"

    # Earnings and the bill share one label column, so the two blocks line up.
    # A row's third part is a note on a line of its own under it: beside the
    # amount it would not fit a phone.
    earnings = [("House load saved", _signed(currency, f.home), at(f.home_kwh, t.home_value, t.home_supplied)),
                ("Exported", _signed(currency, f.exported), None)]
    if f.imported != 0:
        earnings.append(("Battery charging", _signed(currency, -f.imported), None))
    earnings_total = ("Earnings", money(f.total), _vs(money(previous.earnings), previous) if previous else None)
    bill = [("Imported", money(f.bought), at(f.import_kwh, f.bought_value, f.bought_kwh)),
            ("Exported", money(-f.exported), at(f.export_kwh, f.sold_value, f.sold_kwh))]
    standing_charge = 0.0
    if standing is not None:
        standing_charge = _p(standing)
        bill.append(("Standing charge", money(standing_charge), None))
    bill_total = ("Bill", money(_p(f.bought - f.exported + standing_charge)),
                  _vs(money(previous.bill), previous) if previous else None)

    every = [*earnings, earnings_total, *bill, bill_total]
    # One space more than the longest label, so the signs stand clear of it.
    label_width = max(len(label) for label, _, _ in every) + 1
    amount_width = max(len(amount) for _, amount, _ in every)

    def sum_block(parts: list[tuple[str, str, str | None]], total_row: tuple[str, str, str | None]) -> str:
        rows = []
        for label, amount, note in parts:
            rows.append(f"{label.ljust(label_width)} {amount.rjust(amount_width)}")
            if note:
                rows.append(f"  {note}")
        rows.append(" " * (label_width + 1) + "─" * amount_width)
        rows.append(f"{total_row[0].ljust(label_width)} {total_row[1].rjust(amount_width)}")
        if total_row[2]:
            rows.append(f"  {total_row[2]}")
        return "\n".join(rows)

    # Where the energy went: each row a source, each column where it went,
    # "Batt" both ways so it fits TABLE_WIDTH.
    # The bottom row is what each one took in, the right-hand column what
    # each one gave out.
    if has_battery:
        flows = [
            ["kWh", "House", "Batt", "Grid", "Total"],
            ["Solar", _kwh(f.s2h), _kwh(f.s2b), _kwh(f.s2g), _kwh(f.solar)],
            ["Batt", _kwh(f.b2h), NONE_CELL, _kwh(f.b2g), _kwh(f.supplied)],
            ["Grid", _kwh(f.g2h), _kwh(f.g2b), NONE_CELL, _kwh(f.import_kwh)],
            ["Total", _kwh(f.house), _kwh(f.charged), _kwh(f.export_kwh), ""],
        ]
    else:
        flows = [
            ["kWh", "House", "Grid", "Total"],
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

    def versus(shown: str) -> str:
        return f" ({_vs(shown, previous)})" if previous is not None else ""

    # Under the Energy table: solar, the battery and the house.
    energy_notes = [f"☀️ Solar: generated {_kwh(f.solar)} kWh"
                    + versus(f"{_kwh(previous.solar)} kWh" if previous else "")
                    + (f" with a power peak of {peak}" if peak else "")]
    stored = _stored(f, levels, capacity) if has_battery else None
    if has_battery:
        battery = [f"charged {_kwh(f.charged)} kWh", f"discharged {_kwh(f.supplied)} kWh"]
        # A peak only there as the period began was not reached in it.
        if charge_peak is not None and charge_peak[1] is not None:
            reached = _when(charge_peak[1], end)
            battery.append(f"full at {reached}" if charge_peak[0] >= 100
                           else f"peak {_percent(charge_peak[0])} at {reached}")
        energy_notes.append("🔋 Battery: " + ", ".join(battery))
        if capacity and f.supplied > 0:
            energy_notes.append(f"🔄 {f.supplied / capacity:.1f} cycles "
                                f"({_kwh(f.supplied)} kWh out of a {capacity:g} kWh battery)")
        if stored is not None and stored[1] > 0 and f.supplied > 0:
            lost = stored[1]
            trip = round(100 * f.supplied / (f.supplied + lost))
            energy_notes.append(f"🌡️ About {_kwh(lost)} kWh lost in charging and discharging "
                                f"(round trip about {trip}%)")
    energy_notes.append(f"🏠 House load: {_kwh(f.house)} kWh"
                        + versus(f"{_kwh(previous.house)} kWh" if previous else ""))
    if f.house > 0:
        energy_notes.append(f"🏠 {t.covered:.0f}% of the house ran on {source}")

    # Under the Grid prices table: what the battery bought and sold at.
    grid_notes = []
    spread = _spread(currency, t) if has_battery else None
    if spread is not None:
        bought_at, sold_at, gap_value = spread
        gap = _rate_text(currency, abs(gap_value))
        shown = (f"{_article(gap)} {gap} spread" if gap_value >= 0 else f"{gap} less than it cost")
        if previous is not None and previous.spread is not None:
            shown += versus(_rate_text(currency, previous.spread))
        grid_notes.append(f"💹 Bought at {_rate_text(currency, bought_at)}, "
                          f"sold at {_rate_text(currency, sold_at)}{_sold_when(currency, sales, t)}: {shown}")
    # Octopoints over a day only when some were earned in it: they arrive days
    # after the saving session, so most days nothing moves.
    if points is not None and (period != "daily" or round(points[1]) > 0):
        grid_notes.append("🐙 Octopoints: "
                          + _points_text(points, points_per_pound, money, period, rolling))

    # Each table a <pre> block, straight after its heading: Telegram shows a
    # block on a line of its own, so a line break before one would leave an
    # empty line. Inline <code> would avoid the "copy" bar desktop Telegram
    # puts on every block, but some clients do not keep its columns aligned.
    lines: list[str] = [
        f"<b>{title}</b>",
        f"{ROLLING_HEADING if rolling else HEADINGS.get(period, period)} ({span})",
        "",
        f"<b>💷 Total earnings</b><pre>{sum_block(earnings, earnings_total)}</pre>",
        "",
        f"<b>🧾 Bill</b><pre>{sum_block(bill, bill_total)}</pre>",
        "",
        f"<b>⚡ Energy</b><pre>{_table(flows, label_gap=0)}</pre>",
        "",
        *energy_notes,
        "",
        f"<b>💱 Grid prices</b><pre>{_table(grid, min_width=6)}</pre>",
    ]
    if grid_notes:
        lines += ["", *grid_notes]

    lines += _arbitrage_lines(arbitrage, arbitrage_note, money)
    lines += _estimate_lines(t, period)
    return _escape("\n".join(lines))
