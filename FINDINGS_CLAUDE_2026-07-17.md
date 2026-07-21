# Temuan Sesi Claude — 17 Jul 2026

Ringkasan investigasi dan perbaikan sepanjang sesi ini, dari perbaikan workflow
`stockbit.yml` sampai debugging `refresh-bearer.yml`. Ditulis supaya sesi
berikutnya (Claude lain atau Codex) tidak mengulang jalur yang sudah terbukti
salah.

## 1. Perbaikan workflow `stockbit.yml` (selesai, sudah di-commit)

### 1.1 `wait-for-data` gagal tapi `fetch-unified` tetap jalan

Akar masalah: `if` di job `fetch-unified` cuma mengecek
`needs.check-bearer.result == 'success'`, tidak pernah mengecek hasil
`needs.wait-for-data`. Jadi walau `wait-for-data` merah (mis. bearer 401),
`fetch-unified` tetap start.

Fix: tambah syarat eksplisit
`needs.wait-for-data.result == 'success' || needs.wait-for-data.result == 'skipped'`.

### 1.2 Cancel workflow tidak menghentikan job yang sedang jalan

Akar masalah: job `fetch-unified` dan `notify-done` pakai `if: always()`.
`always()` di GitHub Actions secara dokumentatif membuat job/step TETAP jalan
walau run di-cancel manual — bukan bug acak, itu memang perilaku resmi
`always()`.

Fix: ganti `always()` → `!cancelled()` di kedua job itu. `!cancelled()` tetap
mengizinkan job jalan walau ada `needs` yang skip, tapi tetap ikut berhenti
kalau run di-cancel.

Status: sudah diterapkan di `stockbit.yml` (lalu disempurnakan lebih lanjut
oleh Codex, termasuk gating `fetch-ohlc` ke `wait-for-data` juga per 16 Jul).

## 2. Fallback Telegram `/bearer <token>` (tidak terselesaikan, bukan di repo ini)

Sudah digrep menyeluruh di `stockbit-runner`, `stockbit-fetcher-runner`, dan
`stockbit-autorunner` — tidak ada kode yang menangani pesan Telegram masuk
(`getUpdates`/webhook) untuk command `/bearer`. Yang ada cuma notifikasi
outbound yang menyuruh user mengirim command itu. Kesimpulan: mekanisme itu
kemungkinan hidup di sistem lain di luar ketiga folder ini, atau sudah putus
sejak refactor besar penambahan gap-scan. Solusi yang diambil: bukan
memperbaiki jalur Telegram itu, tapi menggantinya total dengan auto-refresh
bearer GitHub-native (lihat bagian 3).

## 3. Auto-refresh bearer 100% GitHub Actions — batasan nyata

### 3.1 Kenapa GitHub-hosted runner biasa tidak cukup

Login Stockbit yang tidak minta OTP/approval berulang bergantung pada
"trusted device" — fingerprint browser + profile yang stabil dari waktu ke
waktu. GitHub-hosted runner adalah VM baru tiap kali jalan (IP beda, fingerprint
beda), jadi tidak bisa dipakai untuk ini. Solusi yang dipakai: self-hosted
runner (jalan di komputer sendiri, `/home/fatih/actions-runner-stockbit-bearer`),
supaya profile Chrome persisten (`~/.stockbit-bearer/profiles/<akun>`) bisa
dipertahankan antar-run.

Konsekuensi yang perlu disadari: ini TETAP butuh komputer nyala 24/7 (bukan
solusi "komputer boleh mati") — cuma orkestrasi jadwal/trigger-nya yang
sekarang lewat GitHub, bukan cron lokal.

### 3.2 Akun `primary` — root cause: bukan approval HP, tapi anti-bot detection

Kronologi diagnosis (penting, supaya tidak diulang):

1. Gejala awal: `login_stockbit()` selalu bilang "Sesi belum/tidak login" dan
   0 kandidat token tertangkap, bahkan saat cuma MEMBACA sesi lama (belum
   submit form apa pun).
