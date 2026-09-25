#!/usr/bin/env python3
"""Exercise the arithmetic and the message, with no Home Assistant involved.

usage: python3 tests/test_report.py
"""
import importlib
import random
import re
import sys
import types
from datetime import datetime
from pathlib import Path

# report, message and window import nothing from Home Assistant on purpose, but
# the package's own __init__ does. Standing in a bare module with the right
# __path__ lets their relative imports resolve without running it, so the maths
# can be tested with nothing installed.
PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "energy_report"
stub = types.ModuleType("energy_report")
stub.__path__ = [str(PACKAGE)]
sys.modules["energy_report"] = stub

_message = importlib.import_module("energy_report.message")
render, peak_text = _message.render, _message.peak_text
_report = importlib.import_module("energy_report.report")
Bucket, summarise, split, choose_fallback = _report.Bucket, _report.summarise, _report.split, _report.choose_fallback
_window = importlib.import_module("energy_report.window")
span_label, window_for = _window.span_label, _window.window_for

failures = 0


def check(label, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (expected {want!r})"))


def plain(message):
    return re.sub(r"</?[bi]>", "", message)


def invariants(label, message):
    """Every £ in the body is a term in the earnings line, and every term is
    visible below it. Costs ("costing") and a planner's figure ("included in the
    total") are exempt from the first rule; the grand total from the second."""
    text = plain(message)
    earnings = text.split("\n\n")[1]
    body = "\n".join(l for l in text.split("💷")[1].split("\n\n", 1)[1].splitlines()
                     if "costing" not in l and "included in the total" not in l)
    check(f"{label}: no money in the body that is not a term above",
          [a for a in re.findall(r"£\d+\.\d\d", body) if a not in earnings], [])
    terms = earnings.split(" = ")[0]
    check(f"{label}: every term is visible below",
          [a for a in re.findall(r"£\d+\.\d\d", terms) if a not in text.split("☀️")[1]], [])


def adds_up(label, message):
    """Parse the kWh out of each paragraph: every total is the sum of the parts
    beside it, and each flow reads the same in every paragraph it appears in."""
    t = plain(message)
    num = r"(-?\d+(?:\.\d+)?)"

    def grab(pattern, default=None):
        m = re.search(pattern, t)
        return tuple(float(g) for g in m.groups()) if m else default

    gen, = grab(r"Generated " + num + " kWh")
    solar = grab(num + " to the house, " + num + " into the battery, " + num + " exported")
    s2h, s2b, s2g = solar if solar else (*grab(num + " to the house, " + num + " exported"),)[:1] + (0.0,) + \
        grab(num + " to the house, " + num + " exported")[1:]
    check(f"{label}: solar parts add up", round(s2h + s2b + s2g, 1), gen)

    c_tot = c_grid = d_tot = b2h = b2g = 0.0
    if "🔋" in t:
        charged = grab(r"Charged " + num + " kWh: " + num + " from solar, " + num + " from the grid")
        if charged:
            c_tot, c_sol, c_grid = charged
            check(f"{label}: charged parts add up", round(c_sol + c_grid, 1), c_tot)
            check(f"{label}: battery 'from solar' is solar 'into the battery'", c_sol, s2b)
        else:
            only = grab(r"Charged " + num + " kWh, all from solar")
            c_tot = only[0] if only else 0.0
            check(f"{label}: an all-solar charge is solar 'into the battery'", c_tot, s2b)
        supplied = grab(r"Supplied " + num + " kWh: " + num + " to the house, " + num + " exported")
        if supplied:
            d_tot, b2h, b2g = supplied
            check(f"{label}: supplied parts add up", round(b2h + b2g, 1), d_tot)
        else:
            only = grab(r"Supplied " + num + " kWh, all to the house")
            d_tot = b2h = only[0] if only else 0.0
        sign, net = re.search(r"Net ([+-])" + num + " kWh", t).groups()
        check(f"{label}: net is charged minus supplied",
              round((1 if sign == "+" else -1) * float(net), 1), round(c_tot - d_tot, 1))

    used, home = grab(r"Used " + num + r" kWh, \d+% from [a-z ]+ \(" + num + " kWh")
    h_sol, = grab(num + " straight from solar")
    h_bat = grab(r"straight from solar, " + num + " from the battery", (0.0,))[0]
    bought = grab(num + " bought costing", (0.0,))[0]
    check(f"{label}: house parts add up", round(h_sol + h_bat + bought, 1), used)
    check(f"{label}: home used is solar plus battery", round(h_sol + h_bat, 1), home)
    check(f"{label}: house 'from solar' is solar 'to the house'", h_sol, s2h)
    check(f"{label}: house 'from the battery' is the battery's 'to the house'", h_bat, b2h)

    exp_tot, = grab(num + " kWh exported, earning")
    split_ = grab(r"earning £[\d.]+: " + num + " from solar, " + num + " from the battery")
    e_sol, e_bat = split_ if split_ else (exp_tot, 0.0)
    check(f"{label}: export parts add up", round(e_sol + e_bat, 1), exp_tot)
    check(f"{label}: export 'from solar' is solar 'exported'", e_sol, s2g)
    check(f"{label}: export 'from the battery' is the battery's 'exported'", e_bat, b2g)


RATE = 0.2635
EXPORT = 0.12

print("== every counter closes, in every bucket")
# Random buckets across the whole plausible range, including ones the counters
# could never produce. The house, solar, battery-out and export equations close
# whatever the input; battery-in and import close whenever the counters balance.
rng = random.Random(4)
bad = []
for _ in range(5000):
    p, i, e, c, d = (round(rng.uniform(0, 3), 2) * (rng.random() < 0.7) for _ in range(5))
    f = split(p, i, e, c, d)
    closes = {
        "house": abs(f["s2h"] + f["b2h"] + f["g2h"] - f["house"]) < 1e-9,
        "solar": abs(f["s2h"] + f["s2b"] + f["s2g"] - p) < 1e-9,
        "battery out": abs(f["b2h"] + f["b2g"] + f["b2b"] - d) < 1e-9,
        "export": abs(f["s2g"] + f["b2g"] + f["g2g"] - e) < 1e-9,
        "none negative": min(f.values()) >= 0,
    }
    if p + i + d - e - c >= 0:
        closes["battery in"] = abs(f["s2b"] + f["g2b"] + f["b2b"] - c) < 1e-9
        closes["import"] = abs(f["g2h"] + f["g2b"] + f["g2g"] - i) < 1e-9
    bad += [(k, (p, i, e, c, d)) for k, ok in closes.items() if not ok]
check("5000 random buckets: nothing fails to close", bad[:3], [])

print("\n== a normal day")
# Every bucket balances: solar + import + discharge == house + export + charge.
#   1  night      pv 0.0  imp 0.4  dis 1.1                    -> house 1.5
#   2  morning    pv 6.0  imp 0.6            chg 4.6          -> house 2.0, grid tops up the battery
#   3  afternoon  pv 6.4           dis 1.4          exp 3.2   -> house 4.6, 1.4 sold from the battery
DAY = [
    Bucket(pv=0.0, imported=0.4, discharged=1.1, import_rate=RATE, export_rate=EXPORT),
    Bucket(pv=6.0, imported=0.6, charged=4.6, import_rate=RATE, export_rate=EXPORT),
    Bucket(pv=6.4, discharged=1.4, exported=3.2, import_rate=RATE, export_rate=EXPORT),
]
t = summarise(DAY)
check("house is derived from the counters", round(t.house, 2), 8.1)
check("solar goes house, battery, grid",
      (round(t.solar_to_house, 2), round(t.solar_to_battery, 2), round(t.solar_to_grid, 2)), (6.6, 4.0, 1.8))
check("battery goes house, grid", (round(t.battery_to_house, 2), round(t.battery_to_grid, 2)), (1.1, 1.4))
check("grid goes house, battery", (round(t.grid_to_house, 2), round(t.grid_to_battery, 2)), (0.4, 0.6))
check("coverage counts solar and battery", round(t.covered), 95)
check("home used is solar plus battery at the rate used", round(t.home_value, 4), round(7.7 * RATE, 4))
check("exported is everything sold", round(t.export_value, 4), round(3.2 * EXPORT, 4))
check("grid charging is a cost against the total", round(t.battery_cost, 4), round(0.6 * RATE, 4))
check("total is home used + exported - grid charging", round(t.total_earnings, 3),
      round(7.7 * RATE + 3.2 * EXPORT - 0.6 * RATE, 3))
check("which is house value minus the net grid bill", round(t.total_earnings, 3),
      round(8.1 * RATE - ((0.4 + 0.6) * RATE - 3.2 * EXPORT), 3))

print("\n== nothing is counted twice")
check("solar only stored earns nothing yet",
      summarise([Bucket(pv=3.0, charged=3.0, import_rate=RATE)]).total_earnings, 0.0)
check("it is valued when the house uses it",
      round(summarise([Bucket(discharged=3.0, import_rate=RATE)]).home_value, 4), round(3 * RATE, 4))
spread = summarise([Bucket(imported=2.0, charged=2.0, import_rate=0.1529),
                    Bucket(discharged=2.0, import_rate=0.3566)])
check("charge cheap, use at peak: exactly the spread",
      round(spread.total_earnings, 4), round(2 * 0.3566 - 2 * 0.1529, 4))
trade = summarise([Bucket(imported=5.0, charged=5.0, import_rate=0.1529),
                   Bucket(discharged=4.5, exported=4.5, import_rate=0.3566, export_rate=0.30)])
check("charge cheap, sell dear: sale in exported", round(trade.export_value, 4), round(4.5 * 0.30, 4))
check("purchase in grid charging", round(trade.battery_cost, 4), round(5 * 0.1529, 4))
check("the gain, net of round-trip loss", round(trade.total_earnings, 4), round(4.5 * 0.30 - 5 * 0.1529, 4))
before = summarise([Bucket(imported=5.0, charged=5.0, import_rate=0.1529)])
after = summarise([Bucket(discharged=4.5, exported=4.5, export_rate=0.30)])
check("a trade split across two reports adds up to the whole",
      round(before.total_earnings + after.total_earnings, 4), round(trade.total_earnings, 4))
check("grid energy never earns on its own",
      summarise([Bucket(imported=2.0, import_rate=RATE)]).total_earnings, 0.0)

print("\n== noise inside a bucket is kept out of real flows")
churn = summarise([Bucket(charged=1.0, discharged=1.0, import_rate=RATE)])
check("charge and discharge in one bucket is churn", round(churn.battery_churn, 2), 1.0)
check("churn is not a grid charge", churn.battery_cost, 0.0)
check("churn is not an export", churn.export_value, 0.0)
hunt = summarise([Bucket(imported=0.02, exported=0.02, import_rate=RATE, export_rate=EXPORT)])
check("meter hunting is recorded as hunting", round(hunt.grid_hunting, 2), 0.02)
check("meter hunting is not a battery export", hunt.battery_to_grid, 0.0)
check("meter hunting earns nothing", hunt.export_value, 0.0)

print("\n== rates are per bucket")
priced = summarise([Bucket(pv=1.0, import_rate=0.2635), Bucket(pv=1.0, import_rate=0.3485),
                    Bucket(pv=1.0, import_rate=0.2635)])
check("each hour at its own rate", round(priced.home_value, 4), round(0.2635 + 0.3485 + 0.2635, 4))
check("beats one flat rate by exactly the session bonus", round(priced.home_value - 3 * 0.2635, 4), 0.085)
check("nothing estimated when every bucket is priced", priced.estimated, False)
mixed = summarise([Bucket(pv=1.0, import_rate=0.3485), Bucket(pv=1.0)], fallback_import_rate=0.2635)
check("an unpriced bucket uses the fallback", round(mixed.home_value, 4), round(0.3485 + 0.2635, 4))
check("partly estimated", (mixed.estimated, mixed.fully_estimated), (True, False))

print("\n== the fallback is never the live price if anything else exists")
# The bug behind a £3.08 report: unpriced buckets took the live rate at 19:00,
# which was peak plus a saving session.
check("the average attribute wins", round(choose_fallback(26.35, 30.0, [0.4503], 44.07, 100), 4), 0.2635)
check("then the configured fallback", round(choose_fallback(None, 25.47, [0.4503], 44.07, 100), 4), 0.2547)
check("a zero configured fallback is ignored", round(choose_fallback(None, 0, [0.2635, 0.3566], 44.07, 100), 4),
      round((0.2635 + 0.3566) / 2, 4))
check("then the mean of what was recorded", round(choose_fallback(None, None, [0.2635, None, 0.3566], 44.07, 100), 4),
      round((0.2635 + 0.3566) / 2, 4))
check("the live price only as a last resort", round(choose_fallback(None, None, [], 44.07, 100), 4), 0.4407)
check("nothing at all is zero, not an error", choose_fallback(None, None, [], "unavailable", 100), 0.0)
check("a NaN average is skipped", round(choose_fallback(float("nan"), 25.47, [], None, 100), 4), 0.2547)

print("\n== awkward input")
check("nothing at all", (summarise([]).house, summarise([]).covered), (0.0, 0.0))
check("a bucket that nets negative is not a negative house",
      summarise([Bucket(exported=0.01)]).house, 0.0)
check("a null change is zero", summarise([Bucket(pv=None, imported=2.0)]).pv, 0.0)
check("a NaN is treated as missing", summarise([Bucket(pv=float("nan"), imported=2.0)]).pv, 0.0)
odd = summarise([Bucket(pv=1.0, exported=3.0, discharged=2.0)])
check("export beyond the solar is battery", (round(odd.solar_to_grid, 2), round(odd.battery_to_grid, 2)), (1.0, 2.0))

print("\n== windows")
friday = datetime(2026, 9, 18, 19, 0)
check("daily is the previous 24 hours", [d.isoformat() for d in window_for("daily", friday)],
      ["2026-09-17T19:00:00", "2026-09-18T19:00:00"])
check("a late run snaps to the five-minute boundary",
      [d.isoformat() for d in window_for("daily", datetime(2026, 9, 18, 19, 3, 27))],
      ["2026-09-17T19:00:00", "2026-09-18T19:00:00"])
check("sunset at 19:47 reports up to 19:45",
      window_for("daily", datetime(2026, 9, 18, 19, 47))[1].isoformat(), "2026-09-18T19:45:00")
monday = datetime(2026, 9, 21, 8, 0)
check("weekly is the Monday-Sunday just finished",
      [d.date().isoformat() for d in window_for("weekly", monday)], ["2026-09-14", "2026-09-21"])
check("weekly from a Sunday still covers the same week",
      [d.date().isoformat() for d in window_for("weekly", datetime(2026, 9, 20, 8, 0))], ["2026-09-07", "2026-09-14"])
check("weekly is labelled Mon-Sun", span_label("weekly", *window_for("weekly", monday)), "14 Sep - 20 Sep")
check("monthly is the calendar month just finished",
      [d.date().isoformat() for d in window_for("monthly", datetime(2026, 10, 1, 8))], ["2026-09-01", "2026-10-01"])
check("March reports February, not a 30-day slice",
      [d.date().isoformat() for d in window_for("monthly", datetime(2027, 3, 1, 8))], ["2027-02-01", "2027-03-01"])
check("January reports the previous December",
      [d.date().isoformat() for d in window_for("monthly", datetime(2027, 1, 1, 8))], ["2026-12-01", "2027-01-01"])

print("\n== message")
text = plain(render(t, "daily", "Thu 17 Sep 19:00 - Fri 18 Sep 19:00", arbitrage=0.32,
                    peak=peak_text("daily", 3812.0, datetime(2026, 9, 18, 12, 40))))
check("header and span", text.splitlines()[:2], ["SOLAR SUMMARY - Last 24 hours", "Thu 17 Sep 19:00 - Fri 18 Sep 19:00"])
check("earnings line", "£2.03 home used + £0.38 exported - £0.16 grid charging = £2.25" in text, True)
check("solar section with the peak",
      "Generated 12.4 kWh, peak 3.8 kW at 12:40\n6.6 to the house, 4.0 into the battery, 1.8 exported" in text, True)
check("battery section",
      "Charged 4.6 kWh: 4.0 from solar, 0.6 from the grid costing £0.16\n"
      "Supplied 2.5 kWh: 1.1 to the house, 1.4 exported\n"
      "Net +2.1 kWh, stored for later" in text, True)
check("house section",
      "Used 8.1 kWh, 95% from solar and battery (7.7 kWh, worth £2.03)\n"
      "6.6 straight from solar, 1.1 from the battery, 0.4 bought costing £0.11" in text, True)
check("export section", "3.2 kWh exported, earning £0.38: 1.8 from solar, 1.4 from the battery" in text, True)
check("arbitrage is included, not added", "£0.32 gained from energy arbitrage, included in the total above" in text, True)
check("sections in order", [text.index(e) for e in "💷☀🔋🏠⚡📈"] == sorted(text.index(e) for e in "💷☀🔋🏠⚡📈"), True)
invariants("normal day", text)
adds_up("normal day", text)

check("weekly peak names the day", peak_text("weekly", 4100.0, datetime(2026, 9, 17, 12)), "4.1 kW on Thu 17 Sep")
check("no peak without a reading", peak_text("daily", None, None), None)
check("no peak on a zero-watt period", peak_text("daily", 0.0, datetime(2026, 9, 18, 12)), None)
check("no peak clause when there is none", "peak" in render(t, "daily", "x"), False)

# 24 Sep's battery: 4.64 kWh from solar and 0.13 from the grid is 4.77, which
# rounds to 4.8 - but the parts round to 4.6 and 0.1. The total is the parts' sum.
edge = plain(render(summarise([Bucket(pv=4.64, charged=4.77, imported=0.13, import_rate=RATE)]), "daily", "x"))
check("a total is the sum of its rounded parts", "Charged 4.7 kWh: 4.6 from solar, 0.1 from the grid" in edge, True)
adds_up("rounding edge", edge)

quiet = plain(render(summarise([Bucket(pv=5.0, charged=3.0, import_rate=RATE)]), "daily", "x"))
check("an all-solar charge says so", "Charged 3.0 kWh, all from solar" in quiet, True)
check("an idle battery says so", "Supplied nothing" in quiet, True)
check("no grid-charging term when there was none", "grid charging" in quiet, False)
check("stored energy is explained", "Net +3.0 kWh, stored for later" in quiet, True)
adds_up("idle battery", quiet)
drained = plain(render(summarise([Bucket(discharged=3.0, import_rate=RATE)]), "daily", "x"))
check("energy stored earlier is explained", "Net -3.0 kWh, ran on energy stored earlier" in drained, True)
adds_up("drained", drained)

no_battery = plain(render(summarise([Bucket(pv=4.0, imported=1.0, exported=1.5, import_rate=RATE, export_rate=EXPORT)]),
                          "daily", "x", has_battery=False))
check("no battery section without battery entities", "🔋" in no_battery, False)
check("coverage says solar alone", "from solar (" in no_battery, True)
adds_up("no battery", no_battery)
invariants("no battery", no_battery)

trade_text = plain(render(trade, "daily", "x"))
check("a trade shows the sale on the battery line", "Supplied 4.5 kWh: 0.0 to the house, 4.5 exported" in trade_text, True)
adds_up("trade", trade_text)
invariants("trade", trade_text)

check("no arbitrage section when not configured", "📈" in render(t, "daily", "x"), False)
check("a gated planner says why", "£0.00 - the controller is watching, not controlling" in
      render(t, "daily", "x", arbitrage=0.0, arbitrage_note="the controller is watching, not controlling"), True)
check("a loss is included too", "£0.41 lost to energy arbitrage, included" in render(t, "daily", "x", arbitrage=-0.41), True)
check("footnote when estimated", "Valued at an estimated rate" in render(summarise([Bucket(pv=1.0)]), "daily", "x"), True)
check("and partly when only some is", "Partly valued at an estimated rate" in render(mixed, "daily", "x"), True)
for period in ("daily", "weekly", "monthly"):
    msg = render(t, period, "x", arbitrage=0.32)
    invariants(period, msg)
    adds_up(period, msg)

print()
if failures:
    print(f"{failures} check(s) failed")
    sys.exit(1)
print("Energy Report checks passed")
