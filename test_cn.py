"""Headless acceptance tests for the China (Tmall) section. Run: python3 test_cn.py"""
import sys
from streamlit.testing.v1 import AppTest

def run(app, **kw):
    at = AppTest.from_file(app, default_timeout=60).run()
    at.checkbox(key="auto_rates").uncheck()
    for k, v in kw.items():
        w = {"cn_type": at.radio, "cn_ship": at.selectbox}.get(k, at.number_input)
        w(key=k).set_value(v)
        at.run()
    assert not at.exception, at.exception
    return at

def metrics(at):
    return {m.label: m.value for m in at.metric}

def text(at):
    return " ".join(str(x.value) for x in list(at.markdown) + list(at.text) + list(at.caption))

ok = True
def check(name, got, want):
    global ok
    good = got == want
    ok &= good
    print(f"{'PASS' if good else 'FAIL'} {name}: got {got} want {want}")

# Test 1 — DG, €75 net, ¥1,089, 100 ml, Large, rate 7.80
at = run("app.py", eur_cny=7.80, cn_purchase=75.0, cn_sell=1089.0, cn_size=100.0)
m, t = metrics(at), text(at)
check("T1 ROI", m["ROI"], "53.54%")
check("T1 COGS", m["COGS (EUR)"], "84.31")
check("T1 PPU", m["Profit / PPU (EUR)"], "45.14")
check("T1 cost/unit", m["Cost / unit (EUR)"], "94.47")
check("T1 margin", "Margin **32.33%**" in t, True)
check("T1 tax+buyer", "tax **23.06%** · buyer pays **¥1,340.12**" in t, True)
check("T1 fees", "**Platform fees** (EUR): **10.16**" in t, True)

# Test 2 — NDG, €20 net, ¥350, 30 g, Light make-up
at = run("app.py", eur_cny=7.80, cn_type="NDG", cn_ship="Light make-up · ~350 un (NDG only)",
         cn_purchase=20.0, cn_sell=350.0, cn_size=30.0)
m, t = metrics(at), text(at)
check("T2 ROI", m["ROI"], "51.80%")
check("T2 COGS", m["COGS (EUR)"], "25.40")
check("T2 PPU", m["Profit / PPU (EUR)"], "13.16")
check("T2 buyer", "buyer pays **¥430.71**" in t, True)
check("T2 fees", "**Platform fees** (EUR): **6.31**" in t, True)

# Round-trip: buying at the max buy price gives the target ROI (17%)
at = run("app.py", eur_cny=7.80, cn_sell=1089.0, cn_size=100.0)
mbp = float(metrics(at)["Max buy price @ 17% ROI (EUR excl VAT)"])
at = run("app.py", eur_cny=7.80, cn_purchase=mbp, cn_sell=1089.0, cn_size=100.0)
roi = float(metrics(at)["ROI"].rstrip("%"))
check(f"MBP round-trip (MBP {mbp})", 17.0 <= roi < 17.02, True)

# Linked price fields — real Tmall orders of 31 Oct 2026 and the Double 11 Givenchy price
num = lambda at, k: round(at.number_input(key=k).value, 2)
at = run("app.py", cn_sell=544.0, cn_size=100.0)            # YSL: ¥544 + ¥49.50 tax
check("YSL net→gross", num(at, "cn_sell_gross"), 593.50)
at = run("app.py", cn_sell_gross=841.0, cn_size=100.0)      # Valentino: 价格已含税
check("Valentino gross→net", num(at, "cn_sell"), 770.85)
at = run("app.py", cn_size=80.0, cn_sell_gross=609.0)       # Givenchy 80 ml, size first
check("Givenchy gross→net", num(at, "cn_sell"), 558.20)
at = run("app.py", cn_sell_gross=609.0)                     # no size → 9.1% assumed
check("No size gross→net", num(at, "cn_sell"), 558.20)
at = run("app.py", cn_sell_gross=1500.0, cn_size=100.0)     # 23.06% bracket
check("High bracket gross→net", num(at, "cn_sell"), 1218.92)
at = run("app.py", cn_sell_gross=1150.0, cn_size=100.0)     # ¥11.50/ml incl. tax: dead zone
check("Dead zone uses 23.06%", num(at, "cn_sell"), 934.50)
check("Dead zone warning", any("tax jump" in w.value for w in at.warning), True)
a = metrics(run("app.py", eur_cny=7.80, cn_purchase=50.0, cn_sell=558.2, cn_size=80.0))["ROI"]
b = metrics(run("app.py", eur_cny=7.80, cn_purchase=50.0, cn_sell_gross=609.0, cn_size=80.0))["ROI"]
check(f"Same ROI from either field ({a})", a, b)

print("ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
