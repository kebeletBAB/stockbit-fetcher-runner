# Audit Bearer, Workflow, dan Auth Stockbit

Tanggal audit: 2026-07-14  
Lokasi repo: `/home/fatih/Documents/Saham Indo/Python/stockbit-runner`

Dokumen ini merangkum temuan audit agar bisa dipakai lintas sesi. Audit ini tidak mengubah file kode.

## Ringkasan pendek

- Workflow utama fetch masih bergantung pada `BEARER_TOKEN` statis di GitHub Secret.
- Jalur auto-refresh bearer yang ada saat ini masih lokal, memakai Playwright + keyring + cron lokal, bukan GitHub Actions.
- Login Stockbit untuk refresh bearer belum 100% non-interaktif karena masih ada kemungkinan OTP.
- Pemisahan akun bearer-pusher vs akun checkpoint sudah ada di desain lokal, tetapi belum diwujudkan sebagai arsitektur GitHub Actions penuh.
- Perbaikan workflow untuk kasus `wait-for-data` gagal sebagian sudah benar di level gate script, tetapi penanganan cancel belum bersih karena `always()` masih ada di workflow aktif.
- Fallback Telegram `/bearer <token>` tidak terlihat terimplementasi inbound di repo ini; yang ada hanya notifikasi outbound yang menyuruh user mengirim command itu ke bot.

## Cakupan file yang diaudit

- `.github/workflows/stockbit.yml`
- `workflows/stockbit.yml` sebagai pembanding
- `stockbit_auth_bearer_pusher.py`
- `push_bearer_to_github.py`
- `setup_keyring_bearer_pusher.py`
- `stockbit_checkpoint.py`
- `check_bearer.py`
- `stockbit_broksum.py` pada jalur `--wait-only`
- `stockbit_unified_daily.py`
- `cron_setup.sh`
- folder pembanding lama `stockbit-autorunner`

## 1. Struktur alur autentikasi saat ini

### 1.1 Workflow utama fetch

Workflow aktif ada di `.github/workflows/stockbit.yml`.

Alurnya:

1. Trigger:
   - `schedule` pada `0 5 * * 1-5`
   - `workflow_dispatch` dengan input `bearer`, `force_run`, `run_backfill`, `run_broksum_db_backfill`

2. Job `check-bearer`:
   - memakai `inputs.bearer || secrets.BEARER_TOKEN`
   - jika input `bearer` diisi, workflow menulis ulang GitHub Secret `BEARER_TOKEN` via `gh secret set`
   - lalu validasi bearer via `check_bearer.py`
   - jika gagal, kirim notifikasi Telegram

3. Job `wait-for-data`:
   - memanggil `python -u stockbit_broksum.py --wait-only`
   - berfungsi sebagai gate tunggu data settle

4. Job `fetch-unified`:
   - memakai `secrets.BEARER_TOKEN`
   - menjalankan `stockbit_unified_daily.py`
   - setup Google credentials untuk flat aggregate dan broker-level

5. Job lanjutan:
   - `backfill`
   - `backfill-broksum-db`
   - `notify-done`

Referensi:

- `.github/workflows/stockbit.yml:18-319`
- `check_bearer.py:1-42`

### 1.2 Jalur refresh bearer yang sekarang ada

Refresh bearer otomatis sudah ada, tetapi jalurnya bukan GitHub Actions.

Alurnya:

1. `push_bearer_to_github.py` memanggil `login_stockbit()` dari `stockbit_auth_bearer_pusher.py`
2. `login_stockbit()` membuka browser Playwright headless dengan persistent Chrome profile
3. token bearer ditangkap dari request ke `exodus.stockbit.com`
4. token lalu didorong ke GitHub Secret `BEARER_TOKEN` via `gh secret set`
5. script ini didesain untuk dijalankan lewat cron lokal sebelum jam workflow GitHub jalan

Referensi:

- `push_bearer_to_github.py:1-117`
- `stockbit_auth_bearer_pusher.py:1-281`