2. Dugaan awal (SALAH sebagian): masalah symlink profile `primary` rusak.
   Dicek — symlink ke `/home/fatih/chrome-remote-profile-akun2` ternyata
   valid dan isinya nyata (bukan folder kosong).
3. Dugaan kedua (SALAH): Chrome enkripsi cookies pakai OS keyring
   (GNOME Keyring/libsecret via DBUS session), dan runner non-desktop-session
   tidak bisa akses itu, jadi cookies gagal didekripsi. Fix yang diterapkan:
   `--password-store=basic`. **Perbaikan ini tetap dipertahankan** karena valid
   untuk skenario itu, tapi TERNYATA bukan penyebab utama kegagalan yang
   diamati.
4. User membuktikan manual: login lewat Chrome asli (`google-chrome
   --user-data-dir=chrome-remote-profile-akun2`) sukses mulus TANPA approval
   HP. Ini membuktikan profile & kredensial valid.
5. Tapi Playwright headless (bahkan setelah manual login sukses) tetap gagal
   identik. Ditambah polling wait 20 detik (dugaan: butuh waktu approve HP) —
   **tidak membantu**, sudah di-revert. Screenshot debug (`DEBUG_LOGIN=true`)
   akhirnya mengungkap yang sebenarnya terjadi:
   - Setelah submit email+password, halaman pindah ke homepage sebagai
     **guest** (tombol Login/Register masih ada), bukan ke feed yang sudah
     login.
   - Tidak ada pesan error apa pun ditampilkan.
   - Ini pola khas anti-bot/fingerprint detection: request login diproses,
     tapi ditolak diam-diam karena browser dikenali sebagai automation
     (headless Chromium bawaan Playwright).

Kesimpulan saat ini: kredensial `primary` VALID (dikonfirmasi manual), profile
valid, tapi **Playwright headless Chromium kemungkinan besar terdeteksi
sebagai bot oleh Stockbit** dan login-nya ditolak diam-diam.

### 3.3 Percobaan fix "channel=chrome + headless=False + Xvfb" — DIBATALKAN

Upaya: bikin automation semirip mungkin dengan Chrome manual yang terbukti
sukses — pakai `channel="chrome"` (binary Chrome asli, bukan Chromium bawaan
Playwright), `headless=False` (jalan headed via Xvfb), plus
`--disable-blink-features=AutomationControlled`.

