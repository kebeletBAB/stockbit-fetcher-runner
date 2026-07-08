"""
Stockbit Unified Daily Pipeline
================================
Gabungan stockbit_broksum.py + stockbit_broksum_db.py + stockbit_broker_flatlog_db.py
menjadi SATU pipeline harian, supaya tidak fetch Stockbit berkali-kali untuk
data yang sebenarnya sama.

KONSOLIDASI FETCH:
  - Data "hari ini" (bandar_detector + broker_summary dalam SATU response API,
    period=BROKER_SUMMARY_PERIOD_LATEST) di-fetch SATU KALI per ticker per hari,
    lalu dipakai untuk TIGA tujuan sekaligus:
      1. Push wide-format ke "10 Fungsi" (tabID "daily") -- broksum block +
         volume/broker-summary block, PLUS satu baris baru (row 190 di sisi
         GAS) berisi angka broker-count-net-buy-5D.
      2. Push flat aggregate row ke BROKSUM_DB (spreadsheet per universe,
         tab per bulan) -- riwayat historis untuk riset.
      3. Push baris individual per-broker ke BROKSUM_DB_*_Broker (spreadsheet
         ber-rotasi, tab per bulan) -- data mentah broker level, satu-satunya
         sumber identitas broker (MG/YP/TP/dst), dipakai buat hitung
         broker-count-5D.
  - Weekly (period=LAST_7_DAYS) dan Monthly (period=THIS_MONTH) TETAP fetch
    terpisah -- itu window agregat bawaan Stockbit sendiri, tidak bisa
    direkonstruksi dari data harian. Jadi total per ticker per hari: 3 call
    (bukan 5 seperti sebelumnya kalau ketiga script lama dijalankan semua).

BROKER-COUNT-NET-BUY-5D (buat Edge #3, lihat FINDINGS_FINAL_2EDGE_7Jul2026.md):
  - Definisi PERSIS sama seperti broker_count_test.py di riset: agregat net
    value (buy - sell) per KODE BROKER selama 5 HARI BURSA TERAKHIR (bukan
    kalender -- lompatin weekend & libur nasional otomatis, karena window-nya
    dibangun dari tanggal yang BENERAN ADA datanya di BROKSUM_DB_*_Broker),
    baru hitung berapa broker yang hasilnya net POSITIF.
  - H-1 s.d. H-5 (5 hari bursa SEBELUM hari ini, BUKAN termasuk hari ini
    -- lihat CHANGELOG V1.7) diambil semuanya dari data yang SUDAH
    tersimpan di BROKSUM_DB_*_Broker (di-load sekali per bulan per
    universe di awal run, di-cache di memory -- supaya tidak baca ulang
    sheet per-ticker, hemat panggilan Sheets API). Data live hari ini
    TIDAK dipakai untuk hitungan ini sama sekali (baru dipakai besok,
    setelah ter-push dan settle).
  - Hasil angka ini ditulis ke "10 Fungsi" sebagai baris ke-11 di payload
    volume (vol_data), format ["Broker Net-Buy 5D", <angka>, "", "", "", ""] --
    otomatis jatuh di baris 190 kolom vStartCol(label)/vStartCol+1(angka),
    KARENA vRange di GAS panjangnya mengikuti vData.length -- tidak perlu
    ubah kode GAS-nya sama sekali (lihat CHANGELOG V1.9.9.5 "10 Fungsi").

Setup: SAMA seperti 3 script lama -- reuse credentials.json, BEARER_TOKEN,
  CSV_FILE (queue ticker+universe), URL_MAP ("10 Fungsi"), BROKSUM_DB_IDX/
  EIPO/BFR2016 (flat aggregate), broker_db_registry_{universe}.json (rotasi
  individual broker, dibuat otomatis kalau belum ada).

V1.8 (9 Jul 2026 FIX -- timeout GAS "10 Fungsi"): push_to_gas() sekarang
  retry inline 3x (5s/10s/20s) kalau timeout/error ke script.google.com --
  banyak kasus timeout GAS itu sesaat (cold-start/antrian eksekusi), jadi
  biasanya langsung sembuh di percobaan ke-2/3 tanpa perlu nunggu sampai
  retry akhir workflow (job notify-done). Retry akhir tetap ada sebagai
  fallback kalau GAS beneran down lebih lama dari 3x percobaan ini.

V1.7 (9 Jul 2026 FIX -- root cause broker5D selalu None): window
  broker-count-5D DIPERBAIKI dari H0..H-4 (termasuk hari ini, pakai data
  live fetch) menjadi H-1..H-5 (5 hari bursa SEBELUM hari ini, murni dari
  index tersimpan yang sudah settle). Dikonfirmasi lewat pengecekan
  ulang ke broker_count_test.py (riset asli): window di sana pakai slice
  `dates[i-5:i]` -- TIDAK menyertakan event_date/H0. Komentar lama di
  file ini yang bilang "H-hari-ini diambil dari fetch live" TERNYATA
  SALAH/tidak sesuai riset yang divalidasi -- kemungkinan asumsi keliru
  waktu port awal ke produksi. Dampak bug lama: broker5D jadi None terus-
  menerus setiap kali broker_summary Stockbit utk hari itu belum lengkap
  saat jam pipeline jalan (fetch live gagal/kosong), WALAU histori index
  4 hari sebelumnya sudah cukup -- padahal seharusnya tidak pernah butuh
  data live sama sekali untuk hitungan ini. Sekarang broker5D 100% dari
  data yang sudah settle di BROKSUM_DB_*_Broker, tidak bergantung fetch
  hari berjalan.

V1.3 (9 Jul 2026 -- untuk integrasi GitHub Actions/stockbit.yml):
  - BATCH_INDEX/TOTAL_BATCHES: pembagian batch paralel (pola sama seperti
    stockbit_ohlc_db.py/stockbit_broksum.py), default TOTAL_BATCHES=1 =
    semua ticker 1 proses (perilaku lama, tidak berubah kalau tidak diset).
  - error_log sekarang ikut simpan kolom "target" (bukan cuma ticker/
    universe/error) -- dipakai buat rebuild retry_queue.csv (format
    ticker,target) di job notify-done, biar ticker gagal bisa di-retry
    otomatis lewat CSV_FILE override, sama seperti workflow lama.

V1.6 (9 Jul 2026 FIX -- kuota Google Sheets API "per user"): flat aggregate
  (BROKSUM_DB_IDX/EIPO/BFR2016) sekarang bisa pakai service account
  TERPISAH per universe (CREDENTIALS_FLAT_IDX/_EIPO/_BFR2016, opsional,
  fallback ke CREDENTIALS kalau tidak diset). Root cause dikonfirmasi dari
  GCP Console (APIs & Services -> Quotas): limit yang kena bukan project-
  wide (300/menit, ~10% terpakai), tapi "Read requests per menit PER USER"
  (60/menit PER SERVICE ACCOUNT) -- dulu 3 flat spreadsheet + OHLC_DB semua
  numpuk ke 1 identitas (credentials.json) yang dipakai 9+3 batch paralel
  sekaligus. Lihat juga CREDENTIALS_BROKER (V1.2) yang sudah lebih dulu
  benar dipisah per universe untuk broker-level.

V1.5 (9 Jul 2026 FIX -- ditemukan dari run pertama GitHub Actions, 9 batch
  paralel): beberapa batch gagal dengan "429 Quota exceeded -- Read
  requests per menit per user" (Google Sheets API) karena gc.open_by_key()
  (baik di flat_spreadsheets maupun BrokerDbRegistry._open()) TIDAK punya
  retry, beda dari get_or_create_tab/push_rows_to_tab yang sudah ada.
  Ditambahkan: (1) open_by_key_with_retry() dengan backoff, dipakai di
  kedua tempat itu; (2) stagger start kecil per BATCH_INDEX supaya 9 batch
  tidak nembak API Google di detik yang sama persis.

V1.4 (9 Jul 2026 FIX -- PENTING, ditemukan waktu susun stockbit.yml):
  - run_daily(): dulu kalau BROKSUM_DB(flat)/broker-registry belum diset
    untuk suatu universe, SELURUH universe itu di-skip TERMASUK push "10
    Fungsi". Ini regresi dari stockbit_broksum.py lama yang push "10
    Fungsi" TANPA SYARAT tiap hari kerja (BROKSUM_DB dulu opt-in terpisah,
    default OFF). Sekarang push "10 Fungsi" SELALU dicoba untuk semua
    universe di CSV, independen dari status flat/broker -- flat & broker
    cuma di-skip masing-masing kalau memang belum dikonfigurasi, broker-
    count-5D jadi None (bukan 0) kalau registry belum ada.

V1.2 (9 Jul 2026 FIX -- PENTING): spreadsheet broker-level (flatlog) itu
  FILE YANG BEDA SAMA SEKALI dari BROKSUM_DB flat aggregate, PLUS pakai
  3 service account terpisah (lihat HANDOVER_FINAL_BrokerDB.md). Env var:
    - BROKSUM_DB_IDX / _EIPO / _BFR2016         -> flat aggregate (CREDENTIALS biasa)
    - BROKSUM_DB_BROKER_IDX / _EIPO / _BFR2016  -> broker-level (spreadsheet BEDA)
    - CREDENTIALS_IDX / _EIPO / _BFR2016        -> service account KHUSUS broker-level
      (fallback ke CREDENTIALS kalau tidak diset -- tapi di setup Ajat SELALU diset,
      karena 3 spreadsheet broker-level itu di-share HANYA ke masing-masing
      service account ini, BUKAN ke credentials.json biasa)
  Kedua set (flat & broker) independen -- boleh salah satu kosong per universe,
  tapi run_daily() butuh KEDUANYA terisi untuk universe itu supaya bisa push ke
  flat + broker + hitung broker-count-5D sekaligus (lihat guard di run_daily).

Jalankan:
  python stockbit_unified_daily.py

Token expired? Buka stockbit.com -> DevTools -> Network ->
klik request exodus.stockbit.com -> copy Authorization header baru ke BEARER_TOKEN.
"""

