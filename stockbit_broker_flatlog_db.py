"""
Stockbit Broker Summary — BROKSUM_BROKER_DB (level individual broker)
========================================================================
Pipeline SATU KELAS dengan stockbit_broksum_db.py (gspread, tab per bulan,
backfill/daily mode) — tapi tangkap level BROKER INDIVIDUAL (kode broker:
MG, YP, TP, dst), bukan cuma agregat Top1/Top3/Top5/Avg.

Kenapa ini perlu (bukan cuma "nice to have"):
  stockbit_broksum_db.py motong tangkapan dari data["bandar_detector"] —
  itu udah teragregasi, gak bisa dijawab "MG akumulasi N hari berturut-turut
  di ticker ini?" karena identitas broker udah ilang begitu jadi Top1/Top3/
  Top5/Avg. Field individual (data["broker_summary"]["brokers_buy"/"_sell"],
  yang punya netbs_broker_code) ADA di response API yang sama, cuma gak
  pernah ditangkap ke spreadsheet manapun.

2 mode, SAMA seperti stockbit_broksum_db.py:
  - BACKFILL : fetch 600 hari ke belakang (one-time, first run)
  - DAILY    : auto catch-up dari last date di Sheet sampai hari ini
  - GAP_SCAN : strict zero gap — load semua tanggal per ticker, isi yang
               bolong DI TENGAH (bukan cuma dari tanggal terakhir), lebih
               lambat (load seluruh tab dulu) tapi strict. Pakai ini kalau
               curiga ada hari yang kelewat gara-gara error/rate-limit
               waktu daily run.

Tab naming: [UNIVERSE]_[YYYY-MM]_Broker   (suffix _Broker biar gak nabrak
  tab [UNIVERSE]_[YYYY-MM] yang udah dipakai stockbit_broksum_db.py)

Format kolom (9 kolom — satu baris = satu broker, satu sisi, satu hari):
  Ticker | Tanggal | Broker | Type | Side | Value | Lot | AvgPrice | Rank

  - Type  : Asing / Lokal / Pemerintah (dari field API "type")
  - Side  : buy / sell
  - Rank  : urutan broker di hari itu (1=top volume), berguna buat filter
            cepat "Top-5 broker doang" tanpa harus re-sort tiap query

Rotasi cell-limit: kalau spreadsheet aktif mendekati limit Google Sheets
  (10jt cell/file), otomatis bikin file baru (BROKSUM_DB_[UNIVERSE]_BROKER_2,
  dst), share ke YOUR_EMAIL, lanjut nulis di situ. Daftar file per universe
  disimpan di broker_db_registry_{universe}.json — JANGAN dihapus manual,
  itu satu-satunya cara script tau file mana aja yang udah dipakai.

Multi-credentials (opsional): kalau mau jalanin 3 proses paralel (1 per
  universe) tanpa berbagi 1 quota Google Sheets API service account, set
  CREDENTIALS_IDX / CREDENTIALS_EIPO / CREDENTIALS_BFR2016 ke file JSON
  service account yang beda-beda. Kalau gak diset, fallback ke CREDENTIALS.

Setup: SAMA PERSIS seperti stockbit_broksum_db.py — reuse credentials.json,
  BEARER_TOKEN, CSV_FILE (queue ticker+universe), DAN spreadsheet ID yang
  sama (BROKSUM_DB_IDX/EIPO/BFR2016) — data broker individual ditulis ke
  TAB TERPISAH di file yang sama, bukan spreadsheet baru.

Jalankan:
  python stockbit_broker_flatlog_db.py

Token expired? Buka stockbit.com -> DevTools -> Network ->
klik request exodus.stockbit.com -> copy Authorization header baru ke BEARER_TOKEN.
"""

import requests
import gspread
import csv
import time
import os
import re
from datetime import datetime, date, timedelta
# V1.1 (9 Jul 2026, GitHub Actions): import oauth_helper dipindah jadi LAZY
# (di dalam _get_oauth_gc(), bukan top-level) -- ini modul cuma dibutuhkan
# kalau spreadsheet aktif kena cell-limit dan perlu create() file baru via
# OAuth akun pribadi. Untuk MODE=gap_scan/daily biasa (termasuk gap kecil
# yang kita fill sekarang) fitur ini TIDAK terpakai, jadi tidak perlu
# oauth_helper.py/oauth_token.json ikut di-commit ke repo GitHub (lagipula
# oauth_token.json isinya token OAuth pribadi, sensitif, tidak boleh masuk
# repo). Kalau nanti beneran kena limit & perlu rotate di GitHub Actions,
# baru perlu setup OAuth headless di sana secara terpisah.