Kenapa dibatalkan: step `playwright install chrome` di CI memicu Playwright
otomatis menjalankan `sudo apt-get install ...` di baliknya (pesan "Switching
to root user to install dependencies..."), dan karena runner CI tidak
interaktif, `sudo` menunggu password yang tidak pernah datang → step macet
total (~beberapa menit, harus di-cancel manual). Ini terjadi bahkan setelah
dependency di-install manual duluan via terminal (`sudo pip3 install
--break-system-packages playwright && sudo python3 -m playwright install
--with-deps chrome`) — kemungkinan Playwright tetap mencoba `sudo apt-get`
setiap kali `install chrome` dipanggil, terlepas apakah paketnya sudah
lengkap atau belum.

Fix yang tersedia tapi BELUM diterapkan (atas permintaan user, di-revert dulu
ke kondisi awal): tambahkan sudoers rule `NOPASSWD` khusus untuk
`apt-get`/`apt` bagi user runner, supaya `sudo apt-get` yang dipanggil
Playwright tidak nyangkut minta password:

```bash
sudo visudo -f /etc/sudoers.d/stockbit-bearer-runner
# isi:
fatih ALL=(root) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt
```

Status akhir sesi ini: sudah di-**revert total** ke `headless=True` + Chromium
bawaan Playwright (kondisi sebelum eksperimen ini), supaya tidak ada risiko
macet di CI. `--password-store=basic` tetap dipertahankan (tidak berkaitan
dengan hang, dan tetap valid untuk kasus keyring).

### 3.4 Akun `secondary` — kasus BEDA, bukan bug

Profile `secondary` (`~/.stockbit-bearer/profiles/secondary`) belum pernah
dipakai login sama sekali (bukan symlink ke mana pun, folder baru kosong).
Saat dicoba, muncul **OTP email asli** yang terdeteksi NORMAL oleh kode, dan
gagal dengan pesan yang benar: "OTP dibutuhkan tapi script jalan
non-interaktif". Ini bukan bug — memang perlu satu kali login manual di
profile itu (isi OTP manual) supaya jadi trusted, mirip proses yang sudah
dilakukan untuk `primary` dulu.

## 4. Rekomendasi untuk sesi berikutnya

1. **Jangan ulangi jalur `channel=chrome` + Xvfb** tanpa dulu memasang sudoers
   `NOPASSWD` untuk apt (lihat 3.3) — kalau tidak, step install akan macet
   lagi.
2. Untuk akun `primary`, opsi yang belum dicoba dan lebih ringan (tidak perlu
   ganti browser/mode) untuk mengurangi deteksi bot:
   - tambah `--disable-blink-features=AutomationControlled` ke Chromium
     headless yang ADA SEKARANG (tanpa ganti ke Chrome asli/headed/Xvfb).
   - pertimbangkan header/locale/timezone context tambahan supaya makin mirip
     browser asli.
   - kalau semua opsi ringan gagal, baru pertimbangkan lagi jalur Chrome asli
     + Xvfb, tapi siapkan sudoers dulu SEBELUM mencoba di CI.
3. Untuk akun `secondary`: login manual sekali (isi OTP) di
   `~/.stockbit-bearer/profiles/secondary` sebelum mengandalkan auto-refresh
   akun ini.
4. Ingat baseline yang sudah terbukti jalan sebelumnya (dari audit Codex):
   akun `primary` PERNAH berhasil auto-refresh penuh via self-hosted runner
   dengan profile trusted `chrome-remote-profile-akun2` — jadi kegagalan
   sekarang kemungkinan regresi baru (sesi Stockbit yang expired ulang, atau
   perubahan deteksi bot dari sisi Stockbit), bukan berarti pendekatan
   self-hosted-nya salah dari awal.
5. Repo kerja yang benar tetap:
   `/home/fatih/Documents/Saham Indo/Python/stockbit-runner/stockbit-fetcher-runner`
   (bukan folder induk `stockbit-runner`, bukan `stockbit-autorunner`).

## 5. Update Codex — solusi final CDP 9223 (selesai, 17 Jul 2026)

Bagian 3.2-3.3 di atas sekarang harus dibaca sebagai histori investigasi,
bukan rekomendasi final. Root cause praktisnya: script lama membuka browser
baru (`launch_persistent_context`) sehingga Stockbit melihat fingerprint/device
berbeda dari Chrome yang sudah trusted. Solusi yang berhasil adalah attach ke
Chrome asli yang sudah berjalan lewat CDP port `9223`.

Perubahan yang diterapkan:

- `stockbit_auth_bearer_pusher.py` mendukung `STOCKBIT_CDP_URL`.
- `.github/workflows/refresh-bearer.yml` memakai CDP tetap per account:
  `primary` -> `http://127.0.0.1:9223`, `secondary` -> `http://127.0.0.1:9225`.
- Saat CDP hidup, script attach ke Chrome itu, buka tab baru Stockbit, ambil
  bearer dari request network, validasi, lalu tutup tab.
- Saat CDP mati, script launch `google-chrome` sendiri dengan profile
  `${HOME}/.stockbit-bearer/profiles/<account>` dan port dari
  `STOCKBIT_CDP_URL`, lalu menutup Chrome hanya jika Chrome itu dibuka oleh
  script.
- Dalam mode CDP, script tidak submit login otomatis. Kalau Chrome belum login
  Stockbit, script gagal cepat dan minta login manual di Chrome terkait.

Validasi yang sudah berhasil:

```text
STOCKBIT_ACCOUNT_LABEL=primary \
STOCKBIT_CDP_URL=http://127.0.0.1:9223 \
DEBUG_LOGIN=true \
python3 -c "from stockbit_auth_bearer_pusher import login_stockbit; t=login_stockbit(); print('TOKEN_OK', t[:15]+'...'+t[-8:], len(t))"

TOKEN_OK ... len=831
```

End-to-end push GitHub Secret juga sudah berhasil:

```text
Bearer token VALID ditemukan
Push ke GitHub secret BEARER_TOKEN @ kebeletBAB/stockbit-fetcher-runner ...
Bearer token berhasil di-push ke GitHub Secret.
```

Catatan penting untuk sesi berikutnya:

1. Jangan kembali ke jalur headless Chromium untuk akun `primary` kecuali
   memang ingin debug legacy fallback.
2. Jalur utama refresh bearer `primary` sekarang adalah Chrome CDP `9223`.
3. Port `9223` boleh sedang dipakai selama browser/profile itu memiliki sesi
   Stockbit valid untuk `primary`. Script tidak menggerakkan mouse/keyboard;
   hanya membuka dan menutup tab lewat CDP.
4. Kalau port `9223` hidup tetapi memakai profile lain yang tidak login
   Stockbit, script akan gagal sebagai `Chrome CDP belum login ke Stockbit`.
5. `secondary` memakai port CDP tetap `9225`; jangan diarahkan ke `9223`
   karena `9223` adalah sesi/profile primary.

## Update Codex — fallback primary ke secondary (17 Jul 2026)

Behavior final refresh bearer setelah secondary tervalidasi:

- Cron/schedule tetap mulai dari `primary`.
- `primary` memakai Chrome CDP `http://127.0.0.1:9223`.
- `secondary` memakai Chrome CDP `http://127.0.0.1:9225`.
- Jika `primary` gagal pada tahap login/ambil bearer/token capture, workflow
  otomatis fallback ke `secondary`.
- Jika kegagalan terjadi setelah token didapat, misalnya `gh secret set` gagal
  atau trigger workflow fetch gagal, fallback tidak dijalankan karena masalahnya
  bukan akun Stockbit.
- Kegagalan login primary saat fallback tersedia tidak mengirim Telegram gagal
  dulu, supaya tidak ada notifikasi false alarm sebelum secondary dicoba.
- Kalau PC/runner mati, workflow tidak bisa menolong: primary dan secondary
  sama-sama tidak jalan. Kalau PC hidup dan Chrome/profile tersedia, refresh
  berjalan lewat CDP sebagaimana mestinya.

Validasi secondary lokal sudah sukses dengan command non-push:

```text
STOCKBIT_ACCOUNT_LABEL=secondary \
STOCKBIT_CDP_URL=http://127.0.0.1:9225 \
DEBUG_LOGIN=true \
python3 -c "from stockbit_auth_bearer_pusher import login_stockbit; t=login_stockbit(); print('TOKEN_OK', t[:15]+'...'+t[-8:], len(t))"

TOKEN_OK ... len=835
```

Catatan: file ini tetap lokal dan jangan di-upload ke GitHub.

## Update Codex — fallback tiga lapis final (20 Jul 2026)

Bagian sebelumnya tentang CDP tetap relevan sebagai histori, tetapi desain
operasional final sekarang tiga lapis:

1. `primary` lewat Chrome CDP `http://127.0.0.1:9223`
2. `secondary` lewat Chrome CDP `http://127.0.0.1:9225`
3. fallback terakhir persistent Playwright memakai akun `secondary` dan profile
   autorunner:
   `/home/fatih/Documents/Saham Indo/Python/stockbit-autorunner/logs/chrome_profile`

Perubahan penting:

- mode CDP sekarang bisa auto-login ID/password bila port hidup tetapi sesi
  Stockbit belum login.
- jika CDP mati dan runner service tidak punya `DISPLAY`, script gagal cepat
  dan fallback lanjut ke tahap berikutnya.
- fallback ketiga memakai profile Playwright autorunner yang sudah dipanaskan
  oleh cron harian `stockbit_checkpoint.py`.
- akun secondary belum verifikasi KTP, jadi secondary harus dianggap cadangan
  minimal untuk refresh bearer singkat, bukan jalur rutin workload berat.
- fallback hanya dilakukan untuk gagal login/ambil bearer/token capture
  (`push_bearer_to_github.py` exit 10). Jika token sudah didapat tetapi gagal
  `gh secret set` atau trigger workflow fetch, fallback tidak dijalankan.

Catatan operasional:

- layar monitor boleh mati, tetapi PC tidak boleh sleep/suspend.
- Chrome CDP perlu port hidup (`9223`/`9225`) bila runner berjalan sebagai
  service tanpa sesi grafis.
- fallback Playwright autorunner seharusnya jarang dipakai dan normalnya cepat,
  karena cron harian menjaga profile tetap fresh/trusted.

## Update Codex — status final workflow Stockbit (17 Jul 2026 malam)

Perubahan final yang sudah dipush ke GitHub sampai commit `e615c48`:

- `refresh-bearer.yml`
  - `primary` memakai Chrome CDP `http://127.0.0.1:9223`.
  - `secondary` memakai Chrome CDP `http://127.0.0.1:9225`.
  - cron/schedule mulai dari `primary`; jika gagal pada tahap login/token
    capture, workflow fallback otomatis ke `secondary`.
  - jika gagal di tahap non-akun seperti `gh secret set` atau trigger workflow
    fetch, fallback tidak dijalankan.
- Sinkronisasi bearer manual
  - `Refresh Stockbit Bearer`, `Stockbit Fetcher`, dan `Stockbit Profile
    Monthly` semua menulis ke secret yang sama: `BEARER_TOKEN`.
  - input bearer manual dari `Stockbit Fetcher` atau `Stockbit Profile Monthly`
    akan dipakai untuk run itu dan sekaligus update secret untuk run berikutnya.
  - catatan penting: secret update tidak mengubah env job yang sudah terlanjur
    berjalan; efeknya untuk run/job berikutnya.
- `stockbit.yml`
  - downstream job tidak lagi memakai job output bearer karena GitHub Actions
    mengosongkan/masking output yang berisi secret.
  - semua job memakai ekspresi langsung `inputs.bearer || secrets.BEARER_TOKEN`.
  - `check_bearer.py` dan debug curl sekarang validasi endpoint workload nyata
    `marketdetectors/BBCA` dengan `limit=25`, bukan `limit=1`.
- `profile-monthly.yml`
  - manual bearer input ditambahkan dan bisa update `BEARER_TOKEN`.
  - downstream job memakai `inputs.bearer || secrets.BEARER_TOKEN`, bukan output
    bearer antar-job.
  - cancel behavior diperbaiki: tidak lagi memaksa lanjut dengan `always()` saat
    workflow dibatalkan.
- `stockbit_profile.py`
  - endpoint profile diganti dari `/emitten/<ticker>/profile` ke
    `/emitten/<ticker>/info` karena request browser Stockbit sekarang memakai
    endpoint `/info`.
  - jika semua ticker gagal, script exit gagal supaya workflow tidak lagi
    false-success dengan pesan retry ratusan ticker.
- `stockbit_broksum.py` / `wait-for-data`
  - interval tunggu dibuat configurable.
  - workflow sekarang set `WAIT_FOR_DATA_ATTEMPTS=18` dan
    `WAIT_FOR_DATA_SLEEP_SECONDS=900`, jadi cek tiap 15 menit dengan total
    tunggu sekitar 4 jam 15-30 menit.
  - batas ini masih aman untuk GitHub-hosted runner default 360 menit / 6 jam.

Status operasional yang diharapkan:

- Kalau bearer expired dan PC/runner mati: refresh otomatis tidak bisa jalan dan
  fetch/profile akan gagal sampai token diperbarui manual atau PC hidup lagi.
- Kalau bearer expired dan PC/runner hidup: refresh bearer bisa ambil token dari
  primary, fallback ke secondary jika perlu, lalu update `BEARER_TOKEN`.
- Kalau `Refresh Stockbit Bearer` queue, bearer manual bisa dimasukkan lewat
  `Stockbit Fetcher` atau `Stockbit Profile Monthly`; keduanya akan sinkron ke
  secret yang sama untuk run berikutnya.

Catatan: file ini tetap lokal dan jangan di-upload ke GitHub.