import requests
import gspread
import csv
import time
import os
import re
import json
from datetime import datetime, date, timedelta

# ====================================================================
# CONFIG
# ====================================================================

BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")
CSV_FILE     = os.environ.get("CSV_FILE", "queue_full_eipo-idx-bfr2016.csv")
CREDENTIALS  = os.environ.get("CREDENTIALS", "credentials.json")
YOUR_EMAIL   = os.environ.get("YOUR_EMAIL", "")

# V1.1 (9 Jul 2026): credential per-universe -- SAMA seperti pola opsional
# di stockbit_broker_flatlog_db.py lama. Dipakai KHUSUS untuk
# BrokerDbRegistry (BROKSUM_DB_*_Broker / flatlog) -- 3 service account
# TERPISAH (credentials_idx.json/credentials_eipo.json/credentials_bfr2016.json),
# masing-masing di-share HANYA ke spreadsheet broker-level miliknya sendiri
# (lihat HANDOVER_FINAL_BrokerDB.md). credentials.json biasa (CREDENTIALS)
# dipakai untuk flat aggregate (BROKSUM_DB) + hal lain, TIDAK ikut di-share
# ke spreadsheet broker-level -- makanya kalau ss_id broker dibuka pakai
# CREDENTIALS default, hasilnya 404 (bukan soal ID salah).
CREDENTIALS_BROKER = {
    "IDX":     os.environ.get("CREDENTIALS_IDX", CREDENTIALS),
    "eIPO":    os.environ.get("CREDENTIALS_EIPO", CREDENTIALS),
    "Bfr2016": os.environ.get("CREDENTIALS_BFR2016", CREDENTIALS),
}

# V1.6 (9 Jul 2026 FIX -- kuota Google Sheets API): ditemukan dari GCP
# Console (APIs & Services -> Quotas) bahwa limit yang kena bukan project-
# wide (300 req/menit, cuma ~10% terpakai), tapi "Read requests per menit
# PER USER" (60/menit PER SERVICE ACCOUNT). Dulu flat aggregate (3
# spreadsheet BROKSUM_DB_IDX/EIPO/BFR2016) SEMUANYA pakai satu identitas
# credentials.json yang SAMA -- dengan 9 batch paralel x 3 universe, bisa
# sampai puluhan open_by_key() per menit numpuk ke satu jatah 60/menit itu.
# Sekarang tiap universe (flat) bisa pakai service account SENDIRI (opsional
# -- fallback ke CREDENTIALS kalau tidak diset, supaya tidak wajib bikin
# service account baru buat yang belum sempat). Total sekarang bisa sampai
# 7 identitas berbeda (3 flat + 3 broker + kelak 1 OHLC di luar file ini),
# masing-masing dapat jatah 60/menit sendiri-sendiri.
CREDENTIALS_FLAT = {
    "IDX":     os.environ.get("CREDENTIALS_FLAT_IDX", CREDENTIALS),
    "eIPO":    os.environ.get("CREDENTIALS_FLAT_EIPO", CREDENTIALS),
    "Bfr2016": os.environ.get("CREDENTIALS_FLAT_BFR2016", CREDENTIALS),
}

