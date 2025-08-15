import os
import json
import time
import asyncio
import uuid
import math
from datetime import datetime

import aiohttp
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread.exceptions import APIError

# =========================
#   CONFIG DA ENV
# =========================
SYMBOL = os.getenv("SYMBOL", "PAXGUSDT").lower()  # per ws serve lowercase
HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "60"))
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "true").lower() == "true"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

# =========================
#   UTILS
# =========================
def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(msg: str):
    if not ALERTS_ENABLED:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] token/chat non configurati, skip.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        r = requests.post(url, json=data, timeout=10)
        if r.status_code != 200:
            print(f"[TELEGRAM] {r.status_code} - {r.text}")
        else:
            print("[TELEGRAM] inviato.")
    except Exception as e:
        print(f"[TELEGRAM] errore invio: {e}")

# =========================
#   GOOGLE SHEETS LOGGER
# =========================
class SheetLogger:
    """
    Fogli attesi:
      - 'Log'    : Timestamp | Stato | Prezzo | Messaggio | Extra | Fonte
      - 'Trade'  : (layout libero, usiamo ricerca ID in colonna B)
    """
    def __init__(self, creds_json_or_dict, spreadsheet_id):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        if isinstance(creds_json_or_dict, dict):
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json_or_dict, scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name(creds_json_or_dict, scope)

        client = gspread.authorize(creds)
        self.sheet = client.open_by_key(spreadsheet_id)
        self.ws_log = self._ws("Log")
        self.ws_trade = self._ws("Trade")

    def _ws(self, name):
        try:
            return self.sheet.worksheet(name)
        except gspread.WorksheetNotFound:
            return self.sheet.add_worksheet(title=name, rows=1000, cols=20)

    def _safe_append(self, ws, row, max_retries=5):
        delay = 1
        for i in range(max_retries):
            try:
                ws.append_row(row, value_input_option="USER_ENTERED")
                return True
            except APIError as e:
                code = getattr(e.response, "status_code", None)
                msg = str(e)
                if code in (429, 500, 502, 503, 504) or "quota" in msg.lower():
                    print(f"[GSHEET] retry {i+1}/{max_retries} ({code}): {msg}")
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                print(f"[GSHEET] errore non retriable: {msg}")
                return False
            except Exception as e:
                print(f"[GSHEET] errore generico: {e} (tent {i+1})")
                time.sleep(delay)
                delay = min(delay * 2, 30)
        return False

    def _find_row_by_trade_id(self, trade_id):
        try:
            # per coerenza con i tuoi fogli: ID trade in colonna B
            cells = self.ws_trade.findall(trade_id)
            for c in cells:
                if c.col == 2:  # colonna B
                    return c.row
        except Exception as e:
            print(f"[GSHEET] find trade id error: {e}")
        return None

    # ---- API di logging usate dal bot ----
    def log_loop(self, prezzo, msg="loop ok (ws-only)"):
        row = [now_ts(), "BOT ATTIVO", prezzo if prezzo is not None else "", msg, "", "bot"]
        ok = self._safe_append(self.ws_log, row)
        if not ok:
            print("[LOG] append loop fallito")

    def log_heartbeat(self):
        row = [now_ts(), "Heartbeat OK", "", "", "", "bot"]
        ok = self._safe_append(self.ws_log, row)
        if ok:
            print(f"[HEARTBEAT] Registrato alle {row[0]}")
        else:
            print("[HEARTBEAT] fallito")

    def log_open(self, trade_id, side, entry_price, qty, sl_pct, tp1_pct, tp2_pct, strategy="v1", note=""):
        row = [
            now_ts(),            # A Data/Ora
            trade_id,            # B ID trade
            side.upper(),        # C Lato
            "APERTO",            # D Stato
            entry_price,         # E Prezzo ingresso
            qty,                 # F Qty
            sl_pct,              # G SL %
            tp1_pct,             # H TP1 %
            tp2_pct,             # I TP2 %
            "",                  # J Prezzo chiusura
            "",                  # K Ultimo ping / TP info
            "",                  # L P&L %
            "",                  # M P&L valore
            "",                  # N Equity post-trade
            strategy,            # O Strategia
            note,                # P Note
        ]
        ok = self._safe_append(self.ws_trade, row)
        if not ok:
            print("[TRADE] append open fallito")

    def log_close(self, trade_id, close_price, pnl_pct, pnl_value, equity_after, note="TP/SL"):
        # trovo la riga dell'ID trade in colonna B e aggiorno celle chiave
        row = self._find_row_by_trade_id(trade_id)
        if not row:
            print(f"[TRADE] riga per {trade_id} non trovata")
            return
        try:
            self.ws_trade.update(f"D{row}", "CHIUSO")                # Stato
            self.ws_trade.update(f"J{row}", close_price)             # Prezzo chiusura
            self.ws_trade.update(f"L{row}", round(pnl_pct, 4))       # P&L %
            self.ws_trade.update(f"M{row}", round(pnl_value, 2))     # P&L valore
            self.ws_trade.update(f"N{row}", round(equity_after, 2))  # Equity post-trade
            self.ws_trade.update(f"P{row}", note)                    # Note
        except Exception as e:
            print(f"[TRADE] update close error: {e}")

# =========================
#   POSIZIONI (demo)
# =========================
class Position:
    def __init__(self, side, entry_price, qty, sl_pct, tp1_pct, tp2_pct):
        self.trade_id = f"{SYMBOL.upper()}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        self.side = side
        self.entry = entry_price
        self.qty = qty
        self.sl_pct = sl_pct
        self.tp1_pct = tp1_pct
        self.tp2_pct = tp2_pct
        self.tp1_hit = False

