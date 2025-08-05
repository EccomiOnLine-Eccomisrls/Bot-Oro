import time
import gspread
from twilio.rest import Client
from datetime import datetime, timedelta
from binance.client import Client as BinanceClient
import json
import os

# === CONFIG da Environment ===
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
DESTINATION_NUMBER = os.getenv("DESTINATION_NUMBER")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
SPREADSHEET_NAME = "BOT ORO – TEST"

STOP_LOSS = float(os.getenv("STOP_LOSS", -0.5))
TAKE_PROFIT1 = float(os.getenv("TAKE_PROFIT1", 1))
TAKE_PROFIT2 = float(os.getenv("TAKE_PROFIT2", 2))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", -3))
TRADE_SIZE = float(os.getenv("TRADE_SIZE", 1))

# === CONNESSIONI ===
binance_client = BinanceClient(BINANCE_API_KEY, BINANCE_API_SECRET)
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
creds = json.loads(GOOGLE_CREDENTIALS)
gc = gspread.service_account_from_dict(creds)
sh = gc.open(SPREADSHEET_NAME)
sheet_operations = sh.sheet1
try:
    sheet_summary = sh.worksheet("Riepilogo")
except:
    sheet_summary = sh.add_worksheet(title="Riepilogo", rows=100, cols=10)

# === FUNZIONI ===
def send_whatsapp(message):
    try:
        twilio_client.messages.create(
            body=message,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=DESTINATION_NUMBER
        )
        print("[INFO] Messaggio inviato su WhatsApp.")
    except Exception as e:
        print("[ERRORE] Invio WhatsApp fallito:", e)

def get_price():
    try:
        data = binance_client.get_symbol_ticker(symbol="PAXGUSDT")
        return float(data['price'])
    except Exception as e:
        print("[ERRORE] Lettura prezzo Binance:", e)
        return 0.0

def simulate_trade(price):
    outcome = "WIN" if (datetime.now().second % 2 == 0) else "LOSS"
    pct = TAKE_PROFIT1 if outcome == "WIN" else STOP_LOSS
    return outcome, pct

def log_trade():
    price = get_price()
    outcome, profit_pct = simulate_trade(price)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    profit_value = TRADE_SIZE * (profit_pct / 100)
    sheet_operations.append_row([
        now, "SIMULAZIONE", price, STOP_LOSS, TAKE_PROFIT1, TAKE_PROFIT2,
        profit_value, outcome, profit_pct, "Operazione simulata"
    ])
    print(f"[OK] Operazione registrata: {outcome} {profit_pct}%")
    return profit_pct

def update_summary():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    sheet_summary.append_row([now, "Aggiornamento", "Report automatico inviato"])

def daily_report():
    now = datetime.now().strftime("%Y-%m-%d")
    sheet_summary.append_row([now, "Giornaliero", "Riepilogo giornaliero inviato"])
    send_whatsapp(f"📅 Riepilogo Giornaliero {now}\nOperazioni simulate registrate.")

def weekly_report():
    now = datetime.now().strftime("%Y-%m-%d")
    sheet_summary.append_row([now, "Settimanale", "Riepilogo settimanale inviato"])
    send_whatsapp(f"📊 Riepilogo Settimanale {now}\nOperazioni simulate registrate.")

# === AVVIO ===
now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
try:
    sheet_operations.update('B1', [[f"Bot attivo – {now}"]])  # <<< FIX QUI
    print(f"[INFO] Stato bot aggiornato su Google Sheets: Bot attivo – {now}")
except Exception as e:
    print("[ERRORE] Impossibile aggiornare B1:", e)

send_whatsapp(
    f"🚀 Bot ORO Simulatore Avviato\nSL: {STOP_LOSS}% TP1: {TAKE_PROFIT1}% TP2: {TAKE_PROFIT2}%\nPerdita massima giornaliera: {DAILY_LOSS_LIMIT}%"
)

profit_today = 0
while True:
    profit_pct = log_trade()
    profit_today += profit_pct
    if profit_today <= DAILY_LOSS_LIMIT:
        send_whatsapp(f"⚠️ Raggiunto limite perdita {DAILY_LOSS_LIMIT}%. Parametri adattati.")
        TAKE_PROFIT1 *= 0.8
        TAKE_PROFIT2 *= 0.8
    hour = datetime.now().hour
    minute = datetime.now().minute
    if hour in [8,12,16,20] and minute == 0:
        update_summary()
    if hour == 0 and minute == 0:
        daily_report()
    if datetime.now().weekday() == 6 and hour == 0 and minute == 0:
        weekly_report()
    time.sleep(1800)  # ogni 30 minuti
