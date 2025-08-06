
import os
import json
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client
from twilio.rest import Client as TwilioClient
from datetime import datetime

# ======= CONFIGURAZIONE =======
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
DESTINATION_NUMBER = os.getenv("TWILIO_TO", "whatsapp:+393205616977")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS") or os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_NAME = os.getenv("SHEET_NAME", "Foglio1")

if not SPREADSHEET_ID:
    raise Exception("Variabile d'ambiente SPREADSHEET_ID non trovata")
if not GOOGLE_CREDENTIALS:
    raise Exception("Variabile d'ambiente GOOGLE_CREDENTIALS non trovata")

STOP_LOSS = float(os.getenv("STOP_LOSS", -0.5))
TAKE_PROFIT1 = float(os.getenv("TAKE_PROFIT1", 1))
TAKE_PROFIT2 = float(os.getenv("TAKE_PROFIT2", 2))

# ======= CONNESSIONI =======
binance_client = Client(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDENTIALS), scope)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
print(f"[SHEETS] Connesso al foglio: {SHEET_NAME}")

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
        invia_messaggio(f"⚠️ ERRORE SCRITTURA SHEETS: {e}")

# ======= CICLO PRINCIPALE =======
def main():
    invia_messaggio(f"🤖 Bot Oro avviato! Test scrittura su: {SHEET_NAME}")
    try:
        sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "TEST AVVIO", "Connessione OK"])
        invia_messaggio("✅ Test scrittura su Google Sheets riuscito!")
    except Exception as e:
        invia_messaggio(f"❌ Test scrittura su Google Sheets fallito: {e}")
        print(f"[ERRORE TEST SHEETS] {e}")
    while True:
        try:
            prezzo = float(binance_client.get_symbol_ticker(symbol="PAXGUSDT")["price"])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            scrivi_su_sheets([timestamp, "BOT ATTIVO", prezzo, STOP_LOSS, TAKE_PROFIT1, TAKE_PROFIT2, "", "", "", "Bot in esecuzione"])
            print(f"[BOT] Prezzo attuale: {prezzo}")
        except Exception as e:
            print(f"[ERRORE CICLO] {e}")
            invia_messaggio(f"⚠️ ERRORE CICLO: {e}")
        time.sleep(300)

if __name__ == "__main__":
    main()
