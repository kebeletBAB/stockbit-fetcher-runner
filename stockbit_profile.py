"""
Stockbit Profile Fetcher
========================
Fetch data profile (shareholders, company history, number of shareholders)
dari Stockbit API langsung, lalu push ke Google Apps Script.

Setup:
  1. Pastikan sudah: pip install requests (di virtualenv)
  2. Edit CONFIG di bawah (BEARER_TOKEN, CSV_FILE)
  3. Jalankan: python stockbit_profile.py

Token expired? Buka stockbit.com → DevTools → Network →
klik request exodus.stockbit.com → copy Authorization header baru ke BEARER_TOKEN.
"""

import requests
import csv
import time
import json
import re
from datetime import datetime

# ====================================================================
# CONFIG — EDIT DI SINI
# ====================================================================

import os
BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")

CSV_FILE = "queue_full_eipo-idx-bfr2016.csv"

# Batch config (parallel GitHub Actions)
import os
BATCH_INDEX   = int(os.environ.get("BATCH_INDEX", "0"))
TOTAL_BATCHES = int(os.environ.get("TOTAL_BATCHES", "1"))  # kolom 1=ticker, kolom 2=target sheet

FETCH_SHAREHOLDERS      = False  # True = kirim data shareholders
FETCH_COMPANY_HISTORY   = False  # True = kirim data company history
FETCH_NUM_SHAREHOLDERS  = True   # True = kirim number of shareholders (paling sering)

DELAY_ANTAR_SAHAM = 1.0  # detik jeda antar ticker

