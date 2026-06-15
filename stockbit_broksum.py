"""
Stockbit Broker Summary Fetcher
================================
Fetch data broker summary dari Stockbit API langsung,
transform ke format GAS, lalu push ke Google Apps Script.

Setup:
  1. pip install requests
  2. Edit CONFIG di bawah (BEARER_TOKEN, CSV_FILE, MODE)
  3. Jalankan: python stockbit_broksum.py

Token expired? Buka stockbit.com → DevTools → Network →
klik request exodus.stockbit.com → copy Authorization header baru ke BEARER_TOKEN.
"""

import requests
import csv
import time
import json
from datetime import datetime

# ====================================================================
# CONFIG — EDIT DI SINI
# ====================================================================

import os
BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")

CSV_FILE = "queue_full_eipo-idx-bfr2016"  # kolom 1=ticker, kolom 2=target sheet

MODE_DAILY   = True   # True = jalankan daily
MODE_WEEKLY  = True  # True = jalankan weekly
MODE_MONTHLY = True  # True = jalankan monthly

DELAY_ANTAR_SAHAM = 1.0  # detik jeda antar ticker (hindari rate limit)

# URL map GAS — sama persis dengan Super Daily 2
URL_MAP = {
    "UJI_COBA": "https://script.google.com/macros/s/AKfycbxCla6hNRobGV4J2lMb_kb0Uaw-Fi0kZ5qCv1rdrtVQOMAc6uOgFnL-01XP6_ABRlF-/exec",
    "AE_eIPO":   "https://script.google.com/macros/s/AKfycbx9wUmo_1QgtiYuNvkiaMA4Gip7pWV9ePRS6dgV__o2CtHwxja71jrQlK_T4wHLW6Fq/exec",
    "FJ_eIPO":   "https://script.google.com/macros/s/AKfycbwrWk0aexhj0wCE5ZNmpUePrNDj-3jmD_3iH8Q7J2vs-Dh0sO7rF0BqM_bkvmt4B_Au/exec",
    "KO_eIPO":   "https://script.google.com/macros/s/AKfycbxHT4L8sZc1nKW-tv7iLTjjJLztSiPM2UEe9J_vbwormNGZpMzivgq_NVVlxFFNh0mCdQ/exec",
    "PT_eIPO":   "https://script.google.com/macros/s/AKfycbx4Ek4wpjdaPrS-rmHJejQsw-pPCCiCvxDwu-75clUBQX81KAqCVok0BJOOm3IJuU9s/exec",
    "UZ_eIPO":   "https://script.google.com/macros/s/AKfycbzTdKapBHZrxD-JuX0IFXZlJ_TAPqIltHkoGisALOORiptIlwC-VGOz5psygVHnjHDi/exec",
    "AE_IDX":    "https://script.google.com/macros/s/AKfycbxeX0aY28TBR4ZFqPDQ-_z4LH_TFP_S7aq9dBf7PyEHEcohyJPxL1JzjOn7j7FCi_2J/exec",
    "FJ_IDX":    "https://script.google.com/macros/s/AKfycbyzYNIzE-HfLuTKlr9Md3OVv-mqGW-nJVWmOI9uExKUuPN9Pd2AAsQ9BuP4GTlVem10/exec",
    "KO_IDX":    "https://script.google.com/macros/s/AKfycbzJZqFfJcll5SpqspSMYrBVA2hDgvF2fiR8obUrIwi64yDOSzQlE2nw4R0KhN3yamMcsw/exec",
    "PT_IDX":    "https://script.google.com/macros/s/AKfycbxLbDHOgLyrve3KCldFvBrSVS-KSJsFuub74zCN4E7_e7Bu_SxSp9iQGVSfOTA73g9rtA/exec",
    "UZ_IDX":    "https://script.google.com/macros/s/AKfycbz95aSpXXoDUXkFyWdRPz5X8vHCKVdNm2drcEup1MwT4kB4tdxnNNrg3uAoScFslboJyA/exec",
    "AE_Bfr2016":"https://script.google.com/macros/s/AKfycbwCun9-uda8Zi4DvH3dKIKX6rWMx1NoyCw2v3n074sS9AZQ5CtZn-hWk6ekMUymlqgz/exec",
    "FJ_Bfr2016":"https://script.google.com/macros/s/AKfycbywrZQ23R3UpRTFKjkVwnq-ldeG8ZUkd6N7759XTBe1BXkKyV4Xx6Nxo1JDx7LTbDm6/exec",
    "KO_Bfr2016":"https://script.google.com/macros/s/AKfycbyr3l4mmi_SY_NZdV0eRTtSz7x-i98-UXbRl5p9K_PkIrH1iANg4_6Br9_YlYXzWpEl/exec",
    "PT_Bfr2016":"https://script.google.com/macros/s/AKfycbzZacBNJp9K4dUvnPraGqTQsP-rg4_jFF5KoLfT1cZ9x8062o1yQyj6XsN5fNwqiwVT/exec",
    "UZ_Bfr2016":"https://script.google.com/macros/s/AKfycby0x1PzDzFCP1b2xtvqlDE6LllEZay8cAlf_va_zlBzL5nk3VaYLVDEuMPi4iEZxX8z/exec",
}

