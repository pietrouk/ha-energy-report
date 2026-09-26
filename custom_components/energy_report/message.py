"""Render a Totals into the message that gets sent.

Pure formatting, no Home Assistant imports, so the tests can assert on the exact
text. Two rules hold the layout together, and the tests check both:

  * every money figure in the body traces to the earnings block: "worth" is the
    house-load term, "imported for" is the imported term, and the solar and
    battery "exported for" amounts add up to the export term. "Bought costing"
    - the house's own grid purchases - is shown for information and is not a
    term, because buying from the grid saves nothing;
  * every kWh total is the sum of the parts printed beside it, and each flow
    reads the same wherever it appears - solar's "into the battery" is the
    battery's "from solar".

Both are kept by rounding each part once - kWh to 0.1, money to 0.01 - and
building every total from the rounded parts. A total can then differ from the
raw figure by 0.1 kWh or a penny; that is the price of never printing
"4.8 kWh: 4.6 + 0.1".
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
    """Two decimals always: £0.30 must not print as £0.3. A negative amount
    prints as -£0.12, not £-0.12."""
    sign = "-" if amount < -0.004 else ""
    return f"{sign}{currency}{abs(amount):.2f}"


def _p(value: float) -> float:
    """Round a money part once, to what gets printed."""
    return round(value + 0.0, 2)


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


BUCKET_MINUTES = {"daily": 5}


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
    """Build the report text. Telegram-flavoured HTML; delivery strips the tags
    for anything that would show them."""
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

    home = _p(t.home_value)
    # A flow that prints as 0.0 kWh is worth £0.00 here too, so the export total
    # never holds a penny that no line below shows.
    solar_sold = _p(t.solar_export_value) if s2g > 0 else 0.0
    battery_sold = _p(t.battery_export_value) if has_battery and b2g > 0 else 0.0
    exported = _p(solar_sold + battery_sold)
    imported = _p(t.battery_cost)
    total = _p(home + exported - imported)

    def money(amount: float) -> str:
        return _money(currency, amount)

    source = "solar & battery" if has_battery else "solar"
    title = "SOLAR & BATTERY REPORT" if has_battery else "SOLAR REPORT"
    earnings = [f"{money(home)} house load saved by {source}", f"+{money(exported)} exported"]
    if imported > 0:
        earnings.append(f"-{money(imported)} imported")
    earnings.append(f"= {money(total)}")

    solar_parts = f"{_kwh(s2h)} to the house"
    if has_battery:
        solar_parts += f", {_kwh(s2b)} into the battery"
    solar_parts += f", {_kwh(s2g)} exported"
    if s2g > 0:
        solar_parts += f" for {money(solar_sold)}"

    lines: list[str] = [
        f"<b>{title}</b>",
        f"{HEADINGS.get(period, period)} ({span})",
        "",
        "<b>💷 Total earnings</b>",
        *earnings,
        "",
        "<b>☀️ Solar</b>",
        f"Generated {_kwh(shown_solar)} kWh" + (f", with a power peak of {peak}" if peak else ""),
        solar_parts,
    ]

    if has_battery:
        if imported > 0 or g2b > 0:
            charged = (f"Charged {_kwh(shown_charged)} kWh: {_kwh(s2b)} from solar, "
                       f"{_kwh(g2b)} imported for {money(imported)}")
        elif shown_charged > 0:
            charged = f"Charged {_kwh(shown_charged)} kWh, all from solar"
        else:
            charged = "Charged nothing"
        if b2g > 0:
            supplied = (f"Supplied {_kwh(shown_supplied)} kWh: {_kwh(b2h)} to the house, "
                        f"{_kwh(b2g)} exported for {money(battery_sold)}")
        elif shown_supplied > 0:
            supplied = f"Supplied {_kwh(shown_supplied)} kWh, all to the house"
        else:
            supplied = "Supplied nothing"
        sign = "+" if shown_net >= 0 else "-"
        net = (f"Net {sign}{_kwh(abs(shown_net))} kWh, "
               + ("stored for later" if shown_net >= 0 else "ran on energy stored earlier"))
        lines += ["", "<b>🔋 Battery</b>", charged, supplied, net]

    house_parts = f"{_kwh(s2h)} straight from solar"
    if has_battery:
        house_parts += f", {_kwh(b2h)} from the battery"
    if t.house_cost >= 0.005:
        house_parts += f", {_kwh(g2h)} bought costing {money(t.house_cost)}"
    lines += [
        "",
        "<b>🏠 House</b>",
        f"Used {_kwh(shown_house)} kWh, {t.covered:.0f}% from {source.replace('&', 'and')} "
        f"({_kwh(shown_home)} kWh, worth {money(home)})",
        house_parts,
    ]

    export = f"{_kwh(shown_export)} kWh exported, earning {money(exported)}"
    if has_battery and b2g > 0:
        export += f": {_kwh(s2g)} from solar, {_kwh(b2g)} from the battery"
    elif shown_export > 0:
        export += ", all from solar"
    lines += ["", "<b>⚡ Export</b>", export]

    if arbitrage is not None:
        lines += ["", "<b>📈 Arbitrage</b>"]
        if arbitrage_note:
            lines.append(f"{money(0)} - {arbitrage_note}")
        else:
            gained = "gained from" if arbitrage >= 0 else "lost to"
            lines.append(f"{money(abs(arbitrage))} {gained} energy arbitrage, "
                         "included in the total above")

    if t.estimated:
        if t.fully_estimated:
            note = "No rates were recorded for this period, so all of it was priced at an estimated rate."
        else:
            unpriced = _duration(t.unpriced_buckets * BUCKET_MINUTES.get(period, 60))
            note = (f"{unpriced} of this period had no recorded rate, so that part was "
                    "priced at an estimated rate.")
        lines += ["", f"<i>{note}</i>"]

    return "\n".join(lines)
