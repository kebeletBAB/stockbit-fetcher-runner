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
CREDENTIALS   = os.environ.get("CREDENTIALS", "credentials.json")
YOUR_EMAIL    = os.environ.get("YOUR_EMAIL", "")       # Email untuk share spreadsheet baru

# 1 spreadsheet per universe (hindari 10M cell limit)
BROKSUM_DB_IDS = {
    "IDX":     os.environ.get("BROKSUM_DB_IDX", ""),
    "eIPO":    os.environ.get("BROKSUM_DB_EIPO", ""),
    "Bfr2016": os.environ.get("BROKSUM_DB_BFR2016", ""),
}

BATCH_INDEX   = int(os.environ.get("BATCH_INDEX", "0"))
TOTAL_BATCHES = int(os.environ.get("TOTAL_BATCHES", "1"))

BACKFILL_DAYS     = 600
DELAY_ANTAR_SAHAM = 3.0
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
    for attempt in range(5):
        try:
            ws = spreadsheet.worksheet(tab)
            return ws
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=tab, rows=5000, cols=len(HEADERS_ROW))
            ws.append_row(HEADERS_ROW)
            print(f"  Tab baru dibuat: {tab}")
            return ws
        except gspread.exceptions.APIError as e:
            if "429" in str(e):
                wait = (attempt + 1) * 30
                print(f"  Rate limit, tunggu {wait}s ...")
                time.sleep(wait)
            else:
                raise
    raise Exception(f"Gagal akses tab {tab} setelah 5 percobaan")

def push_rows_to_tab(ws, rows):
    if not rows:
        return
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i+BATCH_SIZE]
        for attempt in range(5):
            try:
                ws.append_rows(chunk, value_input_option="RAW")
                time.sleep(0.5)
                break
            except gspread.exceptions.APIError as e:
                if "429" in str(e):
                    wait = (attempt + 1) * 30
                    print(f"  Rate limit push, tunggu {wait}s ...")
                    time.sleep(wait)
                else:
                    raise

# ====================================================================
# CORE LOGIC
# ====================================================================

def process_ticker_dates(ticker, universe, dates, spreadsheets):
    # Pilih spreadsheet sesuai universe
    spreadsheet = spreadsheets.get(universe)
    if not spreadsheet:
        print(f"    Spreadsheet untuk {universe} tidak diset, skip")
        return 0, 0

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
            for attempt in range(3):
                try:
                    json_resp = fetch_by_date_range(ticker, str(d), str(d))
                    data = json_resp.get("data")
                    if not data:
                        break
                    row = extract_row(ticker, d, data)
                    rows_to_push.append(row)
                    total_ok += 1
                    break
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "Rate limit" in err_str.lower():
                        wait = (attempt + 1) * 60
                        print(f"\n  Rate limit, tunggu {wait}s ...")
                        time.sleep(wait)
                    elif attempt < 2:
                        time.sleep(5)
                    else:
                        print(f"\n    {ticker} {fmt_date(d)}: {e}")
                        total_err += 1
            time.sleep(DELAY_ANTAR_HARI)

        if rows_to_push:
            push_rows_to_tab(ws, rows_to_push)

    return total_ok, total_err

# ====================================================================
# MODE: BACKFILL
# ====================================================================