MODE_DAILY   = os.environ.get("MODE_DAILY", "1") == "1"
MODE_WEEKLY  = os.environ.get("MODE_WEEKLY", "1") == "1"
MODE_MONTHLY = os.environ.get("MODE_MONTHLY", "1") == "1"

# V1.9 (9 Jul 2026, force-run tanpa nunggu wait-for-data): kalau diset
# (format YYYY-MM-DD), dipakai sebagai "today" pengganti date.today() --
# buat kasus mau paksa fetch SEKARANG walau Stockbit API masih balikin
# LATEST = data hari kemarin (market belum buka/data belum settle), tapi
# tetap mau label baris di sheet dengan tanggal yang BENAR (bukan ikut
# tanggal sistem yang sudah ganti hari). Kosong (default) = perilaku lama,
# pakai date.today() seperti biasa.
TARGET_DATE = os.environ.get("TARGET_DATE", "")

# V1.3: batch paralel (lihat load_tickers()) -- default TOTAL_BATCHES=1
# artinya semua ticker diproses 1 proses (perilaku lokal yang sudah diuji,
# tidak berubah kalau tidak di-set lewat GitHub Actions matrix).
BATCH_INDEX   = int(os.environ.get("BATCH_INDEX", "0"))
TOTAL_BATCHES = int(os.environ.get("TOTAL_BATCHES", "1"))

DELAY_ANTAR_SAHAM = 1.5
BATCH_SIZE        = 50
MAX_BROKER_PER_SISI = 25  # sama seperti flatlog_db.py -- limit API=25

BROKSUM_DB_IDS = {
    "IDX":     os.environ.get("BROKSUM_DB_IDX", ""),
    "eIPO":    os.environ.get("BROKSUM_DB_EIPO", ""),
    "Bfr2016": os.environ.get("BROKSUM_DB_BFR2016", ""),
}

# V1.2 (9 Jul 2026 FIX): spreadsheet broker-level (flatlog) itu FILE YANG
# BEDA SAMA SEKALI dari BROKSUM_DB flat aggregate di atas -- bukan cuma
# kredensial yang beda, ID-nya juga beda (lihat HANDOVER_FINAL_BrokerDB.md:
# "BROKSUM_DB_BROKER_IDX/EIPO/BFR2016", spreadsheet baru terpisah dari
# produksi lama). Versi sebelumnya (V1.1) SALAH -- masih pakai ss_id yang
# sama dari BROKSUM_DB_IDS untuk BrokerDbRegistry, padahal harusnya independen.
BROKSUM_DB_BROKER_IDS = {
    "IDX":     os.environ.get("BROKSUM_DB_BROKER_IDX", ""),
    "eIPO":    os.environ.get("BROKSUM_DB_BROKER_EIPO", ""),
    "Bfr2016": os.environ.get("BROKSUM_DB_BROKER_BFR2016", ""),
}

# URL map "10 Fungsi" -- sama persis dengan stockbit_broksum.py
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

# ====================================================================
# LIBUR BURSA -- sama persis di ketiga script lama
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

def get_last_n_hari_bursa(anchor_date, n):
    """Mundur dari anchor_date (termasuk anchor_date sendiri kalau hari
    bursa), kumpulkan n hari bursa terakhir -- otomatis lompat weekend &
    libur nasional, TIDAK PEDULI berapa hari kalender yang dibutuhkan buat
    dapat n hari bursa itu. Return list terurut lama->baru."""
    out = []
    d = anchor_date
    while len(out) < n:
        if is_hari_bursa(d):
            out.append(d)
        d -= timedelta(days=1)
    out.reverse()
    return out

# ====================================================================
# HELPER FORMAT
# ====================================================================

def fmt_val(v):
    try:
        v = float(v)
    except Exception:
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
    except Exception:
        return str(v)

def fmt_avg(v):
    try:
        return str(int(float(v)))
    except Exception:
        return str(v)

def fmt_rp_b(amount):
    try:
        return round(float(amount) / 1_000_000_000, 1)
    except Exception:
        return 0

def fmt_date_ddmmyyyy(d):
    return d.strftime("%d/%m/%Y")

def tab_name_monthly(universe, d):
    return f"{universe}_{d.strftime('%Y-%m')}"

def tab_name_broker(universe, d):
    return f"{universe}_{d.strftime('%Y-%m')}_Broker"

# ====================================================================
# FETCH STOCKBIT
# ====================================================================

def fetch_period(ticker, period):
    """period: BROKER_SUMMARY_PERIOD_LATEST / _LAST_7_DAYS / _THIS_MONTH.
    Dipakai buat SEMUA kebutuhan -- daily (LATEST) DIREUSE buat 3 tujuan
    (10 Fungsi daily, BROKSUM_DB flat, BROKSUM_DB_Broker); weekly/monthly
    cuma buat 10 Fungsi (window agregat khusus Stockbit, tidak bisa direkon
    dari data harian)."""
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
# TRANSFORM: "10 Fungsi" (broksum + volume block), reuse pola broksum.py
# ====================================================================

TYPE_COLOR = {
    "Asing":      "#D73F3C",
    "Lokal":      "#7924C3",
    "Pemerintah": "#0C8414",
}

def _accdist_color(accdist_str):
    ad = (accdist_str or "").strip().lower()
    if ad == "acc":  return "#90E89780"
    if ad == "dist": return "#ff988e80"
    if "acc" in ad:  return "#90E897"
    if "dist" in ad: return "#ff988e"
    return "#DADADA"

