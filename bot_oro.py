
import os
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client
from twilio.rest import Client as TwilioClient
from datetime import datetime

# ======= CONFIGURAZIONE =======
# Binance
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = "whatsapp:+14155238886"  # Numero sandbox Twilio
DESTINATION_NUMBER = "whatsapp:+393205616977"     # Il tuo numero (abilitato al sandbox)

# Google Sheets
import json
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

if not GOOGLE_CREDENTIALS:
    raise Exception("Variabile d'ambiente GOOGLE_CREDENTIALS non trovata")

credentials = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDENTIALS), scope)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key(SPREADSHEET_ID).sheet1

# Trading
TRADE_SIZE = float(os.getenv("TRADE_SIZE", 1))
STOP_LOSS = float(os.getenv("STOP_LOSS", -0.5))
TAKE_PROFIT1 = float(os.getenv("TAKE_PROFIT1", 1))
TAKE_PROFIT2 = float(os.getenv("TAKE_PROFIT2", 2))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", -3))

# ======= CONNESSIONI =======
# Binance
binance_client = Client(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)

# Twilio
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials = ServiceAccountCredentials.from_json_keyfile_name("google_credentials.json", scope)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key(SPREADSHEET_ID).sheet1

# ======= FUNZIONI =======
def invia_messaggio(messaggio):
    try:
        twilio_client.messages.create(
            body=messaggio,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=DESTINATION_NUMBER
        )
        print(f"[WHATSAPP] Messaggio inviato: {messaggio}")
    except Exception as e:
        print(f"[ERRORE WHATSAPP] {e}")

def scrivi_su_sheets(dati):
    try:
        sheet.append_row(dati)
        print(f"[SHEETS] Dati scritti: {dati}")
    except Exception as e:
        print(f"[ERRORE SHEETS] {e}")

# ======= CICLO PRINCIPALE =======
def main():
    invia_messaggio("🤖 Bot Oro avviato correttamente e operativo!")
    while True:
        try:
            prezzo = float(binance_client.get_symbol_ticker(symbol="XAUUSDT")["price"])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            scrivi_su_sheets([timestamp, "BOT ATTIVO", prezzo, STOP_LOSS, TAKE_PROFIT1, TAKE_PROFIT2, "", "", "", "Bot in esecuzione"])
            print(f"[BOT] Prezzo attuale: {prezzo}")
        except Exception as e:
            print(f"[ERRORE CICLO] {e}")
        time.sleep(300)  # ogni 5 minuti

if __name__ == "__main__":
    main()