# ====================================================================
# CONFIG
# ====================================================================

BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")
CSV_FILE     = os.environ.get("CSV_FILE", "queue_full_eipo-idx-bfr2016.csv")
MODE         = os.environ.get("MODE", "daily")        # "backfill" / "daily" / "gap_scan"
CREDENTIALS  = os.environ.get("CREDENTIALS", "credentials.json")
# Dipakai buat auto-share spreadsheet BARU saat rotasi cell-limit (poin 3).
# Wajib diisi kalau mau rotate otomatis, atau share manual sendiri nanti.
YOUR_EMAIL_FOR_ROTATE = os.environ.get("YOUR_EMAIL", "")


BATCH_INDEX   = int(os.environ.get("BATCH_INDEX", "0"))
TOTAL_BATCHES = int(os.environ.get("TOTAL_BATCHES", "1"))

BACKFILL_DAYS     = 600
DELAY_ANTAR_SAHAM = 3.0
DELAY_ANTAR_HARI  = 0.3
BATCH_SIZE        = 50

MAX_BROKER_PER_SISI = 25  # API limit=25 (sama seperti script grid) — cukup,
                           # broker di luar top-25 kontribusinya immaterial

# ====================================================================
# LIBUR BURSA — copy persis dari stockbit_broksum_db.py supaya tanggal
# hari bursa konsisten antara BROKSUM_DB dan BROKER_DB (gampang di-join
# nanti kalau perlu cross-reference).
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
# UNIVERSE MAP — sama persis, biar CSV queue yang sama bisa dipakai ulang
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

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "id,en-US;q=0.9,en;q=0.8",
    "authorization": f"Bearer {BEARER_TOKEN}",
    "origin": "https://stockbit.com",
    "referer": "https://stockbit.com/",
    "x-platform": "web",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
}

# V1.2 (9 Jul 2026 FIX -- 429 crash saat gap-scan 9 batch paralel):
# gc.open_by_key() TIDAK punya retry (beda dari get_or_create_tab/
# push_rows_to_tab yang sudah ada), padahal ini juga API call ke Google
# Sheets. Dengan 9 batch buka spreadsheet yang sama nyaris bersamaan
# pakai 1 identitas kredensial per universe, gampang kena "429 Quota
# exceeded -- Read requests per menit per user" dan CRASH TOTAL (exit
# code 1, bukan retry). Root-cause sama persis dengan yang sudah
# diperbaiki di stockbit_unified_daily.py V1.5.
def open_by_key_with_retry(gc, ss_id):
    # V1.3 (9 Jul 2026 FIX): ternyata bukan cuma 429 (quota) yang perlu
    # retry -- gspread/Sheets API kadang balikin 500/503 (internal error
    # transient di sisi Google, bukan soal kuota kita) terutama saat
    # banyak proses baca bersamaan. Perlakukan sama seperti 429: retry
    # dengan backoff, jangan langsung crash.
    RETRYABLE = ("429", "500", "503")
    for attempt in range(6):
        try:
            return gc.open_by_key(ss_id)
        except gspread.exceptions.APIError as e:
            if any(code in str(e) for code in RETRYABLE):
                wait = (attempt + 1) * 20
                print(f"  Error transient ({e}), tunggu {wait}s ...", flush=True)
                time.sleep(wait)
            else:
                raise
    raise Exception(f"Gagal buka spreadsheet {ss_id} setelah 6 percobaan (error transient terus)")

def fmt_date(d):
    return d.strftime("%d/%m/%Y")

def tab_name(universe, d):
    # Suffix "_Broker" — biar gak nabrak tab [UNIVERSE]_[YYYY-MM] yang udah
    # dipakai stockbit_broksum_db.py di spreadsheet yang sama.
    return f"{universe}_{d.strftime('%Y-%m')}_Broker"

HEADERS_ROW = ["Ticker", "Tanggal", "Broker", "Type", "Side", "Value", "Lot", "AvgPrice", "Rank"]

