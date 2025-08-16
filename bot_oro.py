# bot_oro.py
# ------------------------------------------------------------
# BOT ORO – heartbeat + logging + notifiche Telegram
# Timezone allineata (Europe/Rome) e integrazione Google Sheet.
# ------------------------------------------------------------

import os
import json
import time
from datetime import datetime
import pytz
import requests

# Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# opzionale: python-binance (per future estensioni/trading)
try:
    from binance.client import Client as BinanceClient
except Exception:
    BinanceClient = None


# =========[ Config da ENV ]==================================

SPREADSHEET_ID        = os.getenv("SPREADSHEET_ID", "").strip()
GOOGLE_CREDENTIALS    = os.getenv("GOOGLE_CREDENTIALS", "").strip()

TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SYMBOL                = os.getenv("SYMBOL", "PAXGUSDT").upper().strip()
HEARTBEAT_SEC         = int(os.getenv("HEARTBEAT_SEC", "60"))

# dove scriviamo l’“Ultimo ping” nel foglio Trade
TRADE_SHEET_NAME      = os.getenv("TRADE_SHEET_NAME", "Trade")
TRADE_LASTPING_CELL   = os.getenv("TRADE_LASTPING_CELL", "K2")  # colonna “Ultimo ping” (screenshot)

LOG_SHEET_NAME        = os.getenv("LOG_SHEET_NAME", "Log")

# timezone unica e coerente
TZ = pytz.timezone("Europe/Rome")


def now_str() -> str:
    """Ritorna l’ora corrente già convertita in Europe/Rome e formattata."""
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


# =========[ Notifiche Telegram ]=============================

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{self.token}"

    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled():
            return
        try:
            r = requests.post(
                f"{self.base}/sendMessage",
                json={"chat_id": self.chat_id, "text": text}
            )
            r.raise_for_status()
        except Exception as e:
            # niente raise: non blocchiamo il bot per un problema di notifica
            print(f"[TELEGRAM] errore invio: {e}")

    # helper semantici
    def info(self, text: str):  self.send(f"ℹ️ {text}")
    def ok(self, text: str):    self.send(f"✅ {text}")
    def warn(self, text: str):  self.send(f"⚠️ {text}")
    def err(self, text: str):   self.send(f"❌ {text}")


# =========[ Google Sheet logger ]============================

class SheetLogger:
    """
    Scrive su:
      - Foglio Log: righe [Data/Ora, Livello, Messaggio, Sorgente]
      - Foglio Trade: cella 'Ultimo ping' (ad es. K2)
    """
    def __init__(self, creds_json_str: str, spreadsheet_id: str):
        if not creds_json_str:
            raise RuntimeError("GOOGLE_CREDENTIALS mancante.")
        if not spreadsheet_id:
            raise RuntimeError("SPREADSHEET_ID mancante.")

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        try:
            creds_dict = json.loads(creds_json_str)
        except json.JSONDecodeError:
            raise RuntimeError(
                "GOOGLE_CREDENTIALS non è un JSON valido. "
                "Verifica le \\n nella private_key."
            )

        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        gc = gspread.authorize(creds)
        self.ss = gc.open_by_key(spreadsheet_id)

        # cache worksheet
        self.ws_log = self.ss.worksheet(LOG_SHEET_NAME)
        self.ws_trade = self.ss.worksheet(TRADE_SHEET_NAME)

    def _append_log(self, level: str, message: str, source: str = "bot"):
        row = [now_str(), level, message, source]
        self.ws_log.append_row(row, value_input_option="USER_ENTERED")

    def info(self, message: str, source: str = "bot"):
        self._append_log("INFO", message, source)

    def error(self, message: str, source: str = "bot"):
        self._append_log("ERRORE", message, source)

    def heartbeat(self, price: float):
        """
        Scrive:
          - Log: "Heartbeat OK – {price}"
          - Trade!K2 (o cella configurata): "{timestamp} – {price}"
        """
        ts = now_str()
        msg = f"Heartbeat OK – {price}"
        self._append_log("INFO", msg, "bot")

        # Aggiorna “Ultimo ping”
        try:
            self.ws_trade.update(TRADE_LASTPING_CELL, f"{ts} – {price}")
        except Exception as e:
            # non bloccare il loop se la update singola fallisce
            self._append_log("ERRORE", f"Aggiornamento Ultimo ping fallito: {e}", "bot")


# =========[ Price feed ]=====================================

def fetch_price(symbol: str) -> float:
    """
    Ritorna l'ultimo prezzo come float.
    - Prima prova tramite endpoint pubblico Binance (ticker price)
    - In caso di problemi, rilancia l’eccezione
    """
    # REST pubblico (nessuna API key richiesta)
    url = "https://api.binance.com/api/v3/ticker/price"
    try:
        r = requests.get(url, params={"symbol": symbol}, timeout=5)
        r.raise_for_status()
        data = r.json()
        price = float(data["price"])
        return price
    except Exception as e:
        raise RuntimeError(f"Impossibile ottenere prezzo {symbol}: {e}")


# =========[ Main loop ]======================================

def main():
    # init Telegram
    tg = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    # init Sheets
    try:
        logger = SheetLogger(GOOGLE_CREDENTIALS, SPREADSHEET_ID)
    except Exception as e:
        print(f"[BOOT] ERRORE init SheetLogger: {e}")
        tg.err(f"Avvio fallito: errore Google Sheet – {e}")
        raise

    # Avvio
    boot_msg = f"Bot ORO avviato ✓ – simbolo {SYMBOL}, heartbeat ogni {HEARTBEAT_SEC}s"
    print("[BOOT]", boot_msg)
    logger.info("BOT ATTIVO", "bot")
    tg.ok(boot_msg)

    # Loop “sempre vivo”
    while True:
        try:
            px = fetch_price(SYMBOL)
            logger.heartbeat(px)
        except Exception as e:
            err = f"Loop errore: {e}"
            print("[LOOP] ERRORE:", e)
            logger.error(err, "bot")
            tg.err(err)
        finally:
            time.sleep(HEARTBEAT_SEC)


if __name__ == "__main__":
    main()
