# GitHub-Native Bearer Refresh

Dokumen ini menjelaskan arsitektur refresh bearer yang sekarang dipakai.

## Tujuan

- refresh bearer dijalankan oleh GitHub Actions
- akun bisa dipilih saat `workflow_dispatch`
- browser profile persisten per akun dipertahankan di self-hosted runner
- workflow fetch utama tetap hanya membaca `BEARER_TOKEN`
- input manual bearer di workflow fetch tetap bisa dipakai sebagai fallback

## Komponen

### Producer token

Workflow:

- `.github/workflows/refresh-bearer.yml`

Script:

- `push_bearer_to_github.py`
- `stockbit_auth_bearer_pusher.py`

Fungsi:

- pilih akun
- login Stockbit
- tangkap bearer
- validasi token
- update GitHub Secret `BEARER_TOKEN`
- opsional trigger workflow fetch utama

### Consumer token

Workflow:

- `.github/workflows/stockbit.yml`

Fungsi:

- membaca `inputs.bearer || secrets.BEARER_TOKEN`
- memvalidasi token via `check_bearer.py`
- menjalankan fetch utama

## Runner yang dibutuhkan

Refresh bearer **bukan** untuk GitHub-hosted runner ephemeral.

Yang dibutuhkan:

- self-hosted runner Linux
- label runner:
  - `self-hosted`
  - `linux`
  - `stockbit-bearer`

Alasan:

- Stockbit mengandalkan browser/profile yang persisten
- trusted device harus tetap sama antar run
- OTP diharapkan hanya muncul saat profile baru/rusak

## Profile browser per akun

Workflow refresh menyimpan profile per akun di runner:

- `${HOME}/.stockbit-bearer/profiles/primary`
- `${HOME}/.stockbit-bearer/profiles/secondary`

Jangan hapus folder ini kecuali memang ingin paksa re-auth akun terkait.

## Secrets GitHub yang dibutuhkan

### Wajib untuk refresh bearer

- `STOCKBIT_PRIMARY_USERNAME`
- `STOCKBIT_PRIMARY_PASSWORD`
- `STOCKBIT_SECONDARY_USERNAME`
- `STOCKBIT_SECONDARY_PASSWORD`
- `GH_PAT`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

### Tetap dipakai workflow fetch utama

- `BEARER_TOKEN`
- `GOOGLE_CREDENTIALS`
- `GOOGLE_CREDENTIALS_IDX`
- `GOOGLE_CREDENTIALS_EIPO`
- `GOOGLE_CREDENTIALS_BFR2016`
- `BROKSUM_DB_IDX`
- `BROKSUM_DB_EIPO`
- `BROKSUM_DB_BFR2016`
- `BROKSUM_DB_BROKER_IDX`
- `BROKSUM_DB_BROKER_EIPO`
- `BROKSUM_DB_BROKER_BFR2016`

## Cara pakai

### Schedule normal

Workflow `refresh-bearer.yml` jalan otomatis pada:

- `50 4 * * 1-5` UTC

Setelah sukses:

- `BEARER_TOKEN` di-update
- workflow fetch utama terjadwal tetap bisa memakai token baru

### Manual trigger

`workflow_dispatch` menyediakan input:

- `account`
  - `primary`
  - `secondary`
- `trigger_fetch`
  - `true`
  - `false`
- `force_fetch_run`
  - `true`
  - `false`
- `debug_login`
  - `true`
  - `false`

### Pola pakai saat GitHub molor

1. jalankan `Refresh Stockbit Bearer`
2. pilih `account`
3. bila perlu set `trigger_fetch=true`
4. bila fetch harus dipaksa walau jam terlambat/libur, set `force_fetch_run=true`

## OTP behavior

Asumsi desain ini:

- OTP bukan bagian rutin
- OTP hanya muncul bila browser/profile dianggap baru atau rusak

Jika OTP muncul di runner non-interaktif:

- workflow refresh akan gagal
- Telegram akan memberi sinyal gagal login
- recovery harus dilakukan pada runner/profile akun terkait

Setelah profile akun pulih, run berikutnya diharapkan normal tanpa OTP.

## Catatan workflow utama

`stockbit.yml` sudah dirapikan agar:

- `fetch-unified` tidak jalan bila `wait-for-data` gagal
- `notify-done` tidak jalan saat workflow di-cancel
- beberapa `always()` yang menyebabkan perilaku sulit dihentikan sudah diganti guard `!cancelled()`

## Fallback manual

Fallback tetap ada:

- user bisa mengambil bearer manual dari browser mana pun
- lalu memasukkan token lewat `workflow_dispatch` di `stockbit.yml`

Artinya sistem sekarang punya 2 producer:

- producer otomatis: `refresh-bearer.yml`
- producer manual: input `bearer` pada `stockbit.yml`

