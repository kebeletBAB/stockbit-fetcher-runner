"""
OHLC DB — Fetch OHLC via yfinance → Google Sheet
=================================================
Modes: backfill | daily | gap_scan

Env vars:
  BEARER_TOKEN  - tidak dipakai, tapi dibiarkan untuk konsistensi
  OHLC_DB_ID    - Google Spreadsheet ID (kosong = auto create)
  YOUR_EMAIL    - Email untuk share spreadsheet baru
  CREDENTIALS   - path credentials.json (default: credentials.json)
  CSV_FILE      - queue CSV (default: queue_full_eipo-idx-bfr2016.csv)
  MODE          - backfill | daily | gap_scan (default: daily)
  UNIVERSE      - IDX | eIPO | Bfr2016 | kosong = semua
  BATCH_INDEX   - untuk parallel (default: 0)
  TOTAL_BATCHES - untuk parallel (default: 1)

Tab naming: IDX_2026-06, eIPO_2026-06, Bfr2016_2026-06
Format: Ticker | Tanggal | Open | High | Low | Close | Volume
"""

import yfinance as yf
import gspread
import csv
import time
import os
import re
from datetime import datetime, date, timedelta

# ====================================================================
# CONFIG
# ====================================================================

OHLC_DB_ID    = os.environ.get("OHLC_DB_ID", "")
YOUR_EMAIL    = os.environ.get("YOUR_EMAIL", "")
CREDENTIALS   = os.environ.get("CREDENTIALS", "credentials.json")
CSV_FILE      = os.environ.get("CSV_FILE", "queue_full_eipo-idx-bfr2016.csv")
MODE          = os.environ.get("MODE", "daily")
BATCH_INDEX   = int(os.environ.get("BATCH_INDEX", "0"))
TOTAL_BATCHES = int(os.environ.get("TOTAL_BATCHES", "1"))

BACKFILL_DAYS     = 600
DELAY_ANTAR_SAHAM = 0.5  # yfinance lebih ringan dari Stockbit API
BATCH_SIZE        = 50

# ====================================================================
# LIBUR BURSA (sama dengan broksum_db)
# ====================================================================

LIBUR_BURSA = set([
    "2024-01-01", "2024-02-08", "2024-02-09", "2024-02-10",
    "2024-03-11", "2024-03-29", "2024-04-08", "2024-04-09",
    "2024-04-10", "2024-04-11", "2024-04-12", "2024-05-01",
    "2024-05-09", "2024-05-23", "2024-05-24", "2024-06-17",
    "2024-06-18", "2024-08-17", "2024-10-14", "2024-12-25", "2024-12-26",
    "2025-01-01", "2025-01-27", "2025-01-28", "2025-01-29",
    "2025-03-28", "2025-03-31", "2025-04-01", "2025-04-02",
    "2025-04-03", "2025-04-04", "2025-04-07", "2025-04-18",
    "2025-05-01", "2025-05-12", "2025-05-13", "2025-05-29",
    "2025-06-02", "2025-06-03", "2025-06-04", "2025-08-17",
    "2025-09-05", "2025-12-25", "2025-12-26",
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
# HELPERS
# ====================================================================

def fmt_date(d):
    return d.strftime("%d/%m/%Y")

def tab_name(universe, d):
    return f"{universe}_{d.strftime('%Y-%m')}"

HEADERS_ROW = ["Ticker", "Tanggal", "Open", "High", "Low", "Close", "Volume"]

# ====================================================================
# FETCH OHLC via yfinance
# ====================================================================

def fetch_ohlc(ticker, from_date, to_date):
    """
    Fetch OHLC dari Yahoo Finance.
    Ticker IDX format: AADI.JK
    Return: list of [Ticker, Tanggal, Open, High, Low, Close, Volume]
    """
    yf_ticker = f"{ticker}.JK"
    # yfinance end date exclusive, tambah 1 hari
    df = yf.download(
        yf_ticker,
        start=str(from_date),
        end=str(to_date + timedelta(days=1)),
        progress=False,
        auto_adjust=True,
    )

    if df.empty:
        return []

    # Flatten MultiIndex columns (yfinance terbaru)
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = df.columns.get_level_values(0)

    rows = []
    for idx, row in df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        if not is_hari_bursa(d):
            continue
        try:
            rows.append([
                ticker,
                fmt_date(d),
                round(float(row["Open"]), 0),
                round(float(row["High"]), 0),
                round(float(row["Low"]), 0),
                round(float(row["Close"]), 0),
                int(float(row["Volume"])),
            ])
        except Exception as e:
            continue

    return rows

# ====================================================================
# GSPREAD OPERATIONS
# ====================================================================

def get_or_create_tab(spreadsheet, tab):
    for attempt in range(5):
        try:
            return spreadsheet.worksheet(tab)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=tab, rows=15000, cols=len(HEADERS_ROW))
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
    raise Exception(f"Gagal akses tab {tab}")

def sanitize_row(row):
    """V1.1 (9 Jul 2026 FIX): yfinance kadang balikin NaN (float) buat
    ticker yang suspend/gak ada transaksi di hari itu -- NaN BUKAN JSON
    valid, dan gspread.append_rows() ngirim payload via JSON, jadi crash
    TOTAL (ValueError: Out of range float values are not JSON compliant)
    kalau ada 1 aja NaN di 1 baris. Ganti NaN jadi string kosong sebelum
    dikirim -- jangan sampai 1 ticker bermasalah menjatuhkan seluruh batch."""
    clean = []
    for v in row:
        if isinstance(v, float) and v != v:  # NaN != NaN, cara cek tanpa import math
            clean.append("")
        else:
            clean.append(v)
    return clean

def push_rows_to_tab(ws, rows):
    if not rows:
        return
    rows = [sanitize_row(r) for r in rows]
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i+BATCH_SIZE]
        for attempt in range(5):
            try:
                ws.append_rows(chunk, value_input_option="RAW")
                time.sleep(0.3)
                break
            except gspread.exceptions.APIError as e:
                if "429" in str(e):
                    wait = (attempt + 1) * 30
                    print(f"  Rate limit push, tunggu {wait}s ...")
                    time.sleep(wait)
                else:
                    raise

