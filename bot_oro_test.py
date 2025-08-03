
import time
import gspread
from twilio.rest import Client
from datetime import datetime
from binance.client import Client as BinanceClient
import json
import os

# === CONFIG ===
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
DESTINATION_NUMBER = os.getenv("DESTINATION_NUMBER")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
SPREADSHEET_NAME = "BOT ORO – TEST"

# === CONNESSIONI ===
binance_client = BinanceClient(BINANCE_API_KEY, BINANCE_API_SECRET)
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
creds = json.loads(GOOGLE_CREDENTIALS)
gc = gspread.service_account_from_dict(creds)
sh = gc.open(SPREADSHEET_NAME)
sheet_operations = sh.sheet1

# === FUNZIONI ===
def send_whatsapp(message):
    twilio_client.messages.create(
        body=message,
        from_=TWILIO_WHATSAPP_NUMBER,
        to=DESTINATION_NUMBER
    )

def get_price():
    data = binance_client.get_symbol_ticker(symbol="PAXGUSDT")
    return float(data['price'])

def log_trade():
    price = get_price()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet_operations.append_row([now, "TEST", price, "", "", "", price, "OK", "0.0", "Operazione di test"])
    send_whatsapp(f"📈 Bot ORO attivo!
Prezzo PAXGUSDT: {price} USD
Operazione registrata.")

# === AVVIO ===
send_whatsapp("🚀 Bot ORO avviato con successo! Inizio test di 1 ora.")
for i in range(2):
    log_trade()
    time.sleep(30)  # ogni 30 secondi per test veloce
send_whatsapp("✅ Test completato. Bot ORO operativo.")
