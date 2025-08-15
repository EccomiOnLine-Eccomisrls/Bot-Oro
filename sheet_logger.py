# sheet_logger.py
import os, json
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === Config fogli ===
TRADE_SHEET_NAME = "Trade"
LOG_SHEET_NAME   = "Log"

TRADE_HEADERS = [
    "Data/Ora","ID trade","Lato","Stato","Prezzo ingresso","Qty",
    "SL %","TP1 %","TP2 %","Prezzo chiusura","Chiusura",
    "P&L %","P&L valore","Equity post-trade","Strategia","Note"
]

# Cella “Ultimo ping”
HEARTBEAT_LABEL_CELL = "K1"   # etichetta
HEARTBEAT_CELL       = "K2"   # timestamp + prezzo

# === Helpers ===
def _gc_client():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS mancante")
    info = json.loads(creds_json)
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
    return gspread.authorize(creds)

def _safe_update_cell(ws, a1: str, value):
    """Aggiorna una singola cella (richiesto [[valore]] da gspread 6.x)."""
    ws.update(a1, [[value]])

def _open_sheets():
    gc = _gc_client()
    ss_id = os.getenv("SPREADSHEET_ID")
    if not ss_id:
        raise RuntimeError("SPREADSHEET_ID mancante")
    sh = gc.open_by_key(ss_id)

    # --- Trade ---
    try:
        trade_ws = sh.worksheet(TRADE_SHEET_NAME)
    except gspread.WorksheetNotFound:
        trade_ws = sh.add_worksheet(title=TRADE_SHEET_NAME, rows=2000, cols=26)
        trade_ws.append_row(TRADE_HEADERS, value_input_option="USER_ENTERED")
        trade_ws.format("1:1", {"textFormat": {"bold": True}})

    # riallinea header se necessario
    current_headers = trade_ws.row_values(1)
    if current_headers != TRADE_HEADERS:
        # A..P (abbiamo 16 colonne)
        trade_ws.update("A1:P1", [TRADE_HEADERS])

    # etichetta “Ultimo ping”
    _safe_update_cell(trade_ws, HEARTBEAT_LABEL_CELL, "Ultimo ping")

    # --- Log ---
    try:
        log_ws = sh.worksheet(LOG_SHEET_NAME)
    except gspread.WorksheetNotFound:
        log_ws = sh.add_worksheet(title=LOG_SHEET_NAME, rows=2000, cols=6)
        log_ws.append_row(["Data/Ora","Stato","Prezzo","Messaggio","Extra","Fonte"],
                          value_input_option="USER_ENTERED")
        log_ws.format("1:1", {"textFormat": {"bold": True}})

    return trade_ws, log_ws

class SheetLogger:
    def __init__(self):
        self.trade_ws, self.log_ws = _open_sheets()

    # -------- Heartbeat (NO append su Trade) --------
    def heartbeat(self, price: float, msg: str = "BOT ATTIVO", fonte: str = "bot"):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # singola cella -> [[valore]]
        _safe_update_cell(self.trade_ws, HEARTBEAT_CELL, f"{now} · {price}")
        # storicizza su Log
        self.log_ws.append_row([now, "BOT ATTIVO", price, msg, "", fonte],
                               value_input_option="USER_ENTERED")

    # -------- Apertura trade --------
    def log_open(self, *, trade_id: str, side: str, entry_price: float, qty: float,
                 sl_pct: float, tp1_pct: float, tp2_pct: float,
                 strategy: str = "v1", note: str = ""):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = {
            "Data/Ora": now,
            "ID trade": trade_id,
            "Lato": side,
            "Stato": "APERTO",
            "Prezzo ingresso": entry_price,
            "Qty": qty,
            "SL %": sl_pct,
            "TP1 %": tp1_pct,
            "TP2 %": tp2_pct,
            "Prezzo chiusura": "",
            "Chiusura": "",
            "P&L %": "",
            "P&L valore": "",
            "Equity post-trade": "",
            "Strategia": strategy,
            "Note": note
        }
        ordered = [row.get(h, "") for h in TRADE_HEADERS]
        self.trade_ws.append_row(ordered, value_input_option="USER_ENTERED")

    # -------- Chiusura trade (TP1/TP2/SL/MANUALE) --------
    def log_close(self, *, trade_id: str, close_price: float, close_type: str,
                  pnl_pct: float, pnl_value: float, equity_after: float, note: str = ""):
        """
        Completa la riga dell'apertura (se trovata). Se non trova l'apertura,
        crea una riga 'CHIUSA' di fallback.
        """
        # cerca l'ultima riga con quel trade_id e Stato=APERTO
        id_col = self.trade_ws.col_values(2)  # colonna B = "ID trade"
        target_row = None
        # scorri dal basso verso l'alto (ignorando header)
        for i in range(len(id_col)-1, 1, -1):
            if id_col[i-1] == trade_id:
                stato = self.trade_ws.cell(i, 4).value  # col D = "Stato"
                if str(stato).upper() == "APERTO":
                    target_row = i
                    break

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if isinstance(target_row, int) and target_row > 1:
            # J..N = Prezzo chiusura, Chiusura, P&L %, P&L valore, Equity post-trade
            self.trade_ws.update(f"J{target_row}:N{target_row}", [[
                close_price, close_type, pnl_pct, pnl_value, equity_after
            ]])
            # aggiornamenti a cella singola (sempre [[valore]])
            _safe_update_cell(self.trade_ws, f"D{target_row}", "CHIUSO")  # Stato
            _safe_update_cell(self.trade_ws, f"A{target_row}", now)       # Data/Ora (timestamp chiusura)
            if note:
                _safe_update_cell(self.trade_ws, f"P{target_row}", note)  # Note
        else:
            # fallback: scrivi riga chiusa
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