# ====================================================================
# URL MAP — sama persis dengan broksum
# ====================================================================
# Format per entry: (url1_shareholders, url2_history, url3_num_shareholders)
URL_MAP = {
    "UJI_COBA": (
        "https://script.google.com/macros/s/AKfycby7hDGCrHf3MEJ-VscJmTGI3fCPSKKnYJY0xFSFiJcJOyEgt9E6KCdVGXDHTM9Qimld/exec",
        "https://script.google.com/macros/s/AKfycbyQDmdy6ROVeopJjxSs6O7SGgk3_x3AYqNMAS_OU2OWjENLjeS2-buYJtq36UNA2Al0/exec",
        "https://script.google.com/macros/s/AKfycbx7Zhe4yQOTFz7-Geah7ZOGE12Sz7NwPa9H0t4_u9C82cn6MtYw2S3o5JshaEl_KRyS/exec",
    ),
    "AE_eIPO": (
        "https://script.google.com/macros/s/AKfycbwZFml6lh6RekvgomabHdIx6zUWFAIlaINiX3Jm7avaPZZFx2VWTnwZ6JQxrRZFIWzE/exec",
        "https://script.google.com/macros/s/AKfycbxHy2_z5rhk3oEWUN5R5UDZu5wtWrFhm-4ZZ-F9bd_JgPquSFaSFllpKeNyvFtC7FAr/exec",
        "https://script.google.com/macros/s/AKfycbyx3b8cl7oVdQJfexAesASSvDt3TNG5GcHS4cjAWqaHfqKqXn1CVp6kEtn-UFPDYCFS/exec",
    ),
    "FJ_eIPO": (
        "https://script.google.com/macros/s/AKfycbwvllmtqvwizWGPXJIcIQaH2vI1voJauCpZsEpXtltjPSE5FOegRUgHkP43QUGs5LIR/exec",
        "https://script.google.com/macros/s/AKfycbwvF1kqP5ELdAz0J6y8wD1Xq1knHwWgJRcgaX4CSP2XAx1gieuHUjZcAHEYV8oGpi6O/exec",
        "https://script.google.com/macros/s/AKfycbx9Q2nS_dr2CH7ZjyWh41UGbtXO8MPkAvVJnZtHvsjfP0v6VPt3PPM0HvyyVxpV7QD4/exec",
    ),
    "KO_eIPO": (
        "https://script.google.com/macros/s/AKfycbzBC2b8vp-uc8mITXdUPD4JV0najUEPCNq8Qcn4Y2scI4dVcQeWYA3RnsGJe4dBbRwMIg/exec",
        "https://script.google.com/macros/s/AKfycbyUnBlHgGdDdEQLJdHe-ySf3N9EZwzc444ixuz_lDOf29dOjBLTcqP9dpr_vlKBwsMk/exec",
        "https://script.google.com/macros/s/AKfycbwYZLhMn0r-1wZ_NA5tmqc6tR13iEuYVqWgPY9rJwMTn6rmRQ_PCoVlx56d0na_y6pFuA/exec",
    ),
    "PT_eIPO": (
        "https://script.google.com/macros/s/AKfycbxXglLlBwi4WMPh82hVbA8yevbjeOQrZ69rhuk8Z5qNcnhmhDwRLVQwHGsGwMO5OlA/exec",
        "https://script.google.com/macros/s/AKfycbyOvIlJjrjFqggTBZa9uDzf6_oG2spF6dlDechJCjv24zCh4beZABjAhZhd5TFN-kLv/exec",
        "https://script.google.com/macros/s/AKfycbyFjIZ8UE2BKcetCrn-zrfcr8SEnWXj6mgwj9vITaOF7sC2mhrI3hrFlmTcgb6D48kK/exec",
    ),
    "UZ_eIPO": (
        "https://script.google.com/macros/s/AKfycbzpfIhcOk5ZK7v9G_YUmy9r99uVxs2lV8jE6XiSStZ5c5XyJxKYFafhhJp7el0cmMfP/exec",
        "https://script.google.com/macros/s/AKfycbzcqx7tlGuAACAO4ajJiYu5Ng5bkCZmtBlHkze5gV9gf33womHPU7lm5dqqKMeQzkgg/exec",
        "https://script.google.com/macros/s/AKfycbyP1IOD50o3NIMsoKJoOaGZ-tod4wTmbs-VpUeh5Mx1h2M97hqPQMQJ6wHUk0m8MWW_/exec",
    ),
    "AE_IDX": (
        "https://script.google.com/macros/s/AKfycbzikLrG3n0p5f0cREfyOELcZBzI6KKb5izmW1AxwqIJDzUm_eCT_AWL2w9tFwqkf32H/exec",
        "https://script.google.com/macros/s/AKfycbxDaB5vyYIGtiCH37fkGmAcrvu1S1JuTDsHNv_YDM9s8a20A8s0v8Z2LIVW4iytZxRz/exec",
        "https://script.google.com/macros/s/AKfycbzrsEgLVG5Lqp6KyydFIYR6ZfzNFiW35yRiwjH8Au8wD1TyPFahitZa1ZoBqpGfdWIQ/exec",
    ),
    "FJ_IDX": (
        "https://script.google.com/macros/s/AKfycbzMf33D5dzy3h5wN7lm1JvctOSaVr6qCPUz0L8hO4SE1Xb32CBgUg5lwtMFIPdKynbw/exec",
        "https://script.google.com/macros/s/AKfycbzDQoBN4_gtXJmrKQYNuxbgbDP_AYEObHvB0wkYj5fqCvhjhrsmezijYH4AScFfOCtT/exec",
        "https://script.google.com/macros/s/AKfycbyZlkEdvGLpZTEJxFCvVNpIM158Exv-tIr2hqkn2fgd_e7bcHpJrPakP3JFiSv8lgBB/exec",
    ),
    "KO_IDX": (
        "https://script.google.com/macros/s/AKfycbyWhct2GgvbOuLBzQ4m4fZBOX_3owthGzkLRsU0TgNRAe1PvnbKRIY0Tq0T-fyFEzwP1A/exec",
        "https://script.google.com/macros/s/AKfycbx2j7bHM6VvnKcpVNiZr0gEirO-Zcz-LgUMVinN8LUChRAU1vsHxFugioNNa3CkNKuDZQ/exec",
        "https://script.google.com/macros/s/AKfycbzUy9nZAt3Au4viBDKCDLn7i73oOwkeaEsYy8OFhc3Q7LS5f0E30WDsaY1fHQxhrc83OQ/exec",
    ),
    "PT_IDX": (
        "https://script.google.com/macros/s/AKfycbwnxFCbI_8LXO2qDySXvFX84JHSp-Xb3rpEJ_CuU_LWUFGNmfnk7bkPNSvtKvf2h7YQGw/exec",
        "https://script.google.com/macros/s/AKfycbyI2c6m5GhCtGOGGfmmmo1iKWPmj8pvhVorBZK8lx4KPSSZKiIM4a7WGl2iHJPX6o0syFw/exec",
        "https://script.google.com/macros/s/AKfycbyvIishi3hP4U7ZTFqSai0dd1ZxE0QVnHsycH0JbzeBBldo9lLymgjUFgrMqnt9STsLiQ/exec",
    ),
    "UZ_IDX": (
        "https://script.google.com/macros/s/AKfycbzIX1nqXF-OkRGV53mNvth6DZfxPw4eHzQ4dVT3Y9_1FtznmCh1OSji9K4IUcxfd1sSZg/exec",
        "https://script.google.com/macros/s/AKfycbxNA94nQCfTEwCH94-6KXZE840sOjYehajlP29Mxd-RDUqAu40USZ526z3bJJIeKpMpBA/exec",
        "https://script.google.com/macros/s/AKfycbwt6wYY4ncMgVNIJe_X4Zq3VJ-2hhW4V4B1FkZVispLES_5Do5TUY9ODJj1M5eSzt085w/exec",
    ),
    "AE_Bfr2016": (
        "https://script.google.com/macros/s/AKfycbyU9xnIhbyFXz5P1VBVqJqSJ8pD9bH-dtsFQtSvDMAfZ-_0oHFha5Ggc4nB0D9u9YuE/exec",
        "https://script.google.com/macros/s/AKfycbyXF85iKn_q65i7lARB5cR42Wbp2jif-d2qRxYzkint7uX3w7p30ixK1hXQumCS5vE/exec",
        "https://script.google.com/macros/s/AKfycbxmz9GsXhq814iUA0DbIeBxlX0jrQCtWFmHCUcwso2L6LFhZVPL2Ro7m5LxuBm55a5Q/exec",
    ),
    "FJ_Bfr2016": (
        "https://script.google.com/macros/s/AKfycbyxAjCpkG4FGBWmyZOqmR_KereHodE17GaNnjHE9Qxfw_llBurHdsibF4Cl4BZ66qZA/exec",
        "https://script.google.com/macros/s/AKfycbwqJ-11RFpoUwS0coQG-wMw9TKCK3KbUuulSg6TMbyxza1U0sWxd2BdgnYfIIlD0Xd2/exec",
        "https://script.google.com/macros/s/AKfycbzFVlUo6Z3ql4wUNEe9k02a9lN5-CGvLpar7ODTwpgFfuYAuyM7QigXuxY8cHN0NHIO/exec",
    ),
    "KO_Bfr2016": (
        "https://script.google.com/macros/s/AKfycbwMa8dCrrzx0VcL6kITGrAwJEliCEjcMQ-hQw14S79DSYjZk2ue6M-pl0-4z8S4WXZw/exec",
        "https://script.google.com/macros/s/AKfycbzzVncLETMMtNRQPlokL4hac_aG0w6aQwKmPEq-qqxcwxq1-4iNKgNviIia-_tEaGov/exec",
        "https://script.google.com/macros/s/AKfycbxNUeC0t5gU8TurCvq5vZwL9t-3Pf398zNbttWgta5v4xCrHor7qp1POzlBlpuTWnjh/exec",
    ),
    "PT_Bfr2016": (
        "https://script.google.com/macros/s/AKfycbwJF76plwqskGmB-RQZJYXcH6o-GVV7slm-CpBNOeM2YpCzP7mFu0oLasfCFo58bsyy/exec",
        "https://script.google.com/macros/s/AKfycbyw_INZYMX2lh904otR1suEaciKpThkzZMH-lOtg4gS53QhkV9UQiDKUsYsAtdZI5Qu/exec",
        "https://script.google.com/macros/s/AKfycby36KH4t8jgkY3DZfYEqQpa-eMLaWlHsBV5VRtxqJRlZLY1Aw0XJ8mjajGCxcb5huGC/exec",
    ),
    "UZ_Bfr2016": (
        "https://script.google.com/macros/s/AKfycbwopzjjhfxX7_bnco-37hxAXguxKdqwNls3ApBw0KqWxof95PoSHhc9V2SRH9bvowtl/exec",
        "https://script.google.com/macros/s/AKfycby_b9llS5tYFCTGtBgQ_xL8cW4QiQSWNzhEsBtvnJYFkZKTKVqGwah5BAdOLhY_W3ST/exec",
        "https://script.google.com/macros/s/AKfycbybnlBj_NzGdl2eA_e3IlyIvf80wENsW8lgJgW7XyWbrjsrbXQjA57TRaFQOlyNWI8M/exec",
    ),
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
# HELPER: KONVERSI BULAN
# ====================================================================
MONTH_MAP = {
    "Jan": "Januari", "Feb": "Februari", "Mar": "Maret",
    "Apr": "April", "May": "Mei", "Jun": "Juni",
    "Jul": "Juli", "Aug": "Agustus", "Sep": "September",
    "Oct": "Oktober", "Nov": "November", "Dec": "Desember"
}

def convert_shareholder_date(date_str):
    """Konversi '31 May 2026' → 'Mei 26'"""
    try:
        parts = date_str.strip().split()
        if len(parts) == 3:
            day, month_en, year = parts
            month_id = MONTH_MAP.get(month_en[:3], month_en)
            year_short = year[-2:]
            return f"{month_id} {year_short}"
    except:
        pass
    return date_str

# ====================================================================
# FETCH API STOCKBIT
# ====================================================================
def fetch_profile(ticker):
    url = f"https://exodus.stockbit.com/emitten/{ticker}/profile"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()

# ====================================================================
# TRANSFORM & PUSH
# ====================================================================
def process_ticker(ticker, target, urls):
    url1, url2, url3 = urls

    json_resp = fetch_profile(ticker)
    data = json_resp.get("data", {})

    # --- 1. SHAREHOLDERS ---
    shareholders = []
    for s in data.get("shareholder", []):
        shareholders.append({
            "name": s.get("name", ""),
            "qty":  s.get("value", "-"),
            "percent": s.get("percentage", "-"),
        })

    # --- 1. SHAREHOLDERS ---
    if FETCH_SHAREHOLDERS and shareholders:
        payload1 = {"emiten": ticker, "shareholders": shareholders}
        r1 = requests.post(url1, json=payload1, timeout=30)
        print(f"  Shareholders → {r1.text[:60]}")

    # --- 2. COMPANY HISTORY ---
    if FETCH_COMPANY_HISTORY:
        h = data.get("history", {})
        underwriters = h.get("underwriters", [])
        underwriter_str = ", ".join(underwriters) if underwriters else "-"
        payload2 = {
            "ticker":      ticker,
            "listingDate": h.get("date", "-"),
            "ipoPrice":    h.get("price", "-"),
            "ipoAmount":   h.get("amount", "-"),
            "ipoShares":   h.get("shares", "-"),
            "freeFloat":   h.get("free_float", "-"),
            "underwriter": underwriter_str,
        }
        r2 = requests.post(url2, json=payload2, timeout=30)
        print(f"  Company History → {r2.text[:60]}")

    # --- 3. NUMBER OF SHAREHOLDERS ---
    if FETCH_NUM_SHAREHOLDERS:
        shareholder_numbers = data.get("shareholder_numbers", [])
        num_data = []
        for item in shareholder_numbers:
            label = convert_shareholder_date(item.get("shareholder_date", ""))
            total = item.get("total_share", "0").replace(",", "")
            change = item.get("change", 0)
            num_data.append({"label": label, "total": total, "change": change})
        if num_data:
            payload3 = {"emiten": ticker, "data": num_data}
            encoded = requests.utils.quote(json.dumps(payload3))
            r3 = requests.get(f"{url3}?dataPayload={encoded}", timeout=30)
            print(f"  Num Shareholders → {r3.text[:60]}")

# ====================================================================
# MAIN LOOP
# ====================================================================
def run():
    print(f"\n{'='*50}")
    print(f"  MEMULAI PROFILE EXTRACTOR")
    print(f"{'='*50}\n")

    error_log = []

    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if r and r[0].strip()]
        rows = rows[BATCH_INDEX::TOTAL_BATCHES]
        print(f"Batch {BATCH_INDEX+1}/{TOTAL_BATCHES} — {len(rows)} ticker")

    for i, row in enumerate(rows, 1):
        ticker = row[0].strip().upper()
        target = re.sub(r"[^a-zA-Z0-9_]", "", row[1].strip()) if len(row) > 1 else ""

        urls = URL_MAP.get(target)
        if not urls:
            print(f"[{i:03d}] ⚠️  {ticker} | target '{target}' tidak ada di URL_MAP, skip")
            error_log.append({"ticker": ticker, "target": target, "error": "target tidak ditemukan"})
            continue

        print(f"[{i:03d}] {ticker} → {target}")

        try:
            process_ticker(ticker, target, urls)
        except Exception as e:
            ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            print(f"  ❌ ERROR: {e}")
            error_log.append({"ticker": ticker, "target": target, "timestamp": ts, "error": str(e)})

        if i < len(rows):
            time.sleep(DELAY_ANTAR_SAHAM)

    print(f"\n{'='*50}")
    print(f"  SELESAI — {len(rows)} saham diproses, {len(error_log)} error")
    print(f"{'='*50}\n")

    if error_log:
        err_file = f"error_log_profile_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(err_file, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ticker", "target", "timestamp", "error"])
            w.writeheader()
            w.writerows(error_log)
        print(f"Error log disimpan: {err_file}")


if __name__ == "__main__":
    run()
