"""Render a Totals into the message that gets sent.

Pure formatting, no Home Assistant imports, so the tests can assert on the exact
text. Three rules hold the layout together, and the tests check all of them:

  * every money figure in the body is a term in the earnings line, so a reader
    cannot add two figures that already contain one another;
  * every term in the earnings line appears somewhere below it, so each number
    can be traced to where it came from;
  * every kWh total is the sum of the parts printed beside it, and each flow
    reads the same wherever it appears - solar's "into the battery" is the
    battery's "from solar".

Costs are exempt from the first rule - they are spent, not earned - and are
always phrased "costing". So is a planner's savings figure, which is part of the
total rather than added to it and says "included in the total".

The third rule is kept by rounding each flow once and building every total from
the rounded parts. A total can then differ from the raw counter by 0.1 kWh;
that is the price of never printing "4.8 kWh: 4.6 + 0.1".
"""

from __future__ import annotations

from datetime import datetime

from .report import Totals

__all__ = ["peak_text", "render"]

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
    """Two decimals always: £0.30 must not print as £0.3."""
    return f"{currency}{amount:.2f}"


def peak_text(period: str, watts: float | None, at: datetime | None) -> str | None:
    """'3.8 kW at 12:40' for a day, '3.8 kW on Thu 17 Sep' for longer."""
    if not watts or watts <= 0 or at is None:
        return None
    when = f"at {at:%H:%M}" if period == "daily" else f"on {at:%a} {at.day} {at:%b}"
    return f"{watts / 1000:.1f} kW {when}"


def render(
    totals: Totals,
    period: str,
    span: str,
    currency: str = "£",
    arbitrage: float | None = None,
    arbitrage_note: str | None = None,
    has_battery: bool = True,
    peak: str | None = None,
) -> str:
    """Build the report text. Telegram-flavoured HTML, harmless as plain text."""
    t = totals
    s2h, s2b, s2g = _r(t.solar_to_house), _r(t.solar_to_battery), _r(t.solar_to_grid)
    b2h, b2g = _r(t.battery_to_house), _r(t.battery_to_grid)
    g2h, g2b = _r(t.grid_to_house), _r(t.grid_to_battery)

    shown_solar = _r(s2h + s2b + s2g)
    shown_charged = _r(s2b + g2b)
    shown_supplied = _r(b2h + b2g)
    shown_house = _r(s2h + b2h + g2h)
    shown_home = _r(s2h + b2h)
    shown_export = _r(s2g + b2g)
    shown_net = _r(shown_charged - shown_supplied)

    grid_charging = t.battery_cost >= 0.005
    terms = f"{_money(currency, t.home_value)} home used + {_money(currency, t.export_value)} exported"
    if grid_charging:
        terms += f" - {_money(currency, t.battery_cost)} grid charging"

    lines: list[str] = [
        f"<b>SOLAR SUMMARY - {HEADINGS.get(period, period)}</b>",
        span,
        "",
        "💷 <b>Total earnings</b>",
        f"<b>{terms} = {_money(currency, t.total_earnings)}</b>",
        "",
        "☀️ <b>Solar</b>",
        f"Generated {_kwh(shown_solar)} kWh" + (f", peak {peak}" if peak else ""),
        f"{_kwh(s2h)} to the house"
        + (f", {_kwh(s2b)} into the battery" if has_battery else "")
        + f", {_kwh(s2g)} exported",
    ]

    if has_battery:
        if grid_charging:
            charged = (f"Charged {_kwh(shown_charged)} kWh: {_kwh(s2b)} from solar, "
                       f"{_kwh(g2b)} from the grid costing {_money(currency, t.battery_cost)}")
        elif shown_charged > 0:
            charged = f"Charged {_kwh(shown_charged)} kWh, all from solar"
        else:
            charged = "Charged nothing"
        if b2g > 0:
            supplied = f"Supplied {_kwh(shown_supplied)} kWh: {_kwh(b2h)} to the house, {_kwh(b2g)} exported"
        elif shown_supplied > 0:
            supplied = f"Supplied {_kwh(shown_supplied)} kWh, all to the house"
        else:
            supplied = "Supplied nothing"
        sign = "+" if shown_net >= 0 else "-"
        net = (f"Net {sign}{_kwh(abs(shown_net))} kWh, "
               + ("stored for later" if shown_net >= 0 else "ran on energy stored earlier"))
        lines += ["", "🔋 <b>Battery</b>", charged, supplied, net]

    source = "solar and battery" if has_battery else "solar"
    house_parts = f"{_kwh(s2h)} straight from solar"
    if has_battery:
        house_parts += f", {_kwh(b2h)} from the battery"
    if t.house_cost >= 0.005:
        house_parts += f", {_kwh(g2h)} bought costing {_money(currency, t.house_cost)}"
    lines += [
        "",
        "🏠 <b>House</b>",
        f"Used {_kwh(shown_house)} kWh, {t.covered:.0f}% from {source} "
        f"({_kwh(shown_home)} kWh, worth {_money(currency, t.home_value)})",
        house_parts,
    ]

    export = f"{_kwh(shown_export)} kWh exported, earning {_money(currency, t.export_value)}"
    if has_battery and b2g > 0:
        export += f": {_kwh(s2g)} from solar, {_kwh(b2g)} from the battery"
    elif shown_export > 0:
        export += ", all from solar"
    lines += ["", "⚡ <b>Export</b>", export]

    if arbitrage is not None:
        lines += ["", "📈 <b>Arbitrage</b>"]
        if arbitrage_note:
            lines.append(f"{_money(currency, 0)} - {arbitrage_note}")
        else:
            gained = "gained from" if arbitrage >= 0 else "lost to"
            lines.append(f"{_money(currency, abs(arbitrage))} {gained} energy arbitrage, "
                         "included in the total above")

    if t.estimated:
        how = "Valued" if t.fully_estimated else "Partly valued"
        lines += ["", f"<i>{how} at an estimated rate - recorded rates do not cover "
                      "this whole period yet.</i>"]

    return "\n".join(lines)
