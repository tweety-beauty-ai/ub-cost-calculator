import json
import math
import os
import re
import requests
import streamlit as st

st.set_page_config(page_title="UB Cost Calculator", page_icon="🐱", layout="wide",
                   initial_sidebar_state="expanded")
st.title("UB Cost Calculator")
st.caption("UK · AU · CA · US — side by side")

# ─── CONFIG: Load / Save ──────────────────────────────────────────────────────

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

DEFAULTS = {
    # Fallback rates only — with auto_rates on (the default) live ECB rates are
    # used instead. Refreshed 12 Oct 2026 (ECB 9 Oct); a stale EUR/USD here was
    # making the calculator disagree with the Products Analyzer by ~0.5pp on US ROI.
    "eur_gbp": 0.8476, "eur_aud": 1.6052, "eur_usd": 1.1206, "usd_cad": 1.4226,
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

RATES_URLS = (
    # frankfurter.app now 301-redirects to frankfurter.dev — call the new host
    # first and keep the old one as a second try.
    "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=GBP,AUD,USD,CAD",
    "https://api.frankfurter.app/latest?from=EUR&to=GBP,AUD,USD,CAD",
)


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_live_rates_cached():
    # Raises on failure ON PURPOSE: st.cache_data does not cache exceptions, so
    # a failed fetch is retried on the next rerun. It used to return None, which
    # WAS cached — one bad fetch after the app woke up pinned the fallback
    # rates for a full hour, and each rerun in that hour re-cached the failure.
    errors = []
    for url in RATES_URLS:
        for _ in range(2):
            try:
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                data = r.json()
                rates = data["rates"]
                return {
                    "eur_gbp": round(rates["GBP"], 4),
                    "eur_aud": round(rates["AUD"], 4),
                    "eur_usd": round(rates["USD"], 4),
                    "usd_cad": round(rates["CAD"] / rates["USD"], 4),
                    "date": data.get("date", "unknown"),
                }
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {type(e).__name__}")
    raise RuntimeError("; ".join(errors))


def fetch_live_rates():
    """(rates|None, error_text). Failures are never cached."""
    try:
        return _fetch_live_rates_cached(), ""
    except Exception as e:
        return None, str(e)

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
def _load_cost_tables_cached():
    # Raises on failure (not cached) so one bad read can't stick for 6 hours.
    creds_info = _sheet_creds()
    if not creds_info:
        raise LookupError("not connected")
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


def load_cost_tables():
    """({(ean,market): freight_eur}, {(ean,market): customs_eur}, source)."""
    try:
        return _load_cost_tables_cached()
    except LookupError:
        return {}, {}, "not connected — sidebar values used"
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


# ─── PER-EAN FBA FEE (SellerSnap sheet → Keepa → flat sidebar) ────────────────
# A flat FBA fee per market is wrong for most products: SellerSnap's real
# per-SKU fee runs e.g. US $5.20–7.22, CA 5.29–9.72 CAD, UK £1.86–3.20,
# AU 6.78–9.81 AUD (Masha, Oct 2026). With an EAN we use the product's own fee.
#
# The "SellerSnap FBA fees" sheet has one tab per market (US/CA/UK/AU) holding a
# raw SellerSnap listings export pasted as-is. Columns are found by header
# (sku, fulfillment_cost) and the EAN is the digit run at the end of the SKU
# (BW1_8410190631182, US_FBA_NEW_BW_8411061725863). To refresh: paste a new
# export over the tab.

FBA_SHEET_ID = "1hiIkVYdcHzm_M9QUR_asdK_0vNoatrneAYEQU4KCiBs"
FBA_MARKETS = ("US", "CA", "UK", "AU")
KEEPA_DOMAINS = {"US": 1, "UK": 2, "CA": 6}        # Keepa has no AU marketplace


def _norm_ean(ean):
    return ean.zfill(13) if 8 < len(ean) < 13 else ean


def _parse_ss_export(rows, market, out):
    """Add {(ean, market): fee} from one pasted SellerSnap export; returns row count."""
    if not rows:
        return 0
    head = [str(h).strip().lower() for h in rows[0]]
    if "sku" not in head or "fulfillment_cost" not in head:
        return -1
    i_sku, i_fee = head.index("sku"), head.index("fulfillment_cost")
    n = 0
    for r in rows[1:]:
        if len(r) <= max(i_sku, i_fee):
            continue
        m = re.search(r"(\d{8,14})$", str(r[i_sku]).strip())
        try:
            fee = float(str(r[i_fee]).replace(",", "."))
        except ValueError:
            continue
        if not m or fee <= 0:
            continue
        key = (_norm_ean(m.group(1)), market)
        # Several SKUs can share an EAN; keep the higher fee (conservative).
        out[key] = max(fee, out.get(key, 0.0))
        n += 1
    return n


@st.cache_data(ttl=21600, show_spinner=False)
def _load_fba_table_cached():
    # Raises on failure so a failed read is retried next rerun, not cached 6h.
    creds_info = _sheet_creds()
    if not creds_info:
        raise RuntimeError("not connected")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    resp = svc.spreadsheets().values().batchGet(
        spreadsheetId=FBA_SHEET_ID,
        ranges=[f"{m}!A1:CZ" for m in FBA_MARKETS]).execute()
    out, counts = {}, []
    for market, vr in zip(FBA_MARKETS, resp.get("valueRanges", [])):
        n = _parse_ss_export(vr.get("values", []), market, out)
        counts.append(f"{market} {n if n >= 0 else '? (no sku/fulfillment_cost header)'}")
    return out, "SellerSnap sheet (" + " · ".join(counts) + ")"


def load_fba_table():
    """({(ean, market): fee in marketplace currency}, status text)."""
    try:
        return _load_fba_table_cached()
    except Exception as e:
        return {}, f"SellerSnap sheet unavailable ({type(e).__name__})"


def _keepa_key():
    try:
        if st.secrets.get("keepa_key"):
            return st.secrets["keepa_key"]
    except Exception:
        pass
    return os.environ.get("KEEPA_KEY", "")


@st.cache_data(ttl=21600, show_spinner=False)
def _keepa_fba_cached(ean, domain, key):
    # Raises on network/API errors so those are retried, not cached; a clean
    # "Keepa has no fee for this product" (None) IS cached for 6h.
    r = requests.get("https://api.keepa.com/product",
                     params={"key": key, "domain": domain, "code": ean, "history": 0},
                     timeout=30)
    r.raise_for_status()
    for p in r.json().get("products") or []:
        fee = (p.get("fbaFees") or {}).get("pickAndPackFee")
        if fee and fee > 0:
            return fee / 100
    return None


def lookup_fba(ean, market):
    """(fee in marketplace currency | None, source text)."""
    if not ean:
        return None, ""
    table, _ = load_fba_table()
    fee = table.get((_norm_ean(ean), market))
    if fee is not None:
        return fee, "SellerSnap"
    domain, key = KEEPA_DOMAINS.get(market), _keepa_key()
    if domain and key:
        try:
            fee = _keepa_fba_cached(_norm_ean(ean), domain, key)
        except Exception as e:
            return None, f"Keepa error {type(e).__name__}"
        if fee is not None:
            return fee, "Keepa"
    return None, "flat default"


cfg = load_config()

# ─── SIDEBAR: Parameters ──────────────────────────────────────────────────────

with st.sidebar:
    st.header("Parameters")

    with st.expander("Exchange Rates", expanded=True):
        live, rates_err = fetch_live_rates()
        auto_rates = st.checkbox(
            "Auto-use live ECB rates", value=cfg.get("auto_rates", True), key="auto_rates",
            help="On: every calculation uses the current ECB rate, so this app cannot "
                 "drift away from the Products Analyzer. Off: the manual values below are used.")
        if live:
            st.caption(f"Live ECB ({live['date']}): GBP {live['eur_gbp']} · AUD {live['eur_aud']} · "
                       f"USD {live['eur_usd']} · CAD/USD {live['usd_cad']}")
        else:
            st.caption(f"⚠️ Live rates unavailable ({rates_err}) — the manual values below are used.")
            if st.button("Retry live rates"):
                _fetch_live_rates_cached.clear()
                st.rerun()
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
         "(COGS Shipping Calculator sheet) and its real FBA fee (SellerSnap export, "
         "else Keepa for US/UK/CA) instead of the flat sidebar values.")