# ====================================================================
# FETCH STOCKBIT — endpoint sama persis dengan pipeline BROKSUM_DB
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
# TRANSFORM — level broker individual, dari broker_summary (bukan
# bandar_detector yang udah teragregasi)
# ====================================================================

def extract_broker_rows(ticker, d, data):
    bs = data.get("broker_summary", {})
    buys  = bs.get("brokers_buy", [])[:MAX_BROKER_PER_SISI]
    sells = bs.get("brokers_sell", [])[:MAX_BROKER_PER_SISI]

    tgl = fmt_date(d)
    rows = []

    for rank, b in enumerate(buys, start=1):
        rows.append([
            ticker, tgl,
            b.get("netbs_broker_code", "-"),
            b.get("type", ""),
            "buy",
            round(float(b.get("bval", 0) or 0), 2),
            int(float(b.get("blot", 0) or 0)),
            round(float(b.get("netbs_buy_avg_price", 0) or 0), 2),
            rank,
        ])

    for rank, s in enumerate(sells, start=1):
        rows.append([
            ticker, tgl,
            s.get("netbs_broker_code", "-"),
            s.get("type", ""),
            "sell",
            round(abs(float(s.get("sval", 0) or 0)), 2),
            int(abs(float(s.get("slot", 0) or 0))),
            round(float(s.get("netbs_sell_avg_price", 0) or 0), 2),
            rank,
        ])

    return rows

# ====================================================================
# GSPREAD OPERATIONS — pola sama dengan stockbit_broksum_db.py
# ====================================================================

RETRYABLE_CODES = ("429", "500", "503")

def is_retryable(e):
    """V1.3 (9 Jul 2026): dipakai di semua tempat yang retry gspread
    APIError -- bukan cuma 429 (quota), tapi juga 500/503 (transient error
    di sisi Google, sering muncul kalau banyak proses baca bersamaan)."""
    return any(code in str(e) for code in RETRYABLE_CODES)

def get_or_create_tab(spreadsheet, tab):
    for attempt in range(5):
        try:
            ws = spreadsheet.worksheet(tab)
            return ws
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=tab, rows=20000, cols=len(HEADERS_ROW))
            ws.append_row(HEADERS_ROW)
            print(f"  Tab baru dibuat: {tab}")
            return ws
        except gspread.exceptions.APIError as e:
            if is_retryable(e):
                wait = (attempt + 1) * 30
                print(f"  Error transient ({e}), tunggu {wait}s ...", flush=True)
                time.sleep(wait)
            else:
                raise
    raise Exception(f"Gagal akses tab {tab} setelah 5 percobaan")

def push_rows_to_tab(ws, rows):
    if not rows:
        return
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        for attempt in range(5):
            try:
                ws.append_rows(chunk, value_input_option="RAW")
                time.sleep(0.5)
                break
            except gspread.exceptions.APIError as e:
                if is_retryable(e):
                    wait = (attempt + 1) * 30
                    print(f"  Error transient push ({e}), tunggu {wait}s ...", flush=True)
                    time.sleep(wait)
                else:
                    raise

# ====================================================================
# CORE LOGIC
# ====================================================================

import json

CELL_LIMIT_SAFETY = 9_200_000  # margin di bawah limit resmi 10jt (ganti ke
                                # 18.5jt kalau org lo udah masuk beta 20jt cell)