### 1.3 Jalur checkpoint

`stockbit_checkpoint.py` adalah jalur login lain yang dipakai untuk checkpoint generator, bukan untuk workflow fetch utama.

Karakteristiknya:

- service keyring: `stockbit_radar`
- login via Playwright
- reuse cookies dari `logs/stockbit_session.json`
- jika perlu OTP, minta input manual
- setelah login, token dipakai untuk fetch market mover dan scrape IHSG

Referensi:

- `stockbit_checkpoint.py:29-30`
- `stockbit_checkpoint.py:91-93`
- `stockbit_checkpoint.py:119-303`

### 1.4 Pemisahan akun

Pemisahan akun memang sudah didesain:

- akun checkpoint / radar memakai service `stockbit_radar`
- akun bearer-pusher memakai service `stockbit_bearer_pusher`
- folder profile browser untuk bearer-pusher juga dipisah

Ini sudah sesuai target pemisahan akun, tetapi baru pada workflow lokal, belum pada GitHub Actions penuh.

Referensi:

- `stockbit_checkpoint.py:29`
- `stockbit_auth_bearer_pusher.py:2-12`
- `stockbit_auth_bearer_pusher.py:29`
- `setup_keyring_bearer_pusher.py:1-42`

## 2. Apakah login Stockbit butuh OTP, CAPTCHA, cookie browser, Telegram, atau interaksi manusia

### 2.1 OTP

Ya, OTP masih mungkin dibutuhkan.

Temuan:

- `stockbit_checkpoint.py` mendeteksi halaman OTP lalu meminta input manual.
- `stockbit_auth_bearer_pusher.py` juga mendeteksi OTP dan akan gagal bila script berjalan non-interaktif.

Referensi:

- `stockbit_checkpoint.py:226-277`
- `stockbit_auth_bearer_pusher.py:216-259`

Kesimpulan:

- login belum bisa dianggap 100% headless/non-interaktif
- OTP masih bagian nyata dari flow auth

### 2.2 Cookie browser / persistent profile

Ya, sangat bergantung pada browser state.

Temuan:

- checkpoint lama memakai reuse cookies dari file JSON
- bearer-pusher baru memakai persistent Chrome profile
- changelog di folder pembanding `stockbit-autorunner` menegaskan bahwa fingerprint browser stabil penting agar Stockbit mengenali trusted device

Referensi:

- `stockbit_checkpoint.py:154-182`
- `stockbit_checkpoint.py:288-293`
- `stockbit_auth_bearer_pusher.py:113-172`
- `stockbit-autorunner/stockbit_auth.py` changelog V6-V7

Kesimpulan:

- login bearer saat ini praktis membutuhkan state browser persisten
- sekadar username/password tanpa state browser tidak cukup untuk andal

### 2.3 CAPTCHA

Tidak ada handling CAPTCHA di kode yang diaudit.

Kesimpulan:

- jika CAPTCHA muncul, implementasi sekarang kemungkinan gagal
- repo ini belum punya strategi eksplisit untuk CAPTCHA

### 2.4 Telegram

Telegram bukan bagian dari login Stockbit.

Telegram saat ini dipakai untuk:

- notifikasi bearer expired
- notifikasi sukses/gagal push bearer
- notifikasi checkpoint

Saya tidak menemukan implementasi inbound bot handler untuk menerima `/bearer <token>` di repo ini.

Kesimpulan:

- Telegram hanya notifikasi outbound pada repo ini
- fallback `/bearer` tampaknya hidup di sistem lain di luar repo ini, atau sudah terputus

### 2.5 Interaksi manusia

Masih diperlukan pada kondisi tertentu.

Kondisi yang memaksa interaksi manusia:

- OTP muncul saat sesi belum trusted
- browser profile hilang/korup/expired
- sesi dianggap device baru

Pada bearer-pusher, jika OTP muncul di mode non-interaktif, script akan keluar gagal dan meminta run manual sekali.

Referensi:

- `stockbit_auth_bearer_pusher.py:231-239`

