import os
import json
import time
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client as BinanceClient
from twilio.rest import Client as TwilioClient
import requests

# ======= CONFIGURAZIONE =======
# Binance
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# Twilio WhatsApp
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
DESTINATION_NUMBER = os.getenv("TWILIO_TO")  # esempio: "whatsapp:+393201234567"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Google Sheet
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# ======= CLASSI =======
class SheetLogger:
    def __init__(self, spreadsheet_id):
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]

        raw = os.getenv("GOOGLE_CREDENTIALS")
        if not raw:
            raise RuntimeError("Variabile d'ambiente GOOGLE_CREDENTIALS mancante.")

        try:
            creds_dict = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("GOOGLE_CREDENTIALS non è un JSON valido (controlla le \\n nella private_key).")

        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        self.sheet = client.open_by_key(spreadsheet_id)

    def log_trade(self, data):
        try:
            ws = self.sheet.worksheet("Log")
            ws.append_row(data, value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[ERRORE] Scrittura log trade: {e}")

    def log_error(self, message):
        try:
            ws = self.sheet.worksheet("Errori")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ws.append_row([now, message], value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[ERRORE] Scrittura log errore: {e}")

    def log_heartbeat(self):
        try:
            ws = self.sheet.worksheet("Log")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ws.append_row([now, "Heartbeat OK"], value_input_option="USER_ENTERED")
            print(f"[HEARTBEAT] Registrato su Google Sheet alle {now}")
        except Exception as e:
            print(f"[ERRORE] Scrittura log heartbeat: {e}")


class Notifier:
    def __init__(self):
        self.twilio_client = None
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            self.twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    def send_whatsapp(self, message):
        if not self.twilio_client:
            print("[WHATSAPP] Twilio non configurato.")
            return
        try:
            msg = self.twilio_client.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER,
                body=message,
                to=DESTINATION_NUMBER
            )
            print(f"[WHATSAPP] Inviato. SID: {msg.sid}")
        except Exception as e:
            print(f"[ERRORE WHATSAPP] {e}")

    def send_telegram(self, message):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("[TELEGRAM] Non configurato.")
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
            r = requests.post(url, data=payload)
            if r.status_code == 200:
                print("[TELEGRAM] Messaggio inviato.")
            else:
                print(f"[ERRORE TELEGRAM] {r.text}")
        except Exception as e:
            print(f"[ERRORE TELEGRAM] {e}")


# ======= FUNZIONI =======
def get_gold_price():
    """Ottiene il prezzo dell'oro in tempo reale da Binance (XAUUSDT)."""
    try:
        client = BinanceClient(BINANCE_API_KEY, BINANCE_API_SECRET)
        ticker = client.get_symbol_ticker(symbol="XAUUSDT")
        return float(ticker["price"])
    except Exception as e:
        print(f"[ERRORE] Lettura prezzo oro: {e}")
        return None


def main():
    logger = SheetLogger(SPREADSHEET_ID)
    notifier = Notifier()

    print("[BOT] Avviato. Monitoraggio in corso...")

    prezzo_ingresso = None
    capitale_iniziale = 10000
    saldo = capitale_iniziale

    while True:
        prezzo = get_gold_price()
        if prezzo:
            print(f"[PREZZO ORO] {prezzo}")

            if prezzo_ingresso is None:
                prezzo_ingresso = prezzo
                notifier.send_whatsapp(f"📈 Apertura posizione a {prezzo}")
                notifier.send_telegram(f"📈 Apertura posizione a {prezzo}")
                logger.log_trade([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prezzo, "", "Apertura", "", saldo])

            elif prezzo >= prezzo_ingresso * 1.01:
                profitto = (prezzo - prezzo_ingresso) * 10
                saldo += profitto
                notifier.send_whatsapp(f"✅ Take Profit a {prezzo} (+{profitto:.2f} USD)")
                notifier.send_telegram(f"✅ Take Profit a {prezzo} (+{profitto:.2f} USD)")
                logger.log_trade([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prezzo_ingresso, prezzo, "TP", profitto, saldo])
                prezzo_ingresso = None

            elif prezzo <= prezzo_ingresso * 0.995:
                perdita = (prezzo - prezzo_ingresso) * 10
                saldo += perdita
                notifier.send_whatsapp(f"❌ Stop Loss a {prezzo} ({perdita:.2f} USD)")
                notifier.send_telegram(f"❌ Stop Loss a {prezzo} ({perdita:.2f} USD)")
                logger.log_trade([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prezzo_ingresso, prezzo, "SL", perdita, saldo])
                prezzo_ingresso = None

        logger.log_heartbeat()
        time.sleep(10)


if __name__ == "__main__":
    main()