def run_backfill(tickers_by_universe, spreadsheets):
    today = date.today()
    from_date = today - timedelta(days=BACKFILL_DAYS)
    all_dates = get_hari_bursa_range(from_date, today)

    # Filter universe kalau UNIVERSE env var diset
    target_universe = os.environ.get("UNIVERSE", "")

    # Progress file untuk resume kalau error
    progress_file = f"backfill_progress_{target_universe or 'ALL'}.txt"
    done_tickers = set()
    if os.path.exists(progress_file):
        with open(progress_file) as f:
            done_tickers = set(line.strip() for line in f if line.strip())
        print(f"Resume dari progress file: {len(done_tickers)} ticker sudah selesai")

    print(f"\nBackfill: {fmt_date(from_date)} -> {fmt_date(today)}")
    print(f"Total hari bursa: {len(all_dates)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        if target_universe and universe != target_universe:
            print(f"Skip {universe} (UNIVERSE={target_universe})")
            continue

        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")
        for i, ticker in enumerate(tickers, 1):
            key = f"{universe}:{ticker}"
            if key in done_tickers:
                print(f"[{i:03d}/{len(tickers):03d}] {ticker} -- skip (sudah selesai)")
                continue

            print(f"[{i:03d}/{len(tickers):03d}] {ticker} ... ", end="", flush=True)
            ok, err = process_ticker_dates(ticker, universe, all_dates, spreadsheets)
            print(f"OK {ok} rows, ERR {err}")

            # Catat progress
            with open(progress_file, "a") as f:
                f.write(f"{key}\n")

            time.sleep(DELAY_ANTAR_SAHAM)

    print(f"\nBackfill selesai! Progress file: {progress_file}")


# ====================================================================
# MODE: GAP SCAN (strict zero gap)
# ====================================================================

def run_gap_scan(tickers_by_universe, spreadsheets):
    """
    Scan semua tanggal per ticker, fetch yang missing di tengah.
    Lebih lambat dari daily tapi strict zero gap.
    """
    today = date.today()
    from_date = today - timedelta(days=BACKFILL_DAYS)
    all_dates = set(str(d) for d in get_hari_bursa_range(from_date, today))

    target_universe = os.environ.get("UNIVERSE", "")

    print(f"\nGap Scan: {fmt_date(from_date)} -> {fmt_date(today)}")
    print(f"Total hari bursa expected: {len(all_dates)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        if target_universe and universe != target_universe:
            continue

        ss = spreadsheets.get(universe)
        if not ss:
            print(f"Skip {universe} - spreadsheet tidak diset")
            continue

        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")

        # Load semua data dari semua tab universe ini sekaligus
        print(f"Loading semua tab {universe} ...")
        existing = {}  # {ticker: set of date strings}
        for ws in ss.worksheets():
            time.sleep(1.0)  # hindari rate limit saat load tab
            if not ws.title.startswith(universe):
                continue
            for attempt in range(5):
                try:
                    rows = ws.get_all_values()
                    for row in rows[1:]:  # skip header
                        if len(row) >= 2 and row[0] and row[1]:
                            ticker = row[0]
                            try:
                                d = datetime.strptime(row[1], "%d/%m/%Y").date()
                                if ticker not in existing:
                                    existing[ticker] = set()
                                existing[ticker].add(str(d))
                            except:
                                pass
                    break
                except gspread.exceptions.APIError as e:
                    if "429" in str(e):
                        wait = (attempt + 1) * 30
                        print(f"  Rate limit load tab {ws.title}, tunggu {wait}s ...")
                        time.sleep(wait)
                    else:
                        print(f"  Error load tab {ws.title}: {e}")
                        break
                except Exception as e:
                    print(f"  Error load tab {ws.title}: {e}")
                    break

        print(f"Data loaded. Scanning gaps ...\n")

        total_gap = 0
        for i, ticker in enumerate(tickers, 1):
            ticker_dates = existing.get(ticker, set())
            missing = sorted([
                date.fromisoformat(d)
                for d in all_dates
                if d not in ticker_dates
            ])

            if not missing:
                print(f"[{i:03d}/{len(tickers):03d}] {ticker} -- OK (no gap)")
                continue

            print(f"[{i:03d}/{len(tickers):03d}] {ticker} -- {len(missing)} gap dates, fetching ...")
            total_gap += len(missing)
            ok, err = process_ticker_dates(ticker, universe, missing, spreadsheets)
            print(f"  -> OK {ok} rows, ERR {err}")
            time.sleep(DELAY_ANTAR_SAHAM)

        print(f"\n{universe} gap scan selesai. Total gap filled: {total_gap}")

# ====================================================================
# MODE: DAILY (AUTO CATCH-UP)
# ====================================================================

def run_daily(tickers_by_universe, spreadsheets):
    today = date.today()
    target_universe = os.environ.get("UNIVERSE", "")
    print(f"\nDaily catch-up -> {fmt_date(today)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        if target_universe and universe != target_universe:
            continue
        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")

        for i, ticker in enumerate(tickers, 1):
            last_date = None
            ss = spreadsheets.get(universe)
            if ss:
                for offset in [0, 1]:
                    check_month = (today.replace(day=1) - timedelta(days=offset*28)).replace(day=1)
                    tab = tab_name(universe, check_month)
                    try:
                        ws = ss.worksheet(tab)
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
            ok, err = process_ticker_dates(ticker, universe, missing_dates, spreadsheets)
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

    # Connect atau auto-create per universe
    spreadsheets = {}
    universe_names = {
        "IDX":     ("BROKSUM_DB_IDX",     "Broksum_DB_IDX"),
        "eIPO":    ("BROKSUM_DB_EIPO",    "Broksum_DB_eIPO"),
        "Bfr2016": ("BROKSUM_DB_BFR2016", "Broksum_DB_Bfr2016"),
    }

    target_universe = os.environ.get("UNIVERSE", "")

    for universe, (env_key, ss_name) in universe_names.items():
        if target_universe and universe != target_universe:
            continue
        ss_id = os.environ.get(env_key, "")
        if ss_id:
            ss = gc.open_by_key(ss_id)
            print(f"Connected: {ss.title}")
            spreadsheets[universe] = ss
        else:
            if not YOUR_EMAIL:
                print(f"  {env_key} tidak diset, skip {universe}")
                continue
            print(f"  Membuat spreadsheet baru: {ss_name} ...")
            ss = gc.create(ss_name)
            ss.share(YOUR_EMAIL, perm_type="user", role="writer")
            spreadsheets[universe] = ss
            print(f"  Dibuat! ID: {ss.id}")
            print(f"  --> export {env_key}=\"{ss.id}\"")

    if not spreadsheets:
        print("Tidak ada spreadsheet yang bisa digunakan!")
        exit(1)

    print()

    tickers_by_universe = load_tickers()
    total = sum(len(v) for v in tickers_by_universe.values())
    print(f"Batch {BATCH_INDEX+1}/{TOTAL_BATCHES} -- {total} ticker\n")

    if MODE == "backfill":
        run_backfill(tickers_by_universe, spreadsheets)
    elif MODE == "gap_scan":
        run_gap_scan(tickers_by_universe, spreadsheets)
    else:
        run_daily(tickers_by_universe, spreadsheets)

    print(f"\n{'='*60}")
    print(f"  SELESAI -- {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")
