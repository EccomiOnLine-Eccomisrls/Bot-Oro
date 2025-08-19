# bot_oro.py
# --------------------------------------------
# BOT ORO – Simulazione con max 5 posizioni, Ping per riga, P&L, notifiche
# --------------------------------------------

import os
import time
import logging
import json
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client
from twilio.rest import Client as TwilioClient

# ================== CONFIG ==================
HEARTBEAT_SECS = 60   # intervallo aggiornamento ping
MAX_OPEN_TRADES = 5   # massimo numero operazioni aperte
CAPITALE_INIZIALE = 10000

# Binance
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
DESTINATION_NUMBER = os.getenv("TWILIO_TO", "whatsapp:+393205616977")

# Google Sheets
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

# ================== LOGGER ==================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BOT-ORO")

# ================== BOT CLASS ==================
class BotOro:
    def __init__(self):
        # Binance
        self.client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

        # Twilio
        self.twilio = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        # Google Sheets
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        gc = gspread.authorize(creds)

        self.sheet = gc.open_by_key(SPREADSHEET_ID)
        self.ws_trade = self.sheet.worksheet("Trade")
        self.ws_log = self.sheet.worksheet("Log")

        self.last_equity = CAPITALE_INIZIALE

        # colonne del foglio Trade
        self.COLS = {
            "DATA": 1, "ID": 2, "LATO": 3, "STATO": 4, "PREZZO_ING": 5,
            "QTY": 6, "SL": 7, "TP1": 8, "TP2": 9, "PREZZO_CHIUS": 10,
            "PING": 11, "PL_PERC": 12, "PL_VAL": 13, "EQUITY": 14,
            "STRATEGIA": 15, "NOTE": 16
        }

    # ================== UTILS ==================
    def notify_telegram(self, text):
        try:
            self.twilio.messages.create(
                from_=TWILIO_WHATSAPP_NUMBER,
                body=text,
                to=DESTINATION_NUMBER
            )
        except Exception as e:
            logger.error(f"Errore invio notifica: {e}")

    def log(self, msg):
        logger.info(msg)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        self.ws_log.append_row([now, "INFO", msg, "bot"])

    def get_price(self):
        ticker = self.client.get_symbol_ticker(symbol="XAUUSDT")
        return float(ticker["price"])

    # ================== TRADE LOGIC ==================
    def apri_trade(self, lato, prezzo, qty=0.001):
        """Apre trade simulato e lo registra"""
        trade_id = f"PAXGUSDT-{int(time.time()*1000)}"
        row = [
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            trade_id,
            lato,
            "APERTO",
            prezzo,
            qty,
            round(prezzo * (1 - 0.005), 2),   # SL
            0.005, 0.0002,                    # TP1 %, TP2 %
            "", "", "", "", "", "v1", "TP1"
        ]
        self.ws_trade.append_row(row)
        self.log(f"Aperto trade {trade_id} @ {prezzo}")
        self.notify_telegram(f"📈 Nuovo trade aperto {lato} @ {prezzo}")

    def aggiorna_ping(self):
        """Aggiorna ping, P&L e equity di ogni riga con Stato=APERTO"""
        prezzo_attuale = self.get_price()
        righe = self.ws_trade.get_all_values()

        for i, riga in enumerate(righe[1:], start=2):  # salta intestazione
            stato = riga[self.COLS["STATO"] - 1]
            if stato != "APERTO":
                continue

            prezzo_ing = float(riga[self.COLS["PREZZO_ING"] - 1])
            qty = float(riga[self.COLS["QTY"] - 1])

            pl_val = (prezzo_attuale - prezzo_ing) * qty
            pl_perc = (pl_val / self.last_equity) * 100

            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            text_ping = f"{now} - {prezzo_attuale:.2f}"

            self.ws_trade.update_cell(i, self.COLS["PING"], text_ping)
            self.ws_trade.update_cell(i, self.COLS["PL_PERC"], round(pl_perc, 4))
            self.ws_trade.update_cell(i, self.COLS["PL_VAL"], round(pl_val, 2))
            self.ws_trade.update_cell(i, self.COLS["EQUITY"], round(self.last_equity + pl_val, 2))

    def check_trades(self):
        """Chiude i trade se raggiungono SL o TP"""
        prezzo_attuale = self.get_price()
        righe = self.ws_trade.get_all_values()

        for i, riga in enumerate(righe[1:], start=2):
            stato = riga[self.COLS["STATO"] - 1]
            if stato != "APERTO":
                continue

            prezzo_ing = float(riga[self.COLS["PREZZO_ING"] - 1])
            qty = float(riga[self.COLS["QTY"] - 1])
            sl = float(riga[self.COLS["SL"] - 1])

            try:
                tp1 = prezzo_ing * (1 + float(riga[self.COLS["TP1"] - 1]))
                tp2 = prezzo_ing * (1 + float(riga[self.COLS["TP2"] - 1]))
            except:
                tp1, tp2 = prezzo_ing * 1.01, prezzo_ing * 1.02

            chiudi = None
            nota = ""
            if prezzo_attuale <= sl:
                chiudi = prezzo_attuale
                nota = "SL"
            elif prezzo_attuale >= tp2:
                chiudi = prezzo_attuale
                nota = "TP2"
            elif prezzo_attuale >= tp1:
                chiudi = prezzo_attuale
                nota = "TP1"

            if chiudi:
                pl_val = (chiudi - prezzo_ing) * qty
                self.last_equity += pl_val

                self.ws_trade.update_cell(i, self.COLS["STATO"], "CHIUSO")
                self.ws_trade.update_cell(i, self.COLS["PREZZO_CHIUS"], chiudi)
                self.ws_trade.update_cell(i, self.COLS["PL_VAL"], round(pl_val, 2))
                self.ws_trade.update_cell(i, self.COLS["EQUITY"], round(self.last_equity, 2))
                self.ws_trade.update_cell(i, self.COLS["NOTE"], nota)

                self.log(f"Chiuso trade {riga[self.COLS['ID'] - 1]} @ {chiudi} ({nota})")
                self.notify_telegram(f"✅ Trade chiuso {nota} @ {chiudi} (PL: {pl_val:.2f})")

                # rimpiazza subito: apri nuovo trade
                self.apri_trade("LONG", prezzo_attuale)

    # ================== MAIN LOOP ==================
    def run(self):
        self.log("🤖 Bot Oro (sim) avviato – vROW-PING. Max 5 posizioni, SL -0.5%, TP1 +1%, TP2 +2%.")

        prezzo = self.get_price()
        for _ in range(MAX_OPEN_TRADES):
            self.apri_trade("LONG", prezzo)

        while True:
            try:
                self.aggiorna_ping()
                self.check_trades()
                self.log(f"Heartbeat OK – {self.get_price():.2f}")
                time.sleep(HEARTBEAT_SECS)
            except Exception as e:
                self.log(f"Errore loop: {e}")
                time.sleep(10)

# ================== MAIN ==================
if __name__ == "__main__":
    bot = BotOro()
    bot.run()
