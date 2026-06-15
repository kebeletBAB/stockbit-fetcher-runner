import requests
import sys
import os

TOKEN = os.environ.get("BEARER_TOKEN", "")
if not TOKEN:
    print("❌ Bearer token tidak ditemukan di environment!")
    sys.exit(1)

print(f"Token length: {len(TOKEN)}")
headers = {
    "authorization": f"Bearer {TOKEN}",
    "origin": "https://stockbit.com",
    "referer": "https://stockbit.com/",
    "x-platform": "web",
}
try:
    r = requests.get(
        "https://exodus.stockbit.com/marketdetectors/BBCA"
        "?transaction_type=TRANSACTION_TYPE_NET"
        "&market_board=MARKET_BOARD_REGULER"
        "&investor_type=INVESTOR_TYPE_ALL"
        "&limit=1"
        "&period=BROKER_SUMMARY_PERIOD_LATEST",
        headers=headers,
        timeout=10
    )
    print(f"Status: {r.status_code}")
    if r.status_code == 401:
        print("❌ Bearer token EXPIRED!")
        sys.exit(1)
    elif r.status_code in [200, 400]:
        print("✅ Bearer token valid!")
    else:
        print(f"⚠️ Status: {r.status_code}")
        sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