# ====================================================================
# API HEADERS
# ====================================================================
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "id,en-US;q=0.9,en;q=0.8",
    "authorization": f"Bearer {BEARER_TOKEN}",
    "origin": "https://stockbit.com",
    "referer": "https://stockbit.com/",
    "x-platform": "web",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
}

# ====================================================================
# HELPER: FORMAT ANGKA
# ====================================================================
def fmt_val(v):
    """Format nilai ke string ringkas: 11300000 → '11.3M', 388000 → '388K'"""
    try:
        v = float(v)
    except:
        return str(v)
    abs_v = abs(v)
    sign = "-" if v < 0 else ""
    if abs_v >= 1_000_000:
        s = f"{abs_v/1_000_000:.3g}M"
    elif abs_v >= 1_000:
        s = f"{abs_v/1_000:.3g}K"
    else:
        s = f"{abs_v:.3g}"
    return sign + s

def fmt_lot(v):
    """Format lot ke integer string"""
    try:
        return str(int(float(v)))
    except:
        return str(v)

def fmt_avg(v):
    """Format avg price ke integer"""
    try:
        return str(int(float(v)))
    except:
        return str(v)

def fmt_rp_b(amount):
    """Format amount ke Rp(B) = miliar, 1 desimal"""
    try:
        v = float(amount) / 1_000_000_000
        return round(v, 1)
    except:
        return 0

