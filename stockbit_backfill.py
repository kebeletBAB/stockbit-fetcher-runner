"""
Stockbit Broker Summary — BACKFILL
====================================
Untuk saham yang ketinggalan / baru ditambahkan ke list.
Fetch semua tab: historis (1,2,3,4,8) + monthly (2606) + weekly + daily.

Setup:
  1. pip install requests
  2. Siapkan CSV: kolom 1=ticker, kolom 2=target sheet (sama seperti queue utama)
     Contoh isi backfill_queue.csv:
       TKIM,KO_IDX
       SRIL,AE_IDX
  3. Jalankan: python stockbit_backfill.py

Token expired? Buka stockbit.com → DevTools → Network →
klik request exodus.stockbit.com → copy Authorization header baru ke BEARER_TOKEN.
"""

import requests
import csv
import time
import re
import os
from datetime import datetime

# ====================================================================
# CONFIG — EDIT DI SINI
# ====================================================================

BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")

CSV_FILE = os.environ.get("CSV_FILE", "backfill_queue.csv")

DELAY_ANTAR_TAB   = 1.0  # detik jeda antar tab per ticker
DELAY_ANTAR_SAHAM = 2.0  # detik jeda antar ticker

# Tab yang akan dijalankan — set False untuk skip
RUN_TAB = {
    "1":      True,   # 2016-01-01 s/d 2026-01-27
    "2":      True,   # 2026-01-28 s/d 2026-02-27
    "3":      True,   # 2026-03-02 s/d 2026-03-31
    "4":      True,   # 2026-04-01 s/d 2026-04-30
    "8":      True,   # 2026-05-01 s/d 2026-05-31
    "monthly":True,   # bulan ini (auto tab ID: 2606, 2607, dst)
    "weekly": True,   # 7 hari terakhir
    "daily":  True,   # hari ini
}

# Date range untuk tab historis
TAB_DATE_RANGE = {
    "1": ("2016-01-01", "2026-01-27"),
    "2": ("2026-01-28", "2026-02-27"),
    "3": ("2026-03-02", "2026-03-31"),
    "4": ("2026-04-01", "2026-04-30"),
    "8": ("2026-05-01", "2026-05-31"),
}

