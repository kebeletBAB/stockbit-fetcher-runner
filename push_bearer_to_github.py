"""
Push Bearer Token ke GitHub Secret — GitHub-native refresh helper
==================================================================
Dipakai workflow refresh-bearer untuk:
  1. login Stockbit dengan akun yang dipilih
  2. tangkap bearer token baru
  3. push token ke GitHub Secret BEARER_TOKEN
  4. opsional trigger workflow fetch utama
"""

import os
import sys
import subprocess

from stockbit_auth_bearer_pusher import login_stockbit, SERVICE_NAME, _from_keyring

REPO = os.environ.get("TARGET_GH_REPO", "kebeletBAB/stockbit-fetcher-runner")
SECRET_NAME = os.environ.get("TARGET_GH_SECRET", "BEARER_TOKEN")
WORKFLOW_FILE = os.environ.get("FETCH_WORKFLOW_FILE", "stockbit.yml")
ACCOUNT_LABEL = os.environ.get("STOCKBIT_ACCOUNT_LABEL", "default")


def kirim_telegram(pesan: str):
    token = os.environ.get("TELEGRAM_TOKEN", "").strip() or _from_keyring("telegram_token")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or _from_keyring("telegram_chat_id")
    if not token or not chat_id:
        return
    try:
        import requests

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": pesan, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"[WARN] Notif Telegram gagal: {e}")


def gh_env():
    env = os.environ.copy()
    if "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env:
        pat = _from_keyring("github_pat")
        if pat:
            env["GH_TOKEN"] = pat
    return env


def push_secret(token: str) -> bool:
    env = gh_env()
    result = subprocess.run(
        ["gh", "secret", "set", SECRET_NAME, "--repo", REPO, "--body", token],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] gh secret set gagal:\n{result.stderr}")
        return False
    return True


def trigger_fetch(env: dict, token: str) -> bool:
    result = subprocess.run(
        [
            "gh",
            "workflow",
            "run",
            WORKFLOW_FILE,
            "--repo",
            REPO,
            "-f",
            f"bearer={token}",
            "-f",
            f"force_run={os.environ.get('FORCE_FETCH_RUN', 'false')}",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] gh workflow run gagal:\n{result.stderr}")
        return False
    print("✅ Workflow fetch berhasil dipicu.")
    return True


def main():
    print("=" * 55)
    print(f"  PUSH BEARER TOKEN -> GitHub Secret ({ACCOUNT_LABEL})")
    print("=" * 55)

    try:
        print(f"Login Stockbit (akun {ACCOUNT_LABEL}, profile Chrome persisten) ...")
        token = login_stockbit(account_label=ACCOUNT_LABEL)
        print(f"Token didapat: {token[:15]}...{token[-8:]} (len={len(token)})")
    except Exception as e:
        msg = f"Gagal ambil bearer token baru: {e}"
        print(f"[ERROR] {msg}")
        kirim_telegram(f"🚨 <b>Push bearer GAGAL (login)</b>\n{msg}")
        sys.exit(1)

    env = gh_env()
    print(f"Push ke GitHub secret {SECRET_NAME} @ {REPO} ...")
    ok = push_secret(token)

    if not ok:
        kirim_telegram(
            f"🚨 <b>Push bearer GAGAL (gh secret set)</b>\n"
            f"Cek auth GitHub/token untuk service {SERVICE_NAME}."
        )
        sys.exit(1)

    print("✅ Bearer token berhasil di-push ke GitHub Secret.")

    if os.environ.get("TRIGGER_FETCH_AFTER_REFRESH", "false").lower() == "true":
        print(f"Trigger workflow fetch {WORKFLOW_FILE} ...")
        if not trigger_fetch(env, token):
            kirim_telegram(
                f"⚠️ <b>Bearer refresh sukses, trigger fetch gagal</b>\n"
                f"Akun: {ACCOUNT_LABEL}\nRepo: {REPO}"
            )
            sys.exit(1)
        kirim_telegram(
            f"✅ <b>Bearer refresh + trigger fetch sukses</b>\n"
            f"Akun: {ACCOUNT_LABEL}\nRepo: {REPO}"
        )
        return

    kirim_telegram(
        f"✅ <b>Bearer token auto-refresh sukses</b>\n"
        f"Akun: {ACCOUNT_LABEL}\nToken baru sudah di-push ke {REPO} secret {SECRET_NAME}."
    )


if __name__ == "__main__":
    main()
