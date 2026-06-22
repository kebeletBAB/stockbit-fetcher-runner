"""
Stockbit Broker Summary — BROKSUM_DB
=====================================
Push broksum historis ke Google Sheet "Broksum_DB" via gspread.

2 mode:
  - BACKFILL : fetch 600 hari ke belakang (one-time, first run)
  - DAILY    : auto catch-up dari last date di Sheet sampai hari ini

Tab naming: [UNIVERSE]_[YYYY-MM]
  Contoh: IDX_2026-06, eIPO_2026-06, Bfr2016_2026-06

Format kolom (25 kolom):
  Ticker | Tanggal | Top1_Vol | Top1_% | Top1_RpB | Top1_AccDist |
  Top3_Vol | Top3_% | Top3_RpB | Top3_AccDist |
  Top5_Vol | Top5_% | Top5_RpB | Top5_AccDist |
  Avg_Vol | Avg_% | Avg_RpB | Avg_AccDist |
  Broker_Buyer | Broker_Seller | Broker_# | Broker_AccDist |
  NetVol | NetVal | Avg_Price

Setup:
  1. pip install requests gspread
  2. Siapkan Google Service Account credentials.json
  3. Set environment variables (lihat README di bawah)
  4. Jalankan: python stockbit_broksum_db.py

Token expired? Buka stockbit.com -> DevTools -> Network ->
klik request exodus.stockbit.com -> copy Authorization header baru ke BEARER_TOKEN.
"""

import requests
import gspread
import csv
import time
import os
import re
from datetime import datetime, date, timedelta, timezone

# ====================================================================
# CONFIG
# ====================================================================

BEARER_TOKEN  = os.environ.get("BEARER_TOKEN", "")
CSV_FILE      = os.environ.get("CSV_FILE", "queue_full_eipo-idx-bfr2016.csv")
MODE          = os.environ.get("MODE", "daily")        # "backfill" atau "daily"
BROKSUM_DB_ID = os.environ.get("BROKSUM_DB_ID", "")   # Google Spreadsheet ID (kosong = auto create)
CREDENTIALS   = os.environ.get("CREDENTIALS", "credentials.json")
YOUR_EMAIL    = os.environ.get("YOUR_EMAIL", "")       # Email untuk share spreadsheet baru

BATCH_INDEX   = int(os.environ.get("BATCH_INDEX", "0"))
TOTAL_BATCHES = int(os.environ.get("TOTAL_BATCHES", "1"))

BACKFILL_DAYS     = 600
DELAY_ANTAR_SAHAM = 1.5
DELAY_ANTAR_HARI  = 0.3
BATCH_SIZE        = 50

# ====================================================================
# LIBUR BURSA
# ====================================================================

LIBUR_BURSA = set([
    # 2024
    "2024-01-01", "2024-02-08", "2024-02-09", "2024-02-10",
    "2024-03-11", "2024-03-29", "2024-04-08", "2024-04-09",
    "2024-04-10", "2024-04-11", "2024-04-12", "2024-05-01",
    "2024-05-09", "2024-05-23", "2024-05-24", "2024-06-17",
    "2024-06-18", "2024-08-17", "2024-10-14", "2024-12-25", "2024-12-26",
    # 2025
    "2025-01-01", "2025-01-27", "2025-01-28", "2025-01-29",
    "2025-03-28", "2025-03-31", "2025-04-01", "2025-04-02",
    "2025-04-03", "2025-04-04", "2025-04-07", "2025-04-18",
    "2025-05-01", "2025-05-12", "2025-05-13", "2025-05-29",
    "2025-06-02", "2025-06-03", "2025-06-04", "2025-08-17",
    "2025-09-05", "2025-12-25", "2025-12-26",
    # 2026
    "2026-01-01", "2026-01-16", "2026-02-16", "2026-02-17",
    "2026-03-18", "2026-03-19", "2026-03-20", "2026-03-23", "2026-03-24",
    "2026-04-03", "2026-05-01", "2026-05-14", "2026-05-15",
    "2026-05-27", "2026-05-28", "2026-06-01", "2026-06-16",
    "2026-08-17", "2026-08-25", "2026-12-24", "2026-12-25",
])

def is_hari_bursa(d):
    if d.weekday() >= 5:
        return False
    if d.strftime("%Y-%m-%d") in LIBUR_BURSA:
        return False
    return True

def get_hari_bursa_range(from_date, to_date):
    result = []
    current = from_date
    while current <= to_date:
        if is_hari_bursa(current):
            result.append(current)
        current += timedelta(days=1)
    return result

# ====================================================================
# UNIVERSE MAP
# ====================================================================

