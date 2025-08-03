
import os
import json
import time
import gspread
from twilio.rest import Client
from datetime import datetime, timedelta
from binance.client import Client as BinanceClient
import random

# === CONFIG ===
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
DESTINATION_NUMBER = os.getenv("DESTINATION_NUMBER")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")  # JSON delle credenziali
SPREADSHEET_NAME = "BOT ORO – TEST"

# === GOOGLE SHEETS ===
creds = json.loads(GOOGLE_CREDENTIALS)
gc = gspread.service_account_from_dict(creds)
sh = gc.open(SPREADSHEET_NAME)
sheet_operations = sh.sheet1
try:
    sheet_summary = sh.worksheet("Riepilogo")
except:
    sheet_summary = sh.add_worksheet(title="Riepilogo", rows=20, cols=5)

# === BINANCE + TWILIO ===
binance_client = BinanceClient(BINANCE_API_KEY, BINANCE_API_SECRET)
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# === PARAMETRI BOT ===
TRADE_SIZE = 1.0
STOP_LOSS = -0.5/100
TAKE_PROFIT1 = 1/100
TAKE_PROFIT2 = 2/100
MAX_POSITIONS = 5
LOSS_ALERT_THRESHOLD = -3.0

def send_whatsapp(message):
    twilio_client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, body=message, to=DESTINATION_NUMBER)

def log_operation(data):
    sheet_operations.append_row(data)

def update_summary(total_trades, wins, losses, profit_pct, projection):
    sheet_summary.update("A1", [["Totale Trades","Vincite","Perdite","Profitto (%)","Proiezione 7g"]])
    sheet_summary.update("A2", [[total_trades,wins,losses,profit_pct,projection]])

def get_price():
    ticker = binance_client.get_symbol_ticker(symbol="XAUUSDT")
    return float(ticker['price'])

def simulate_trade():
    price_entry = get_price()
    sl = price_entry*(1+STOP_LOSS)
    tp1 = price_entry*(1+TAKE_PROFIT1)
    tp2 = price_entry*(1+TAKE_PROFIT2)
    exit_price = random.choice([sl,tp1,tp2])
    outcome = "SL" if exit_price==sl else ("TP1" if exit_price==tp1 else "TP2")
    profit_pct = ((exit_price - price_entry)/price_entry)*100
    return price_entry,sl,tp1,tp2,exit_price,outcome,profit_pct

def run_session(duration_hours):
    start_time = datetime.now()
    end_time = start_time + timedelta(hours=duration_hours)
    trades = 0
    wins = 0
    losses = 0
    profit_total = 0.0
    send_whatsapp(f"🚀 Inizio sessione BOT ORO - Durata: {duration_hours}h")
    while datetime.now() < end_time:
        if trades < MAX_POSITIONS:
            price_entry, sl, tp1, tp2, exit_price, outcome, profit_pct = simulate_trade()
            trades += 1
            profit_total += profit_pct
            if profit_pct>0: wins+=1
            else: losses+=1
            log_operation([datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"BUY",price_entry,sl,tp1,tp2,exit_price,outcome,round(profit_pct,3),""])
            send_whatsapp(f"📊 Trade #{trades}: {outcome} | P/L: {round(profit_pct,2)}%")
            if profit_total <= LOSS_ALERT_THRESHOLD:
                send_whatsapp(f"⚠️ ALERT: perdita sessione {round(profit_total,2)}%")
        time.sleep(5)
    projection = round((profit_total/duration_hours)*24*7,2)
    update_summary(trades,wins,losses,round(profit_total,2),projection)
    send_whatsapp(f"✅ Sessione terminata. Totale: {trades} | Vincite: {wins} | Perdite: {losses} | P/L: {round(profit_total,2)}% | Proiezione 7g: {projection}%")

while True:
    run_session(12)
    run_session(24)