ean_in = "".join(ch for ch in ean_in if ch.isdigit())
_fba_tab, _fba_src = load_fba_table()
st.caption(f"Cost sheet: {_cost_src} · FBA fees: {_fba_src}"
           f" · Keepa fallback: {'on' if _keepa_key() else 'off (no keepa_key secret)'}")

c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1, 1])

with c1:
    purchase_eur = st.number_input(
        "Purchase price (EUR, excl VAT)",
        min_value=0.0, value=0.00, step=0.50, format="%.2f",
        help="Leave at 0 to see only the max buy price per market"
    )
    target_roi = st.number_input(
        "Target ROI (%)", min_value=-50.0, value=17.0, step=1.0, format="%.1f",
        help="Used for the max buy price: the highest purchase price (EUR, excl VAT) "
             "that still gives this ROI at the sell price entered"
    ) / 100
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
    fba_real, fba_src = lookup_fba(ean_in, "UK")
    fba_gbp_eff = fba_real if fba_real is not None else fba_gbp
    uk_shipping_eff = ship_real if ship_real is not None else uk_shipping
    p    = p_eur * eur_gbp
    sl   = (uk_shipping_eff + uk_labor + customs_eur) * eur_gbp
    cogs = p + sl
    s    = s_gbp / (1 + uk_vat)
    ref  = s * ref_uk
    dsf  = (ref + fba_gbp_eff) * dsf_rate
    # The digital services fee is charged by our country of establishment
    # (Spain = 3%), so it applies on UK exactly as on AU/CA and IS deducted from
    # profit. The original UK spreadsheet showed it without deducting it, which
    # overstated UK ROI by ~1pp.
    fees = ref + fba_gbp_eff + dsf
    ppu  = s - cogs - fees
    roi  = ppu / cogs if cogs > 0 else 0
    return dict(cur="GBP", purchase=p, ship_labor=sl, tariff_gst=None,
                cogs=cogs, sell_ex=s, ref=ref, fba=fba_gbp_eff, dsf=dsf,
                fees=fees, ppu=ppu, roi=roi,
                tax_note=(f"VAT {uk_vat:.0%} stripped from sell price"
                          + (f" · {cost_note}" if cost_note else "")
                          + (f" · FBA {fba_gbp_eff:.2f} GBP ({fba_src})" if fba_src else "")))