UNIVERSE_MAP = {
    "UJI_COBA":   "TEST",
    "AE_eIPO":    "eIPO", "FJ_eIPO": "eIPO", "KO_eIPO": "eIPO",
    "PT_eIPO":    "eIPO", "UZ_eIPO": "eIPO",
    "AE_IDX":     "IDX",  "FJ_IDX":  "IDX",  "KO_IDX":  "IDX",
    "PT_IDX":     "IDX",  "UZ_IDX":  "IDX",
    "AE_Bfr2016": "Bfr2016", "FJ_Bfr2016": "Bfr2016", "KO_Bfr2016": "Bfr2016",
    "PT_Bfr2016": "Bfr2016", "UZ_Bfr2016": "Bfr2016",
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
# HELPERS FORMAT
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
        return round(float(amount) / 1_000_000_000, 1)
    except:
        return 0

def fmt_date(d):
    return d.strftime("%d/%m/%Y")

def tab_name(universe, d):
    return f"{universe}_{d.strftime('%Y-%m')}"

# ====================================================================
# HEADERS ROW
# ====================================================================

HEADERS_ROW = [
    "Ticker", "Tanggal",
    "Top1_Vol", "Top1_%", "Top1_RpB", "Top1_AccDist",
    "Top3_Vol", "Top3_%", "Top3_RpB", "Top3_AccDist",
    "Top5_Vol", "Top5_%", "Top5_RpB", "Top5_AccDist",
    "Avg_Vol",  "Avg_%",  "Avg_RpB",  "Avg_AccDist",
    "Broker_Buyer", "Broker_Seller", "Broker_#", "Broker_AccDist",
    "NetVol", "NetVal", "Avg_Price"
]

# ====================================================================
# FETCH STOCKBIT
# ====================================================================

def fetch_by_date_range(ticker, from_date, to_date):
    url = (
        f"https://exodus.stockbit.com/marketdetectors/{ticker}"
        f"?from={from_date}&to={to_date}"
        f"&transaction_type=TRANSACTION_TYPE_NET"
        f"&market_board=MARKET_BOARD_REGULER"
        f"&investor_type=INVESTOR_TYPE_ALL"
        f"&limit=25"
    )
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()

# ====================================================================
# TRANSFORM
# ====================================================================

def extract_row(ticker, d, data):
    bd = data.get("bandar_detector", {})

    def vol_fields(key):
        obj = bd.get(key, {})
        return [
            fmt_lot(obj.get("vol", 0)),
            f"{obj.get('percent', 0):.1f}",
            fmt_rp_b(obj.get("amount", 0)),
            obj.get("accdist", "Neutral"),
        ]

    row = [ticker, fmt_date(d)]
    row += vol_fields("top1")
    row += vol_fields("top3")
    row += vol_fields("top5")
    row += vol_fields("avg")
    row += [
        str(bd.get("total_buyer", 0)),
        str(bd.get("total_seller", 0)),
        str(bd.get("number_broker_buysell", 0)),
        bd.get("broker_accdist", "Neutral"),
        fmt_lot(bd.get("volume", 0)),
        fmt_val(bd.get("value", 0)),
        fmt_avg(bd.get("average", 0)),
    ]
    return row

# ====================================================================
# GSPREAD OPERATIONS
# ====================================================================

def get_or_create_tab(spreadsheet, tab):
    try:
        ws = spreadsheet.worksheet(tab)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab, rows=100000, cols=len(HEADERS_ROW))
        ws.append_row(HEADERS_ROW)
        print(f"  Tab baru dibuat: {tab}")
    return ws

def push_rows_to_tab(ws, rows):
    if not rows:
        return
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i+BATCH_SIZE]
        ws.append_rows(chunk, value_input_option="RAW")
        time.sleep(0.5)

# ====================================================================
# CORE LOGIC
# ====================================================================

def process_ticker_dates(ticker, universe, dates, spreadsheet):
    by_month = {}
    for d in dates:
        key = d.strftime("%Y-%m")
        if key not in by_month:
            by_month[key] = []
        by_month[key].append(d)

    total_ok = 0
    total_err = 0

    for ym, month_dates in sorted(by_month.items()):
        tab = tab_name(universe, month_dates[0])
        ws = get_or_create_tab(spreadsheet, tab)
        rows_to_push = []

        for d in month_dates:
            try:
                json_resp = fetch_by_date_range(ticker, str(d), str(d))
                data = json_resp.get("data")
                if not data:
                    continue
                row = extract_row(ticker, d, data)
                rows_to_push.append(row)
                total_ok += 1
            except Exception as e:
                print(f"    {ticker} {fmt_date(d)}: {e}")
                total_err += 1
            time.sleep(DELAY_ANTAR_HARI)

        if rows_to_push:
            push_rows_to_tab(ws, rows_to_push)

    return total_ok, total_err

# ====================================================================
# MODE: BACKFILL
# ====================================================================

