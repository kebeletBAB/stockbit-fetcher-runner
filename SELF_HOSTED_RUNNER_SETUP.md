# Self-Hosted Runner Setup

Dokumen ini adalah checklist praktis untuk menyalakan workflow refresh bearer.

## Tujuan akhir

Runner ini dipakai untuk:

- menjalankan `.github/workflows/refresh-bearer.yml`
- mempertahankan browser profile per akun
- refresh `BEARER_TOKEN` tanpa laptop utama harus hidup

## Label runner yang wajib

Runner harus punya label:

- `self-hosted`
- `linux`
- `stockbit-bearer`

Workflow refresh akan mencari label itu.

## 1. Siapkan mesin runner

Mesin yang cocok:

- Linux
- koneksi internet stabil
- bisa hidup terus
- punya storage persisten untuk browser profile

Direkomendasikan:

- user khusus untuk runner, mis. `actions`
- home directory permanen

## 2. Install GitHub Actions self-hosted runner

Di repo GitHub:

1. buka `Settings`
2. buka `Actions`
3. buka `Runners`
4. klik `New self-hosted runner`
5. pilih Linux
6. ikuti command install dari GitHub

Saat konfigurasi:

- tambahkan label `stockbit-bearer`

Jika ingin pakai helper script dari repo ini:

1. jalankan `scripts/bootstrap_stockbit_bearer_runner.sh`
2. isi `scripts/stockbit_bearer_runner.env.example` menjadi file env lokal
3. export variabelnya
4. jalankan `scripts/register_stockbit_bearer_runner.sh`

## 3. Install dependency sistem

Minimal yang dibutuhkan:

- `git`
- `python3`
- `python3-pip`
- `gh`

Chromium tidak harus diinstall manual bila Playwright berhasil mengunduh browser-nya, tetapi dependency OS untuk browser headless biasanya tetap perlu.

Untuk Ubuntu/Debian, baseline yang biasanya aman:

```bash
sudo apt update
sudo apt install -y git curl unzip python3 python3-pip
```

Install GitHub CLI jika belum ada:

```bash
type -p curl >/dev/null || sudo apt install curl -y
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
  sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | \
  sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update
sudo apt install gh -y
```

## 4. Pastikan path profile persisten

Workflow refresh akan membuat profile browser di:

```text
${HOME}/.stockbit-bearer/profiles/primary
${HOME}/.stockbit-bearer/profiles/secondary
```

Jangan arahkan HOME ke lokasi sementara.

### Status aktual yang sudah terbukti sukses

Saat ini akun `primary` TIDAK memakai folder profile baru kosong, tetapi memakai
profile trusted lama lewat symlink:

```text
/home/fatih/.stockbit-bearer/profiles/primary -> /home/fatih/chrome-remote-profile-akun2
```

Ini penting karena profile trusted tersebut sudah lolos challenge approval HP
dan berhasil dipakai untuk:

- login Stockbit
- tangkap bearer
- validasi token
- push `BEARER_TOKEN` ke GitHub Secret

Kalau nanti runner atau workflow tiba-tiba gagal login lagi, cek symlink ini
lebih dulu sebelum debugging yang lain.

## 5. Siapkan secrets GitHub

Masuk ke repo GitHub:

`Settings` -> `Secrets and variables` -> `Actions`

Tambahkan:

### Secrets untuk refresh bearer

- `STOCKBIT_PRIMARY_USERNAME`
- `STOCKBIT_PRIMARY_PASSWORD`
- `STOCKBIT_SECONDARY_USERNAME`
- `STOCKBIT_SECONDARY_PASSWORD`
- `GH_PAT`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

### Secrets fetch utama yang harus tetap ada

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

## 6. Scope token GitHub

`GH_PAT` harus bisa:

- membaca repo
- menjalankan workflow
- menulis Actions secret

Minimal praktis:

- `repo`
- `workflow`

Jika nanti Anda ganti pendekatan auth GitHub, ini bisa diperketat lagi.

## 7. Start runner

Masuk ke direktori runner, lalu jalankan:

```bash
./run.sh
```

Atau install sebagai service jika ingin otomatis hidup saat boot:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
```

Jika Anda memakai helper script registrasi repo ini, lokasi default runner:

```text
${HOME}/actions-runner-stockbit-bearer
```

## 8. Smoke test yang disarankan

Urutan tes:

1. runner muncul online di GitHub
2. trigger workflow `Refresh Stockbit Bearer`
3. pilih `account=primary`
4. set `trigger_fetch=false`
5. set `debug_login=true`
6. cek apakah secret `BEARER_TOKEN` berhasil diperbarui

Jika sukses:

7. jalankan lagi dengan `trigger_fetch=true`
8. jika perlu, set `force_fetch_run=true`

### Smoke test yang sudah tervalidasi

Run yang sudah terbukti sukses:

- workflow: `Refresh Stockbit Bearer`
- `account=primary`
- `trigger_fetch=false`
- `force_fetch_run=false`
- `debug_login=true`

Hasil sukses yang pernah teramati:

- login masuk tanpa OTP
- tidak minta approval HP lagi
- 1 kandidat bearer tertangkap
- validasi token `200`
- GitHub Secret `BEARER_TOKEN` berhasil diperbarui

## 9. Jika OTP muncul

Itu berarti profile akun itu belum trusted atau sudah rusak.

Tindakan:

1. login ke mesin runner
2. jalankan flow login manual pada profile akun yang bermasalah
3. pastikan profile tersimpan
4. ulangi workflow refresh

## 10. Jalur fallback

Jika refresh otomatis gagal:

1. ambil bearer manual dari browser seperti biasa
2. jalankan `stockbit.yml` via `workflow_dispatch`
3. isi input `bearer`

Itu tetap jalur fallback resmi.

## 11. Catatan untuk kerja lintas sesi

Supaya tidak bingung pada sesi berikutnya:

- repo aktif yang benar adalah:
  - `/home/fatih/Documents/Saham Indo/Python/stockbit-runner/stockbit-fetcher-runner`
- profile trusted akun `primary` adalah:
  - `/home/fatih/chrome-remote-profile-akun2`
- symlink yang harus tetap ada:
  - `/home/fatih/.stockbit-bearer/profiles/primary`
- workflow refresh aktif:
  - `.github/workflows/refresh-bearer.yml`

Kalau nanti ada agen lain yang melanjutkan, mulai dari asumsi bahwa masalah
path/file workflow sudah beres, dan baseline sukses sekarang bergantung pada
profile trusted tersebut.

## 12. SOP singkat saat token expired / refresh gagal

Ringkasnya:

1. jalankan manual `Refresh Stockbit Bearer`
2. bila sukses, ulangi `Stockbit Fetcher`
3. bila gagal, cek symlink profile `primary`
4. bila challenge HP/OTP muncul, selesaikan challenge akun itu
5. bila perlu update sheet segera, pakai fallback manual input `bearer`

Fallback manual tercepat:

- login manual ke Stockbit
- ambil bearer
- jalankan `Stockbit Fetcher`
- isi field `bearer`
- set `force_run=true` bila perlu

