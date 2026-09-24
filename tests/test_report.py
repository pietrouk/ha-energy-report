#!/usr/bin/env python3
"""Exercise the arithmetic and the message, with no Home Assistant involved.

usage: python3 tests/test_report.py
"""
import importlib
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

render = importlib.import_module("energy_report.message").render
_report = importlib.import_module("energy_report.report")
Bucket, summarise = _report.Bucket, _report.summarise
_window = importlib.import_module("energy_report.window")
span_label, window_for = _window.span_label, _window.window_for

failures = 0


def check(label, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}" + ("" if ok else f" (expected {want!r})"))


RATE = 0.2635
EXPORT_RATE = 0.12

# A day that balances in every bucket: solar + import + discharge == house +
# export + charge. Unbalanced fixtures leave imported energy belonging to
# neither the house nor the battery and the invariants below stop meaning
# anything.
#
#   1  night      pv 0.0  house 1.5  dis 1.1  chg 0.0  imp 0.4  exp 0.0
#   2  morning    pv 6.0  house 2.0  dis 0.0  chg 4.6  imp 0.6  exp 0.0   <- grid charge
#   3  afternoon  pv 6.4  house 4.6  dis 1.4  chg 0.0  imp 0.0  exp 3.2
DAY = [
    Bucket(pv=0.0, house=1.5, discharged=1.1, imported=0.4, import_rate=RATE, export_rate=EXPORT_RATE),
    Bucket(pv=6.0, house=2.0, charged=4.6, imported=0.6, import_rate=RATE, export_rate=EXPORT_RATE),
    Bucket(pv=6.4, house=4.6, discharged=1.4, exported=3.2, import_rate=RATE, export_rate=EXPORT_RATE),
]

print("== energy split")
t = summarise(DAY)
check("solar adds up", round(t.pv, 2), 12.4)
check("house adds up", round(t.house, 2), 8.1)
check("the night shortfall is charged to the house", round(t.grid_to_house, 2), 0.4)
check("the morning grid charge is charged to the battery", round(t.grid_to_battery, 2), 0.6)
check("every imported kWh is attributed to one or the other",
      round(t.grid_to_house + t.grid_to_battery, 6), round(t.imported, 6))
check("home supply is house load less its own grid share", round(t.home_supplied, 2), 7.7)
check("coverage", round(t.covered), 95)
check("the old whole-period formula would have said 88%",
      round((t.house - t.imported) / t.house * 100), 88)

print("\n== money")
check("avoided imports", round(t.avoided, 2), 2.03)
check("the house paid only for its own share", round(t.house_cost, 2), 0.11)
check("the battery paid for its own", round(t.battery_cost, 2), 0.16)
check("export income", round(t.export_income, 2), 0.38)
check("battery banked what it did not pay for", round(t.battery_value, 2), 0.4)
check("total", round(t.total_earnings(0.32), 2), 3.13)
# The reattribution moves money between terms without creating any.
old = ((t.house - t.imported) * RATE + t.battery_net * RATE + t.export_income)
check("the grand total is what house + net - import always gave",
      round(t.total_earnings(), 6), round(old, 6))

print("\n== rates are per bucket, not one snapshot")
# Three identical hours off the battery, with a saving session lifting the
# middle one. 26.35 -> 34.85p is a real Octopus session: 68 octopoints at 8 per
# penny.
session = [
    Bucket(house=1.0, discharged=1.0, import_rate=0.2635),
    Bucket(house=1.0, discharged=1.0, import_rate=0.3485),
    Bucket(house=1.0, discharged=1.0, import_rate=0.2635),
]
s = summarise(session)
check("each hour is valued at its own rate", round(s.avoided, 4), round(0.2635 + 0.3485 + 0.2635, 4))
check("which beats one flat rate by exactly the session bonus",
      round(s.avoided - 3 * 0.2635, 4), 0.085)
check("nothing is estimated when every bucket is priced", s.estimated, False)

mixed = summarise([Bucket(house=1.0, discharged=1.0, import_rate=0.3485),
                   Bucket(house=1.0, discharged=1.0)], fallback_import_rate=0.2635)
check("an unpriced bucket falls back to the live rate", round(mixed.avoided, 4), round(0.3485 + 0.2635, 4))
check("and is reported as partly estimated", (mixed.estimated, mixed.fully_estimated), (True, False))
none = summarise([Bucket(house=1.0, discharged=1.0)], fallback_import_rate=0.2635)
check("no rates at all is fully estimated", (none.estimated, none.fully_estimated), (True, True))
check("export is priced per bucket too",
      round(summarise([Bucket(exported=2.0, export_rate=0.12),
                       Bucket(exported=2.0, export_rate=0.30)]).export_income, 4), 0.84)

print("\n== awkward input")
check("nothing at all", (summarise([]).house, summarise([]).covered), (0.0, 0.0))
check("zero house load does not divide by zero", summarise([Bucket()]).covered, 0.0)
check("import above house load cannot make home supply negative",
      summarise([Bucket(house=3.0, imported=9.0)]).home_supplied, 0.0)