# ====================================================================
# CORE: PROCESS TICKER DATE RANGE
# ====================================================================

def process_ticker(ticker, from_date, to_date, spreadsheet):
    """Fetch OHLC ticker untuk range, group per bulan, push ke sheet"""
    try:
        all_rows = fetch_ohlc(ticker, from_date, to_date)
    except Exception as e:
        print(f"  ERROR fetch {ticker}: {e}")
        return 0, 1

    if not all_rows:
        return 0, 0

    # Group per bulan
    by_month = {}
    for row in all_rows:
        d = datetime.strptime(row[1], "%d/%m/%Y").date()
        key = d.strftime("%Y-%m")
        if key not in by_month:
            by_month[key] = []
        by_month[key].append(row)

    total = 0
    for ym, month_rows in sorted(by_month.items()):
        d0 = datetime.strptime(month_rows[0][1], "%d/%m/%Y").date()
        universe = None
        # Tentukan universe dari spreadsheet context (dipass dari caller)
        tab = f"__placeholder__{ym}"  # dioverride di caller
        total += len(month_rows)

    return len(all_rows), 0

def process_ticker_full(ticker, universe, from_date, to_date, spreadsheet):
    """Fetch + push ke tab yang benar"""
    try:
        all_rows = fetch_ohlc(ticker, from_date, to_date)
    except Exception as e:
        print(f"\n  ERROR fetch {ticker}: {e}")
        return 0, 1

    if not all_rows:
        return 0, 0

    # Group per bulan
    by_month = {}
    for row in all_rows:
        d = datetime.strptime(row[1], "%d/%m/%Y").date()
        key = d.strftime("%Y-%m")
        if key not in by_month:
            by_month[key] = []
        by_month[key].append(row)

    total_ok, total_err = 0, 0
    for ym, month_rows in sorted(by_month.items()):
        d0 = datetime.strptime(month_rows[0][1], "%d/%m/%Y").date()
        tab = tab_name(universe, d0)
        try:
            ws = get_or_create_tab(spreadsheet, tab)
            push_rows_to_tab(ws, month_rows)
            total_ok += len(month_rows)
        except Exception as e:
            # V1.1 FIX: jangan sampai 1 ticker/bulan gagal push (NaN, 429
            # yang gak ke-retry, dll) menjatuhkan SELURUH batch -- skip
            # bulan ini, lanjut ke ticker berikutnya.
            print(f"\n  ERROR push {ticker} bulan {ym}: {e} -- skip, lanjut")
            total_err += len(month_rows)

    return total_ok, total_err

# ====================================================================
# MODE: BACKFILL
# ====================================================================