# ====================================================================
# TRANSFORM: API JSON → FORMAT BROKSUM & VOLUME
# ====================================================================
def transform(ticker, data, tab_id):
    bd = data["bandar_detector"]
    bs = data["broker_summary"]

    buys  = bs.get("brokers_buy", [])
    sells = bs.get("brokers_sell", [])

    # --- BROKSUM ---
    # Format: [BY, B.val, B.lot, B.avg, SL, S.val, S.lot, S.avg]
    # Digabung per baris: buy[i] kiri, sell[i] kanan, "-" kalau habis
    max_rows = max(len(buys), len(sells))
    broksum_data = []
    broksum_colors = []

    # Warna berdasarkan type broker (sama seperti Stockbit)
    TYPE_COLOR = {
        "Asing":      "#D73F3C",  # merah  (type="foreign") rgb(215,63,60)
        "Lokal":      "#7924C3",  # ungu   (type="local")   rgb(121,36,195)
        "Pemerintah": "#0C8414",  # hijau  (type="bumn")    rgb(12,132,20)
    }

    for i in range(max_rows):
        row_data = []
        row_color = []

        if i < len(buys):
            b = buys[i]
            b_code_color = TYPE_COLOR.get(b.get("type", ""), "#333333")
            row_data += [
                b.get("netbs_broker_code", "-"),
                fmt_val(b.get("bval", 0)),
                fmt_lot(b.get("blot", 0)),
                fmt_avg(b.get("netbs_buy_avg_price", 0)),
            ]
            row_color += [b_code_color, "#00AB6B", "#00AB6B", "#00AB6B"]
        else:
            row_data += ["-", "-", "-", "-"]
            row_color += ["#333333", "#333333", "#333333", "#333333"]

        if i < len(sells):
            s = sells[i]
            s_code_color = TYPE_COLOR.get(s.get("type", ""), "#333333")
            row_data += [
                s.get("netbs_broker_code", "-"),
                fmt_val(abs(float(s.get("sval", 0)))),
                fmt_lot(abs(float(s.get("slot", 0)))),
                fmt_avg(s.get("netbs_sell_avg_price", 0)),
            ]
            row_color += [s_code_color, "#EE4A49", "#EE4A49", "#EE4A49"]
        else:
            row_data += ["-", "-", "-", "-"]
            row_color += ["#333333", "#333333", "#333333", "#333333"]

        broksum_data.append(row_data)
        broksum_colors.append(row_color)

    # --- VOLUME ---
    # Format sama persis dengan Super Daily 2:
    # Header: ["", "Volume", "%", "", "Rp(B)", "Acc/Dist"]  ← 6 kolom (kolom ke-4 kosong)
    # Row top1/3/5/avg: [label, vol, %, "", rp_b, accdist]
    # Header2: ["", "Buyer", "Seller", "", "#", "Acc/Dist"]
    # Row broker: ["Broker", buyer, seller, "", number, accdist]
    # summary_1: ["Net Volume", net_vol, "", "", "", ""]
    # summary_2: ["Net Value", net_val_str, "", "", "", ""]
    # summary_3: ["Average (Rp)", avg, "", "", "", ""]

    def accdist_color(accdist_str):
        ad = accdist_str.strip().lower()
        if ad == "acc":              return "#90E89780"  # Acc (transparan)
        if ad == "dist":             return "#ff988e80"  # Dist (transparan)
        if "acc" in ad:              return "#90E897"    # Big/Normal/Small Acc
        if "dist" in ad:             return "#ff988e"    # Big/Normal/Small Dist
        return "#DADADA"  # Neutral

    def vol_row(label, obj):
        ad = obj.get("accdist", "Neutral")
        return (
            [label, fmt_lot(obj.get("vol", 0)), f"{obj.get('percent', 0):.1f}",
             "", fmt_rp_b(obj.get("amount", 0)), ad],
            [None, None, None, None, None, accdist_color(ad)]
        )

    vol_data   = []
    vol_colors = []

    # Header baris 1
    vol_data.append(["", "Volume", "%", "", "Rp(B)", "Acc/Dist"])
    vol_colors.append([None]*6)

    for label, key in [("Top 1","top1"),("Top 3","top3"),("Top 5","top5"),("Average","avg")]:
        rd, rc = vol_row(label, bd.get(key, {}))
        vol_data.append(rd)
        vol_colors.append(rc)

    # Header baris 2
    vol_data.append(["", "Buyer", "Seller", "", "#", "Acc/Dist"])
    vol_colors.append([None]*6)

    # Broker row
    broker_ad = bd.get("broker_accdist", "Neutral")
    vol_data.append([
        "Broker",
        str(bd.get("total_buyer", 0)),
        str(bd.get("total_seller", 0)),
        "",
        str(bd.get("number_broker_buysell", 0)),
        broker_ad
    ])
    vol_colors.append([None, None, None, None, None, accdist_color(broker_ad)])

    # Summary rows
    net_vol = bd.get("volume", 0)
    net_val = bd.get("value", 0)
    avg_rp  = bd.get("average", 0)

    vol_data.append(["Net Volume", fmt_lot(net_vol), "", "", "", ""])
    vol_colors.append([None]*6)
    vol_data.append(["Net Value", "0B" if net_val == 0 else fmt_val(net_val), "", "", "", ""])
    vol_colors.append([None]*6)
    vol_data.append(["Average (Rp)", fmt_avg(avg_rp), "", "", "", ""])
    vol_colors.append([None]*6)

    return {
        "ticker": ticker,
        "tab": tab_id,
        "broksum": {"data": broksum_data, "colors": broksum_colors},
        "volume":  {"data": vol_data,     "colors": vol_colors},
    }