# =========================
#   BOT CORE
# =========================
class BotOro:
    def __init__(self, logger: SheetLogger):
        self.logger = logger
        self.last_heartbeat = 0
        self.position = None
        self.equity = 1000.0  # demo

        # parametri “esempio”
        self.qty = float(os.getenv("QTY", "0.0005"))
        self.sl_pct = float(os.getenv("SL_PCT", "0.05"))    # 5% demo
        self.tp1_pct = float(os.getenv("TP1_PCT", "0.02"))  # 2% demo
        self.tp2_pct = float(os.getenv("TP2_PCT", "0.03"))  # 3% demo

    def maybe_heartbeat(self):
        if time.time() - self.last_heartbeat >= HEARTBEAT_SECONDS:
            self.logger.log_heartbeat()
            self.last_heartbeat = time.time()

    def on_price(self, price: float):
        # Log “BOT ATTIVO” leggero (non a ogni tick): ogni ~30 sec
        self.maybe_heartbeat()

        # === Qui va la tua logica segnali ===
        # DEMO: se non ho posizione e il prezzo è multiplo “tondo”, apri LONG
        if not self.position and int(price) % 50 == 0:
            self.open_position("LONG", price)

        # Gestione TP/SL se posizione aperta
        if self.position:
            self.manage_open(price)

    def open_position(self, side, price):
        p = Position(side, price, self.qty, self.sl_pct, self.tp1_pct, self.tp2_pct)
        self.position = p

        # Log sheet + notifica
        self.logger.log_open(
            trade_id=p.trade_id, side=side, entry_price=price, qty=p.qty,
            sl_pct=p.sl_pct, tp1_pct=p.tp1_pct, tp2_pct=p.tp2_pct,
            strategy="v1", note="apertura demo"
        )
        send_telegram(f"📈 {SYMBOL.upper()} APERTO {side} @ {price}\nID: {p.trade_id}")

    def manage_open(self, price):
        p = self.position
        if not p:
            return

        # Calcolo target e stop
        sign = 1 if p.side == "LONG" else -1
        tp1 = p.entry * (1 + sign * p.tp1_pct)
        tp2 = p.entry * (1 + sign * p.tp2_pct)
        sl  = p.entry * (1 - sign * p.sl_pct)

        # TP1
        if not p.tp1_hit and ((p.side == "LONG" and price >= tp1) or (p.side != "LONG" and price <= tp1)):
            p.tp1_hit = True
            self.logger.log_loop(price, "TP1 hit")
            send_telegram(f"✅ {SYMBOL.upper()} TP1 raggiunto @ {price} (ID {p.trade_id})")

        # SL
        if (p.side == "LONG" and price <= sl) or (p.side != "LONG" and price >= sl):
            self.close_position(price, reason="SL")
            return

        # TP2/chiusura
        if (p.side == "LONG" and price >= tp2) or (p.side != "LONG" and price <= tp2):
            self.close_position(price, reason="TP2")

    def close_position(self, price, reason="TP2"):
        p = self.position
        if not p:
            return
        sign = 1 if p.side == "LONG" else -1
        pnl_pct = (price - p.entry) / p.entry * (1 if p.side == "LONG" else -1)
        pnl_value = self.equity * pnl_pct * 0.01  # demo: valore proporzionale
        self.equity += pnl_value

        # Log sheet + notifica
        self.logger.log_close(
            trade_id=p.trade_id,
            close_price=price,
            pnl_pct=pnl_pct * 100,           # in %
            pnl_value=pnl_value,
            equity_after=self.equity,
            note=reason
        )
        send_telegram(f"🔔 {SYMBOL.upper()} CHIUSO {p.side} @ {price} ({reason})\nP&L {pnl_pct*100:.3f}% | ID {p.trade_id}")
        self.position = None

# =========================
#   WEBSOCKET BINANCE (PUBLIC)
# =========================
async def price_stream(symbol: str, on_price_cb):
    url = f"wss://stream.binance.com:9443/ws/{symbol}@trade"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30) as ws:
                    print(f"[WS] connesso a {url}")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            # trade price nel campo 'p'
                            if "p" in data:
                                price = float(data["p"])
                                on_price_cb(price)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print(f"[WS] errore: {msg}")
                            break
        except Exception as e:
            print(f"[WS] eccezione: {e}. Reconnect fra 3s.")
            await asyncio.sleep(3)

# =========================
#   MAIN
# =========================
def build_logger() -> SheetLogger:
    if not GOOGLE_CREDENTIALS:
        raise RuntimeError("GOOGLE_CREDENTIALS non impostato")
    if not SPREADSHEET_ID:
        raise RuntimeError("SPREADSHEET_ID non impostato")

    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
    except json.JSONDecodeError:
        raise RuntimeError("GOOGLE_CREDENTIALS non è un JSON valido (attenzione alle \\n nella private_key)")

    return SheetLogger(creds_dict, SPREADSHEET_ID)

async def main():
    logger = build_logger()
    bot = BotOro(logger)

    # primo log in “Log” per continuità con i tuoi screenshot
    logger.log_loop("", "bot avviato (ws-only)")
    send_telegram("🤖 Bot Oro avviato correttamente (WS only).")

    await price_stream(SYMBOL, bot.on_price)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bye")