def transform_10fungsi(ticker, data, tab_id, broker_count_5d=None):
    bd = data["bandar_detector"]
    bs = data["broker_summary"]

    buys  = bs.get("brokers_buy", [])
    sells = bs.get("brokers_sell", [])

    max_rows = max(len(buys), len(sells))
    broksum_data, broksum_colors = [], []

    for i in range(max_rows):
        row_data, row_color = [], []
        if i < len(buys):
            b = buys[i]
            row_data += [
                b.get("netbs_broker_code", "-"),
                fmt_val(b.get("bval", 0)),
                fmt_lot(b.get("blot", 0)),
                fmt_avg(b.get("netbs_buy_avg_price", 0)),
            ]
            row_color += [TYPE_COLOR.get(b.get("type", ""), "#333333"), "#00AB6B", "#00AB6B", "#00AB6B"]
        else:
            row_data += ["-", "-", "-", "-"]
            row_color += ["#333333"] * 4

        if i < len(sells):
            s = sells[i]
            row_data += [
                s.get("netbs_broker_code", "-"),
                fmt_val(abs(float(s.get("sval", 0) or 0))),
                fmt_lot(abs(float(s.get("slot", 0) or 0))),
                fmt_avg(s.get("netbs_sell_avg_price", 0)),
            ]
            row_color += [TYPE_COLOR.get(s.get("type", ""), "#333333"), "#EE4A49", "#EE4A49", "#EE4A49"]
        else:
            row_data += ["-", "-", "-", "-"]
            row_color += ["#333333"] * 4

        broksum_data.append(row_data)
        broksum_colors.append(row_color)

    def vol_row(label, obj):
        ad = obj.get("accdist", "Neutral")
        return (
            [label, fmt_lot(obj.get("vol", 0)), f"{obj.get('percent', 0):.1f}",
             "", fmt_rp_b(obj.get("amount", 0)), ad],
            [None, None, None, None, None, _accdist_color(ad)]
        )

    vol_data, vol_colors = [], []
    vol_data.append(["", "Volume", "%", "", "Rp(B)", "Acc/Dist"]); vol_colors.append([None]*6)
    for label, key in [("Top 1","top1"),("Top 3","top3"),("Top 5","top5"),("Average","avg")]:
        rd, rc = vol_row(label, bd.get(key, {}))
        vol_data.append(rd); vol_colors.append(rc)
    vol_data.append(["", "Buyer", "Seller", "", "#", "Acc/Dist"]); vol_colors.append([None]*6)

    broker_ad = bd.get("broker_accdist", "Neutral")
    vol_data.append([
        "Broker", str(bd.get("total_buyer", 0)), str(bd.get("total_seller", 0)),
        "", str(bd.get("number_broker_buysell", 0)), broker_ad
    ])
    vol_colors.append([None, None, None, None, None, _accdist_color(broker_ad)])

    net_vol = bd.get("volume", 0); net_val = bd.get("value", 0); avg_rp = bd.get("average", 0)
    vol_data.append(["Net Volume", fmt_lot(net_vol), "", "", "", ""]); vol_colors.append([None]*6)
    vol_data.append(["Net Value", "0B" if net_val == 0 else fmt_val(net_val), "", "", "", ""]); vol_colors.append([None]*6)
    vol_data.append(["Average (Rp)", fmt_avg(avg_rp), "", "", "", ""]); vol_colors.append([None]*6)

    # V9.9 (SR_Engine)/1.9.9.5 ("10 Fungsi"): baris ke-11 -- broker-count-5D
    # buat Edge #3. Cuma diisi untuk tab_id=="daily" (slot "Hari Ini"), dan
    # cuma kalau nilainya sudah berhasil dihitung (bisa None kalau data
    # historis H-1..H-4 belum cukup, mis. ticker baru/awal backfill).
    if tab_id == "daily":
        if broker_count_5d is not None:
            vol_data.append(["Broker Net-Buy 5D", str(broker_count_5d), "", "", "", ""])
        else:
            vol_data.append(["Broker Net-Buy 5D", "-", "", "", "", ""])
        vol_colors.append([None]*6)

    return {
        "ticker": ticker,
        "tab": tab_id,
        "broksum": {"data": broksum_data, "colors": broksum_colors},
        "volume":  {"data": vol_data,     "colors": vol_colors},
    }

def push_to_gas(gas_url, payload):
    """V1.8 (9 Jul 2026 FIX): tambah retry inline 3x (5s/10s/20s) buat
    timeout/error sesaat ke GAS webhook (script.google.com sering lambat
    cold-start / antrian eksekusi). Ini TIDAK menggantikan retry di akhir
    workflow (job notify-done, lewat retry_queue.csv) -- keduanya jalan
    bareng: retry inline ini nangkep kebanyakan kasus timeout SESAAT dalam
    hitungan detik, sisanya (kalau GAS beneran down lama) tetap jatuh ke
    error_log seperti biasa dan ditangkap retry di akhir seperti sebelumnya."""
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(gas_url, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            last_err = e
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"(GAS gagal/timeout, retry {wait}s: {e}) ", end="", flush=True)
                time.sleep(wait)
    raise last_err

# ====================================================================
# TRANSFORM: BROKSUM_DB flat aggregate row (reuse broksum_db.py)
# ====================================================================

HEADERS_ROW_FLAT = [
    "Ticker", "Tanggal",
    "Top1_Vol", "Top1_%", "Top1_RpB", "Top1_AccDist",
    "Top3_Vol", "Top3_%", "Top3_RpB", "Top3_AccDist",
    "Top5_Vol", "Top5_%", "Top5_RpB", "Top5_AccDist",
    "Avg_Vol",  "Avg_%",  "Avg_RpB",  "Avg_AccDist",
    "Broker_Buyer", "Broker_Seller", "Broker_#", "Broker_AccDist",
    "NetVol", "NetVal", "Avg_Price"
]

def extract_flat_row(ticker, d, data):
    bd = data.get("bandar_detector", {})

    def vol_fields(key):
        obj = bd.get(key, {})
        return [fmt_lot(obj.get("vol", 0)), f"{obj.get('percent', 0):.1f}",
                fmt_rp_b(obj.get("amount", 0)), obj.get("accdist", "Neutral")]

    row = [ticker, fmt_date_ddmmyyyy(d)]
    row += vol_fields("top1"); row += vol_fields("top3")
    row += vol_fields("top5"); row += vol_fields("avg")
    row += [
        str(bd.get("total_buyer", 0)), str(bd.get("total_seller", 0)),
        str(bd.get("number_broker_buysell", 0)), bd.get("broker_accdist", "Neutral"),
        fmt_lot(bd.get("volume", 0)), fmt_val(bd.get("value", 0)), fmt_avg(bd.get("average", 0)),
    ]
    return row

