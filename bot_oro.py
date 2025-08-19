# bot_oro.py
# --------------------------------------------
# BOT ORO – Simulazione con max 5 posizioni, Ping per riga, P&L, notifiche
# --------------------------------------------

import os
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Binance (python-binance) ---
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException, BinanceRequestException

# --- Twilio (opzionale) ---
from twilio.rest import Client as TwilioClient


# =========================
# CONFIGURAZIONE DA ENV
# =========================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

SYMBOL = os.getenv("SYMBOL", "PAXGUSDT")  # su Binance rappresenta l'oro tokenizzato
SHEET_ID = os.getenv("SPREADSHEET_ID")   # ID del Google Sheet
SHEET_NAME = os.getenv("SHEET_NAME", "BOT ORO – TEST")

GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")  # JSON service account

# Parametri strategia (percentuali come decimali: 0.005 = 0.5%)
TP1_PCT = Decimal(os.getenv("TP1_PCT", "0.0002"))  # es: 0.02%
TP2_PCT = Decimal(os.getenv("TP2_PCT", "0.0003"))  # es: 0.03%
SL_PCT  = Decimal(os.getenv("SL_PCT",  "0.0050"))  # es: 0.50%
BASE_EQUITY = Decimal(os.getenv("BASE_EQUITY", "10000"))

# Twilio (opzionale)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
TWILIO_TO = os.getenv("TWILIO_TO", "")  # es: whatsapp:+39xxxxxxxxxx

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "8"))


# =========================
# UTILS
# =========================
def d(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x))
    except (InvalidOperation, TypeError):
        return Decimal("0")

def fmt_dec(x: Decimal, q="0.00001") -> str:
    return d(x).quantize(Decimal(q), rounding=ROUND_HALF_UP).normalize().to_eng_string()

def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def send_whatsapp(message: str):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_TO):
        return
    try:
        tw = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        tw.messages.create(from_=TWILIO_FROM, to=TWILIO_TO, body=message)
    except Exception as e:
        print(f"[TWILIO] Errore invio WhatsApp: {e}")


# =========================
# GOOGLE SHEETS
# =========================
HEADERS = [
    "Data/Ora",          # A
    "ID trade",          # B
    "Lato",              # C
    "Stato",             # D (APERTO/CHIUSO)
    "Prezzo ingresso",   # E
    "Qty",               # F
    "SL %",              # G
    "TP1 %",             # H
    "TP2 %",             # I
    "Prezzo chiusura",   # J
    "Ultimo ping",       # K (timestamp - prezzo)
    "P&L %",             # L
    "P&L valore",        # M
    "Equity post-trade", # N
    "Strategia",         # O
    "Note"               # P (TP1/TP2/SL)
]

def open_sheet():
    if not GOOGLE_CREDENTIALS:
        raise RuntimeError("Variabile GOOGLE_CREDENTIALS mancante.")
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=1000, cols=len(HEADERS))
    # Assicura intestazioni
    header = ws.row_values(1)
    if header != HEADERS:
        ws.resize(rows=2)  # evita residui
        ws.update("A1", [HEADERS])
    return ws

def header_index_map(ws):
    header = ws.row_values(1)
    return {name: idx+1 for idx, name in enumerate(header)}  # 1-based for gspread


