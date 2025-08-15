# sheet_logger.py
import json, os
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

TRADE_SHEET_NAME = "Trade"
LOG_SHEET_NAME   = "Log"
TRADE_HEADERS = [
    "Data/Ora","ID trade","Lato","Stato","Prezzo ingresso","Qty",
    "SL %","TP1 %","TP2 %","Prezzo chiusura","Chiusura",
    "P&L %","P&L valore","Equity post-trade","Strategia","Note"
]
HEARTBEAT_CELL = "R2"  # cella singola per "Ultimo ping" (cambia pure, es. "K2")

def _gc_client():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS mancante")
    info = json.loads(creds_json)
    scope = ["https://www.googleapis.com/auth/spreadsheets",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
    return gspread.authorize(creds)

def _open_sheets():
    gc = _gc_client()
    ss_id = os.getenv("SPREADSHEET_ID")
    if not ss_id:
        raise RuntimeError("SPREADSHEET_ID mancante")
    sh = gc.open_by_key(ss_id)
    # Trade
    try:
        trade_ws = sh.worksheet(TRADE_SHEET_NAME)
    except gspread.WorksheetNotFound:
        trade_ws = sh.add_worksheet(title=TRADE_SHEET_NAME, rows=2000, cols=26)
        trade_ws.append_row(TRADE_HEADERS, value_input_option="USER_ENTERED")
        trade_ws.format("1:1", {"textFormat": {"bold": True}})
    if trade_ws.row_values(1) != TRADE_HEADERS:
        trade_ws.update(f"A1:{chr(64+len(TRADE_HEADERS))}1", [TRADE_HEADERS])

    # Log
    try:
        log_ws = sh.worksheet(LOG_SHEET_NAME)
    except gspread.WorksheetNotFound:
        log_ws = sh.add_worksheet(title=LOG_SHEET_NAME, rows=2000, cols=6)
        log_ws.append_row(["Data/Ora","Stato","Prezzo","Messaggio","Extra","Fonte"],
                          value_input_option="USER_ENTERED")
        log_ws.format("1:1", {"textFormat": {"bold": True}})

    # Etichetta "Ultimo ping" sopra la cella del battito
    label_row = int(''.join(filter(str.isdigit, HEARTBEAT_CELL))) - 1
    label_col = ''.join(filter(str.isalpha, HEARTBEAT_CELL))
    trade_ws.update(f"{label_col}{label_row}", "Ultimo ping:")

    return trade_ws, log_ws

class SheetLogger:
    def __init__(self):
        self.trade_ws, self.log_ws = _open_sheets()

    def heartbeat(self, price: float, msg: str = "BOT ATTIVO", fonte: str = "bot"):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.trade_ws.update(HEARTBEAT_CELL, f"{now} · {price}")
        self.log_ws.append_row([now, "BOT ATTIVO", price, msg, "", fonte],
                               value_input_option="USER_ENTERED")

    def log_open(self, *, trade_id: str, side: str, entry_price: float, qty: float,
                 sl_pct: float, tp1_pct: float, tp2_pct: float,
                 strategy: str = "v1", note: str = ""):
        now = datetime.now().strftime("%Y-%-%m-%d %H:%M:%S")
        row = {
            "Data/Ora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ID trade": trade_id, "Lato": side, "Stato": "APERTO",
            "Prezzo ingresso": entry_price, "Qty": qty,
            "SL %": sl_pct, "TP1 %": tp1_pct, "TP2 %": tp2_pct,
            "Prezzo chiusura": "", "Chiusura": "",
            "P&L %": "", "P&L valore": "", "Equity post-trade": "",
            "Strategia": strategy, "Note": note
        }
        ordered = [row.get(h, "") for h in TRADE_HEADERS]
        self.trade_ws.append_row(ordered, value_input_option="USER_ENTERED")

    def log_close(self, *, trade_id: str, close_price: float, close_type: str,
                  pnl_pct: float, pnl_value: float, equity_after: float, note: str = ""):
        # trova l'ultima riga APERTA di quel trade e la completa
        cells = self.trade_ws.findall(trade_id, in_column=2)  # col B = ID trade
        target_row = None
        for c in reversed(cells or []):
            if self.trade_ws.cell(c.row, 4).value == "APERTO":  # col D = Stato
                target_row = c.row
                break
        if target_row:
            self.trade_ws.update(f"J{target_row}:N{target_row}", [[
                close_price, close_type, pnl_pct, pnl_value, equity_after
            ]])
            self.trade_ws.update(f"D{target_row}", "CHIUSO")
            if note:
                self.trade_ws.update(f"P{target_row}", note)
        else:
            # fallback: crea riga chiusa se non trova l'apertura
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = {
                "Data/Ora": now, "ID trade": trade_id, "Lato": "",
                "Stato": "CHIUSO", "Prezzo ingresso": "", "Qty": "",
                "SL %": "", "TP1 %": "", "TP2 %": "",
                "Prezzo chiusura": close_price, "Chiusura": close_type,
                "P&L %": pnl_pct, "P&L valore": pnl_value,
                "Equity post-trade": equity_after,
                "Strategia": "", "Note": f"(chiusura senza apertura) {note}".strip()
            }
            ordered = [row.get(h, "") for h in TRADE_HEADERS]
            self.trade_ws.append_row(ordered, value_input_option="USER_ENTERED")