# ====================================================================
# TRANSFORM: BROKSUM_DB_Broker individual rows (reuse flatlog_db.py)
# ====================================================================

HEADERS_ROW_BROKER = ["Ticker", "Tanggal", "Broker", "Type", "Side", "Value", "Lot", "AvgPrice", "Rank"]

def extract_broker_rows(ticker, d, data):
    bs = data.get("broker_summary", {})
    buys  = bs.get("brokers_buy", [])[:MAX_BROKER_PER_SISI]
    sells = bs.get("brokers_sell", [])[:MAX_BROKER_PER_SISI]
    tgl = fmt_date_ddmmyyyy(d)
    rows = []
    for rank, b in enumerate(buys, start=1):
        rows.append([ticker, tgl, b.get("netbs_broker_code", "-"), b.get("type", ""), "buy",
                      round(float(b.get("bval", 0) or 0), 2), int(float(b.get("blot", 0) or 0)),
                      round(float(b.get("netbs_buy_avg_price", 0) or 0), 2), rank])
    for rank, s in enumerate(sells, start=1):
        rows.append([ticker, tgl, s.get("netbs_broker_code", "-"), s.get("type", ""), "sell",
                      round(abs(float(s.get("sval", 0) or 0)), 2), int(abs(float(s.get("slot", 0) or 0))),
                      round(float(s.get("netbs_sell_avg_price", 0) or 0), 2), rank])
    return rows

# ====================================================================
# GSPREAD HELPERS (generik, dipakai buat flat & broker tab)
# ====================================================================

# V1.5 (9 Jul 2026 FIX): ditemukan waktu 9 batch GitHub Actions jalan
# paralel bersamaan -- gc.open_by_key() TIDAK punya retry buat 429 (beda
# dari get_or_create_tab/push_rows_to_tab di bawah yang sudah ada), padahal
# ini juga API call ke Google Sheets. Dengan 9 proses buka spreadsheet yang
# sama nyaris bersamaan (semua pakai credentials.json yang sama buat flat
# aggregate), gampang kena "429 Quota exceeded -- Read requests per menit
# per user" dari Google. Sekarang dibungkus retry+backoff, sama polanya
# seperti fungsi lain di file ini.
# V1.11 (9 Jul 2026 FIX): bukan cuma 429 (quota) yang perlu retry --
# gspread/Sheets API kadang balikin 500/503 (internal error transient di
# sisi Google, bukan soal kuota) terutama saat banyak proses baca
# bersamaan. Semua titik retry di bawah pakai helper ini sekarang, bukan
# cuma cek "429" doang.
RETRYABLE_CODES = ("429", "500", "503")

def is_retryable(e):
    return any(code in str(e) for code in RETRYABLE_CODES)

def open_by_key_with_retry(gc, ss_id):
    for attempt in range(6):
        try:
            return gc.open_by_key(ss_id)
        except gspread.exceptions.APIError as e:
            if is_retryable(e):
                wait = (attempt + 1) * 20
                print(f"  Error transient open_by_key ({e}), tunggu {wait}s ...")
                time.sleep(wait)
            else:
                raise
    raise Exception(f"Gagal buka spreadsheet {ss_id} setelah 6 percobaan (error transient terus)")

# V1.10 (9 Jul 2026 FIX -- crash 429 di preload_broker_index): ss.worksheets()
# TIDAK punya retry (beda dari open_by_key/get_or_create_tab/push_rows_to_tab
# yang sudah dilindungi), padahal ini juga API call ke Google Sheets. Kena
# 429 pas load index broker (H-1..H-5) langsung crash total, gak sampai
# ke ticker manapun. Root cause sama persis polanya, cuma titik yang beda.
def worksheets_with_retry(ss):
    for attempt in range(6):
        try:
            return ss.worksheets()
        except gspread.exceptions.APIError as e:
            if is_retryable(e):
                wait = (attempt + 1) * 20
                print(f"  Error transient ss.worksheets() ({e}), tunggu {wait}s ...")
                time.sleep(wait)
            else:
                raise
    raise Exception(f"Gagal list worksheets {ss.title} setelah 6 percobaan (error transient terus)")

def get_or_create_tab(spreadsheet, tab, headers_row, rows_hint=5000):
    for attempt in range(5):
        try:
            return spreadsheet.worksheet(tab)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=tab, rows=rows_hint, cols=len(headers_row))
            ws.append_row(headers_row)
            print(f"  Tab baru dibuat: {tab}")
            return ws
        except gspread.exceptions.APIError as e:
            if is_retryable(e):
                wait = (attempt + 1) * 30
                print(f"  Error transient ({e}), tunggu {wait}s ...")
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
                    print(f"  Error transient push ({e}), tunggu {wait}s ...")
                    time.sleep(wait)
                else:
                    raise

# ====================================================================
# REGISTRY ROTASI (BROKSUM_DB_*_Broker) -- reuse flatlog_db.py, disederhanakan
# (skip auto-create-spreadsheet-baru untuk versi awal ini -- kalau nanti
# kena limit cell, tangani manual dulu; auto-rotate bisa ditambah lagi kalau
# perlu, tapi jangan overengineer di iterasi pertama).
# ====================================================================

class BrokerDbRegistry:
    def __init__(self, universe, gc, base_ss_id):
        self.universe = universe
        self.gc = gc
        self.registry_file = f"broker_db_registry_{universe}.json"
        if os.path.exists(self.registry_file):
            with open(self.registry_file) as f:
                self.ids = json.load(f)
        else:
            self.ids = [base_ss_id]
            self._save()
        self.ss_cache = {}

    def _save(self):
        with open(self.registry_file, "w") as f:
            json.dump(self.ids, f)

    def _open(self, ss_id):
        if ss_id not in self.ss_cache:
            self.ss_cache[ss_id] = open_by_key_with_retry(self.gc, ss_id)
        return self.ss_cache[ss_id]

    def active(self):
        return self._open(self.ids[-1])

    def all_spreadsheets(self):
        return [self._open(i) for i in self.ids]