class SpreadsheetRegistry:
    """Kelola rotasi spreadsheet per universe. File registry
    (broker_db_registry_{universe}.json) simpen daftar spreadsheet ID
    urut lama->baru. Yang PALING BARU = 'active' (tempat nulis data baru).
    Saat active penuh (cell usage > CELL_LIMIT_SAFETY), auto-create
    spreadsheet baru, share ke YOUR_EMAIL, tambah ke registry.

    PENTING (fix setelah insiden crash berulang di BBNI): _cell_usage()
    manggil ss.worksheets() -- API call MAHAL. Kalau dipanggil tiap bulan
    x tiap ticker (ribuan kali buat backfill 429 ticker x 21 bulan), itu
    gampang numbuk rate limit dan CRASH TOTAL kalau gak di-retry. Sekarang:
    (1) throttle -- cuma re-check tiap CHECK_EVERY_N_CALLS panggilan,
    (2) dibungkus retry+fallback -- kalau gagal cek (rate limit dll),
        JANGAN crash, pakai spreadsheet aktif yang lama & lanjut jalan.
    """
    CHECK_EVERY_N_CALLS = 25

    def __init__(self, universe, gc, base_ss, your_email, cred_file=None):
        self.universe = universe
        self.gc = gc  # service account client -- dipakai buat BACA/TULIS data
        self._oauth_gc = None  # lazy-loaded, cuma dipakai buat CREATE spreadsheet baru
        self.your_email = your_email
        # FIX 2026-07-04: self.gc.auth.service_account_email crash di versi
        # gspread yang dipakai ('Client' object has no attribute 'auth').
        # Ambil client_email LANGSUNG dari file credentials JSON -- lebih
        # robust, gak bergantung struktur internal gspread yang bisa beda
        # tiap versi.
        self.service_account_email = None
        if cred_file and os.path.exists(cred_file):
            try:
                with open(cred_file) as f:
                    self.service_account_email = json.load(f).get("client_email")
            except Exception as e:
                print(f"  [{universe}] Gagal baca client_email dari {cred_file}: {e}")
        self.registry_file = f"broker_db_registry_{universe}.json"
        self.ss_cache = {}  # {id: gspread Spreadsheet object}
        self._call_count = 0
        self._last_known_ok = True

        if os.path.exists(self.registry_file):
            with open(self.registry_file) as f:
                self.ids = json.load(f)
        else:
            self.ids = [base_ss.id]
            self._save()

        self.ss_cache[base_ss.id] = base_ss

    def _get_oauth_gc(self):
        """Lazy-load OAuth client (akun pribadi, kuota besar) -- dipakai
        KHUSUS saat create() spreadsheet baru, supaya file baru dimiliki
        akun pribadi (kuota besar), bukan service account (kuota 0, gak
        bisa nyimpen file sendiri di My Drive)."""
        if self._oauth_gc is None:
            from oauth_helper import get_oauth_gspread_client
            self._oauth_gc = get_oauth_gspread_client()
        return self._oauth_gc

    def _save(self):
        with open(self.registry_file, "w") as f:
            json.dump(self.ids, f)

    def _open(self, ss_id):
        if ss_id not in self.ss_cache:
            self.ss_cache[ss_id] = open_by_key_with_retry(self.gc, ss_id)
        return self.ss_cache[ss_id]

    def _cell_usage(self, ss):
        """Total sel terpakai (row_count x col_count semua tab -- pakai
        grid size bukan isi aktual, tapi itu yang dihitung Sheets ke limit).
        Return None kalau gagal (rate limit dll) -- JANGAN raise, biar
        caller bisa fallback tanpa crash."""
        for attempt in range(3):
            try:
                total = 0
                for ws in ss.worksheets():
                    total += ws.row_count * ws.col_count
                return total
            except gspread.exceptions.APIError as e:
                if is_retryable(e):
                    wait = (attempt + 1) * 20
                    print(f"  [{self.universe}] Error transient cek cell usage ({e}), tunggu {wait}s ...", flush=True)
                    time.sleep(wait)
                else:
                    print(f"  [{self.universe}] Error cek cell usage: {e} -- skip cek, pakai file aktif")
                    return None
            except Exception as e:
                print(f"  [{self.universe}] Error cek cell usage: {e} -- skip cek, pakai file aktif")
                return None
        print(f"  [{self.universe}] Gagal cek cell usage setelah 3x -- skip cek, pakai file aktif")
        return None

    def get_active(self):
        """Return spreadsheet aktif buat nulis. Auto-rotate kalau penuh.
        Cek cell usage DI-THROTTLE (bukan tiap panggilan) dan gak pernah
        crash proses -- kalau cek gagal, lanjut pakai spreadsheet aktif."""
        active_id = self.ids[-1]
        ss = self._open(active_id)

        self._call_count += 1
        if self._call_count % self.CHECK_EVERY_N_CALLS != 0:
            return ss  # skip cek, hemat API call

        usage = self._cell_usage(ss)
        if usage is None or usage < CELL_LIMIT_SAFETY:
            return ss  # aman, atau gagal cek -> asumsikan aman, lanjut

        print(f"\n  [{self.universe}] Spreadsheet aktif ({ss.title}) sudah "
              f"{usage:,} cell, bikin file baru ...")
        try:
            # PENTING: create() pakai OAuth client (akun pribadi, kuota
            # besar) -- BUKAN self.gc (service account, kuota 0, gagal
            # dengan 403 storageQuotaExceeded kalau dipakai create()).
            # File baru otomatis dimiliki akun pribadi, lalu di-share
            # balik ke service account supaya proses tetap bisa nulis.
            oauth_gc = self._get_oauth_gc()
            new_ss = oauth_gc.create(f"BROKSUM_DB_{self.universe}_BROKER_{len(self.ids)+1}")

            # share balik ke service account (biar proses ini, yang login
            # sbg service account, tetap bisa nulis ke file baru)
            service_account_email = self.service_account_email
            if not service_account_email:
                raise RuntimeError(
                    "service_account_email tidak diketahui (cred_file gak "
                    "kebaca) -- gak bisa share file baru ke service account"
                )
            new_ss.share(service_account_email, perm_type="user", role="writer")

            if self.your_email:
                new_ss.share(self.your_email, perm_type="user", role="writer")

            self.ids.append(new_ss.id)
            self._save()
            # buka via service account client (self.gc) supaya konsisten
            # dgn pola ss_cache yang lain -- file udah writable krn di-share
            self.ss_cache[new_ss.id] = self.gc.open_by_key(new_ss.id)
            print(f"  File baru dibuat via OAuth (akun pribadi): {new_ss.title} (ID: {new_ss.id})")
            print(f"  Di-share ke service account: {service_account_email}")
            print(f"  Registry tersimpan: {self.registry_file}\n")
            return self.ss_cache[new_ss.id]
        except Exception as e:
            print(f"  [{self.universe}] Gagal bikin file baru ({e}) -- lanjut pakai file lama, "
                  f"RISIKO kena limit, cek manual nanti\n")
            return ss

    def all_spreadsheets(self):
        """Semua spreadsheet dalam registry — dipakai buat CARI data lama
        (misal daily catch-up perlu tau tanggal terakhir, bisa ada di file
        manapun, bukan cuma yang aktif)."""
        return [self._open(i) for i in self.ids]


