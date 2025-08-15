import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Config mancante.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload)
        if r.status_code == 200:
            print(f"[TELEGRAM] Messaggio inviato: {text}")
        else:
            print(f"[TELEGRAM ERROR] {r.status_code} - {r.text}")
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")

if __name__ == "__main__":
    send_telegram_message("✅ Test Telegram dal Bot Oro")