# ====================================================================
# PRE-LOAD index broker mentah (H-1 s.d. H-4) -- SEKALI per universe per
# bulan yang relevan di awal run, bukan per-ticker berulang-ulang. Ini
# yang bikin hitungan broker-count-5D murah dijalankan tiap hari.
# ====================================================================

def preload_broker_index(universe, registry, months_needed):
    """months_needed: set of date objects (representative, hari pertama
    bulan) yang tab-nya perlu di-load. Return dict:
      {ticker: {date_str(dd/mm/yyyy): {broker_code: net_value}}}
    """
    index = {}
    tabs_needed = set(tab_name_broker(universe, m) for m in months_needed)
    for ss in registry.all_spreadsheets():
        for ws in worksheets_with_retry(ss):
            if ws.title not in tabs_needed:
                continue
            print(f"  [preload] {universe}: load tab {ws.title} ...")
            for attempt in range(5):
                try:
                    rows = ws.get_all_values()
                    break
                except gspread.exceptions.APIError as e:
                    if is_retryable(e):
                        wait = (attempt + 1) * 30
                        print(f"    Error transient ({e}), tunggu {wait}s ...")
                        time.sleep(wait)
                    else:
                        raise
            else:
                continue
            for row in rows[1:]:  # skip header
                if len(row) < 6:
                    continue
                ticker, tgl, broker, _typ, side, value = row[0], row[1], row[2], row[3], row[4], row[5]
                if not ticker or not tgl or not broker:
                    continue
                try:
                    v = float(value)
                except Exception:
                    continue
                signed = v if side == "buy" else -v
                index.setdefault(ticker, {}).setdefault(tgl, {})
                index[ticker][tgl][broker] = index[ticker][tgl].get(broker, 0) + signed
    return index

def hitung_broker_count_5d(ticker, tanggal_5_hari, index, today_rows_today=None):
    """tanggal_5_hari: list 5 date object H-1..H-5 (5 hari bursa TERAKHIR
    SEBELUM hari ini, urut lama->baru) -- sesuai metodologi tervalidasi
    (window H-1..H-5, BUKAN termasuk hari ini).
    index: hasil preload_broker_index, sudah mencakup ke-5 hari ini karena
    semuanya adalah hari yang sudah settle/di-push sebelumnya.

    V1.7 (9 Jul 2026 FIX -- root cause 'broker5D selalu None'): sebelumnya
    window ini keliru menyertakan HARI INI (H0..H-4) dan mewajibkan data
    fetch live hari ini supaya genap 5 -- kalau broker_summary utk hari
    ini kebetulan belum lengkap di sisi Stockbit (atau gagal fetch),
    hasilnya SELALU None walau histori 5 hari sebelumnya sudah lengkap
    di index. Sekarang window murni H-1..H-5, semuanya dari index yang
    sudah settle, TIDAK butuh data hari ini sama sekali untuk hitungan
    ini. Parameter today_rows_today dipertahankan (default None) supaya
    tidak break kalau ada pemanggil lama, tapi tidak lagi dipakai di
    hitungan -- sengaja tidak dihapus dari signature untuk kompatibilitas.

    Return: int (jumlah broker net-buy positif selama 5 hari bursa
    terakhir), atau None kalau histori index kurang dari 5 hari (misal
    ticker baru / awal backfill individual broker)."""
    net_per_broker = {}
    hari_tersedia = 0

    for d in tanggal_5_hari:
        tgl_str = fmt_date_ddmmyyyy(d)
        data_hari = index.get(ticker, {}).get(tgl_str)
        if data_hari is None:
            continue
        hari_tersedia += 1
        for broker, val in data_hari.items():
            net_per_broker[broker] = net_per_broker.get(broker, 0) + val

    if hari_tersedia < 5:
        # Data historis belum cukup (ticker baru / awal backfill individual
        # broker) -- jangan kasih angka yang menyesatkan, kasih None.
        return None

    return sum(1 for v in net_per_broker.values() if v > 0)

# ====================================================================
# MAIN LOOP: DAILY (fetch sekali, push 3 tujuan + hitung broker-count-5D)
# ====================================================================

def load_tickers():
    tickers_by_universe = {}
    ticker_target = {}  # ticker -> target (buat URL_MAP)
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if r and r[0].strip()]
    # V1.3 (9 Jul 2026): pembagian batch paralel, SAMA seperti pola di
    # stockbit_ohlc_db.py/stockbit_broksum.py -- supaya bisa dijalankan
    # sebagai N job GitHub Actions paralel (matrix batch), bukan 1 proses
    # panjang. BATCH_INDEX/TOTAL_BATCHES di-set lewat env var kalau perlu;
    # default TOTAL_BATCHES=1 (semua ticker, 1 proses -- perilaku lokal
    # yang sudah teruji, TIDAK berubah kalau env var ini tidak di-set).
    rows = rows[BATCH_INDEX::TOTAL_BATCHES]
    for row in rows:
        ticker = row[0].strip().upper()
        target = re.sub(r"[^a-zA-Z0-9_]", "", row[1].strip()) if len(row) > 1 else ""
        universe = UNIVERSE_MAP.get(target, "UNKNOWN")
        if universe == "UNKNOWN":
            continue
        tickers_by_universe.setdefault(universe, []).append(ticker)
        ticker_target[ticker] = target
    return tickers_by_universe, ticker_target

