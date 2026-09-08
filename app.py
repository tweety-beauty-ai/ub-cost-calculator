import json
import os
import requests
import streamlit as st

st.set_page_config(page_title="UB Cost Calculator", page_icon="🐱", layout="wide",
                   initial_sidebar_state="expanded")
st.title("UB Cost Calculator")
st.caption("UK · AU · CA — side by side")

# ─── CONFIG: Load / Save ──────────────────────────────────────────────────────

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULTS = {
    # Fallback rates only — with auto_rates on (the default) live ECB rates are
    # used instead. Refreshed 4 Sep 2026; a stale EUR/USD here was making the
    # calculator disagree with the Products Analyzer by ~0.5pp on US ROI.
    "eur_gbp": 0.859, "eur_aud": 1.613, "eur_usd": 1.162, "usd_cad": 1.380,
    "auto_rates": True,
    "dsf":     3.0,
    # Current per-unit costs (updated 11 Aug 2026: labour 2.35 on every market,
    # UK shipping 0.80, CA shipping 3.12). These literals matter — Streamlit
    # Cloud wipes config.json on every restart, so stale defaults silently
    # resurface and the app then disagrees with the Products Analyzer.
    "uk_ship": 0.80,  "uk_lab": 2.35,  "uk_fba": 3.09, "uk_ref": 15.0, "uk_vat": 20.0,
    "au_ship": 10.40, "au_lab": 2.35,  "au_fba": 7.30, "au_ref": 13.0, "au_gst": 10.0, "au_tar": 5.0,
    "ca_ship": 3.12,  "ca_lab": 2.35,  "ca_fba": 7.33, "ca_ref": 15.0,
    "us_ship": 3.34,  "us_lab": 2.35,  "us_fba": 5.43, "us_ref": 15.0, "us_tar": 10.0,
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            saved = json.load(f)
        return {**DEFAULTS, **saved}
    return DEFAULTS.copy()

def save_config():
    data = {k: st.session_state[k] for k in DEFAULTS}
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

def save_config_from_dict(updates):
    current = load_config()
    current.update(updates)
    with open(CONFIG_FILE, "w") as f:
        json.dump(current, f, indent=2)

@st.cache_data(ttl=3600)
def fetch_live_rates():
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?from=EUR&to=GBP,AUD,USD,CAD",
            timeout=5,
        )
        data = r.json()
        rates = data["rates"]
        return {
            "eur_gbp": round(rates["GBP"], 4),
            "eur_aud": round(rates["AUD"], 4),
            "eur_usd": round(rates["USD"], 4),
            "usd_cad": round(rates["CAD"] / rates["USD"], 4),
            "date": data.get("date", "unknown"),
        }
    except Exception:
        return None

# ─── LIVE PER-EAN COSTS (COGS Shipping Calculator sheet) ──────────────────────
# Same source and service account as the Products Analyzer, so entering an EAN
# here gives exactly the numbers that tool uses: real freight per unit instead
# of the flat market average, plus the actual customs clearance cost.

COST_SHEET_ID = "15-xKszQNrnbsfEf_zqMkjtac7SUuo8-hmD-J1SW4azs"
MARKET_ALIASES = {"USA": "US", "US": "US", "CA": "CA", "UK": "UK", "AU": "AU", "WM": "WM"}


def _sheet_creds():
    """Service-account JSON from Streamlit secrets, or a local file."""
    try:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:
        pass
    local = os.path.expanduser("~/.config/mto-analyzer-sa.json")
    if os.path.exists(local):
        with open(local) as f:
            return json.load(f)
    return None


@st.cache_data(ttl=21600, show_spinner=False)      # 6h; the sheet updates daily
def load_cost_tables():
    """({(ean,market): freight_eur}, {(ean,market): customs_eur}, source)."""
    creds_info = _sheet_creds()
    if not creds_info:
        return {}, {}, "not connected — sidebar values used"
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

        def grab(tab):
            rows = svc.spreadsheets().values().get(
                spreadsheetId=COST_SHEET_ID, range=f"{tab}!A2:D").execute().get("values", [])
            out = {}
            for r in rows:
                if len(r) < 4:
                    continue
                ean = "".join(ch for ch in str(r[0]) if ch.isdigit())
                market = MARKET_ALIASES.get(str(r[1]).strip().upper())
                try:
                    cost = float(str(r[3]).replace(",", "."))
                except ValueError:
                    continue
                if ean and market and cost >= 0:
                    out[(ean.zfill(13) if 8 < len(ean) < 13 else ean, market)] = cost
            return out

        freight, customs = grab("Output"), grab("Output_Customs")
        return freight, customs, f"live sheet ({len(freight)} freight / {len(customs)} customs rows)"
    except Exception as e:
        return {}, {}, f"unavailable ({type(e).__name__}) — sidebar values used"