check("grid to battery is capped by the charge that happened",
      round(summarise([Bucket(house=0.5, imported=9.0, charged=1.0)]).grid_to_battery, 2), 1.0)
check("with no charge, none of the import is billed to the battery",
      summarise([Bucket(house=0.5, imported=9.0)]).grid_to_battery, 0.0)
check("a null change is treated as zero", summarise([Bucket(pv=None, house=2.0)]).pv, 0.0)
check("a NaN is treated as missing, not propagated",
      summarise([Bucket(pv=float("nan"), house=2.0)]).pv, 0.0)

print("\n== windows")
friday = datetime(2026, 9, 18, 19, 0)
start, end = window_for("daily", friday)
check("daily covers the previous 24 hours", (start.day, start.hour, end.hour), (17, 19, 19))
monday = datetime(2026, 9, 21, 8, 0)
start, end = window_for("weekly", monday)
check("weekly is the Monday-Sunday week just finished",
      (start.date().isoformat(), end.date().isoformat()), ("2026-09-14", "2026-09-21"))
check("weekly from a Sunday still covers the same week",
      [d.date().isoformat() for d in window_for("weekly", datetime(2026, 9, 20, 8, 0))],
      ["2026-09-07", "2026-09-14"])
check("weekly is labelled Mon-Sun", span_label("weekly", *window_for("weekly", monday)), "14 Sep - 20 Sep")
start, end = window_for("monthly", datetime(2026, 10, 1, 8, 0))
check("monthly is the calendar month just finished",
      (start.date().isoformat(), end.date().isoformat()), ("2026-09-01", "2026-10-01"))
# A month-length-agnostic step back, not "minus 30 days".
check("March reports February, not a 30-day slice",
      [d.date().isoformat() for d in window_for("monthly", datetime(2027, 3, 1, 8, 0))],
      ["2027-02-01", "2027-03-01"])
check("January reports the previous December",
      [d.date().isoformat() for d in window_for("monthly", datetime(2027, 1, 1, 8, 0))],
      ["2026-12-01", "2027-01-01"])

print("\n== message")
text = render(t, "daily", "Thu 17 Sep 19:00 - Fri 18 Sep 19:00", arbitrage=0.32)
plain = re.sub(r"</?[bi]>", "", text)
check("header names the period", plain.startswith("SOLAR SUMMARY - Last 24 hours"), True)
check("span is the second line", plain.splitlines()[1], "Thu 17 Sep 19:00 - Fri 18 Sep 19:00")
check("earnings block is first", plain.splitlines()[3].startswith("💷"), True)
check("earnings line reads in order",
      "£2.03 home used + £0.40 battery + £0.38 exported + £0.32 arbitraged = £3.13" in plain, True)
check("money keeps two decimals", "earning £0.38" in plain, True)
check("kWh drops trailing zeros", "Charged 4.6 kWh" in plain, True)
check("the battery line names the grid share",
      "0.6 of it from the grid, costing £0.16" in plain, True)
check("and says the value is after paying for it",
      "worth +£0.40 once the grid charging is paid for" in plain, True)
check("the house line bills only its own share",
      "Bought 0.4 kWh straight from the grid, costing £0.11" in plain, True)
check("sections in order",
      [plain.index(e) for e in ("💷", "☀️", "🔋", "🏠", "📈")]
      == sorted(plain.index(e) for e in ("💷", "☀️", "🔋", "🏠", "📈")), True)

# Every money figure in the body must be a term in the earnings line, so a
# reader cannot add two figures that already contain one another. Costs are the
# exception - they are spent, not earned - and are always phrased "costing".
earnings = plain.split("\n\n")[1]
body = "\n".join(l for l in plain.split("💷")[1].split("\n\n", 1)[1].splitlines() if "costing" not in l)
check("no money in the body that is not a term above",
      [a for a in re.findall(r"£\d+\.\d\d", body) if a not in earnings], [])
# ...and the reverse, excluding the grand total which is the sum, not a term.
terms = earnings.split(" = ")[0]
check("every term is visible below",
      [a for a in re.findall(r"£\d+\.\d\d", terms) if a not in plain.split("☀️")[1]], [])

check("no arbitrage section when it is not configured", "📈" in render(t, "daily", "x"), False)
check("no battery section without battery entities",
      "🔋" in render(t, "daily", "x", has_battery=False), False)
drained = summarise([Bucket(pv=1.0, house=8.0, imported=1.0, discharged=6.0, charged=0.5, import_rate=RATE)])
check("a drained battery reads as a negative term",
      "- £1.45 battery" in render(drained, "daily", "x"), True)
check("footnote appears when estimated",
      "Valued at the rate showing now" in render(none, "daily", "x"), True)
check("and says partly when only some is",
      "Partly valued at the rate showing now" in render(mixed, "daily", "x"), True)

print()
if failures:
    print(f"{failures} check(s) failed")
    sys.exit(1)
print("Energy Report checks passed")