## 3. Apakah refresh bearer 100% di GitHub Actions realistis

### Jawaban singkat

Belum realistis dengan arsitektur yang ada sekarang, khususnya bila memakai GitHub-hosted runner biasa.

### Alasan teknis

1. Jalur auth yang ada bergantung pada persistent browser profile
   - GitHub-hosted runner bersifat ephemeral
   - state browser tidak menetap antar run kecuali dibuat mekanisme penyimpanan khusus

2. OTP masih mungkin muncul
   - bila OTP muncul pada run non-interaktif, script bearer-pusher akan gagal
   - tidak ada mekanisme otomasi OTP di repo ini

3. Trusted device tampaknya penting
   - pembanding `stockbit-autorunner` menunjukkan migrasi dari `storage_state()` ke persistent Chrome profile karena reuse cookie biasa tidak cukup stabil

4. Tidak ada API resmi Stockbit untuk refresh bearer di repo ini
   - token ditangkap dari traffic browser, bukan diperoleh dari endpoint login API yang terdokumentasi

### Kesimpulan yang bisa dipakai untuk keputusan kerja

- `100% GitHub Actions` mungkin hanya realistis bila:
  - ada self-hosted runner yang mempertahankan state browser persisten, atau
  - ada cara baru yang benar-benar non-browser dan non-OTP, yang saat ini belum ada di repo

- `100% GitHub-hosted runner murni` saat ini tidak realistis untuk solusi yang andal

## 4. Secrets yang dibutuhkan

### 4.1 Secrets workflow fetch utama saat ini

Berdasarkan workflow aktif:

