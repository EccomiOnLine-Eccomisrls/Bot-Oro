import os
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from binance.client import Client
import time

# ========================
# CONFIGURAZIONE
# ========================
GOOGLE_CREDS_FILE = "google_credentials.json"  # File credenziali Google
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # ID Google Sheet
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Token Bot Telegram
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # Chat ID Telegram

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# Parametri strategia
ENTRATA_DELTA = -0.5  # % sotto il prezzo attuale per aprire
TAKE_PROFIT = 1.0     # % sopra il prezzo ingresso
STOP_LOSS = -0.5      # % sotto il prezzo ingresso
QUANTITA = 0.5        # Lotto di esempio

# ========================
# CLASSE LOGGER
# ========================
class SheetLogger:
    def __init__(self, creds_json, spreadsheet_id):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_json, scope)
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

# ========================
# FUNZIONE TELEGRAM
# ========================
def send_telegram_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        r = requests.post(url, json=payload)
        r.raise_for_status()
        print(f"[TELEGRAM] Messaggio inviato: {text}")
    except Exception as e:
        print(f"[ERRORE] Invio Telegram: {e}")

# ========================
# FUNZIONI DI TRADING
# ========================
def get_gold_price():
    """Ottiene il prezzo XAUUSDT da Binance."""
    try:
        ticker = binance.get_symbol_ticker(symbol="XAUUSDT")
        return float(ticker["price"])
    except Exception as e:
        logger.log_error(f"Errore lettura prezzo: {e}")
        return None

def apri_trade(prezzo_ingresso):
    logger.log_trade([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "APERTURA", prezzo_ingresso, QUANTITA])
    send_telegram_message(f"📈 Trade aperto\nPrezzo ingresso: {prezzo_ingresso}\nQuantità: {QUANTITA}")

def chiudi_trade(prezzo_uscita, profitto):
    logger.log_trade([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "CHIUSURA", prezzo_uscita, profitto])
    send_telegram_message(f"📉 Trade chiuso\nPrezzo uscita: {prezzo_uscita}\nProfitto: {profitto}")

# ========================
# LOOP PRINCIPALE
# ========================
if __name__ == "__main__":
    logger = SheetLogger(GOOGLE_CREDS_FILE, SPREADSHEET_ID)
    binance = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

    print("[BOT ORO] Avviato - monitoraggio oro in corso...")

    trade_attivo = False
    prezzo_ingresso = None

    while True:
        prezzo_attuale = get_gold_price()
        if prezzo_attuale is None:
            time.sleep(10)
            continue

        print(f"[PREZZO] Oro: {prezzo_attuale}")

        if not trade_attivo:
            prezzo_target_entrata = prezzo_attuale * (1 + ENTRATA_DELTA / 100)
            if prezzo_attuale <= prezzo_target_entrata:
                apri_trade(prezzo_attuale)
                prezzo_ingresso = prezzo_attuale
                trade_attivo = True

        else:
            variazione = (prezzo_attuale - prezzo_ingresso) / prezzo_ingresso * 100
            if variazione >= TAKE_PROFIT:
                chiudi_trade(prezzo_attuale, f"+{variazione:.2f}%")
                trade_attivo = False
            elif variazione <= STOP_LOSS:
                chiudi_trade(prezzo_attuale, f"{variazione:.2f}%")
                trade_attivo = False

        time.sleep(10)  # Controlla ogni 10 secondi
