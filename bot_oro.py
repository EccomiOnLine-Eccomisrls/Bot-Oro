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
HEARTBEAT_SECS = 60              # frequenza heartbeat / refresh "Ultimo ping"

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
import time
import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timezone, timedelta

TZ_ITALY = timezone(timedelta(hours=2))
def now_str():
    return datetime.now(TZ_ITALY).strftime("%Y-%m-%d %H:%M:%S")

class SheetLogger:
    def __init__(self, spreadsheet_id: str):
        if not spreadsheet_id:
            raise RuntimeError("SPREADSHEET_ID mancante")

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        raw = os.getenv("GOOGLE_CREDENTIALS")
        if raw:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(raw), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_credentials.json", scope)

        client = gspread.authorize(creds)
        self.sheet = client.open_by_key(spreadsheet_id)
        self.ws_log = self.sheet.worksheet("Log")
        self.ws_trade = self.sheet.worksheet("Trade")

    def log(self, level: str, message: str, extra: str = "bot"):
        """[Data/Ora, Livello, Messaggio, Note]"""
        try:
            self.ws_log.append_row(
                [now_str(), level, message, extra],
                value_input_option="USER_ENTERED"
            )
            # piccola pausa: riduce gli errori quando subito dopo aggiorniamo K2
            time.sleep(0.4)
        except Exception as e:
            print(f"[ERRORE] Scrittura Log fallita: {e}")

    def log_heartbeat(self, price: float | None):
        msg = "Heartbeat OK" if price is None else f"Heartbeat OK – {price:.2f}"
        self.log("INFO", msg, "bot")

    def update_last_ping(self, price: float):
        """Aggiorna Trade!K2 con 'YYYY-mm-dd HH:MM:SS – prezzo' con retry/backoff."""
        text = f"{now_str()} - {price:.2f}"
        for attempt in range(1, 4):  # 3 tentativi
            try:
                # ✅ formato corretto: range + matrice 2D di valori
                self.ws_trade.update("K2", [[text]], value_input_option="RAW")
                return
            except Exception as e:
                if attempt == 3:
                    self.log("ERRORE", f"Aggiornamento Ultimo ping fallito: {e}", "bot")
                # backoff graduale
                time.sleep(0.5 * attempt)

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
    logger.log("BOT ATTIVO", "bot")
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
