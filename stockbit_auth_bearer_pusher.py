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
import time
import subprocess
from urllib.parse import urlparse
from urllib.request import urlopen

import requests

try:
    import keyring
except Exception:
    keyring = None

SERVICE_NAME = "stockbit_bearer_pusher"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/125.0.0.0 Safari/537.36"
)


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


def _account_slug(account_label: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in account_label).strip("-_")
    return slug or "default"


def _default_profile_dir(account_label: str) -> str:
    return os.path.join(_BASE_DIR, "logs", "bearer_profiles", _account_slug(account_label))


def _default_cdp_profile_dir(account_label: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".stockbit-bearer", "profiles", _account_slug(account_label))


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
                "user-agent": _USER_AGENT,
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


def _cdp_is_ready(cdp_url: str, timeout: float = 1.0) -> bool:
    try:
        with urlopen(cdp_url.rstrip("/") + "/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _launch_chrome_for_cdp(cdp_url: str, profile_dir: str):
    parsed = urlparse(cdp_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if not port:
        raise RuntimeError(f"STOCKBIT_CDP_URL harus berisi port: {cdp_url}")

    chrome_bin = os.environ.get("STOCKBIT_CHROME_BIN", "google-chrome")
    cmd = [
        chrome_bin,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-address={host}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--new-window",
        "https://stockbit.com/stream",
    ]

    print(f"🚀 Chrome CDP belum hidup, launch Chrome port {port} ...")
    proc = subprocess.Popen(cmd)
    for _ in range(30):
        if _cdp_is_ready(cdp_url):
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"Chrome berhenti sebelum CDP siap (exit={proc.returncode}).")
        time.sleep(1)

    raise RuntimeError(f"Chrome sudah diluncurkan tapi CDP belum siap: {cdp_url}")


def _stop_launched_chrome(proc):
    if not proc or proc.poll() is not None:
        return
    print("🧹 Tutup Chrome CDP yang dibuka oleh script ...")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()




def _has_login_entrypoint(page) -> bool:
    return page.query_selector("text=LOG IN") is not None or page.query_selector("text=Login") is not None


def _capture_token_from_logged_in_page(page, captured, log_dir: str, debug_mode: bool, label: str) -> str | None:
    print(f"🍪 Cek sesi Stockbit dari Chrome aktif ({label}) ...")
    page.goto("https://stockbit.com/stream", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    _dismiss_modal(page)

    if debug_mode:
        _debug_screenshot(page, log_dir, "debug_01_stream.png")
        print(f"   [DEBUG] URL stream: {page.url}")

    if _has_login_entrypoint(page):
        raise RuntimeError(
            "Chrome CDP belum login ke Stockbit.\n"
            "Buka Chrome port CDP terkait, login manual ke Stockbit, lalu jalankan ulang script."
        )

    print("   ✅ Sesi Chrome aktif sudah login.")
    page.goto("https://stockbit.com/symbol/IHSG", wait_until="domcontentloaded", timeout=30000)
    _wait_network(page, 8)
    if debug_mode:
        _debug_screenshot(page, log_dir, "debug_02_ihsg_session_reuse.png")
        print(f"   [DEBUG] URL IHSG reuse: {page.url}")
        print(f"   [DEBUG] Kandidat request tertangkap: {len(captured.get('candidates', []))}")

    token = _find_valid_token(captured, debug=debug_mode)
    if token:
        return token

    print("   ⚠️  Token belum tertangkap, retry wait lebih lama ...")
    _wait_network(page, 12)
    return _find_valid_token(captured, debug=debug_mode)

def login_stockbit(
    username: str | None = None,
    password: str | None = None,
    profile_dir: str | None = None,
    account_label: str | None = None,
) -> str:
    from playwright.sync_api import sync_playwright

    account_label = account_label or os.environ.get("STOCKBIT_ACCOUNT_LABEL", "default")
    cdp_url = os.environ.get("STOCKBIT_CDP_URL", "").strip()
    username = username or _read_secret("STOCKBIT_USERNAME", "username")
    password = password or _read_secret("STOCKBIT_PASSWORD", "password")

    if not cdp_url and (not username or not password):
        raise RuntimeError(
            "Credential Stockbit tidak ditemukan.\n"
            "Set env STOCKBIT_USERNAME/STOCKBIT_PASSWORD atau setup keyring lokal."
        )

    log_dir = os.path.join(_BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    profile_dir = profile_dir or os.environ.get("STOCKBIT_PROFILE_DIR", "").strip()
    if not profile_dir:
        profile_dir = _default_cdp_profile_dir(account_label) if cdp_url else _default_profile_dir(account_label)
    os.makedirs(profile_dir, exist_ok=True)

    debug_mode = os.environ.get("DEBUG_LOGIN", "false").lower() == "true"
    captured = {"token": None, "candidates": []}

    with sync_playwright() as p:
        if cdp_url:
            launched_chrome = None
            if not _cdp_is_ready(cdp_url):
                launched_chrome = _launch_chrome_for_cdp(cdp_url, profile_dir)

            print(f"🔌 Connect ke Chrome CDP ({cdp_url}) ...")
            try:
                browser = p.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                _stop_launched_chrome(launched_chrome)
                raise RuntimeError(
                    f"Tidak bisa connect ke Chrome CDP: {cdp_url}\n"
                    "Pastikan Chrome untuk Stockbit dijalankan dengan "
                    "--remote-debugging-port, dan portnya sesuai STOCKBIT_CDP_URL."
                ) from e

            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                _intercept_token(page, captured)
                token = _capture_token_from_logged_in_page(page, captured, log_dir, debug_mode, account_label)
                page.close()
                if token:
                    return token
                if debug_mode:
                    print(f"   [DEBUG] Final kandidat token: {len(captured.get('candidates', []))}")
                raise RuntimeError(
                    "Token tidak berhasil ditangkap dari Chrome CDP.\n"
                    "Pastikan Chrome di STOCKBIT_CDP_URL masih login dan halaman Stockbit bisa memuat data."
                )
            finally:
                _stop_launched_chrome(launched_chrome)

        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--password-store=basic",
            ],
            user_agent=_USER_AGENT,
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

        already_logged_in = not _has_login_entrypoint(page)

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

        page.wait_for_timeout(3000)
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
