# bot_oro.py
# --------------------------------------------
# BOT ORO – Simulazione con max 5 posizioni, log su Google Sheet, notifiche Telegram
# --------------------------------------------

import os, json, time, requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ===== Config =====
SYMBOL = "PAXGUSDT"
HEARTBEAT_SECS = 60  # frequenza loop

# Limiti & regole trading (simulazione)
MAX_OPEN   = 5
UNIT_USDT  = 1.0     # capitale per trade
SL_PCT     = 0.005   # -0,5%
TP1_PCT    = 0.01    # +1%
TP2_PCT    = 0.02    # +2%

# Env richiesti
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # obbligatorio

# Telegram (opzionale ma consigliato)
TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")  # es. 203729322

# Fuso orario Italia (CET/CEST)
TZ_ITALY = timezone(timedelta(hours=2))

def now_str():
    return datetime.now(TZ_ITALY).strftime("%Y-%m-%d %H:%M:%S")

def notify_telegram(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
    except Exception:
        pass

# ===== Google Sheets =====
class SheetLogger:
    # Mappa colonne Trade (1-based)
    COLS = {
        "DATAORA": 1, "ID": 2, "LATO": 3, "STATO": 4, "ENTRY": 5, "QTY": 6,
        "SLpct": 7, "TP1pct": 8, "TP2pct": 9, "EXIT": 10, "PING": 11,
        "PNLpct": 12, "PNLval": 13, "EQUITY": 14, "STRAT": 15, "NOTE": 16
    }

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
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(raw), scope)
        else:
            if not os.path.exists("google_credentials.json"):
                raise RuntimeError("Mancano le credenziali Google (env GOOGLE_CREDENTIALS o file google_credentials.json).")
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_credentials.json", scope)

        client = gspread.authorize(creds)
        self.sheet   = client.open_by_key(spreadsheet_id)
        self.ws_log  = self.sheet.worksheet("Log")
        self.ws_trd  = self.sheet.worksheet("Trade")

    # --- LOG sheet ---
    def log(self, level: str, message: str, extra: str="bot"):
        try:
            self.ws_log.append_row([now_str(), level, message, extra], value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[ERRORE] Scrittura Log fallita: {e}")

    def log_heartbeat(self, price: Optional[float]):
        msg = "Heartbeat OK" if price is None else f"Heartbeat OK – {price:.2f}"
        self.log("INFO", msg, "bot")

    # --- TRADE sheet helpers ---
    def list_open_rows(self) -> List[Dict[str, Any]]:
        """Legge tutte le righe con Stato=APERTO (salta intestazioni)."""
        rows = self.ws_trd.get_all_values()  # lista di liste
        out = []
        for idx, r in enumerate(rows, start=1):
            if idx == 1:  # header
                continue
            if len(r) < 16:
                r += [""]*(16-len(r))
            if r[self.COLS["STATO"]-1].strip().upper() == "APERTO":
                out.append({"row": idx, "data": r})
        return out

    def update_ping(self, row_idx: int, price: float):
        text = f"{now_str()} - {price:.2f}"
        self.ws_trd.update_cell(row_idx, self.COLS["PING"], text)

    def update_pnl(self, row_idx: int, pnl_pct: float, pnl_val: float):
        self.ws_trd.update_cell(row_idx, self.COLS["PNLpct"], round(pnl_pct, 4))
        self.ws_trd.update_cell(row_idx, self.COLS["PNLval"], round(pnl_val, 2))

    def mark_partial(self, row_idx: int, note: str):
        cur = self.ws_trd.cell(row_idx, self.COLS["NOTE"]).value or ""
        if "TP1" not in cur:
            new_note = "TP1" if not cur else (cur + " | TP1")
            self.ws_trd.update_cell(row_idx, self.COLS["NOTE"], new_note)

    def close_trade(self, row_idx: int, exit_price: float, reason: str):
        self.ws_trd.update_cell(row_idx, self.COLS["EXIT"], round(exit_price, 2))
        self.ws_trd.update_cell(row_idx, self.COLS["STATO"], "CHIUSO")
        cur = self.ws_trd.cell(row_idx, self.COLS["NOTE"]).value or ""
        new_note = reason if not cur else (cur + " | " + reason)
        self.ws_trd.update_cell(row_idx, self.COLS["NOTE"], new_note)

    def append_new_trade(self, trade_id: str, side: str, entry: float, qty: float,
                         sl_pct: float, tp1_pct: float, tp2_pct: float, strategy: str="v1"):
        row = [
            now_str(),               # Data/Ora
            trade_id,                # ID trade
            side,                    # Lato
            "APERTO",                # Stato
            round(entry, 2),         # Prezzo ingresso
            round(qty, 6),           # Qty
            sl_pct, tp1_pct, tp2_pct,# SL%, TP1%, TP2%
            "",                      # Prezzo chiusura
            "",                      # Ultimo ping
            "", "", "",              # P&L%, P&L valore, Equity post-trade
            strategy,                # Strategia
            ""                       # Note
        ]
        self.ws_trd.append_row(row, value_input_option="USER_ENTERED")

# ===== Prezzo Binance =====
def get_price_binance(symbol: str) -> Optional[float]:
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price",
                         params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return None

# ===== Motore Trading (sim) =====
class TradeEngine:
    def __init__(self, logger: SheetLogger):
        self.lg = logger

    def _qty_for_unit_usdt(self, entry_price: float) -> float:
        if entry_price <= 0:
            return 0.0
        return UNIT_USDT / entry_price

    def _ensure_max_open(self, price: float):
        """Se < MAX_OPEN, apri nuove operazioni (LONG) fino al limite."""
        open_rows = self.lg.list_open_rows()
        n_open = len(open_rows)
        if n_open >= MAX_OPEN:
            return
        # apri fino a MAX_OPEN
        for _ in range(MAX_OPEN - n_open):
            qty = self._qty_for_unit_usdt(price)
            trade_id = f"{SYMBOL}_{int(time.time()*1000)}"
            self.lg.append_new_trade(
                trade_id=trade_id, side="LONG", entry=price, qty=qty,
                sl_pct=SL_PCT, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT, strategy="v1"
            )
            notify_telegram(f"🟢 NUOVA OPERAZIONE\nID: {trade_id}\nSide: LONG\nEntry: {price:.2f}\nQty: {qty:.6f}")
            self.lg.log("INFO", f"Aperto LONG @ {price:.2f}", "bot")

    def _eval_and_update(self, price: float):
        """Aggiorna ping/P&L; gestisce SL, TP1 (parziale), TP2 (chiusura)."""
        opens = self.lg.list_open_rows()
        for it in opens:
            r  = it["data"]; idx = it["row"]
            try:
                entry = float(r[SheetLogger.COLS["ENTRY"]-1] or 0)
                qty   = float(r[SheetLogger.COLS["QTY"]-1] or 0)
                tp1p  = float(r[SheetLogger.COLS["TP1pct"]-1] or TP1_PCT)
                tp2p  = float(r[SheetLogger.COLS["TP2pct"]-1] or TP2_PCT)
                slp   = float(r[SheetLogger.COLS["SLpct"]-1]  or SL_PCT)
            except Exception:
                continue

            # ping & pnl
            self.lg.update_ping(idx, price)
            pnl_pct = (price/entry - 1.0) * 100.0   # %
            pnl_val = (price - entry) * qty         # USDT circa
            self.lg.update_pnl(idx, pnl_pct, pnl_val)

            # soglie
            tp1_hit = price >= entry * (1 + tp1p)
            tp2_hit = price >= entry * (1 + tp2p)
            sl_hit  = price <= entry * (1 - slp)

            note = (r[SheetLogger.COLS["NOTE"]-1] or "").upper()

            # SL chiusura totale
            if sl_hit:
                self.lg.close_trade(idx, price, "SL")
                notify_telegram(f"🔴 CHIUSO (SL)\nEntry: {entry:.2f}\nExit: {price:.2f}\nPnL: {pnl_pct:.2f}% ({pnl_val:.2f})")
                self.lg.log("INFO", f"Chiuso SL @ {price:.2f}", "bot")
                continue

            # TP2 chiusura totale
            if tp2_hit:
                self.lg.close_trade(idx, price, "TP2")
                notify_telegram(f"🟢 CHIUSO (TP2)\nEntry: {entry:.2f}\nExit: {price:.2f}\nPnL: {pnl_pct:.2f}% ({pnl_val:.2f})")
                self.lg.log("INFO", f"Chiuso TP2 @ {price:.2f}", "bot")
                continue

            # TP1 parziale (una sola volta)
            if tp1_hit and "TP1" not in note:
                self.lg.mark_partial(idx, "TP1")
                notify_telegram(f"🟡 PARZIALE (TP1)\nEntry: {entry:.2f}\nPrezzo: {price:.2f}\nPnL: {pnl_pct:.2f}%")
                self.lg.log("INFO", f"TP1 raggiunto @ {price:.2f}", "bot")

    def step(self, price: Optional[float]):
        if price is None:
            self.lg.log_heartbeat(None)
            return
        # heartbeat con prezzo
        self.lg.log_heartbeat(price)
        # valuta tutte le aperte
        self._eval_and_update(price)
        # apri nuove fino al limite
        self._ensure_max_open(price)

# ===== MAIN LOOP =====
def main():
    lg = SheetLogger(SPREADSHEET_ID)
    eng = TradeEngine(lg)

    lg.log("BOT ATTIVO", "bot")
    notify_telegram("🤖 Bot Oro (sim) avviato. Max 5 posizioni, SL -0.5%, TP1 +1%, TP2 +2%.")

    while True:
        try:
            price = get_price_binance(SYMBOL)
            eng.step(price)
        except Exception as e:
            lg.log("ERRORE", f"Loop exception: {e}", "bot")
        time.sleep(HEARTBEAT_SECS)

if __name__ == "__main__":
    main()