def run_backfill(tickers_by_universe, spreadsheet):
    today = date.today()
    from_date = today - timedelta(days=BACKFILL_DAYS)
    all_dates = get_hari_bursa_range(from_date, today)

    print(f"\nBackfill: {fmt_date(from_date)} -> {fmt_date(today)}")
    print(f"Total hari bursa: {len(all_dates)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")
        for i, ticker in enumerate(tickers, 1):
            print(f"[{i:03d}/{len(tickers):03d}] {ticker} ... ", end="", flush=True)
            ok, err = process_ticker_dates(ticker, universe, all_dates, spreadsheet)
            print(f"OK {ok} rows, ERR {err}")
            time.sleep(DELAY_ANTAR_SAHAM)

# ====================================================================
# MODE: DAILY (AUTO CATCH-UP)
# ====================================================================

def run_daily(tickers_by_universe, spreadsheet):
    today = date.today()
    print(f"\nDaily catch-up -> {fmt_date(today)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")

        for i, ticker in enumerate(tickers, 1):
            last_date = None
            for offset in [0, 1]:
                check_month = (today.replace(day=1) - timedelta(days=offset*28)).replace(day=1)
                tab = tab_name(universe, check_month)
                try:
                    ws = spreadsheet.worksheet(tab)
                    all_data = ws.get_all_values()
                    ticker_rows = [r for r in all_data if r and r[0] == ticker]
                    if ticker_rows:
                        last_date = datetime.strptime(ticker_rows[-1][1], "%d/%m/%Y").date()
                        break
                except:
                    continue

            if last_date is None:
                from_date = today - timedelta(days=BACKFILL_DAYS)
                print(f"[{i:03d}] {ticker} -- belum ada data, backfill {BACKFILL_DAYS} hari")
            else:
                from_date = last_date + timedelta(days=1)

            missing_dates = get_hari_bursa_range(from_date, today)

            if not missing_dates:
                print(f"[{i:03d}] {ticker} -- up to date")
                continue

            print(f"[{i:03d}] {ticker} -- catch-up {len(missing_dates)} hari ({fmt_date(missing_dates[0])} -> {fmt_date(missing_dates[-1])}) ... ", end="", flush=True)
            ok, err = process_ticker_dates(ticker, universe, missing_dates, spreadsheet)
            print(f"OK {ok} rows, ERR {err}")
            time.sleep(DELAY_ANTAR_SAHAM)

# ====================================================================
# LOAD TICKERS
# ====================================================================

def load_tickers():
    tickers_by_universe = {}
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if r and r[0].strip()]
        rows = rows[BATCH_INDEX::TOTAL_BATCHES]

    for row in rows:
        ticker = row[0].strip().upper()
        target = re.sub(r"[^a-zA-Z0-9_]", "", row[1].strip()) if len(row) > 1 else ""
        universe = UNIVERSE_MAP.get(target, "UNKNOWN")
        if universe == "UNKNOWN":
            continue
        if universe not in tickers_by_universe:
            tickers_by_universe[universe] = []
        tickers_by_universe[universe].append(ticker)

    return tickers_by_universe

# ====================================================================
# MAIN
# ====================================================================

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  STOCKBIT BROKSUM DB -- MODE: {MODE.upper()}")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")

    if not BEARER_TOKEN:
        print("BEARER_TOKEN tidak diset!")
        exit(1)

    print("Connecting ke Google Sheets ...")
    gc = gspread.service_account(filename=CREDENTIALS)

    if not BROKSUM_DB_ID:
        if not YOUR_EMAIL:
            print("YOUR_EMAIL tidak diset! (dibutuhkan untuk share spreadsheet baru)")
            exit(1)
        print("BROKSUM_DB_ID tidak diset -- membuat spreadsheet baru ...")
        spreadsheet = gc.create("Broksum_DB")
        spreadsheet.share(YOUR_EMAIL, perm_type="user", role="writer")
        print(f"\n{'='*60}")
        print(f"  Spreadsheet baru berhasil dibuat!")
        print(f"  ID   : {spreadsheet.id}")
        print(f"  URL  : https://docs.google.com/spreadsheets/d/{spreadsheet.id}")
        print(f"\n  PENTING: Simpan ID ini!")
        print(f"  export BROKSUM_DB_ID=\"{spreadsheet.id}\"")
        print(f"{'='*60}\n")
    else:
        spreadsheet = gc.open_by_key(BROKSUM_DB_ID)
        print(f"Connected: {spreadsheet.title}\n")

    tickers_by_universe = load_tickers()
    total = sum(len(v) for v in tickers_by_universe.values())
    print(f"Batch {BATCH_INDEX+1}/{TOTAL_BATCHES} -- {total} ticker\n")

    if MODE == "backfill":
        run_backfill(tickers_by_universe, spreadsheet)
    else:
        run_daily(tickers_by_universe, spreadsheet)

    print(f"\n{'='*60}")
    print(f"  SELESAI -- {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")
