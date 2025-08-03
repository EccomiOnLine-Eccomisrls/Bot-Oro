
import os
import json
import time
import gspread
from twilio.rest import Client
from datetime import datetime, timedelta
from binance.client import Client as BinanceClient
import random

print("=== Avvio BOT in modalità DEBUG ===")

# === CONFIG ===
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
DESTINATION_NUMBER = os.getenv("DESTINATION_NUMBER")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
SPREADSHEET_NAME = "BOT ORO – TEST"

# === GOOGLE SHEETS ===
try:
    creds = json.loads(GOOGLE_CREDENTIALS)
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open(SPREADSHEET_NAME)
    sheet_operations = sh.sheet1
    try:
        sheet_summary = sh.worksheet("Riepilogo")
    except:
        sheet_summary = sh.add_worksheet(title="Riepilogo", rows=20, cols=5)
    print("✅ Connessione a Google Sheets riuscita.")
    sheet_operations.append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "DEBUG", "BOT AVVIATO"])
except Exception as e:
    print("❌ Errore connessione Google Sheets:", e)

# === BINANCE + TWILIO ===
try:
    binance_client = BinanceClient(BINANCE_API_KEY, BINANCE_API_SECRET)
    prezzo_test = binance_client.get_symbol_ticker(symbol="PAXGUSDT")
    print("✅ Connessione a Binance riuscita. Prezzo PAXG:", prezzo_test)
except Exception as e:
    print("❌ Errore connessione Binance:", e)

try:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    twilio_client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, body="BOT AVVIATO IN DEBUG - Connessione OK", to=DESTINATION_NUMBER)
    print("✅ Messaggio di test WhatsApp inviato.")
except Exception as e:
    print("❌ Errore invio WhatsApp:", e)

print("=== Fine fase di test DEBUG. Se vedi tutti i ✅, il bot è pronto a funzionare. ===")