def process_ticker_dates(ticker, universe, dates, registries):
    registry = registries.get(universe)
    if not registry:
        print(f"    Registry BROKER_DB untuk {universe} tidak diset, skip")
        return 0, 0

    by_month = {}
    for d in dates:
        key = d.strftime("%Y-%m")
        by_month.setdefault(key, []).append(d)

    total_ok, total_err = 0, 0

    for ym, month_dates in sorted(by_month.items()):
        try:
            spreadsheet = registry.get_active()  # cek/rotate cell limit di sini
            tab = tab_name(universe, month_dates[0])
            ws = get_or_create_tab(spreadsheet, tab)
        except Exception as e:
            print(f"\n    [{ticker}] Gagal akses tab bulan {ym}: {e} -- skip bulan ini, lanjut")
            total_err += len(month_dates)
            continue

        rows_to_push = []

        for d in month_dates:
            for attempt in range(3):
                try:
                    json_resp = fetch_by_date_range(ticker, str(d), str(d))
                    data = json_resp.get("data")
                    if not data:
                        break
                    rows = extract_broker_rows(ticker, d, data)
                    rows_to_push.extend(rows)
                    total_ok += 1
                    break
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "rate limit" in err_str.lower():
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
            try:
                push_rows_to_tab(ws, rows_to_push)
            except Exception as e:
                print(f"\n    [{ticker}] Gagal push {len(rows_to_push)} baris bulan {ym}: {e} -- lanjut ke bulan berikutnya")
                total_err += len(month_dates)

    return total_ok, total_err

# ====================================================================
# MODE: BACKFILL
# ====================================================================

def run_backfill(tickers_by_universe, registries):
    today = date.today()
    from_date = today - timedelta(days=BACKFILL_DAYS)
    all_dates = get_hari_bursa_range(from_date, today)

    target_universe = os.environ.get("UNIVERSE", "")
    progress_file = f"broker_backfill_progress_{target_universe or 'ALL'}.txt"
    done_tickers = set()
    if os.path.exists(progress_file):
        with open(progress_file) as f:
            done_tickers = set(line.strip() for line in f if line.strip())
        print(f"Resume dari progress file: {len(done_tickers)} ticker sudah selesai")

    print(f"\nBackfill BROKER_DB: {fmt_date(from_date)} -> {fmt_date(today)}")
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
            try:
                ok, err = process_ticker_dates(ticker, universe, all_dates, registries)
                print(f"OK {ok} hari, ERR {err}")
            except Exception as e:
                print(f"CRASH tak terduga: {e} -- SKIP ticker ini, lanjut ke berikutnya "
                      f"(ticker ini TIDAK ditandai selesai, bakal dicoba lagi run berikutnya)")
                time.sleep(DELAY_ANTAR_SAHAM)
                continue

            with open(progress_file, "a") as f:
                f.write(f"{key}\n")

            time.sleep(DELAY_ANTAR_SAHAM)

    print(f"\nBackfill BROKER_DB selesai! Progress file: {progress_file}")