def run_backfill(tickers_by_universe, spreadsheet):
    today = date.today()
    from_date = today - timedelta(days=BACKFILL_DAYS)
    target_universe = os.environ.get("UNIVERSE", "")

    progress_file = f"ohlc_progress_{target_universe or 'ALL'}.txt"
    done_tickers = set()
    if os.path.exists(progress_file):
        with open(progress_file) as f:
            done_tickers = set(line.strip() for line in f if line.strip())
        print(f"Resume: {len(done_tickers)} ticker sudah selesai")

    print(f"\nBackfill: {fmt_date(from_date)} -> {fmt_date(today)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        if target_universe and universe != target_universe:
            continue
        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")

        for i, ticker in enumerate(tickers, 1):
            key = f"{universe}:{ticker}"
            if key in done_tickers:
                print(f"[{i:03d}/{len(tickers):03d}] {ticker} -- skip")
                continue

            print(f"[{i:03d}/{len(tickers):03d}] {ticker} ... ", end="", flush=True)
            ok, err = process_ticker_full(ticker, universe, from_date, today, spreadsheet)
            print(f"OK {ok} rows, ERR {err}")

            with open(progress_file, "a") as f:
                f.write(f"{key}\n")

            time.sleep(DELAY_ANTAR_SAHAM)

    print(f"\nBackfill selesai!")

# ====================================================================
# MODE: DAILY (AUTO CATCH-UP)
# ====================================================================

def run_daily(tickers_by_universe, spreadsheet):
    today = date.today()
    target_universe = os.environ.get("UNIVERSE", "")

    print(f"\nDaily catch-up -> {fmt_date(today)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        if target_universe and universe != target_universe:
            continue
        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")

        for i, ticker in enumerate(tickers, 1):
            # Cek last date di sheet
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

            from_date = (last_date + timedelta(days=1)) if last_date else (today - timedelta(days=BACKFILL_DAYS))

            if from_date > today:
                print(f"[{i:03d}] {ticker} -- up to date")
                continue

            print(f"[{i:03d}] {ticker} ... ", end="", flush=True)
            ok, err = process_ticker_full(ticker, universe, from_date, today, spreadsheet)
            print(f"OK {ok} rows, ERR {err}")
            time.sleep(DELAY_ANTAR_SAHAM)

# ====================================================================
# MODE: GAP SCAN
# ====================================================================

def run_gap_scan(tickers_by_universe, spreadsheet):
    today = date.today()
    from_date = today - timedelta(days=BACKFILL_DAYS)
    all_dates = set(str(d) for d in get_hari_bursa_range(from_date, today))
    target_universe = os.environ.get("UNIVERSE", "")

    print(f"\nGap Scan: {fmt_date(from_date)} -> {fmt_date(today)}")
    print(f"Expected: {len(all_dates)} hari bursa")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        if target_universe and universe != target_universe:
            continue
        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")

        # Load existing data
        print(f"Loading tabs {universe} ...")
        existing = {}
        for ws in spreadsheet.worksheets():
            if not ws.title.startswith(universe):
                continue
            try:
                rows = ws.get_all_values()
                for row in rows[1:]:
                    if len(row) >= 2 and row[0] and row[1]:
                        t = row[0]
                        try:
                            d = datetime.strptime(row[1], "%d/%m/%Y").date()
                            if t not in existing:
                                existing[t] = set()
                            existing[t].add(str(d))
                        except:
                            pass
                time.sleep(1.0)
            except Exception as e:
                print(f"  Error load {ws.title}: {e}")

        print(f"Loaded. Scanning ...\n")

        for i, ticker in enumerate(tickers, 1):
            ticker_dates = existing.get(ticker, set())
            missing = sorted([date.fromisoformat(d) for d in all_dates if d not in ticker_dates])

            if not missing:
                print(f"[{i:03d}/{len(tickers):03d}] {ticker} -- OK")
                continue

            print(f"[{i:03d}/{len(tickers):03d}] {ticker} -- {len(missing)} gap, fetching ...")
            ok, err = process_ticker_full(ticker, universe, missing[0], missing[-1], spreadsheet)
            print(f"  -> OK {ok} rows, ERR {err}")
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
    print(f"  OHLC DB -- MODE: {MODE.upper()}")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")

    print("Connecting ke Google Sheets ...")
    gc = gspread.service_account(filename=CREDENTIALS)

    if not OHLC_DB_ID:
        if not YOUR_EMAIL:
            print("OHLC_DB_ID dan YOUR_EMAIL tidak diset!")
            exit(1)
        print("Membuat spreadsheet baru: OHLC_DB ...")
        spreadsheet = gc.create("OHLC_DB")
        spreadsheet.share(YOUR_EMAIL, perm_type="user", role="writer")
        print(f"\n  ID   : {spreadsheet.id}")
        print(f"  URL  : https://docs.google.com/spreadsheets/d/{spreadsheet.id}")
        print(f"  --> export OHLC_DB_ID=\"{spreadsheet.id}\"\n")
    else:
        spreadsheet = gc.open_by_key(OHLC_DB_ID)
        print(f"Connected: {spreadsheet.title}\n")

    tickers_by_universe = load_tickers()
    total = sum(len(v) for v in tickers_by_universe.values())
    print(f"Batch {BATCH_INDEX+1}/{TOTAL_BATCHES} -- {total} ticker\n")

    if MODE == "backfill":
        run_backfill(tickers_by_universe, spreadsheet)
    elif MODE == "gap_scan":
        run_gap_scan(tickers_by_universe, spreadsheet)
    else:
        run_daily(tickers_by_universe, spreadsheet)

    print(f"\n{'='*60}")
    print(f"  SELESAI -- {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")