def run_daily(tickers_by_universe, ticker_target, flat_spreadsheets, broker_registries):
    today = date.fromisoformat(TARGET_DATE) if TARGET_DATE else date.today()
    if TARGET_DATE:
        print(f"[force] TARGET_DATE diset -- pakai {fmt_date_ddmmyyyy(today)} sebagai 'today', "
              f"BUKAN tanggal sistem. Pastikan Stockbit LATEST memang masih data tanggal ini.")
    print(f"\n{'='*60}\n  DAILY -- {fmt_date_ddmmyyyy(today)}\n{'='*60}\n")

    if not is_hari_bursa(today):
        print("Hari ini bukan hari bursa (weekend/libur) -- skip daily run.")
        return

    error_log = []

    for universe, tickers in sorted(tickers_by_universe.items()):
        registry = broker_registries.get(universe)
        flat_ss = flat_spreadsheets.get(universe)

        # V1.4 (9 Jul 2026 FIX -- PENTING): dulu kalau registry/flat_ss
        # belum diset, SELURUH universe di-skip termasuk push "10 Fungsi".
        # Ini regresi dari perilaku stockbit_broksum.py lama, yang push
        # "10 Fungsi" TANPA SYARAT tiap hari kerja (BROKSUM_DB dulu
        # opt-in terpisah, default OFF di workflow). Sekarang "10 Fungsi"
        # SELALU jalan untuk semua universe yang ada di CSV -- flat/broker
        # cuma di-skip MASING-MASING kalau memang belum di-set, TIDAK
        # menggugurkan push "10 Fungsi" untuk universe itu.
        if not flat_ss:
            print(f"  [info] {universe}: BROKSUM_DB (flat) belum diset -- skip push flat aggregate.")
        if not registry:
            print(f"  [info] {universe}: broker-registry belum diset -- skip push broker-level + broker-count-5D jadi None.")

        # V1.7 FIX: 5 hari bursa TERAKHIR SEBELUM hari ini (H-1..H-5),
        # BUKAN termasuk hari ini -- sesuai metodologi tervalidasi & supaya
        # broker-count-5D tidak bergantung pada data live hari ini yang
        # kadang belum lengkap di sisi Stockbit (root cause broker5D=None
        # terus-menerus walau histori index sudah cukup).
        tanggal_5_hari = get_last_n_hari_bursa(today - timedelta(days=1), 5)
        bulan_dibutuhkan = set(d.replace(day=1) for d in tanggal_5_hari)

        print(f"\n--- {universe} ({len(tickers)} ticker) ---")
        index_broker = preload_broker_index(universe, registry, bulan_dibutuhkan) if registry else {}

        flat_tab = get_or_create_tab(flat_ss, tab_name_monthly(universe, today), HEADERS_ROW_FLAT) if flat_ss else None
        broker_tab = get_or_create_tab(registry.active(), tab_name_broker(universe, today), HEADERS_ROW_BROKER, rows_hint=20000) if registry else None

        flat_rows_batch, broker_rows_batch = [], []

        for i, ticker in enumerate(tickers, 1):
            target = ticker_target.get(ticker, "")
            gas_url = URL_MAP.get(target)
            print(f"[{i:03d}/{len(tickers):03d}] {ticker} ... ", end="", flush=True)

            try:
                # --- SATU fetch, dipakai buat sampai 3 tujuan (tergantung
                # apa saja yang di-konfigurasi untuk universe ini) ---
                json_resp = fetch_period(ticker, "BROKER_SUMMARY_PERIOD_LATEST")
                data = json_resp.get("data")
                if not data:
                    raise ValueError(f"Response tidak ada 'data': {json_resp}")

                # V1.9 SAFETY CHECK: kalau TARGET_DATE dipaksa (force_fetch_date),
                # WAJIB pastikan data yang beneran di-fetch dari Stockbit itu
                # tanggalnya SAMA PERSIS dengan TARGET_DATE -- kalau ternyata
                # Stockbit sudah rollover ke hari berikutnya (misal market
                # sudah mulai update), JANGAN push dengan label yang salah.
                # Lebih baik gagal jelas (masuk error_log, bisa di-retry nanti)
                # daripada diam-diam menulis data salah tanggal ke sheet.
                if TARGET_DATE:
                    data_date_raw = str(data.get("from", ""))[:10]
                    if data_date_raw != TARGET_DATE:
                        raise ValueError(
                            f"TARGET_DATE={TARGET_DATE} tapi Stockbit LATEST sudah "
                            f"'{data_date_raw}' -- sudah rollover, SKIP biar gak salah label."
                        )

                # 1) Flat aggregate row -> BROKSUM_DB (kalau flat_ss ada)
                if flat_ss:
                    flat_rows_batch.append(extract_flat_row(ticker, today, data))

                # 2) Individual broker rows -> BROKSUM_DB_*_Broker (kalau registry ada)
                broker_rows_today = extract_broker_rows(ticker, today, data) if registry else []
                if registry:
                    broker_rows_batch.extend(broker_rows_today)

                # 3) Broker-count-5D -- None kalau registry belum diset
                #    (bukan dianggap 0, biar tidak menyesatkan Edge3).
                #    V1.7: tidak lagi butuh broker_rows_today (data hari
                #    ini) -- window murni H-1..H-5 dari index yang sudah
                #    settle, lihat komentar di hitung_broker_count_5d().
                broker_count_5d = hitung_broker_count_5d(
                    ticker, tanggal_5_hari, index_broker
                ) if registry else None

                # 4) Push "10 Fungsi" daily (kalau URL target dikenali) --
                #    SELALU dicoba, tidak bergantung flat_ss/registry.
                if gas_url:
                    payload = transform_10fungsi(ticker, data, "daily", broker_count_5d)
                    push_to_gas(gas_url, payload)
                else:
                    print(f"(target '{target}' tidak ada di URL_MAP, skip 10 Fungsi) ", end="")

                print(f"OK (broker5D={broker_count_5d})")

            except Exception as e:
                print(f"ERROR: {e}")
                # V1.3: tambah kolom "target" -- dipakai notify-done job di
                # GitHub Actions buat rebuild retry_queue.csv (format
                # ticker,target, sama seperti CSV_FILE asli) supaya ticker
                # yang gagal bisa di-retry otomatis, bukan cuma dicatat.
                error_log.append({"ticker": ticker, "target": target, "universe": universe,
                                   "error": str(e),
                                   "ts": datetime.now().strftime("%d/%m/%Y %H:%M:%S")})

            time.sleep(DELAY_ANTAR_SAHAM)

        if flat_rows_batch:
            push_rows_to_tab(flat_tab, flat_rows_batch)
        if broker_rows_batch:
            push_rows_to_tab(broker_tab, broker_rows_batch)

    if error_log:
        err_file = f"error_log_unified_daily_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(err_file, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ticker", "target", "universe", "ts", "error"])
            w.writeheader()
            w.writerows(error_log)
        print(f"\nError log disimpan: {err_file}")

    print(f"\n{'='*60}\n  DAILY SELESAI -- {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n{'='*60}\n")

