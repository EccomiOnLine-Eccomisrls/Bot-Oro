# -*- coding: utf-8 -*-
"""
BOT ORO – monitoraggio PAXGUSDT + log su Google Sheet + notifiche Telegram
• Env richieste:
  - GOOGLE_CREDENTIALS  (JSON completo della service account)
  - SPREADSHEET_ID
  - TELEGRAM_BOT_TOKEN
  - TELEGRAM_CHAT_ID
  - ALERTS_ENABLED=true/false  (opzionale, default true)

Fogli attesi nel Google Spreadsheet:
  - Trade  (intestazioni in riga 1 come nello screenshot)
  - Log
"""

import os
import json
import time
from datetime import datetime
import requests

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- NOTIFICHE TELEGRAM ---
# Assicùrati di avere notifier_telegram.py nel progetto con la classe TelegramNotifier
from notifier_telegram import TelegramNotifier


# ==========================
# Config
# ==========================
SYMBOL = "PAXGUSDT"               # Oro tokenizzato su Binance
HEARTBEAT_SEC = 60                # ogni quanto fare ping/heartbeat
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "true").lower() != "false"


# ==========================
# Helpers
# ==========================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_price(symbol: str) -> float | None:
    """
    Legge il last price da Binance public REST.
    Ritorna float o None in caso di errore.
    """
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        data = r.json()
        return float(data["price"])
    except Exception:
        return None