# ====================================================================
# FETCH API STOCKBIT
# ====================================================================
def fetch_stockbit(ticker, period):
    """
    period: 'BROKER_SUMMARY_PERIOD_LATEST' (daily) atau 'BROKER_SUMMARY_PERIOD_WEEKLY' (weekly)
    """
    # Coba endpoint marketdetectors dulu (ini yang paling lengkap)
    url = (
        f"https://exodus.stockbit.com/marketdetectors/{ticker}"
        f"?transaction_type=TRANSACTION_TYPE_NET"
        f"&market_board=MARKET_BOARD_REGULER"
        f"&investor_type=INVESTOR_TYPE_ALL"
        f"&limit=25"
        f"&period={period}"
    )
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()

# ====================================================================
# PUSH KE GAS
# ====================================================================
def push_to_gas(gas_url, payload):
    resp = requests.post(gas_url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.text

# ====================================================================
# MAIN LOOP
# ====================================================================
def run(mode):
    """mode: 'daily' atau 'weekly'"""
    if mode == "daily":
        tab_id = "daily"
    elif mode == "weekly":
        tab_id = "weekly"
    else:
        # Auto-generate tab ID bulanan: format YYMM, contoh Jun 2026 = "2606"
        now = datetime.now()
        tab_id = now.strftime("%y%m")  # "2606", "2607", dst
    if mode == "daily":
        period = "BROKER_SUMMARY_PERIOD_LATEST"
    elif mode == "weekly":
        period = "BROKER_SUMMARY_PERIOD_LAST_7_DAYS"
    else:
        period = "BROKER_SUMMARY_PERIOD_THIS_MONTH"

    print(f"\n{'='*50}")
    print(f"  MEMULAI FASE {mode.upper()}")
    print(f"{'='*50}\n")

    error_log = []

    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if r and r[0].strip()]

    for i, row in enumerate(rows, 1):
        ticker = row[0].strip().upper()
        target = row[1].strip().replace(r"[^a-zA-Z0-9_]", "") if len(row) > 1 else ""
        # Bersihkan karakter non-alphanumeric (sama seperti GAS regex)
        import re
        target = re.sub(r"[^a-zA-Z0-9_]", "", target)

        gas_url = URL_MAP.get(target)
        if not gas_url:
            print(f"[{i:03d}] ⚠️  {ticker} | target '{target}' tidak ada di URL_MAP, skip")
            error_log.append({"ticker": ticker, "target": target, "error": "target tidak ditemukan di URL_MAP"})
            continue

        print(f"[{i:03d}] {ticker} → {target} ... ", end="", flush=True)

        try:
            # Fetch
            json_resp = fetch_stockbit(ticker, period)
            data = json_resp.get("data")
            if not data:
                raise ValueError(f"Response tidak ada 'data': {json_resp}")

            # Transform
            payload = transform(ticker, data, tab_id)

            # Push
            result = push_to_gas(gas_url, payload)
            print(f"✅  GAS: {result[:60]}")

        except Exception as e:
            ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            print(f"❌  ERROR: {e}")
            error_log.append({"ticker": ticker, "target": target, "timestamp": ts, "error": str(e)})

        if i < len(rows):
            time.sleep(DELAY_ANTAR_SAHAM)

    print(f"\n{'='*50}")
    print(f"  SELESAI — {len(rows)} saham diproses, {len(error_log)} error")
    print(f"{'='*50}\n")

    if error_log:
        err_file = f"error_log_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(err_file, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ticker","target","timestamp","error"])
            w.writeheader()
            w.writerows(error_log)
        print(f"Error log disimpan: {err_file}")


if __name__ == "__main__":
    if MODE_DAILY:
        run("daily")
    if MODE_WEEKLY:
        run("weekly")
    if MODE_MONTHLY:
        run("monthly")