# ====================================================================
# WEEKLY / MONTHLY -- tetap terpisah, cuma buat "10 Fungsi" (window
# agregat bawaan Stockbit, tidak bisa direkonstruksi dari data harian)
# ====================================================================

def run_period_only(mode, tickers_by_universe, ticker_target):
    if mode == "weekly":
        tab_id, period = "weekly", "BROKER_SUMMARY_PERIOD_LAST_7_DAYS"
    else:
        tab_id, period = datetime.now().strftime("%y%m"), "BROKER_SUMMARY_PERIOD_THIS_MONTH"

    print(f"\n{'='*60}\n  {mode.upper()} (tab_id={tab_id})\n{'='*60}\n")
    error_log = []

    for universe, tickers in sorted(tickers_by_universe.items()):
        print(f"\n--- {universe} ({len(tickers)} ticker) ---")
        for i, ticker in enumerate(tickers, 1):
            target = ticker_target.get(ticker, "")
            gas_url = URL_MAP.get(target)
            if not gas_url:
                continue
            print(f"[{i:03d}/{len(tickers):03d}] {ticker} ... ", end="", flush=True)
            try:
                json_resp = fetch_period(ticker, period)
                data = json_resp.get("data")
                if not data:
                    raise ValueError(f"Response tidak ada 'data': {json_resp}")
                payload = transform_10fungsi(ticker, data, tab_id)
                push_to_gas(gas_url, payload)
                print("OK")
            except Exception as e:
                print(f"ERROR: {e}")
                error_log.append({"ticker": ticker, "target": target, "universe": universe,
                                   "error": str(e),
                                   "ts": datetime.now().strftime("%d/%m/%Y %H:%M:%S")})
            time.sleep(DELAY_ANTAR_SAHAM)

    if error_log:
        err_file = f"error_log_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with open(err_file, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ticker", "target", "universe", "ts", "error"])
            w.writeheader()
            w.writerows(error_log)
        print(f"Error log disimpan: {err_file}")

# ====================================================================
# MAIN
# ====================================================================

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  STOCKBIT UNIFIED DAILY PIPELINE")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")

    if not BEARER_TOKEN:
        print("BEARER_TOKEN tidak diset!")
        exit(1)

    # V1.5: stagger start antar batch -- kalau dijalankan sebagai matrix
    # GitHub Actions (9 job paralel), semuanya start nyaris bersamaan dan
    # langsung tembak Google Sheets API (open_by_key + preload_broker_index)
    # di detik yang sama, gampang numpuk kena 429. Jeda kecil ini
    # (BATCH_INDEX x beberapa detik) nyebar titik mulainya supaya nggak
    # semua nembak API persis bareng -- TIDAK berpengaruh kalau dijalankan
    # 1 proses lokal (BATCH_INDEX default 0 = tanpa jeda).
    if BATCH_INDEX > 0:
        stagger = BATCH_INDEX * 4
        print(f"Stagger start: tunggu {stagger}s (batch {BATCH_INDEX}) ...")
        time.sleep(stagger)

    print("Connecting ke Google Sheets ...")
    gc = gspread.service_account(filename=CREDENTIALS)

    # V1.1: cache gspread client per file kredensial -- kalau
    # CREDENTIALS_IDX/EIPO/BFR2016 sama persis dengan CREDENTIALS (tidak
    # diset), tidak perlu bikin client baru, cukup reuse `gc` di atas.
    _gc_cache = {CREDENTIALS: gc}
    def _gc_for(cred_file):
        if cred_file not in _gc_cache:
            print(f"  Connecting ({cred_file}) ...")
            _gc_cache[cred_file] = gspread.service_account(filename=cred_file)
        return _gc_cache[cred_file]

    # V1.2 FIX: flat aggregate (BROKSUM_DB_IDS) dan broker-level
    # (BROKSUM_DB_BROKER_IDS) itu DUA SET SPREADSHEET YANG BEDA, jadi
    # di-loop TERPISAH -- kosong salah satu TIDAK menggugurkan yang lain
    # (dulu 1 loop, jadi kalau flat ID kosong, broker registry ikut ke-skip
    # padahal harusnya independen).
    flat_spreadsheets = {}
    for universe, ss_id in BROKSUM_DB_IDS.items():
        if not ss_id:
            print(f"  BROKSUM_DB_{universe.upper()} (flat) tidak diset, skip {universe}")
            continue
        # V1.6: kredensial per-universe (kalau di-set) -- spread beban ke
        # jatah kuota "per user" masing-masing identitas, bukan numpuk semua
        # ke satu `gc` (credentials.json) yang juga dipakai 9 batch sekaligus.
        gc_flat = _gc_for(CREDENTIALS_FLAT[universe])
        flat_spreadsheets[universe] = open_by_key_with_retry(gc_flat, ss_id)
        print(f"Connected (flat): {universe}")

    broker_registries = {}
    for universe, ss_id in BROKSUM_DB_BROKER_IDS.items():
        if not ss_id:
            print(f"  BROKSUM_DB_BROKER_{universe.upper()} tidak diset, skip broker-DB {universe}")
            continue
        # Broker individual (BROKSUM_DB_*_Broker / flatlog) pakai kredensial
        # per-universe (CREDENTIALS_IDX/EIPO/BFR2016) -- service account
        # BEDA dari credentials.json biasa, di-share HANYA ke spreadsheet
        # broker-level ini (lihat HANDOVER_FINAL_BrokerDB.md).
        gc_broker = _gc_for(CREDENTIALS_BROKER[universe])
        broker_registries[universe] = BrokerDbRegistry(universe, gc_broker, ss_id)
        print(f"Connected (broker-level): {universe}")

    tickers_by_universe, ticker_target = load_tickers()
    total = sum(len(v) for v in tickers_by_universe.values())
    print(f"\nTotal ticker: {total}")
    for _u, _t in sorted(tickers_by_universe.items()):
        print(f"  {_u}: {len(_t)} ticker")
    print()

    if MODE_DAILY:
        run_daily(tickers_by_universe, ticker_target, flat_spreadsheets, broker_registries)
    if MODE_WEEKLY:
        run_period_only("weekly", tickers_by_universe, ticker_target)
    if MODE_MONTHLY:
        run_period_only("monthly", tickers_by_universe, ticker_target)

    print(f"\n{'='*60}")
    print(f"  SEMUA MODE SELESAI -- {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}\n")