# URL map GAS — sama persis dengan stockbit_broksum.py
URL_MAP = {
    "UJI_COBA":  "https://script.google.com/macros/s/AKfycbxCla6hNRobGV4J2lMb_kb0Uaw-Fi0kZ5qCv1rdrtVQOMAc6uOgFnL-01XP6_ABRlF-/exec",
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
    try:
        return str(int(float(v)))
    except:
        return str(v)

def fmt_avg(v):
    try:
        return str(int(float(v)))
    except:
        return str(v)

def fmt_rp_b(amount):
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

    TYPE_COLOR = {
        "Asing":      "#D73F3C",
        "Lokal":      "#7924C3",
        "Pemerintah": "#0C8414",
    }

    max_rows = max(len(buys), len(sells))
    broksum_data = []
    broksum_colors = []

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

    def accdist_color(accdist_str):
        ad = accdist_str.strip().lower()
        if ad == "acc":     return "#90E89780"
        if ad == "dist":    return "#ff988e80"
        if "acc" in ad:     return "#90E897"
        if "dist" in ad:    return "#ff988e"
        return "#DADADA"

    def vol_row(label, obj):
        ad = obj.get("accdist", "Neutral")
        return (
            [label, fmt_lot(obj.get("vol", 0)), f"{obj.get('percent', 0):.1f}",
             "", fmt_rp_b(obj.get("amount", 0)), ad],
            [None, None, None, None, None, accdist_color(ad)]
        )

    vol_data   = []
    vol_colors = []

    vol_data.append(["", "Volume", "%", "", "Rp(B)", "Acc/Dist"])
    vol_colors.append([None]*6)

    for label, key in [("Top 1","top1"),("Top 3","top3"),("Top 5","top5"),("Average","avg")]:
        rd, rc = vol_row(label, bd.get(key, {}))
        vol_data.append(rd)
        vol_colors.append(rc)

    vol_data.append(["", "Buyer", "Seller", "", "#", "Acc/Dist"])
    vol_colors.append([None]*6)

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
def fetch_by_period(ticker, period):
    """Fetch pakai period param (daily/weekly/monthly)."""
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

def fetch_by_date_range(ticker, from_date, to_date):
    """Fetch pakai from/to date range (tab historis 1,2,3,4,8)."""
    url = (
        f"https://exodus.stockbit.com/marketdetectors/{ticker}"
        f"?from={from_date}"
        f"&to={to_date}"
        f"&transaction_type=TRANSACTION_TYPE_NET"
        f"&market_board=MARKET_BOARD_REGULER"
        f"&investor_type=INVESTOR_TYPE_ALL"
        f"&limit=25"
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
# BACKFILL SATU TICKER — SEMUA TAB
# ====================================================================
def backfill_ticker(ticker, gas_url):
    """Jalankan semua tab untuk satu ticker."""

    # Tab order: historis dulu, baru monthly/weekly/daily
    tabs_to_run = []

    # Tab historis (date range)
    for tab_id in ["1", "2", "3", "4", "8"]:
        if RUN_TAB.get(tab_id):
            tabs_to_run.append(("daterange", tab_id))

    # Monthly (period param, tab ID auto dari bulan ini)
    if RUN_TAB.get("monthly"):
        monthly_tab_id = datetime.now().strftime("%y%m")  # "2606", "2607", dst
        tabs_to_run.append(("monthly", monthly_tab_id))

    # Weekly
    if RUN_TAB.get("weekly"):
        tabs_to_run.append(("weekly", "weekly"))

    # Daily
    if RUN_TAB.get("daily"):
        tabs_to_run.append(("daily", "daily"))

    results = []

    for idx, (fetch_mode, tab_id) in enumerate(tabs_to_run):
        print(f"     [{tab_id}] ", end="", flush=True)

        try:
            if fetch_mode == "daterange":
                from_date, to_date = TAB_DATE_RANGE[tab_id]
                json_resp = fetch_by_date_range(ticker, from_date, to_date)
            elif fetch_mode == "monthly":
                json_resp = fetch_by_period(ticker, "BROKER_SUMMARY_PERIOD_THIS_MONTH")
            elif fetch_mode == "weekly":
                json_resp = fetch_by_period(ticker, "BROKER_SUMMARY_PERIOD_LAST_7_DAYS")
            else:  # daily
                json_resp = fetch_by_period(ticker, "BROKER_SUMMARY_PERIOD_LATEST")

            data = json_resp.get("data")
            if not data:
                raise ValueError(f"Response tidak ada 'data': {json_resp}")

            payload = transform(ticker, data, tab_id)
            result = push_to_gas(gas_url, payload)
            print(f"✅")
            results.append({"tab": tab_id, "status": "ok"})

        except Exception as e:
            print(f"❌ {e}")
            results.append({"tab": tab_id, "status": "error", "error": str(e)})

        # Delay antar tab (kecuali tab terakhir)
        if idx < len(tabs_to_run) - 1:
            time.sleep(DELAY_ANTAR_TAB)

    return results

# ====================================================================
# MAIN
# ====================================================================
if __name__ == "__main__":
    print(f"\n{'='*55}")
    print(f"  STOCKBIT BACKFILL")
    print(f"  CSV: {CSV_FILE}")
    print(f"{'='*55}\n")

    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if r and r[0].strip()]

    print(f"Total ticker: {len(rows)}\n")

    all_errors = []

    for i, row in enumerate(rows, 1):
        ticker = row[0].strip().upper()
        target = re.sub(r"[^a-zA-Z0-9_]", "", row[1].strip()) if len(row) > 1 else ""

        gas_url = URL_MAP.get(target)
        if not gas_url:
            print(f"[{i:03d}] ⚠️  {ticker} | target '{target}' tidak ada di URL_MAP, skip\n")
            all_errors.append({"ticker": ticker, "target": target, "tab": "-", "error": "target tidak ditemukan di URL_MAP"})
            continue

        print(f"[{i:03d}] {ticker} → {target}")
        results = backfill_ticker(ticker, gas_url)

        # Kumpulkan error
        for r in results:
            if r["status"] == "error":
                all_errors.append({
                    "ticker": ticker,
                    "target": target,
                    "tab": r["tab"],
                    "error": r.get("error", ""),
                    "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                })

        print()

        # Delay antar saham
        if i < len(rows):
            time.sleep(DELAY_ANTAR_SAHAM)

    # Summary
    print(f"\n{'='*55}")
    print(f"  SELESAI — {len(rows)} ticker, {len(all_errors)} error")
    print(f"{'='*55}\n")

    if all_errors:
        err_file = f"error_log_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(err_file, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ticker","target","tab","timestamp","error"])
            w.writeheader()
            w.writerows(all_errors)
        print(f"Error log disimpan: {err_file}")