- `BEARER_TOKEN`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GH_PAT`
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

Referensi:

- `.github/workflows/stockbit.yml:51-56`
- `.github/workflows/stockbit.yml:132-149`
- `.github/workflows/stockbit.yml:211-217`
- `.github/workflows/stockbit.yml:245-259`

### 4.2 Secret/credential tambahan jika refresh bearer mau dibawa ke GitHub Actions

Yang kemungkinan dibutuhkan:

- username/email akun Stockbit bearer-pusher
- password akun Stockbit bearer-pusher
- mekanisme penyimpanan state browser persisten
- token GitHub yang bisa update Actions secret, jika tetap memakai pola `gh secret set`
- Telegram token/chat id bila notifikasi tetap dipakai

Catatan:

- repo saat ini belum punya desain aman dan final untuk menyimpan state browser persisten di GitHub Actions

## 5. Risiko keamanan

### 5.1 Risiko tertinggi yang langsung terlihat

Ada artefak sensitif di workspace lokal.

Saya menemukan indikasi secret/token ada di file lokal seperti:

- `.env`
- `oauth_credentials.json`
- `oauth_token.json`
- `nohup.out`
- `Format Terminal/format-terminal.txt`

Saya tidak menyalin nilainya ke dokumen ini, tetapi keberadaan file-file itu sendiri adalah temuan keamanan.

### 5.2 Risiko pada bearer workflow

- bearer token diperlakukan sebagai credential yang bisa dipakai langsung untuk akses data Stockbit
- fallback `/bearer <token>` via Telegram berisiko membocorkan token ke channel chat
- `GH_PAT` yang bisa menulis repo secrets adalah secret bernilai tinggi
- browser profile bearer-pusher di `logs/` adalah aset sensitif; bila dicuri, sesi trusted device bisa ikut bocor

### 5.3 Risiko desain GitHub Actions

- jika username/password Stockbit dimasukkan ke GitHub Secrets, compromise workflow/repo admin bisa mengekspos credential utama
- jika state browser dipindah ke artifact/secret tanpa desain yang hati-hati, itu bisa menjadi titik bocor baru

### 5.4 Risiko operasional

- adanya dua workflow file (`.github/workflows/stockbit.yml` dan `workflows/stockbit.yml`) berisiko membingungkan maintainer
- adanya workflow fetch dan auto-refresh bearer pada jalur berbeda membuat orang bisa salah mengira GitHub sudah self-sufficient, padahal masih bergantung pada cron lokal

## 6. Apakah perubahan workflow sebelumnya sudah benar dan lengkap

### 6.1 Temuan terhadap kasus wait-for-data gagal

Di script gate, perilakunya sudah benar.

`stockbit_broksum.py --wait-only`:

- memanggil `wait_for_today_data()`
- bila gagal setelah 30 menit, `exit(1)`
- bila sukses, `exit(0)`

Referensi:

- `stockbit_broksum.py:379-426`

Artinya:

- jika job `wait-for-data` gagal, GitHub Actions secara default memang akan menahan job downstream yang `needs` job itu

### 6.2 Temuan terhadap workflow aktif

Di workflow aktif yang saya baca:

- `fetch-unified` memang punya `needs: [check-bearer, wait-for-data]`
- tetapi saya tidak melihat syarat job-level tambahan yang eksplisit memeriksa hasil `wait-for-data`

Kesimpulan:

- secara fungsional, gate ini kemungkinan sudah cukup karena exit code + `needs`
- tetapi jika kebijakan yang diinginkan adalah syarat eksplisit `needs.wait-for-data.result == 'success'`, itu belum tercermin di file aktif

### 6.3 Temuan terhadap cancel workflow

Bagian ini belum lengkap.

Workflow aktif masih memakai `always()` pada titik penting:

- `fetch-unified` step upload artifact: `.github/workflows/stockbit.yml:166-172`
- `backfill` step upload artifact: `.github/workflows/stockbit.yml:194-200`
- `backfill-broksum-db` notify: `.github/workflows/stockbit.yml:230-235`
- `notify-done` job-level: `.github/workflows/stockbit.yml:241-245`
- `notify-done` setup credentials retry: `.github/workflows/stockbit.yml:302-308`

Temuan penting:

- target audit sebelumnya menyebut `always()` sudah diganti menjadi `!cancelled()` pada `fetch-unified` dan `notify-done`
- tetapi pada file workflow aktif yang saya baca, `notify-done` masih `if: always()`
- jadi perubahan itu belum lengkap atau belum ada di file aktif ini

### 6.4 Putusan audit untuk workflow fix

Status:

- fix `wait-for-data gagal jangan lanjut fetch`: sebagian besar benar secara mekanisme
- fix `workflow bisa dihentikan dengan Cancel`: belum lengkap
- fix yang diklaim sebelumnya: tidak seluruhnya tercermin di workflow aktif

## 7. Fallback Telegram `/bearer <token>`

### Temuan utama

Saya tidak menemukan handler inbound Telegram di repo ini.

Yang ditemukan hanya:

- notifikasi dari workflow: “Bearer Token EXPIRED! Kirim /bearer <token_baru> ke bot.”
- notifikasi dari script lokal

Yang tidak ditemukan:

- endpoint webhook Telegram
- polling `getUpdates`
- parser command `/bearer`
- trigger `workflow_dispatch`
- trigger `repository_dispatch`
- updater GitHub Secret dari command Telegram

### Kesimpulan

Dari sisi repo ini:

- fallback `/bearer <token>` tidak benar-benar terimplementasi inbound
- kemungkinan besar mekanisme itu berada di repo/layanan lain, atau pernah ada lalu putus setelah refactor

## 8. Perubahan file yang kemungkinan diperlukan nanti

Bagian ini bukan eksekusi, hanya daftar perubahan yang kemungkinan dibutuhkan jika lanjut implementasi.

### 8.1 Workflow

- ubah `.github/workflows/stockbit.yml` agar cancel behavior bersih
- hilangkan `always()` pada titik yang seharusnya berhenti saat canceled
- bila diinginkan, tambahkan guard eksplisit hasil `wait-for-data`
- kemungkinan perlu workflow baru khusus refresh bearer

### 8.2 Auth bearer

- refactor `stockbit_auth_bearer_pusher.py` bila ingin dipakai dari GitHub Actions
- tentukan strategi state browser persisten yang aman
- verifikasi apakah persistent profile bisa dipindah/restore dengan andal

### 8.3 Telegram fallback

- jika fallback `/bearer` ingin dipertahankan, perlu implementasi inbound yang jelas
- repositori ini saat ini belum punya bagian itu

### 8.4 Hygiene keamanan

- bersihkan file lokal yang menyimpan token/credential
- rotasi secret yang diduga sudah terekspos lokal
- audit `.gitignore` dan artefak log/output

### 8.5 Dokumentasi

- satukan sumber kebenaran workflow
- hapus atau tandai `workflows/stockbit.yml` sebagai arsip/pembanding agar tidak membingungkan

## 9. Pembanding folder lama `stockbit-autorunner`

Folder lama masih membantu untuk memahami akar masalah auth.

Temuan pembanding:

- ada `stockbit_auth.py` yang merupakan asal/fork dari bearer auth helper baru
- changelog lama menjelaskan bahwa `launch_persistent_context()` dipilih karena reuse cookies biasa tidak cukup menjaga trusted device
- ini menguatkan kesimpulan bahwa auto-refresh bearer bergantung pada state browser yang stabil

Kesimpulan pembanding:

- refactor bearer-pusher tidak rusak secara konsep
- masalah utamanya adalah jalur itu masih lokal dan belum terhubung sebagai solusi GitHub-native

## 10. Kesimpulan akhir audit

1. Alur autentikasi saat ini terbagi dua:
   - workflow fetch GitHub memakai bearer statis dari GitHub Secret
   - auto-refresh bearer berjalan lokal via Playwright + cron + keyring

2. Login Stockbit saat ini masih bisa membutuhkan:
   - OTP
   - browser state persisten
   - interaksi manusia saat sesi dianggap baru/expired

3. Refresh bearer 100% di GitHub-hosted Actions murni belum realistis dengan arsitektur sekarang.

4. Pemisahan akun bearer-pusher dan akun checkpoint sudah benar di desain lokal, tetapi belum otomatis penuh di GitHub Actions.

5. Perbaikan workflow sebelumnya belum lengkap:
   - gate `wait-for-data` pada dasarnya sudah benar
   - cancel behavior belum bersih karena `always()` masih ada di workflow aktif

6. Fallback Telegram `/bearer` tidak terlihat terimplementasi inbound di repo ini.

7. Ada risiko keamanan nyata dari artefak sensitif lokal yang tersimpan di workspace.

## 11. Usulan urutan kerja setelah audit

Jika nanti lanjut implementasi, urutan kerja yang paling aman:

1. bereskan workflow cancel/wait gate dulu
2. putuskan strategi realistis refresh bearer:
   - self-hosted runner persisten, atau
   - tetap lokal tapi dipertegas sebagai dependency eksternal, atau
   - cari metode auth baru yang benar-benar non-OTP
3. baru tentukan apakah fallback Telegram dipertahankan dan diimplementasikan di repo ini atau dipisah
4. lakukan hygiene secret dan dokumentasi

## Update hasil implementasi sesudah audit

Status terbaru sesudah audit ini:

- repo kerja yang dipakai untuk implementasi adalah:
  - `/home/fatih/Documents/Saham Indo/Python/stockbit-runner/stockbit-fetcher-runner`
- workflow refresh bearer GitHub-native sudah dibuat
- self-hosted runner sudah berjalan
- refresh bearer dengan akun `primary` SUDAH BERHASIL

Fakta operasional yang paling penting:

- penyebab kegagalan awal bukan lagi kode capture token, tetapi browser/profile
  yang belum trusted
- saat memakai profile trusted lama:
  - `/home/fatih/chrome-remote-profile-akun2`
  refresh bearer berhasil penuh
- profile `primary` pada runner dipakai lewat symlink ke profile trusted itu

Implikasi:

- baseline sukses saat ini bergantung pada reuse profile trusted
- kalau profile ini hilang/direset/rusak, challenge approval HP kemungkinan
  akan muncul lagi
