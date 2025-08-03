
import os
import gspread
from datetime import datetime, timedelta
from twilio.rest import Client
from binance.client import Client as BinanceClient

# ==== CONFIGURAZIONI ====
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")
WHATSAPP_FROM = os.getenv("WHATSAPP_FROM")
WHATSAPP_TO = os.getenv("WHATSAPP_TO")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
BINANCE_API = os.getenv("BINANCE_API")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")
LOSS_LIMIT = -3.0  # % massimo di perdita giornaliera
SYMBOL = "PAXGUSDT"
SL = -0.5
TP1 = 1.0
TP2 = 2.0

# ==== CONNESSIONI ====
gc = gspread.service_account(filename="bot-oro-4807603afb6b.json")
sh = gc.open(SPREADSHEET_NAME)
ws = sh.sheet1
try:
    report_ws = sh.worksheet("Report")
except:
    report_ws = sh.add_worksheet(title="Report", rows="1000", cols="10")
twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
binance_client = BinanceClient(BINANCE_API, BINANCE_SECRET)

# ==== FUNZIONI ====
def send_whatsapp(msg):
    twilio_client.messages.create(
        from_=f"whatsapp:{WHATSAPP_FROM}",
        to=f"whatsapp:{WHATSAPP_TO}",
        body=msg
    )

def get_price():
    ticker = binance_client.get_symbol_ticker(symbol=SYMBOL)
    return float(ticker['price'])

def log_trade(price, note="Operazione di test"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws.append_row([now, "TEST", price, "", "", "", price, "OK", 0.0, note])

def calculate_daily_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    records = ws.get_all_records()
    daily = [r for r in records if r['Data/Ora'].startswith(today)]
    count = len(daily)
    total_profit = sum(float(r.get('Profitto (%)', 0)) for r in daily)
    return count, total_profit

def write_daily_report():
    count, profit = calculate_daily_stats()
    now = datetime.now().strftime("%Y-%m-%d")
    report_ws.append_row([f"Report Giornaliero {now}", count, profit])
    send_whatsapp(f"📊 Report giornaliero {now}\nOperazioni: {count}\nProfitto: {profit:.2f}%")

def write_weekly_report():
    records = ws.get_all_records()
    week_ago = datetime.now() - timedelta(days=7)
    weekly = [r for r in records if datetime.strptime(r['Data/Ora'], "%Y-%m-%d %H:%M") >= week_ago]
    count = len(weekly)
    total_profit = sum(float(r.get('Profitto (%)', 0)) for r in weekly)
    report_ws.append_row([f"Report Settimanale {datetime.now().strftime('%Y-%m-%d')}", count, total_profit])
    send_whatsapp(f"📊 Report settimanale\nOperazioni: {count}\nProfitto: {total_profit:.2f}%")

def main():
    price = get_price()
    log_trade(price)
    send_whatsapp(f"📈 Bot ORO attivo! Prezzo {SYMBOL}: {price} USD - Operazione registrata.")
    print("[OK] Operazione registrata.")

if __name__ == "__main__":
    main()
