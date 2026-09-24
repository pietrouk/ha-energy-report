"""Render a Totals into the message that gets sent.

Pure formatting, no Home Assistant imports, so the tests can assert on the exact
text. Two rules hold the layout together and both are checked by the tests:

  * every money figure in the body corresponds to a term in the earnings line,
    so a reader cannot add two figures that already contain one another, and
  * every term in the earnings line appears somewhere below it, so each number
    can be traced to where it came from.

Costs are the exception to the first rule - they are money spent, not earned,
and have no term to match. They are always phrased with "costing" so they are
easy to tell apart.
"""

from __future__ import annotations

from .report import Totals

__all__ = ["render"]

HEADINGS = {
    "daily": "Last 24 hours",
    "weekly": "Last week",
    "monthly": "Last month",
}


def _money(currency: str, amount: float) -> str:
    """Two decimals always: £0.30 must not print as £0.3."""
    return f"{currency}{amount:.2f}"

def _kwh(amount: float, places: int = 1) -> str:
    """Trim trailing zeros: 4.60 reads as 4.6, 2.00 as 2."""
    text = f"{amount:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _signed(currency: str, amount: float) -> str:
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{currency}{abs(amount):.2f}"


def _term(currency: str, amount: float, label: str) -> str:
    joiner = "+" if amount >= 0 else "-"
    return f"{joiner} {_money(currency, abs(amount))} {label}"


def render(
    totals: Totals,
    period: str,
    span: str,
    currency: str = "£",
    arbitrage: float | None = None,
    arbitrage_note: str | None = None,
    has_battery: bool = True,
) -> str:
    """Build the report text. Telegram-flavoured HTML, harmless as plain text."""
    arb = arbitrage or 0.0
    total = totals.total_earnings(arb)

    terms = [_money(currency, totals.avoided) + " home used"]
    if has_battery:
        terms.append(_term(currency, totals.battery_value, "battery"))
    terms.append(_term(currency, totals.export_income, "exported"))
    if arbitrage is not None:
        terms.append(_term(currency, arb, "arbitraged"))

    lines: list[str] = [
        f"<b>SOLAR SUMMARY - {HEADINGS.get(period, period)}</b>",
        span,
        "",
        "💷 <b>Total earnings</b>",
        "<b>" + " ".join(terms) + f" = {_money(currency, total)}</b>",
        "",
        "☀️ <b>Solar</b>",
        f"Generated {totals.pv:.1f} kWh",
        f"Exported {totals.exported:.1f} kWh, earning {_money(currency, totals.export_income)}",
    ]

    if has_battery:
        charged = f"Charged {_kwh(totals.charged, 2)} kWh"
        # Only mention the grid share when there is one worth a line; below this
        # it rounds to 0.0 and reads as noise.
        if totals.grid_to_battery >= 0.05:
            charged += (
                f", {totals.grid_to_battery:.1f} of it from the grid, "
                f"costing {_money(currency, totals.battery_cost)}"
            )
        sign = "+" if totals.battery_net >= 0 else "-"
        net = (f"Net {sign}{_kwh(abs(totals.battery_net), 2)} kWh stored, "
               f"worth {_signed(currency, totals.battery_value)}")
        if totals.grid_to_battery >= 0.05:
            net += " once the grid charging is paid for"
        lines += [
            "",
            "🔋 <b>Battery</b>",
            f"Supplied {totals.discharged:.1f} kWh",
            charged,
            net,
        ]

    lines += [
        "",
        "🏠 <b>House</b>",
        f"Used {totals.house:.1f} kWh",
        f"{totals.covered:.0f}% of it from solar{' and battery' if has_battery else ''} "
        f"({totals.home_supplied:.1f} kWh, worth {_money(currency, totals.avoided)})",
        f"Bought {totals.grid_to_house:.1f} kWh straight from the grid, "
        f"costing {_money(currency, totals.house_cost)}",
    ]

    if arbitrage is not None:
        lines += ["", "📈 <b>Arbitrage</b>"]
        if arbitrage_note:
            lines.append(f"{_money(currency, 0)} - {arbitrage_note}")
        else:
            gained = "gained from" if arb >= 0 else "lost to"
            lines.append(f"{_money(currency, abs(arb))} {gained} energy arbitrage")

    if totals.estimated:
        how = "Valued" if totals.fully_estimated else "Partly valued"
        lines += [
            "",
            f"<i>{how} at the rate showing now - recorded rates do not cover "
            "this whole period yet.</i>",
        ]

    return "\n".join(lines)