def calc_au(p_eur, s_aud):
    ship_real, customs_eur, cost_note = lookup_costs(ean_in, "AU")
    fba_real, fba_src = lookup_fba(ean_in, "AU")
    fba_aud_eff = fba_real if fba_real is not None else fba_aud
    au_shipping_eff = ship_real if ship_real is not None else au_shipping
    p        = p_eur * eur_aud
    ship_aud = (au_shipping_eff + customs_eur) * eur_aud
    lab_aud  = au_labor * eur_aud
    tariff   = (p + ship_aud) * au_tariff
    # Import GST is NOT in COGS — it is reclaimable as input tax credit
    cogs     = p + ship_aud + lab_aud + tariff
    s        = s_aud / (1 + au_gst)
    ref      = s * ref_au
    dsf      = (ref + fba_aud_eff) * dsf_rate
    fees     = ref + fba_aud_eff + dsf
    ppu      = s - cogs - fees
    roi      = ppu / cogs if cogs > 0 else 0
    return dict(cur="AUD", purchase=p, ship_labor=ship_aud + lab_aud,
                tariff_gst=tariff,
                cogs=cogs, sell_ex=s, ref=ref, fba=fba_aud_eff, dsf=dsf,
                fees=fees, ppu=ppu, roi=roi,
                tax_note=(f"Import tariff {au_tariff:.0%} in COGS; GST {au_gst:.0%} "
                          f"stripped from sell price only"
                          + (f" · {cost_note}" if cost_note else "")
                          + (f" · FBA {fba_aud_eff:.2f} AUD ({fba_src})" if fba_src else "")))

def calc_ca(p_eur, s_cad):
    ship_real, customs_eur, cost_note = lookup_costs(ean_in, "CA")
    fba_real, fba_src = lookup_fba(ean_in, "CA")
    fba_cad_eff = fba_real if fba_real is not None else fba_cad
    ca_shipping_eff = ship_real if ship_real is not None else ca_shipping
    # Everything in USD
    cad_usd  = 1 / usd_cad
    p_usd    = p_eur * eur_usd
    ship_usd = (ca_shipping_eff + customs_eur) * eur_usd
    lab_usd  = ca_labor * eur_usd
    cogs     = p_usd + ship_usd + lab_usd
    sell_usd = s_cad * cad_usd
    fba_usd  = fba_cad_eff * cad_usd
    ref      = sell_usd * ref_ca
    dsf      = (ref + fba_usd) * dsf_rate
    fees     = ref + fba_usd + dsf
    ppu      = sell_usd - cogs - fees
    roi      = ppu / cogs if cogs > 0 else 0
    return dict(cur="USD", purchase=p_usd, ship_labor=ship_usd + lab_usd, tariff_gst=None,
                cogs=cogs, cogs_cad=cogs * usd_cad, sell_ex=sell_usd, ref=ref, fba=fba_usd, dsf=dsf,
                fees=fees, ppu=ppu, roi=roi,
                tax_note=(f"Sell price {s_cad:.2f} CAD → {sell_usd:.2f} USD. All values in USD."
                          + (f" · {cost_note}" if cost_note else "")
                          + (f" · FBA {fba_cad_eff:.2f} CAD ({fba_src})" if fba_src else "")))