def lookup_costs(ean, market):
    """(freight_eur|None, customs_eur, note) for one product+market."""
    freight, customs, _ = load_cost_tables()
    if not ean:
        return None, 0.0, ""
    key = (ean.zfill(13) if 8 < len(ean) < 13 else ean, market)
    f, c = freight.get(key), customs.get(key, 0.0)
    if f is None and not c:
        return None, 0.0, "not in the cost sheet — flat sidebar values used"
    bits = []
    if f is not None:
        bits.append(f"freight {f:.2f}")
    if c:
        bits.append(f"customs {c:.2f}")
    return f, c, "real " + " + ".join(bits) + " EUR/unit"


cfg = load_config()

# ─── SIDEBAR: Parameters ──────────────────────────────────────────────────────

with st.sidebar:
    st.header("Parameters")

    with st.expander("Exchange Rates", expanded=True):
        live = fetch_live_rates()
        auto_rates = st.checkbox(
            "Auto-use live ECB rates", value=cfg.get("auto_rates", True), key="auto_rates",
            help="On: every calculation uses the current ECB rate, so this app cannot "
                 "drift away from the Products Analyzer. Off: the manual values below are used.")
        if live:
            st.caption(f"Live ECB ({live['date']}): GBP {live['eur_gbp']} · AUD {live['eur_aud']} · "
                       f"USD {live['eur_usd']} · CAD/USD {live['usd_cad']}")
        else:
            st.caption("⚠️ Live rates unavailable — the manual values below are used.")
        eur_gbp = st.number_input("EUR → GBP", value=cfg["eur_gbp"], step=0.001, format="%.4f", key="eur_gbp")
        eur_aud = st.number_input("EUR → AUD", value=cfg["eur_aud"], step=0.001, format="%.4f", key="eur_aud")
        eur_usd = st.number_input("EUR → USD", value=cfg["eur_usd"], step=0.001, format="%.4f", key="eur_usd")
        usd_cad = st.number_input("USD → CAD", value=cfg["usd_cad"], step=0.001, format="%.4f", key="usd_cad")

        # Live rates win when auto is on — the manual boxes above stay visible so
        # you can see (and, with auto off, set) what would be used instead.
        if auto_rates and live:
            eur_gbp, eur_aud, eur_usd, usd_cad = (live["eur_gbp"], live["eur_aud"],
                                                  live["eur_usd"], live["usd_cad"])
            rates_source = f"live ECB {live['date']}"
        else:
            rates_source = "manual values"
        st.caption(f"**In use: {rates_source}**")
    dsf_rate = st.number_input("Digital Svc Fee (%)", value=cfg["dsf"], step=0.5, format="%.1f", key="dsf") / 100

    st.markdown("---")

    with st.expander("🇬🇧  UK Parameters", expanded=True):
        uk_shipping = st.number_input("Shipping / unit (EUR)", value=cfg["uk_ship"], step=0.10, format="%.2f", key="uk_ship")
        uk_labor    = st.number_input("Labor / unit (EUR)",    value=cfg["uk_lab"],  step=0.10, format="%.2f", key="uk_lab")
        fba_gbp     = st.number_input("FBA fee (GBP)",         value=cfg["uk_fba"],  step=0.01, format="%.2f", key="uk_fba")
        ref_uk      = st.number_input("Referral fee (%)",      value=cfg["uk_ref"],  step=0.5,  format="%.1f", key="uk_ref") / 100
        uk_vat      = st.number_input("VAT rate (%)",          value=cfg["uk_vat"],  step=0.5,  format="%.1f", key="uk_vat") / 100
        st.caption("Referral applied to sell ex-VAT. VAT not in COGS.")

    with st.expander("🇦🇺  AU Parameters", expanded=True):
        au_shipping = st.number_input("Shipping / unit (EUR)", value=cfg["au_ship"], step=0.10, format="%.2f", key="au_ship")
        au_labor    = st.number_input("Labor / unit (EUR)",    value=cfg["au_lab"],  step=0.10, format="%.2f", key="au_lab")
        fba_aud     = st.number_input("FBA fee (AUD)",         value=cfg["au_fba"],  step=0.01, format="%.2f", key="au_fba")
        ref_au      = st.number_input("Referral fee (%)",      value=cfg["au_ref"],  step=0.5,  format="%.1f", key="au_ref") / 100
        au_gst      = st.number_input("GST rate (%)",          value=cfg["au_gst"],  step=0.5,  format="%.1f", key="au_gst") / 100
        au_tariff   = st.number_input("Import tariff (%)",     value=cfg["au_tar"],  step=0.5,  format="%.1f", key="au_tar") / 100
        st.caption("Import tariff in COGS. Import GST not in COGS (reclaimable). Referral applied to sell ex-GST.")

    with st.expander("🇨🇦  CA Parameters", expanded=True):
        ca_shipping = st.number_input("Shipping / unit (EUR)", value=cfg["ca_ship"], step=0.10, format="%.2f", key="ca_ship")
        ca_labor    = st.number_input("Labor / unit (EUR)",    value=cfg["ca_lab"],  step=0.10, format="%.2f", key="ca_lab")
        fba_cad     = st.number_input("FBA fee (CAD)",         value=cfg["ca_fba"],  step=0.01, format="%.2f", key="ca_fba")
        ref_ca      = st.number_input("Referral fee (%)",      value=cfg["ca_ref"],  step=0.5,  format="%.1f", key="ca_ref") / 100
        st.caption("All results in USD. Sell price (CAD) and FBA (CAD) converted to USD internally.")

    with st.expander("🇺🇸  US Parameters", expanded=True):
        us_shipping = st.number_input("Shipping / unit (EUR)", value=cfg["us_ship"], step=0.10, format="%.2f", key="us_ship")
        us_labor    = st.number_input("Labor / unit (EUR)",    value=cfg["us_lab"],  step=0.10, format="%.2f", key="us_lab")
        fba_us      = st.number_input("FBA fee (USD)",         value=cfg["us_fba"],  step=0.01, format="%.2f", key="us_fba")
        ref_us      = st.number_input("Referral fee (%)",      value=cfg["us_ref"],  step=0.5,  format="%.1f", key="us_ref") / 100
        us_tariff   = st.number_input("Import tariff (%)",     value=cfg["us_tar"],  step=0.5,  format="%.1f", key="us_tar") / 100
        st.caption("Import tariff in COGS (on product + shipping). No sales tax stripped — "
                   "Amazon collects and remits it. Digital svc fee on the referral fee ONLY, "
                   "which is what Seller Snap shows for US.")

    st.markdown("---")
    if st.button("💾 Save Parameters", use_container_width=True):
        save_config()
        st.success("Saved! Will load automatically next time.")

