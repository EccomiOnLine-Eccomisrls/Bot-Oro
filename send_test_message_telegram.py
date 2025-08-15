import os
import requests

# Legge le variabili d'ambiente
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }
    r = requests.post(url, json=payload)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    try:
        resp = send_telegram_message("✅ Test invio da Bot Oro su Telegram!")
        print("[OK] Inviato:", resp)
    except Exception as e:
        print("[ERRORE] Invio fallito:", e)