# =========================
# BINANCE PRICE
# =========================
def binance_client():
    if not (BINANCE_API_KEY and BINANCE_API_SECRET):
        # sola lettura del prezzo funziona anche senza key, ma lasciamo compatibilità
        return BinanceClient(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
    return BinanceClient(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)

def get_last_price(client) -> Decimal:
    try:
        p = client.get_symbol_ticker(symbol=SYMBOL)
        return d(p["price"])
    except (BinanceAPIException, BinanceRequestException, KeyError, TypeError) as e:
        print(f"[BINANCE] Errore prezzo: {e}")
        return Decimal("0")


# =========================
# LOGICA TRADE
# =========================
def compute_targets(entry: Decimal):
    tp1 = entry * (Decimal("1") + TP1_PCT)
    tp2 = entry * (Decimal("1") + TP2_PCT)
    sl  = entry * (Decimal("1") - SL_PCT)
    return (tp1, tp2, sl)

def pnl_values(side: str, entry: Decimal, close: Decimal, qty: Decimal):
    if qty == 0 or entry == 0 or close == 0:
        return (Decimal("0"), Decimal("0"))
    if side.upper() == "LONG":
        pnl_val = (close - entry) * qty
        pnl_pct = (close / entry - 1) * 100
    else:  # SHORT (non usato ora ma pronto)
        pnl_val = (entry - close) * qty
        pnl_pct = (entry / close - 1) * 100
    return (pnl_pct, pnl_val)


# =========================
# OPERATIVITÀ
# =========================
def ensure_qty(row_vals, H, default_qty=Decimal("1")) -> Decimal:
    try:
        return d(row_vals[H["Qty"]-1])
    except Exception:
        return default_qty

def last_equity(ws, H) -> Decimal:
    # trova ultimo valore valido in colonna Equity
    col = ws.col_values(H["Equity post-trade"])
    for val in reversed(col[1:]):  # skip header
        if val.strip():
            try:
                return d(val)
            except Exception:
                continue
    return BASE_EQUITY

def update_open_rows(ws, client):
    H = header_index_map(ws)
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return

    # Per aggiornare più celle in batch
    updates = []
    now = now_utc_str()
    last_price = get_last_price(client)
    if last_price == 0:
        return

    # Scorri righe (dalla 2 in poi)
    for r_idx in range(2, len(rows)+1):
        row = rows[r_idx-1]
        stato = (row[H["Stato"]-1] or "").upper().strip()
        if stato != "APERTO":
            # Aggiorna comunque l'ultimo ping, utile come "ultimo visto"
            updates.append({
                "range": f"{gspread.utils.rowcol_to_a1(r_idx, H['Ultimo ping'])}",
                "values": [[f"{now} - {fmt_dec(last_price)}"]]
            })
            continue

        side = (row[H["Lato"]-1] or "LONG").upper()
        try:
            entry = d(row[H["Prezzo ingresso"]-1])
        except Exception:
            entry = Decimal("0")

        if entry == 0:
            # Salta righe malformate
            continue

        qty = ensure_qty(row, H)
        tp1, tp2, sl = compute_targets(entry)

        # Aggiorna ultimo ping
        updates.append({
            "range": f"{gspread.utils.rowcol_to_a1(r_idx, H['Ultimo ping'])}",
            "values": [[f"{now} - {fmt_dec(last_price)}"]]
        })

        hit = None
        close_price = None

        # Solo LONG per coerenza con il foglio
        if side == "LONG":
            if last_price >= tp2:
                hit = "TP2"
                close_price = tp2
            elif last_price >= tp1:
                hit = "TP1"
                close_price = tp1
            elif last_price <= sl:
                hit = "SL"
                close_price = sl
        else:
            # gestione SHORT (non usata nello sheet, ma implementata)
            if last_price <= tp2:
                hit = "TP2"
                close_price = tp2
            elif last_price <= tp1:
                hit = "TP1"
                close_price = tp1
            elif last_price >= sl:
                hit = "SL"
                close_price = sl

        if hit is None:
            # Non chiuso, ma aggiorniamo P&L live
            pnl_pct, pnl_val = pnl_values(side, entry, last_price, qty)
            updates.append({
                "range": f"{gspread.utils.rowcol_to_a1(r_idx, H['P&L %'])}",
                "values": [[fmt_dec(pnl_pct, "0.0001")]]
            })
            updates.append({
                "range": f"{gspread.utils.rowcol_to_a1(r_idx, H['P&L valore'])}",
                "values": [[fmt_dec(pnl_val, "0.01")]]
            })
            continue

        # Chiusura trade
        pnl_pct, pnl_val = pnl_values(side, entry, close_price, qty)
        eq_prev = last_equity(ws, H)
        eq_new = eq_prev + pnl_val

        updates.extend([
            {"range": f"{gspread.utils.rowcol_to_a1(r_idx, H['Prezzo chiusura'])}", "values": [[fmt_dec(close_price)]]},
            {"range": f"{gspread.utils.rowcol_to_a1(r_idx, H['P&L %'])}", "values": [[fmt_dec(pnl_pct, "0.0001")]]},
            {"range": f"{gspread.utils.rowcol_to_a1(r_idx, H['P&L valore'])}", "values": [[fmt_dec(pnl_val, "0.01")]]},
            {"range": f"{gspread.utils.rowcol_to_a1(r_idx, H['Equity post-trade'])}", "values": [[fmt_dec(eq_new, "0.01")]]},
            {"range": f"{gspread.utils.rowcol_to_a1(r_idx, H['Stato'])}", "values": [["CHIUSO"]]},
            {"range": f"{gspread.utils.rowcol_to_a1(r_idx, H['Note'])}", "values": [[hit]]},
        ])

        # Notifica
        send_whatsapp(
            f"⛏️ BOT ORO | {SYMBOL}\n"
            f"Trade chiuso: {hit}\n"
            f"Entry: {fmt_dec(entry)}  Close: {fmt_dec(close_price)}\n"
            f"P&L: {fmt_dec(pnl_val, '0.01')} USD  ({fmt_dec(pnl_pct, '0.0001')}%)\n"
            f"Equity: {fmt_dec(eq_new, '0.01')}"
        )

    # Batch update
    if updates:
        body = {"valueInputOption": "USER_ENTERED", "data": updates}
        ws.spreadsheet.values_batch_update(body)


def open_new_trade(ws, trade_id: str, side="LONG", qty=Decimal("1")):
    """Apre una nuova riga di trade APERTO con i parametri strategia correnti."""
    H = header_index_map(ws)
    client = binance_client()
    price = get_last_price(client)
    if price == 0:
        raise RuntimeError("Prezzo non disponibile per apertura trade.")

    row = [""] * len(HEADERS)
    row[H["Data/Ora"]-1] = now_utc_str()
    row[H["ID trade"]-1] = trade_id
    row[H["Lato"]-1] = side.upper()
    row[H["Stato"]-1] = "APERTO"
    row[H["Prezzo ingresso"]-1] = fmt_dec(price)
    row[H["Qty"]-1] = fmt_dec(qty, "0.00000001")
    row[H["SL %"]-1] = fmt_dec(SL_PCT, "0.0000001")
    row[H["TP1 %"]-1] = fmt_dec(TP1_PCT, "0.0000001")
    row[H["TP2 %"]-1] = fmt_dec(TP2_PCT, "0.0000001")
    row[H["Ultimo ping"]-1] = f"{now_utc_str()} - {fmt_dec(price)}"
    row[H["Strategia"]-1] = "v1"
    ws.append_row(row, value_input_option="USER_ENTERED")

    send_whatsapp(f"⛏️ BOT ORO | {SYMBOL}\nAperto trade {trade_id}\nSide: {side}\nEntry: {fmt_dec(price)}\nTP1 {fmt_dec(price*(1+TP1_PCT))} | TP2 {fmt_dec(price*(1+TP2_PCT))} | SL {fmt_dec(price*(1-SL_PCT))}")


def main_loop():
    ws = open_sheet()
    client = binance_client()

    print(f"[START] BOT ORO su {SYMBOL} – sheet: {SHEET_NAME}")
    while True:
        try:
            update_open_rows(ws, client)
        except Exception as e:
            print(f"[LOOP] Errore: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    # Se vuoi aprire un trade manualmente all'avvio, abilita la riga sotto:
    # open_new_trade(open_sheet(), trade_id=f"{SYMBOL}-{int(time.time())}-A", side="LONG", qty=Decimal('1'))
    main_loop()
