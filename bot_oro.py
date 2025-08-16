# bot_oro.py
# --------------------------------------------
# BOT ORO – Monitor + Log + Notifiche (WS-only)
# --------------------------------------------

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========= Config base =========
SYMBOL = "PAXGUSDT"              # Oro tokenizzato su Binance
HEARTBEAT_SECS = int(os.getenv("HEARTBEAT_SECS", "60"))  # frequenza heartbeat / refresh "Ultimo ping"

# Foglio Google: usa variabile d'ambiente GOOGLE_CREDENTIALS (JSON) oppure file locale
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # obbligatoria

# Telegram (opzionale ma consigliato)
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # es. "203729322"

# Fuso orario Italia (CET/CEST) per timestamp leggibili
TZ_ITALY = timezone(timedelta(hours=2))  # in estate UTC+2; se vuoi auto-DST usa pytz

# ========= Utility orario =========
def now_str():
    # Timestamp compatibile con quanto vedi nei tuoi screenshot
    return datetime.now(TZ_ITALY).strftime("%Y-%m-%d %H:%M:%S")

# ========= Notifiche =========
def notify_telegram(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TG_CHAT_ID, "text": text}
        requests.post(url, json=payload, timeout=10)
    except Exception:
        # Non interrompe il bot se Telegram è giù
        pass

# ========= Google Sheets =========
class SheetLogger:
    def __init__(self, spreadsheet_id: str):
        if not spreadsheet_id:
            raise RuntimeError("SPREADSHEET_ID mancante")

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        creds = None
        raw = os.getenv("GOOGLE_CREDENTIALS")
        if raw:
            try:
                creds_dict = json.loads(raw)
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            except Exception as e:
                raise RuntimeError(f"GOOGLE_CREDENTIALS non è un JSON valido: {e}")
        else:
            # fallback a file locale se preferisci
            fname = "google_credentials.json"
            if not os.path.exists(fname):
                raise RuntimeError("Credenziali Google mancanti (env GOOGLE_CREDENTIALS o file google_credentials.json).")
            creds = ServiceAccountCredentials.from_json_keyfile_name(fname, scope)

        client = gspread.authorize(creds)
        self.sheet = client.open_by_key(spreadsheet_id)
        # Caching dei worksheet
        self.ws_log = self.sheet.worksheet("Log")
        self.ws_trade = self.sheet.worksheet("Trade")

    def log(self, level: str, message: str, extra: str = "bot"):
        """Scrive una riga su Log: [Data/Ora, Livello, Messaggio, Note]"""
        try:
            self.ws_log.append_row(
                [now_str(), level, message, extra],
                value_input_option="USER_ENTERED"
            )
        except Exception as e:
            # Ultimo fallback: niente crash
            print(f"[ERRORE] Scrittura Log fallita: {e}")

    def log_heartbeat(self, price: float | None):
        msg = "Heartbeat OK" if price is None else f"Heartbeat OK – {price:.2f}"
        self.log("INFO", msg, "bot")

    def update_last_ping(self, price: float):
        """Aggiorna Trade!K2 con 'YYYY-mm-dd HH:MM:SS – prezzo'."""
        try:
            text = f"{now_str()} - {price:.2f}"
            # fix definitivo: usare update (valori prima, range dopo)
            self.ws_trade.update("K2", [[text]], value_input_option="RAW")
        except Exception as e:
            self.log("ERRORE", f"Aggiornamento Ultimo ping fallito: {e}", "bot")

# ========= Prezzo =========
def get_price_binance(symbol: str) -> float | None:
    """Ritorna ultimo prezzo da Binance REST. None se non disponibile."""
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return float(data["price"])
    except Exception:
        return None

# ========= MAIN LOOP =========
def main():
    logger = SheetLogger(SPREADSHEET_ID)

    # Annuncio avvio
    logger.log("BOT ATTIVO", "Bot Oro avviato")
    notify_telegram("🤖 Bot Oro avviato correttamente!")
    print("[START] Bot Oro avviato")

    # Loop continuo
    while True:
        try:
            price = get_price_binance(SYMBOL)

            if price is not None:
                # 1) Heartbeat nel log con prezzo
                logger.log_heartbeat(price)
                # 2) Aggiornamento “Ultimo ping” su Trade!K2
                logger.update_last_ping(price)
            else:
                # Heartbeat anche se il prezzo non è disponibile
                logger.log_heartbeat(None)

        except Exception as e:
            # Non deve mai fermarsi
            logger.log("ERRORE", f"Loop exception: {e}", "bot")

        time.sleep(HEARTBEAT_SECS)

# ========= Run =========
if __name__ == "__main__":
    main()