# ─── PRODUCT INPUT ────────────────────────────────────────────────────────────

st.subheader("COGS and ROI per market")

_f, _c, _cost_src = load_cost_tables()
ean_in = st.text_input(
    "EAN (optional)", value="", max_chars=14,
    help="Enter the EAN to use this product's real per-unit freight and customs "
         "from the COGS Shipping Calculator sheet instead of the flat sidebar "
         "values — the same figures the Products Analyzer uses.")
ean_in = "".join(ch for ch in ean_in if ch.isdigit())
st.caption(f"Cost sheet: {_cost_src}")

c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1, 1])

with c1:
    purchase_eur = st.number_input(
        "Purchase price (EUR, excl VAT)",
        min_value=0.0, value=0.00, step=0.50, format="%.2f"
    )
with c2:
    sell_gbp = st.number_input(
        "Sell UK (GBP, inc VAT)",
        min_value=0.0, value=0.0, step=1.0, format="%.2f",
        help="Amazon UK listing price — VAT stripped using rate set in parameters"
    )
with c3:
    sell_aud = st.number_input(
        "Sell AU (AUD, inc GST)",
        min_value=0.0, value=0.0, step=1.0, format="%.2f",
        help="Amazon AU listing price — GST stripped using rate set in parameters"
    )
with c4:
    sell_cad = st.number_input(
        "Sell CA (CAD, excl GST)",
        min_value=0.0, value=0.0, step=1.0, format="%.2f",
        help="CAD listing price — converted to USD internally for all calculations"
    )