def calc_us(p_eur, s_usd):
    ship_real, customs_eur, cost_note = lookup_costs(ean_in, "US")
    fba_real, fba_src = lookup_fba(ean_in, "US")
    fba_us_eff = fba_real if fba_real is not None else fba_us
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
    fees     = ref + fba_us_eff + dsf
    ppu      = s_usd - cogs - fees
    roi      = ppu / cogs if cogs > 0 else 0
    return dict(cur="USD", purchase=p_usd, ship_labor=ship_usd + lab_usd,
                tariff_gst=tariff,
                cogs=cogs, sell_ex=s_usd, ref=ref, fba=fba_us_eff, dsf=dsf,
                fees=fees, ppu=ppu, roi=roi,
                tax_note=(f"Import tariff {us_tariff:.0%} in COGS. No sales tax stripped "
                          f"(Amazon remits it). DSF on referral only."
                          + (f" · {cost_note}" if cost_note else "")
                          + (f" · FBA {fba_us_eff:.2f} USD ({fba_src})" if fba_src else "")))

uk = calc_uk(purchase_eur, sell_gbp)
au = calc_au(purchase_eur, sell_aud)
ca = calc_ca(purchase_eur, sell_cad)
us = calc_us(purchase_eur, sell_usd_in)


def max_buy_price(calc, sell, roi_target):
    """Highest purchase price (EUR, excl VAT) that still gives roi_target.

    Solved from the market's own calc function, so it can never drift from the
    ROI maths. In every market the fees (referral, FBA, DSF) depend only on the
    sell price, and COGS is linear in the purchase price: COGS = a*p + b. ROI =
    (sell - fees - COGS) / COGS, so the max COGS is (sell - fees) / (1 + ROI)
    and p = (max COGS - b) / a. Floored to the cent so buying at the shown
    price meets the target. None = target unreachable even at a zero price.
    """
    d0, d1 = calc(0.0, sell), calc(1.0, sell)
    a, b = d1["cogs"] - d0["cogs"], d0["cogs"]
    cogs_max = (d0["sell_ex"] - d0["fees"]) / (1 + roi_target)
    p = (cogs_max - b) / a
    return math.floor(p * 100) / 100 if p > 0 else None


mbp = {
    "UK": max_buy_price(calc_uk, sell_gbp,    target_roi),
    "AU": max_buy_price(calc_au, sell_aud,    target_roi),
    "CA": max_buy_price(calc_ca, sell_cad,    target_roi),
    "US": max_buy_price(calc_us, sell_usd_in, target_roi),
}

# ─── RESULTS ──────────────────────────────────────────────────────────────────

st.divider()
st.subheader("Results")
st.caption(f"Rates in use: {rates_source}")

def roi_icon(roi):
    if roi >= 0.20: return "🟢"
    if roi >= 0.10: return "🟡"
    if roi >  0:    return "🟠"
    return "🔴"

def render_market(title, d, has_sell, mbp_eur):
    st.markdown(f"**{title}**")
    if not has_sell:
        st.caption("Enter a sell price above")
        return
    st.metric(f"Max buy price @ {target_roi:.0%} ROI (EUR excl VAT)",
              f"{mbp_eur:.2f}" if mbp_eur is not None else "—")
    if mbp_eur is None:
        st.caption("Target ROI not reachable at this sell price, even at a zero purchase price.")
    if purchase_eur <= 0:
        st.caption(d["tax_note"])
        st.caption("Enter a purchase price above to see ROI and profit.")
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
    render_market("🇬🇧  United Kingdom", uk, sell_gbp > 0,    mbp["UK"])
with col2:
    render_market("🇦🇺  Australia",      au, sell_aud > 0,    mbp["AU"])
with col3:
    render_market("🇨🇦  Canada",         ca, sell_cad > 0,    mbp["CA"])
with col4:
    render_market("🇺🇸  United States",  us, sell_usd_in > 0, mbp["US"])
