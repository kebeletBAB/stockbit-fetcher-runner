"""
Stockbit Auth — Bearer Pusher (akun terpisah / GitHub-native ready)
===================================================================
Dipakai oleh push_bearer_to_github.py untuk login Stockbit, reuse
persistent browser profile per akun, lalu menangkap bearer token baru.

Credential diutamakan dari environment variables:
  - STOCKBIT_USERNAME
  - STOCKBIT_PASSWORD
  - STOCKBIT_PROFILE_DIR
  - STOCKBIT_ACCOUNT_LABEL

Keyring lokal tetap didukung sebagai fallback untuk pemakaian manual lama.
"""

import os
import sys
import requests

try:
    import keyring
except Exception:
    keyring = None

SERVICE_NAME = "stockbit_bearer_pusher"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _from_keyring(field: str) -> str:
    if keyring is None:
        return ""
    try:
        return keyring.get_password(SERVICE_NAME, field) or ""
    except Exception:
        return ""


def _read_secret(env_name: str, keyring_field: str = "") -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    if keyring_field:
        return _from_keyring(keyring_field).strip()
    return ""


def _default_profile_dir(account_label: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in account_label).strip("-_")
    if not slug:
        slug = "default"
    return os.path.join(_BASE_DIR, "logs", "bearer_profiles", slug)


def _validate_token(token: str, verbose: bool = False) -> bool:
    try:
        resp = requests.get(
            "https://exodus.stockbit.com/order-trade/market-mover"
            "?mover_type=MOVER_TYPE_TOP_GAINER&filter_stocks=FILTER_STOCKS_TYPE_MAIN_BOARD",
            headers={
                "accept": "application/json, text/plain, */*",
                "authorization": f"Bearer {token}",
                "origin": "https://stockbit.com",
                "referer": "https://stockbit.com/",
                "x-platform": "web",
                "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                              "Chrome/125.0.0.0 Safari/537.36",
            },
            timeout=10,
        )
        if verbose:
            print(f"   [DEBUG] Validate → status={resp.status_code}, body={resp.text[:200]}")
        return resp.status_code == 200
    except Exception as e:
        if verbose:
            print(f"   [DEBUG] Validate → EXCEPTION: {e}")
        return False


def _intercept_token(page, captured, debug_requests=None):
    if "candidates" not in captured:
        captured["candidates"] = []

    def on_request(request):
        if debug_requests is not None:
            debug_requests.append(request.url)
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and "exodus.stockbit.com" in request.url:
            token = auth.replace("Bearer ", "").strip()
            if len(token) > 50 and token not in captured["candidates"]:
                captured["candidates"].append(token)

    page.on("request", on_request)


def _find_valid_token(captured, debug=False) -> str | None:
    candidates = captured.get("candidates", [])
    if debug:
        print(f"   [DEBUG] Jumlah token kandidat: {len(candidates)}")
    for i, tok in enumerate(candidates):
        ok = _validate_token(tok, verbose=debug)
        if debug:
            print(f"   [DEBUG] Kandidat #{i+1} ({tok[:15]}...{tok[-8:]}): {'VALID' if ok else 'invalid'}")
        if ok:
            captured["token"] = tok
            print(f"✅ Bearer token VALID ditemukan (kandidat #{i+1}/{len(candidates)})")
            return tok
    return None