with c5:
    sell_usd_in = st.number_input(
        "Sell US (USD)",
        min_value=0.0, value=0.0, step=1.0, format="%.2f",
        help="Amazon US listing price — already tax-exclusive, nothing is stripped"
    )

# ─── CALCULATION FUNCTIONS ────────────────────────────────────────────────────

def calc_uk(p_eur, s_gbp):
    ship_real, customs_eur, cost_note = lookup_costs(ean_in, "UK")
    uk_shipping_eff = ship_real if ship_real is not None else uk_shipping
    p    = p_eur * eur_gbp
    sl   = (uk_shipping_eff + uk_labor + customs_eur) * eur_gbp
    cogs = p + sl
    s    = s_gbp / (1 + uk_vat)
    ref  = s * ref_uk
    dsf  = (ref + fba_gbp) * dsf_rate
    # The digital services fee is charged by our country of establishment
    # (Spain = 3%), so it applies on UK exactly as on AU/CA and IS deducted from
    # profit. The original UK spreadsheet showed it without deducting it, which
    # overstated UK ROI by ~1pp.
    fees = ref + fba_gbp + dsf
    ppu  = s - cogs - fees
    roi  = ppu / cogs if cogs > 0 else 0
    return dict(cur="GBP", purchase=p, ship_labor=sl, tariff_gst=None,
                cogs=cogs, sell_ex=s, ref=ref, fba=fba_gbp, dsf=dsf,
                fees=fees, ppu=ppu, roi=roi,
                tax_note=(f"VAT {uk_vat:.0%} stripped from sell price"
                          + (f" · {cost_note}" if cost_note else "")))

def calc_au(p_eur, s_aud):
    ship_real, customs_eur, cost_note = lookup_costs(ean_in, "AU")
    au_shipping_eff = ship_real if ship_real is not None else au_shipping
    p        = p_eur * eur_aud
    ship_aud = (au_shipping_eff + customs_eur) * eur_aud
    lab_aud  = au_labor * eur_aud
    tariff   = (p + ship_aud) * au_tariff
    # Import GST is NOT in COGS — it is reclaimable as input tax credit
    cogs     = p + ship_aud + lab_aud + tariff
    s        = s_aud / (1 + au_gst)
    ref      = s * ref_au
    dsf      = (ref + fba_aud) * dsf_rate
    fees     = ref + fba_aud + dsf
    ppu      = s - cogs - fees
    roi      = ppu / cogs if cogs > 0 else 0
    return dict(cur="AUD", purchase=p, ship_labor=ship_aud + lab_aud,
                tariff_gst=tariff,
                cogs=cogs, sell_ex=s, ref=ref, fba=fba_aud, dsf=dsf,
                fees=fees, ppu=ppu, roi=roi,
                tax_note=(f"Import tariff {au_tariff:.0%} in COGS; GST {au_gst:.0%} "
                          f"stripped from sell price only"
                          + (f" · {cost_note}" if cost_note else "")))

def calc_ca(p_eur, s_cad):
    ship_real, customs_eur, cost_note = lookup_costs(ean_in, "CA")
    ca_shipping_eff = ship_real if ship_real is not None else ca_shipping
    # Everything in USD
    cad_usd  = 1 / usd_cad
    p_usd    = p_eur * eur_usd
    ship_usd = (ca_shipping_eff + customs_eur) * eur_usd
    lab_usd  = ca_labor * eur_usd
    cogs     = p_usd + ship_usd + lab_usd
    sell_usd = s_cad * cad_usd
    fba_usd  = fba_cad * cad_usd
    ref      = sell_usd * ref_ca
    dsf      = (ref + fba_usd) * dsf_rate
    fees     = ref + fba_usd + dsf
    ppu      = sell_usd - cogs - fees
    roi      = ppu / cogs if cogs > 0 else 0
    return dict(cur="USD", purchase=p_usd, ship_labor=ship_usd + lab_usd, tariff_gst=None,
                cogs=cogs, cogs_cad=cogs * usd_cad, sell_ex=sell_usd, ref=ref, fba=fba_usd, dsf=dsf,
                fees=fees, ppu=ppu, roi=roi,
                tax_note=(f"Sell price {s_cad:.2f} CAD → {sell_usd:.2f} USD. All values in USD."
                          + (f" · {cost_note}" if cost_note else "")))

