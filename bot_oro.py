# bot_oro.py
import os
import json
import time
import threading
import asyncio
import traceback
from datetime import datetime

import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import websockets
import math

# =========================
# Config da Environment
# =========================
SYMBOL = os.getenv("SYMBOL", "PAXGUSDT").lower()   # per Binance WS va in lower
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "60"))

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")          # es: 1234:AA...
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")      # es: 203729322

if not SPREADSHEET_ID:
    raise RuntimeError("Manca SPREADSHEET_ID")
if not GOOGLE_CREDENTIALS:
    raise RuntimeError("Manca GOOGLE_CREDENTIALS (JSON intero)")

# =========================
# Telegram Notifier
# =========================
class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None):
        self.token = token
        self.chat_id = chat_id

    def send(self, text: str):
        if not self.token or not self.chat_id:
            return  # disattivato se non configurato
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            r = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=10)
            r.raise_for_status()
        except Exception as e:
            print(f"[TELEGRAM][ERR] {e}")

# =========================
# Google Sheet Logger
# =========================
class SheetLogger:
    def __init__(self, spreadsheet_id: str, creds_json_str: str):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        try:
            creds_dict = json.loads(creds_json_str)
        except json.JSONDecodeError as e:
            raise RuntimeError("GOOGLE_CREDENTIALS non è un JSON valido") from e

        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        gc = gspread.authorize(creds)
        self.sh = gc.open_by_key(spreadsheet_id)

        # Worksheet richieste dal progetto
        self.ws_log = self._get_ws("Log")
        self.ws_trade = self._get_ws("Trade")

    def _get_ws(self, title: str):
        try:
            return self.sh.worksheet(title)
        except gspread.WorksheetNotFound:
            # crea se manca
            ws = self.sh.add_worksheet(title=title, rows=2000, cols=20)
            return ws

    def log(self, level: str, msg: str):
        """Scrive su sheet Log: [timestamp, level, msg]"""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.ws_log.append_row([now, level, msg], value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[LOG][ERR] {e}")

    def heartbeat(self, price: float | None):
        """Segnala che il bot è vivo (su Log)."""
        txt = "Heartbeat OK" if price is None else f"Heartbeat OK – {price}"
        self.log("INFO", txt)

    def update_trade_last_ping(self, ts: str, price: float | None) -> int:
        """
        Aggiorna colonna K ('Ultimo ping') per tutte le righe APERTO nel foglio Trade.
        Ritorna quante righe ha aggiornato.
        Layout atteso (intestazioni riga 1):
        A:Data/Ora B:ID trade C:Lato D:Stato ... K:Ultimo ping
        """
        try:
            values = self.ws_trade.get_all_values()
            if not values:
                return 0
            header = values[0]
            # Individuo le colonne chiave
            try:
                col_stato = header.index("Stato")
            except ValueError:
                # fallback: la colonna D come nel tuo sheet
                col_stato = 3
            try:
                col_ping = header.index("Ultimo ping")
            except ValueError:
                # fallback: la colonna K (indice 10)
                col_ping = 10

            updates = []
            for r_idx in range(1, len(values)):  # dalla riga 2
                row = values[r_idx]
                stato = row[col_stato] if col_stato < len(row) else ""
                if stato.strip().upper() == "APERTO":
                    # Scrivo "YYYY-mm-dd HH:MM:SS - prezzo"
                    ping_text = ts if price is None else f"{ts} - {price}"
                    # A1 range per la colonna target
                    a1 = gspread.utils.rowcol_to_a1(r_idx + 1, col_ping + 1)
                    updates.append({
                        "range": a1,
                        "values": [[ping_text]],
                    })

            if updates:
                self.ws_trade.batch_update(updates, value_input_option="USER_ENTERED")
            return len(updates)
        except Exception as e:
            print(f"[PING][ERR] {e}")
            self.log("ERROR", f"Ping update error: {e}")
            return 0

# =========================
# Stato prezzo via WebSocket
# =========================
class PriceFeed:
    def __init__(self, symbol_lower: str):
        # esempio stream: paxgusdt@trade (campo 'p' prezzo, stringa)
        self.ws_url = f"wss://stream.binance.com:9443/ws/{symbol_lower}@trade"
        self.last_price: float | None = None
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    async def _ws_loop(self):
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"[WS] Connesso a {self.ws_url}")
                    async for msg in ws:
                        if self._stop.is_set():
                            break
                        try:
                            data = json.loads(msg)
                            # campo 'p' è string del prezzo trade
                            p = float(data.get("p")) if "p" in data else None
                            if p is not None and not (math.isinf(p) or math.isnan(p)):
                                self.last_price = p
                        except Exception:
                            continue
            except Exception as e:
                print(f"[WS][ERR] {e}")
                time.sleep(2)  # piccolo backoff e riprova

    def start(self):
        if self._t and self._t.is_alive():
            return
        self._t = threading.Thread(target=lambda: asyncio.run(self._ws_loop()), daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=3)

# =========================
# Funzioni “trading” (stub)
# =========================
def notify_open(notif: TelegramNotifier, logger: SheetLogger, price: float):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.log("TRADE", f"Apertura LONG @ {price}")
    notif.send(f"📈 Apertura LONG @ {price} ({ts})")

def notify_close(notif: TelegramNotifier, logger: SheetLogger, price: float, reason: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.log("TRADE", f"Chiusura @ {price} – {reason}")
    notif.send(f"📉 Chiusura @ {price} – {reason} ({ts})")

# =========================
# Main loop
# =========================
def main():
    print("[BOOT] Avvio Bot Oro…")

    notifier = TelegramNotifier(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    logger = SheetLogger(SPREADSHEET_ID, GOOGLE_CREDENTIALS)

    feed = PriceFeed(SYMBOL)
    feed.start()

    logger.log("INFO", "Bot Oro avviato correttamente")
    notifier.send("🤖 Bot Oro avviato correttamente!")

    while True:
        try:
            px = feed.last_price
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Heartbeat su Log
            logger.heartbeat(px)

            # Aggiornamento "Ultimo ping" su Trade
            updated = logger.update_trade_last_ping(ts, px)
            print(f"[PING] Aggiornate {updated} righe APERTO | prezzo={px}")

            # QUI andrebbero le tue regole di strategia per aprire/chiudere
            # if condizione_apertura: notify_open(notifier, logger, px)
            # if condizione_chiusura: notify_close(notifier, logger, px, "TP/SL/Manuale")

        except Exception as e:
            err = f"{e}\n{traceback.format_exc(limit=1)}"
            print(f"[LOOP][ERR] {err}")
            logger.log("ERROR", f"Main loop: {e}")

        # pausa fino al prossimo heartbeat
        time.sleep(HEARTBEAT_SECONDS)

if __name__ == "__main__":
    main()