def _dismiss_modal(page):
    for sel in ["text=Skip", "button:has-text('Skip')", "[aria-label='close']", ".ant-modal-close"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def _wait_network(page, seconds=8):
    page.wait_for_timeout(seconds * 1000)


def _debug_screenshot(page, log_dir: str, name: str):
    try:
        page.screenshot(path=os.path.join(log_dir, name))
    except Exception as e:
        print(f"   [DEBUG] Screenshot gagal ({name}): {e}")


def login_stockbit(
    username: str | None = None,
    password: str | None = None,
    profile_dir: str | None = None,
    account_label: str | None = None,
) -> str:
    from playwright.sync_api import sync_playwright

    account_label = account_label or os.environ.get("STOCKBIT_ACCOUNT_LABEL", "default")
    username = username or _read_secret("STOCKBIT_USERNAME", "username")
    password = password or _read_secret("STOCKBIT_PASSWORD", "password")

    if not username or not password:
        raise RuntimeError(
            "Credential Stockbit tidak ditemukan.\n"
            "Set env STOCKBIT_USERNAME/STOCKBIT_PASSWORD atau setup keyring lokal."
        )

    log_dir = os.path.join(_BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    profile_dir = profile_dir or os.environ.get("STOCKBIT_PROFILE_DIR", "").strip() or _default_profile_dir(account_label)
    os.makedirs(profile_dir, exist_ok=True)

    debug_mode = os.environ.get("DEBUG_LOGIN", "false").lower() == "true"
    captured = {"token": None, "candidates": []}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=True,
            # --password-store=basic: Chrome di Linux normalnya enkripsi
            # cookies pakai kunci dari OS keyring (GNOME Keyring/libsecret,
            # via DBUS session desktop). Runner self-hosted yang dipicu dari
            # terminal/tmux ./run.sh TIDAK selalu punya DBUS_SESSION_BUS_ADDRESS
            # yang valid ke session desktop -- jadi walau profile & sesi
            # trusted-nya valid (terbukti: login manual via Chrome asli di
            # desktop sukses TANPA approval HP), Playwright headless tetap
            # gagal decrypt cookies dan selalu lihat "belum login". Ini cocok
            # dengan gejala: gagal identik berulang kali meski sudah approve
            # HP manual. Flag ini bikin Chrome pakai kunci enkripsi statis,
            # tidak bergantung keyring OS sama sekali -- konsisten dipakai
            # headless/non-desktop-session. Konsekuensi: profile yang ada
            # sekarang perlu SEKALI login ulang (kemungkinan minta approval
            # HP sekali lagi) supaya cookies-nya ke-encode ulang pakai mode
            # basic ini; setelah itu run berikutnya seharusnya konsisten.
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--password-store=basic",
            ],
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "Chrome/125.0.0.0 Safari/537.36",
        )

        page = context.pages[0] if context.pages else context.new_page()
        _intercept_token(page, captured)

        print(f"🍪 Cek sesi dari profile Chrome persisten ({account_label}) ...")
        page.goto("https://stockbit.com/feed", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        _dismiss_modal(page)
        if debug_mode:
            _debug_screenshot(page, log_dir, "debug_01_feed.png")
            print(f"   [DEBUG] URL feed: {page.url}")

        already_logged_in = page.query_selector("text=LOG IN") is None and page.query_selector("text=Login") is None

        if already_logged_in:
            print("   ✅ Sesi masih valid, tidak perlu login ulang.")
            page.goto("https://stockbit.com/symbol/IHSG", wait_until="domcontentloaded", timeout=30000)
            _wait_network(page, 8)
            if debug_mode:
                _debug_screenshot(page, log_dir, "debug_02_ihsg_session_reuse.png")
                print(f"   [DEBUG] URL IHSG reuse: {page.url}")
            token = _find_valid_token(captured, debug=debug_mode)

            if not token:
                print("   ⚠️  Token belum tertangkap, retry wait lebih lama ...")
                _wait_network(page, 12)
                token = _find_valid_token(captured, debug=debug_mode)

            if token:
                context.close()
                return token
            print("   ⚠️  Sesi valid tapi token gagal ditangkap, lanjut fallback login form ...")
        else:
            print("   ⚠️  Sesi belum/tidak login, lanjut login form ...")

        print(f"🔐 Login form Stockbit ({username}) ...")
        page.goto("https://stockbit.com/login", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        _dismiss_modal(page)

        if debug_mode:
            _debug_screenshot(page, log_dir, "debug_login_page.png")
            print(f"   [DEBUG] URL saat ini: {page.url}")

        for sel in ["input[type='email']", "input[name='username']", "input[name='email']",
                    "input[placeholder*='Email']", "input[placeholder*='email']"]:
            try:
                page.wait_for_selector(sel, timeout=3000)
                page.fill(sel, username)
                print(f"   → Email diisi ({sel})")
                break
            except Exception:
                continue

        for sel in ["input[type='password']", "input[name='password']"]:
            try:
                page.wait_for_selector(sel, timeout=3000)
                page.fill(sel, password)
                print(f"   → Password diisi ({sel})")
                break
            except Exception:
                continue

        for sel in ["button[type='submit']", "button:has-text('Masuk')",
                    "button:has-text('Login')", "button:has-text('Sign in')"]:
            try:
                page.wait_for_selector(sel, timeout=3000)
                page.click(sel)
                print(f"   → Tombol diklik ({sel})")
                break
            except Exception:
                continue

        # V3 (17 Jul 2026 FIX): dulu cuma tunggu 3 detik lalu langsung
        # nyerah kalau masih di /login -- padahal kalau Stockbit minta
        # approval manual via HP (bukan OTP email), orangnya butuh waktu
        # buka HP dan tap approve, jelas lebih dari 3 detik. Sekarang
        # polling sampai APPROVAL_WAIT_SECONDS (default 20 detik, bisa
        # di-override env), berhenti lebih awal begitu URL sudah pindah
        # dari /login (approval granted) atau token sudah tertangkap.
        approval_wait_seconds = int(os.environ.get("APPROVAL_WAIT_SECONDS", "20"))
        poll_interval_ms = 2000
        elapsed_ms = 0
        while elapsed_ms < approval_wait_seconds * 1000:
            page.wait_for_timeout(poll_interval_ms)
            elapsed_ms += poll_interval_ms
            still_on_login = "/login" in page.url
            has_token = bool(captured.get("candidates"))
            if debug_mode:
                print(f"   [DEBUG] Nunggu approval/redirect ... {elapsed_ms // 1000}s "
                      f"(URL: {page.url}, token kandidat: {len(captured.get('candidates', []))})")
            if not still_on_login or has_token:
                print(f"   → Terdeteksi progres setelah {elapsed_ms // 1000}s "
                      f"(redirect dari /login atau token tertangkap)")
                break
        else:
            print(f"   ⚠️  Masih di halaman login setelah {approval_wait_seconds}s "
                  f"-- kalau ini gara-gara approval HP, mungkin belum sempat di-tap.")

        if debug_mode:
            _debug_screenshot(page, log_dir, "debug_03_after_submit.png")
            print(f"   [DEBUG] URL setelah submit: {page.url}")

        otp_selectors = [
            "text=Verification Code",
            "text=Email Verification",
            "input[maxlength='1']",
            "input[type='number']",
        ]
        is_otp_screen = False
        for sel in otp_selectors:
            try:
                page.wait_for_selector(sel, timeout=4000)
                is_otp_screen = True
                break
            except Exception:
                continue

        if is_otp_screen:
            if not sys.stdin.isatty():
                context.close()
                raise RuntimeError(
                    "OTP dibutuhkan tapi script jalan non-interaktif.\n"
                    "Jalankan manual sekali pada runner/browser profile akun ini\n"
                    "untuk isi OTP, supaya trusted profile tersimpan dan run berikutnya\n"
                    "tidak perlu OTP lagi."
                )

            print("\n" + "=" * 55)
            print("  📧 OTP DIBUTUHKAN")
            print("  Cek email kamu, masukkan kode 6 digit di bawah:")
            print("=" * 55)
            otp_code = input("  OTP Code: ").strip()

            otp_boxes = page.query_selector_all("input[maxlength='1']")
            if otp_boxes and len(otp_boxes) >= 6:
                for i, digit in enumerate(otp_code[:6]):
                    otp_boxes[i].fill(digit)
                    page.wait_for_timeout(100)
            else:
                for sel in ["input[type='number']", "input[type='text']", "input[inputmode='numeric']"]:
                    try:
                        page.fill(sel, otp_code)
                        break
                    except Exception:
                        continue

            for sel in ["button:has-text('Continue')", "button:has-text('Verif')",
                        "button[type='submit']", "button:has-text('Lanjut')"]:
                try:
                    page.wait_for_selector(sel, timeout=3000)
                    page.click(sel)
                    print("   → OTP submitted")
                    break
                except Exception:
                    continue

            page.wait_for_timeout(3000)
            if debug_mode:
                _debug_screenshot(page, log_dir, "debug_04_after_otp.png")
                print(f"   [DEBUG] URL setelah OTP: {page.url}")

        print("   → Menunggu token dari network ...")
        try:
            page.goto("https://stockbit.com/", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            _dismiss_modal(page)
        except Exception:
            pass

        try:
            page.goto("https://stockbit.com/symbol/IHSG", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
        except Exception:
            pass

        _wait_network(page, 5)
        if debug_mode:
            _debug_screenshot(page, log_dir, "debug_05_before_token_scan.png")
            print(f"   [DEBUG] URL sebelum scan token: {page.url}")
            print(f"   [DEBUG] Kandidat request tertangkap: {len(captured.get('candidates', []))}")
            sample = captured.get("candidates", [])[:3]
            for i, tok in enumerate(sample, start=1):
                print(f"   [DEBUG] Sample kandidat #{i}: {tok[:15]}...{tok[-8:]}")

        _find_valid_token(captured, debug=debug_mode)

        print(f"🍪 Profile Chrome tersimpan permanen → {profile_dir}")
        context.close()

    if not captured.get("token"):
        if debug_mode:
            print(f"   [DEBUG] Final kandidat token: {len(captured.get('candidates', []))}")
        raise RuntimeError(
            "Token tidak berhasil ditangkap.\n"
            "Cek credential atau coba lagi. Jalankan dengan DEBUG_LOGIN=true untuk detail."
        )

    return captured["token"]


def get_headers(token: str) -> dict:
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {token}",
        "origin": "https://stockbit.com",
        "referer": "https://stockbit.com/",
        "x-platform": "web",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "Chrome/125.0.0.0 Safari/537.36",
    }