def calc_us(p_eur, s_usd):
    ship_real, customs_eur, cost_note = lookup_costs(ean_in, "US")
    us_shipping_eff = ship_real if ship_real is not None else us_shipping
    # Same maths as the Products Analyzer's calc_us, so the two tools agree:
    # COGS = (goods + shipping) x (1 + tariff) + labour, all converted at EUR/USD.
    p_usd    = p_eur * eur_usd
    ship_usd = us_shipping_eff * eur_usd
    lab_usd  = (us_labor + customs_eur) * eur_usd     # customs clearance is not dutiable
    tariff   = (p_usd + ship_usd) * us_tariff
    cogs     = p_usd + ship_usd + lab_usd + tariff
    # US prices are tax-exclusive (Amazon collects and remits sales tax), so
    # unlike UK/AU nothing is stripped from the sell price.
    ref      = s_usd * ref_us
    # US digital services fee applies to the referral fee alone — verified
    # against Seller Snap's Costs tab (3.001% of referral across 116 rows).
    dsf      = ref * dsf_rate
    fees     = ref + fba_us + dsf
    ppu      = s_usd - cogs - fees
    roi      = ppu / cogs if cogs > 0 else 0
    return dict(cur="USD", purchase=p_usd, ship_labor=ship_usd + lab_usd,
                tariff_gst=tariff,
                cogs=cogs, sell_ex=s_usd, ref=ref, fba=fba_us, dsf=dsf,
                fees=fees, ppu=ppu, roi=roi,
                tax_note=(f"Import tariff {us_tariff:.0%} in COGS. No sales tax stripped "
                          f"(Amazon remits it). DSF on referral only."
                          + (f" · {cost_note}" if cost_note else "")))

uk = calc_uk(purchase_eur, sell_gbp)
au = calc_au(purchase_eur, sell_aud)
ca = calc_ca(purchase_eur, sell_cad)
us = calc_us(purchase_eur, sell_usd_in)

# ─── RESULTS ──────────────────────────────────────────────────────────────────

st.divider()
st.subheader("Results")
st.caption(f"Rates in use: {rates_source}")

def roi_icon(roi):
    if roi >= 0.20: return "🟢"
    if roi >= 0.10: return "🟡"
    if roi >  0:    return "🟠"
    return "🔴"

def render_market(title, d, has_sell):
    st.markdown(f"**{title}**")
    if not has_sell:
        st.caption("Enter a sell price above")
        return
    c    = d["cur"]
    roi  = d["roi"]
    icon = roi_icon(roi)

    st.metric("ROI", f"{roi:.1%}")
    m1, m2 = st.columns(2)
    m1.metric(f"Profit ({c})", f"{d['ppu']:.2f}")
    m2.metric(f"COGS ({c})",   f"{d['cogs']:.2f}")
    st.caption(d["tax_note"])

    with st.expander("Full breakdown"):
        lines = [
            (f"Purchase ({c})",         d["purchase"]),
            (f"Shipping + Labor ({c})", d["ship_labor"]),
        ]
        if d["tariff_gst"] is not None:
            lines.append((f"Import tariff ({c})", d["tariff_gst"]))
        lines += [
            (f"**COGS ({c})**",         d["cogs"]),
        ]
        if d.get("cogs_cad") is not None:
            lines.append(("COGS (CAD)", d["cogs_cad"]))
        lines += [
            ("---", None),
            (f"Sell ex-tax ({c})",      d["sell_ex"]),
            (f"Referral fee ({c})",     d["ref"]),
            (f"FBA fee ({c})",          d["fba"]),
            (f"Digital svc fee ({c})",  d["dsf"]),
            (f"**Total fees ({c})**",   d["fees"]),
            ("---", None),
            (f"**Profit / PPU ({c})**", d["ppu"]),
            ("**ROI**",                 roi),
        ]
        for label, val in lines:
            if val is None:
                st.markdown("---")
            elif label == "**ROI**":
                st.markdown(f"**ROI: {val:.1%}** {icon}")
            elif label.startswith("**"):
                st.markdown(f"{label}: **{val:.2f}**")
            else:
                st.write(f"{label}: {val:.2f}")

col1, col2, col3, col4 = st.columns(4)
with col1:
    render_market("🇬🇧  United Kingdom", uk, sell_gbp > 0)
with col2:
    render_market("🇦🇺  Australia",      au, sell_aud > 0)
with col3:
    render_market("🇨🇦  Canada",         ca, sell_cad > 0)
with col4:
    render_market("🇺🇸  United States",  us, sell_usd_in > 0)