# ==========================
# Google Sheet Logger
# ==========================
class SheetLogger:
    def __init__(self, spreadsheet_id: str):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        raw = os.getenv("GOOGLE_CREDENTIALS")
        if not raw:
            raise RuntimeError("Variabile d'ambiente GOOGLE_CREDENTIALS mancante.")

        try:
            creds_dict = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                "GOOGLE_CREDENTIALS non è un JSON valido (controlla le \\n nella private_key)."
            ) from e

        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        gc = gspread.authorize(creds)
        self.sh = gc.open_by_key(spreadsheet_id)

        # Worksheet handles
        self.ws_trade = self.sh.worksheet("Trade")
        self.ws_log = self.sh.worksheet("Log")

    # ---------- Log generico (foglio Log) ----------
    def log(self, msg: str, tag: str = "bot"):
        try:
            self.ws_log.append_row([now_str(), msg, tag], value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[ERRORE] Scrittura Log: {e}")

    def log_heartbeat(self):
        self.log("Heartbeat OK", "bot")

    # ---------- Sezione TRADE ----------
    def open_trade(self,
                   trade_id: str,
                   side: str,
                   entry_price: float,
                   qty: float,
                   sl_pct: float,
                   tp1_pct: float,
                   tp2_pct: float,
                   strategy: str,
                   note: str = ""):
        """
        Aggiunge una riga nel foglio Trade con Stato = APERTO.
        Le colonne sono allineate al tuo sheet:
        A Data/Ora | B ID trade | C Lato | D Stato | E Prezzo ingresso | F Qty |
        G SL % | H TP1 % | I TP2 % | J Prezzo chiusura | K Ultimo ping | L P&L % |
        M P&L valore | N Equity post-trade | O Strategia | P Note
        """
        row = [
            now_str(),            # A
            trade_id,             # B
            side.upper(),         # C
            "APERTO",             # D
            round(entry_price, 2),# E
            round(qty, 6),        # F
            sl_pct,               # G
            tp1_pct,              # H
            tp2_pct,              # I
            "",                   # J Prezzo chiusura (vuoto)
            "",                   # K Ultimo ping
            "", "", "",           # L, M, N (P&L%, P&L valore, Equity post-trade)
            strategy,             # O
            note                  # P
        ]
        try:
            self.ws_trade.append_row(row, value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[ERRORE] open_trade(): {e}")

    def _find_row_by_trade_id(self, trade_id: str) -> int | None:
        """
        Cerca la riga (index 1-based nel foglio) con quel trade_id in colonna B.
        Se più righe combaciano, ritorna la prima con Stato=APERTO (col D).
        """
        try:
            values = self.ws_trade.get_all_values()
            for i in range(2, len(values) + 1):  # salta header
                row = values[i - 1]
                if len(row) < 4:
                    continue
                if row[1] == trade_id and row[3].upper() == "APERTO":
                    return i
        except Exception as e:
            print(f"[ERRORE] _find_row_by_trade_id(): {e}")
        return None

    def update_ping_all(self, price: float | None):
        """
        Scrive in colonna K (Ultimo ping) di tutte le righe con Stato=APERTO
        il timestamp e – se disponibile – il prezzo.
        """
        if price is None:
            val = f"{now_str()} - prezzo non disponibile"
        else:
            val = f"{now_str()} - {round(price, 2)}"

        try:
            values = self.ws_trade.get_all_values()
            updates = []
            for i in range(2, len(values) + 1):
                row = values[i - 1]
                if len(row) >= 4 and row[3].upper() == "APERTO":
                    # colonna K = 11
                    updates.append({"range": f"K{i}", "values": [[val]]})
            if updates:
                self.ws_trade.batch_update(updates, value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[ERRORE] update_ping_all(): {e}")

    def close_trade(self,
                    trade_id: str,
                    close_price: float,
                    pnl_pct: float,
                    pnl_value: float,
                    equity_after: float,
                    note: str = "TP"):
        """
        Chiude la riga con quel trade_id (col D -> CHIUSO) e compila
        J Prezzo chiusura, K Ultimo ping (aggiunge il tag TP/SL),
        L P&L %, M P&L valore, N Equity post-trade, P Note.
        """
        row_idx = self._find_row_by_trade_id(trade_id)
        if row_idx is None:
            print(f"[WARN] close_trade(): trade_id {trade_id} non trovato.")
            return

        try:
            self.ws_trade.update(f"D{row_idx}", "CHIUSO")    # stato
            self.ws_trade.update(f"J{row_idx}", round(close_price, 2))
            self.ws_trade.update(f"K{row_idx}", f"{now_str()}  {round(close_price, 2)}  {note}")
            self.ws_trade.update(f"L{row_idx}", round(pnl_pct, 4))
            self.ws_trade.update(f"M{row_idx}", round(pnl_value, 2))
            self.ws_trade.update(f"N{row_idx}", round(equity_after, 2))
            self.ws_trade.update(f"P{row_idx}", note)
        except Exception as e:
            print(f"[ERRORE] close_trade(): {e}")


# ==========================
# Notifiche wrapper
# ==========================
class Alerts:
    def __init__(self):
        self.enabled = ALERTS_ENABLED
        self.tg = TelegramNotifier(min_interval_sec=2) if self.enabled else None

    def startup(self):
        if self.tg:
            self.tg.startup()

    def trade_open(self, trade_id: str, side: str, qty: float, price: float, strategy: str):
        if not self.tg:
            return
        txt = (
            f"📈 *Trade APERTO*\n"
            f"• ID: `{trade_id}`\n"
            f"• {side.upper()}  qty: *{qty}*  @ *{round(price,2)}*\n"
            f"• Strategia: `{strategy}`"
        )
        self.tg.send_markdown(txt)

    def trade_close(self, trade_id: str, result: str, price: float, pnl_pct: float, pnl_value: float):
        if not self.tg:
            return
        emoji = "✅" if result.upper() == "TP" else "🛑"
        txt = (
            f"{emoji} *Trade CHIUSO* ({result})\n"
            f"• ID: `{trade_id}` @ *{round(price,2)}*\n"
            f"• P&L: *{pnl_pct:.4f}%*   (*{pnl_value:.2f} USDT*)"
        )
        self.tg.send_markdown(txt)

    def error(self, msg: str):
        if self.tg:
            self.tg.send(f"⚠️ ERRORE: {msg}")


# ==========================
# ESECUZIONE
# ==========================
def main():
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("SPREADSHEET_ID non impostata nelle variabili di ambiente.")

    logger = SheetLogger(spreadsheet_id)
    alerts = Alerts()
    alerts.startup()
    logger.log("BOT ATTIVO", "bot")

    # — Esempio: se vuoi aprire un trade da codice, usa:
    # trade_id = f"{SYMBOL}-{int(time.time())}"
    # logger.open_trade(trade_id, "LONG", 3337.89, 1.497952, 0.005, 0.0002, 0.0003, "v1", "TP1")
    # alerts.trade_open(trade_id, "LONG", 1.497952, 3337.89, "v1")

    while True:
        try:
            px = get_price(SYMBOL)
            # aggiorno colonna "Ultimo ping" per tutte le righe aperte
            logger.update_ping_all(px)

            # heartbeat su foglio Log
            logger.log_heartbeat()

            # (qui andrà la tua strategia: controlli TP/SL -> se chiudi:
            # logger.close_trade(trade_id, close_price, pnl_pct, pnl_val, equity_after, note="TP")
            # alerts.trade_close(trade_id, "TP", close_price, pnl_pct, pnl_val)
            # )

        except Exception as e:
            print(f"[LOOP] ERRORE: {e}")
            logger.log(f"ERRORE loop: {e}", "bot")
            alerts.error(str(e))

        time.sleep(HEARTBEAT_SEC)


if __name__ == "__main__":
    main()