# ====================================================================
# MODE: DAILY (AUTO CATCH-UP)
# ====================================================================

def run_daily(tickers_by_universe, registries):
    today = date.today()
    target_universe = os.environ.get("UNIVERSE", "")
    print(f"\nDaily catch-up BROKER_DB -> {fmt_date(today)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        if target_universe and universe != target_universe:
            continue
        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")

        registry = registries.get(universe)

        for i, ticker in enumerate(tickers, 1):
            last_date = None
            if registry:
                # cek tab bulan ini/bulan lalu di SEMUA file registry (data
                # ticker bisa ada di file manapun tergantung kapan dia
                # ditulis, bukan cuma file yang lagi aktif sekarang)
                for ss in reversed(registry.all_spreadsheets()):
                    for offset in [0, 1]:
                        check_month = (today.replace(day=1) - timedelta(days=offset * 28)).replace(day=1)
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
                    if last_date:
                        break

            if last_date is None:
                from_date = today - timedelta(days=BACKFILL_DAYS)
                print(f"[{i:03d}] {ticker} -- belum ada data, backfill {BACKFILL_DAYS} hari")
            else:
                from_date = last_date + timedelta(days=1)

            missing_dates = get_hari_bursa_range(from_date, today)
            if not missing_dates:
                print(f"[{i:03d}] {ticker} -- up to date")
                continue

            print(f"[{i:03d}] {ticker} -- catch-up {len(missing_dates)} hari "
                  f"({fmt_date(missing_dates[0])} -> {fmt_date(missing_dates[-1])}) ... ", end="", flush=True)
            ok, err = process_ticker_dates(ticker, universe, missing_dates, registries)
            print(f"OK {ok} hari, ERR {err}")
            time.sleep(DELAY_ANTAR_SAHAM)

# ====================================================================
# MODE: GAP SCAN (strict zero gap)
# ====================================================================
# Beda dari run_daily: run_daily cuma cek tanggal TERAKHIR yang ada di
# sheet, lalu catch-up dari situ ke hari ini — kalau ada bolong DI TENGAH
# (misal gagal fetch suatu hari terus kelewat), run_daily gak bakal
# nyadar. Gap scan load SEMUA tanggal yang ada per ticker, banding sama
# expected hari bursa penuh, terus isi yang bolong di mana pun posisinya.
# Lebih lambat (load seluruh tab dulu), tapi strict zero gap.
def run_gap_scan(tickers_by_universe, registries):
    today = date.today()
    from_date = today - timedelta(days=BACKFILL_DAYS)
    all_dates = set(str(d) for d in get_hari_bursa_range(from_date, today))

    target_universe = os.environ.get("UNIVERSE", "")

    print(f"\nGap Scan BROKER_DB: {fmt_date(from_date)} -> {fmt_date(today)}")
    print(f"Total hari bursa expected: {len(all_dates)}")
    print(f"{'='*60}\n")

    for universe, tickers in sorted(tickers_by_universe.items()):
        if target_universe and universe != target_universe:
            continue

        registry = registries.get(universe)
        if not registry:
            print(f"Skip {universe} - registry tidak diset")
            continue

        print(f"\n--- {universe} ({len(tickers)} ticker) ---\n")

        # Load semua data dari SEMUA spreadsheet dalam registry (bisa lebih
        # dari 1 file kalau udah pernah rotate karena cell limit).
        print(f"Loading semua tab {universe} (dari {len(registry.ids)} file) ...")
        existing = {}  # {ticker: set of date strings}
        for ss in registry.all_spreadsheets():
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
                                    existing.setdefault(ticker, set()).add(str(d))
                                except:
                                    pass
                        break
                    except gspread.exceptions.APIError as e:
                        if is_retryable(e):
                            wait = (attempt + 1) * 30
                            print(f"  Error transient load tab {ws.title} ({e}), tunggu {wait}s ...", flush=True)
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
            ok, err = process_ticker_dates(ticker, universe, missing, registries)
            print(f"  -> OK {ok} hari, ERR {err}")
            time.sleep(DELAY_ANTAR_SAHAM)

        print(f"\n{universe} gap scan selesai. Total gap filled: {total_gap}")

# ====================================================================
# LOAD TICKERS — reuse CSV queue yang sama dengan stockbit_broksum_db.py
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
        tickers_by_universe.setdefault(universe, []).append(ticker)

    return tickers_by_universe

# ====================================================================
# MAIN
# ====================================================================

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  STOCKBIT BROKER_DB (level broker individual) -- MODE: {MODE.upper()}")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")

    if not BEARER_TOKEN:
        print("BEARER_TOKEN tidak diset!")
        exit(1)

    # V1.1 (9 Jul 2026, buat jalan paralel di GitHub Actions matrix batch):
    # gap_scan/backfill load SEMUA tab yang ada dulu (get_all_values per tab)
    # sebelum mulai scan per-ticker -- kalau N batch jalan bersamaan pakai
    # 1 identitas kredensial yang sama, semua nembak "load semua tab" nyaris
    # bersamaan dan gampang kena 429 (limit 60 read/menit PER SERVICE
    # ACCOUNT, lihat root-cause yang sama di stockbit_unified_daily.py
    # V1.5/V1.6). Stagger start biar tidak numpuk di detik yang sama.
    if BATCH_INDEX > 0:
        stagger = BATCH_INDEX * 15
        print(f"Stagger start: tunggu {stagger}s (batch {BATCH_INDEX}) ...")
        time.sleep(stagger)

    env_keys = {
        "IDX":     "BROKSUM_DB_IDX",
        "eIPO":    "BROKSUM_DB_EIPO",
        "Bfr2016": "BROKSUM_DB_BFR2016",
    }
    # Per-universe credentials -- opsional. Kalau CREDENTIALS_IDX/EIPO/BFR2016
    # diset, dipakai (biar 3 proses paralel gak berbagi 1 quota Sheets API
    # service account yang sama). Kalau kosong, fallback ke CREDENTIALS biasa.
    cred_keys = {
        "IDX":     os.environ.get("CREDENTIALS_IDX", CREDENTIALS),
        "eIPO":    os.environ.get("CREDENTIALS_EIPO", CREDENTIALS),
        "Bfr2016": os.environ.get("CREDENTIALS_BFR2016", CREDENTIALS),
    }

    target_universe = os.environ.get("UNIVERSE", "")
    registries = {}
    gc_cache = {}

    for universe, env_key in env_keys.items():
        if target_universe and universe != target_universe:
            continue
        ss_id = os.environ.get(env_key, "")
        if not ss_id:
            print(f"  {env_key} tidak diset, skip {universe}")
            continue

        cred_file = cred_keys[universe]
        if cred_file not in gc_cache:
            print(f"Connecting ke Google Sheets pakai {cred_file} ...", flush=True)
            gc_cache[cred_file] = gspread.service_account(filename=cred_file)
        gc = gc_cache[cred_file]

        ss = open_by_key_with_retry(gc, ss_id)
        print(f"Connected: {ss.title} (tab broker: suffix _Broker, auto-rotate kalau cell limit)", flush=True)
        registries[universe] = SpreadsheetRegistry(universe, gc, ss, YOUR_EMAIL_FOR_ROTATE, cred_file=cred_file)

    if not registries:
        print("Tidak ada spreadsheet yang bisa digunakan! Set BROKSUM_DB_IDX/EIPO/BFR2016")
        print("(sama seperti env var yang dipakai stockbit_broksum_db.py).")
        exit(1)

    print()

    tickers_by_universe = load_tickers()
    total = sum(len(v) for v in tickers_by_universe.values())
    print(f"Batch {BATCH_INDEX+1}/{TOTAL_BATCHES} -- {total} ticker\n")

    if MODE == "backfill":
        run_backfill(tickers_by_universe, registries)
    elif MODE == "gap_scan":
        run_gap_scan(tickers_by_universe, registries)
    else:
        run_daily(tickers_by_universe, registries)

    print(f"\n{'='*60}")
    print(f"  SELESAI -- {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")
