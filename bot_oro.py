# bot_oro.py
# --------------------------------------------
# BOT ORO – Simulazione con max 5 posizioni, Ping per riga, P&L, notifiche
# --------------------------------------------

import os
import json
import time
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Binance
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException, BinanceRequestException

# Twilio (opzionale)
from twilio.rest import Client as TwilioClient


# =========================
# CONFIG
# =========================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
SYMBOL = os.getenv("SYMBOL", "PAXGUSDT")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")  # JSON service account

# Nomi TAB (non verranno creati automaticamente)
SHEET_TAB_TRADE = os.getenv("SHEET_TAB_TRADE", "Trade")
SHEET_TAB_LOG = os.getenv("SHEET_TAB_LOG", "Log")

# Strategia
TP1_PCT = Decimal(os.getenv("TP1_PCT", "0.0002"))
TP2_PCT = Decimal(os.getenv("TP2_PCT", "0.0003"))
SL_PCT  = Decimal(os.getenv("SL_PCT",  "0.0050"))
BASE_EQUITY = Decimal(os.getenv("BASE_EQUITY", "10000"))

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
TWILIO_TO = os.getenv("TWILIO_TO", "")

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

def norm(s: str) -> str:
    """Normalizza una stringa per il matching (case/accents/spazi/punt)."""
    s = s or ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    repl = {
        "%": " percent",
        "&": " and ",
        "é": "e", "è": "e", "à": "a", "ò": "o", "ù": "u",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    for ch in " -_/.:;|":
        s = s.replace(ch, " ")
    s = " ".join(s.split())
    return s

def send_whatsapp(message: str):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_TO):
        return
    try:
        tw = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        tw.messages.create(from_=TWILIO_FROM, to=TWILIO_TO, body=message)
    except Exception as e:
        print(f"[TWILIO] Errore: {e}")


# =========================
# GOOGLE SHEETS
# =========================
def open_ws_by_title(gc, sh, title: str):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        raise RuntimeError(
            f"Tab '{title}' non trovata. Creala con questo nome esatto o imposta SHEET_TAB_* correttamente."
        )

def open_sheets():
    if not GOOGLE_CREDENTIALS:
        raise RuntimeError("GOOGLE_CREDENTIALS mancante.")
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws_trade = open_ws_by_title(gc, sh, SHEET_TAB_TRADE)
    ws_log = open_ws_by_title(gc, sh, SHEET_TAB_LOG)
    return ws_trade, ws_log

# alias possibili -> chiave canonica
ALIAS = {
    "data/ora": ["data ora", "timestamp", "dataora", "datetime", "data"],
    "id trade": ["id", "trade id", "id_trad", "ordine id"],
    "lato": ["side", "direzione"],
    "stato": ["status", "state"],
    "prezzo ingresso": ["ingresso", "entry", "entry price", "prezzo entry"],
    "qty": ["quantita", "quantity", "size"],
    "sl %": ["sl", "stop loss", "stoploss", "sl percent", "sl pct"],
    "tp1 %": ["tp1", "take profit 1", "tp1 percent", "tp1 pct"],
    "tp2 %": ["tp2", "take profit 2", "tp2 percent", "tp2 pct"],
    "prezzo chiusura": ["close", "prezzo close", "exit", "chiusura"],
    "ultimo ping": ["ping", "ultimo prezzo", "last price", "last ping"],
    "p&l %": ["pl %", "p l %", "pnl %", "p&l percent", "profit %"],
    "p&l valore": ["pl", "p l", "pnl", "profit", "p&l"],
    "equity post-trade": ["equity", "saldo", "equity post trade", "balance"],
    "strategia": ["strategy", "strat"],
    "note": ["notes", "esito", "tp/sl", "esecuzione"],
}

def build_header_map(header_row):
    """Ritorna dict {canonico: col_index} facendo matching tollerante."""
    H = {}
    # 1-based index
    for idx, name in enumerate(header_row, start=1):
        nname = norm(name)
        for canon, alist in ALIAS.items():
            if nname == norm(canon) or nname in [norm(a) for a in alist]:
                H[canon] = idx
                break
    return H

def get_header(ws):
    header = ws.row_values(1)
    if not header:
        raise RuntimeError(f"La tab '{ws.title}' non ha intestazioni (riga 1 vuota).")
    return header


# =========================
# BINANCE PRICE
# =========================
def binance_client():
    return BinanceClient(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)

def get_last_price(client) -> Decimal:
    try:
        p = client.get_symbol_ticker(symbol=SYMBOL)
        return d(p["price"])
    except Exception as e:
        print(f"[BINANCE] Errore prezzo: {e}")
        return Decimal("0")


# =========================
# LOGICA
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
    else:
        pnl_val = (entry - close) * qty
        pnl_pct = (entry / close - 1) * 100
    return (pnl_pct, pnl_val)


# =========================
# OPERATIVA
# =========================
def last_equity(ws, col_idx) -> Decimal:
    col = ws.col_values(col_idx)
    for val in reversed(col[1:]):
        val = (val or "").strip()
        if val:
            try:
                return d(val)
            except Exception:
                continue
    return BASE_EQUITY

def log(ws_log, level: str, msg: str):
    try:
        ws_log.append_row([now_utc_str(), level, msg, "bot"], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"[LOG] {level}: {msg} (errore append: {e})")

def update_open_rows(ws_trade, ws_log, client):
    header = get_header(ws_trade)
    H = build_header_map(header)

    # Controlli minimi
    needed = ["prezzo ingresso", "ultimo ping", "p&l %", "p&l valore", "equity post-trade"]
    for k in needed:
        if k not in H:
            raise RuntimeError(f"Colonna '{k}' non trovata nella tab '{ws_trade.title}'.")
    # opzionali ma usate
    L_STATO = H.get("stato")
    L_LATO = H.get("lato")
    L_QTY = H.get("qty")
    L_NOTE = H.get("note")
    L_PREZZO_CHIUSURA = H.get("prezzo chiusura")

    rows = ws_trade.get_all_values()
    if len(rows) <= 1:
        return

    now = now_utc_str()
    last_price = get_last_price(client)
    if last_price == 0:
        log(ws_log, "WARN", "Prezzo 0 da Binance")
        return

    updates = []
    # 2..N
    for r_idx in range(2, len(rows)+1):
        row = rows[r_idx-1]
        stato = (row[L_STATO-1] if L_STATO else "").strip().upper() if len(row) >= (L_STATO or 0) else ""
        if stato not in ("APERTO", "CHIUSO", ""):
            stato = "APERTO"
        if stato == "CHIUSO":
            # Aggiorna solo il ping visivo
            updates.append({
                "range": gspread.utils.rowcol_to_a1(r_idx, H["ultimo ping"]),
                "values": [[f"{now} - {fmt_dec(last_price)}"]],
            })
            continue

        side = (row[L_LATO-1] if L_LATO else "LONG").strip().upper() if len(row) >= (L_LATO or 0) else "LONG"
        entry = d(row[H["prezzo ingresso"]-1]) if len(row) >= H["prezzo ingresso"] else Decimal("0")
        if entry == 0:
            continue
        qty = d(row[L_QTY-1]) if L_QTY and len(row) >= L_QTY else Decimal("1")

        tp1, tp2, sl = compute_targets(entry)

        # aggiorna ping
        updates.append({
            "range": gspread.utils.rowcol_to_a1(r_idx, H["ultimo ping"]),
            "values": [[f"{now} - {fmt_dec(last_price)}"]],
        })

        hit = None
        close_price = None

        if side == "LONG":
            if last_price >= tp2:
                hit, close_price = "TP2", tp2
            elif last_price >= tp1:
                hit, close_price = "TP1", tp1
            elif last_price <= sl:
                hit, close_price = "SL", sl
        else:
            if last_price <= tp2:
                hit, close_price = "TP2", tp2
            elif last_price <= tp1:
                hit, close_price = "TP1", tp1
            elif last_price >= sl:
                hit, close_price = "SL", sl

        if not hit:
            pnl_pct, pnl_val = pnl_values(side, entry, last_price, qty)
            updates += [
                {"range": gspread.utils.rowcol_to_a1(r_idx, H["p&l %"]),
                 "values": [[fmt_dec(pnl_pct, "0.0001")]]},
                {"range": gspread.utils.rowcol_to_a1(r_idx, H["p&l valore"]),
                 "values": [[fmt_dec(pnl_val, "0.01")]]},
            ]
            continue

        # chiusura
        pnl_pct, pnl_val = pnl_values(side, entry, close_price, qty)
        eq_prev = last_equity(ws_trade, H["equity post-trade"])
        eq_new = eq_prev + pnl_val

        if L_PREZZO_CHIUSURA:
            updates.append({"range": gspread.utils.rowcol_to_a1(r_idx, L_PREZZO_CHIUSURA),
                            "values": [[fmt_dec(close_price)]]})

        updates += [
            {"range": gspread.utils.rowcol_to_a1(r_idx, H["p&l %"]),
             "values": [[fmt_dec(pnl_pct, "0.0001")]]},
            {"range": gspread.utils.rowcol_to_a1(r_idx, H["p&l valore"]),
             "values": [[fmt_dec(pnl_val, "0.01")]]},
            {"range": gspread.utils.rowcol_to_a1(r_idx, H["equity post-trade"]),
             "values": [[fmt_dec(eq_new, "0.01")]]},
        ]
        if L_STATO:
            updates.append({"range": gspread.utils.rowcol_to_a1(r_idx, L_STATO),
                            "values": [["CHIUSO"]]})
        if L_NOTE:
            updates.append({"range": gspread.utils.rowcol_to_a1(r_idx, L_NOTE),
                            "values": [[hit]]})

        send_whatsapp(
            f"⛏️ BOT ORO | {SYMBOL}\n"
            f"Trade chiuso: {hit}\n"
            f"Entry: {fmt_dec(entry)}  Close: {fmt_dec(close_price)}\n"
            f"P&L: {fmt_dec(pnl_val, '0.01')} USD  ({fmt_dec(pnl_pct, '0.0001')}%)\n"
            f"Equity: {fmt_dec(eq_new, '0.01')}"
        )

    if updates:
        body = {"valueInputOption": "USER_ENTERED", "data": updates}
        ws_trade.spreadsheet.values_batch_update(body)

def open_new_trade(ws_trade, ws_log, trade_id: str, side="LONG", qty=Decimal("1")):
    """Apre manualmente una riga in Trade con stato APERTO. Non tocca le intestazioni."""
    header = get_header(ws_trade)
    H = build_header_map(header)

    needed = ["data/ora", "id trade", "lato", "stato", "prezzo ingresso", "qty", "sl %", "tp1 %", "tp2 %", "ultimo ping"]
    for k in needed:
        if k not in H:
            raise RuntimeError(f"Colonna '{k}' mancante per aprire un trade.")

    client = binance_client()
    price = get_last_price(client)
    if price == 0:
        raise RuntimeError("Prezzo non disponibile per apertura trade.")

    row = [""] * len(header)
    def setval(key, value):
        row[H[key]-1] = value

    setval("data/ora", now_utc_str())
    setval("id trade", trade_id)
    setval("lato", side.upper())
    setval("stato", "APERTO")
    setval("prezzo ingresso", fmt_dec(price))
    setval("qty", fmt_dec(qty, "0.00000001"))
    setval("sl %", fmt_dec(SL_PCT, "0.0000001"))
    setval("tp1 %", fmt_dec(TP1_PCT, "0.0000001"))
    setval("tp2 %", fmt_dec(TP2_PCT, "0.0000001"))
    setval("ultimo ping", f"{now_utc_str()} - {fmt_dec(price)}")

    ws_trade.append_row(row, value_input_option="USER_ENTERED")
    log(ws_log, "INFO", f"Aperto trade {trade_id} @ {fmt_dec(price)}")

def main_loop():
    ws_trade, ws_log = open_sheets()
    client = binance_client()
    log(ws_log, "INFO", f"Bot Oro avviato · tab Trade='{ws_trade.title}', Log='{ws_log.title}' · TP1 {TP1_PCT*100}%, TP2 {TP2_PCT*100}%, SL {SL_PCT*100}%")

    while True:
        try:
            update_open_rows(ws_trade, ws_log, client)
            log(ws_log, "INFO", f"Heartbeat OK - {fmt_dec(get_last_price(client))}")
        except Exception as e:
            log(ws_log, "ERROR", str(e))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    # Per aprire un trade di test all'avvio, scommenta la riga seguente:
    # ws_trade, ws_log = open_sheets(); open_new_trade(ws_trade, ws_log, trade_id=f"{SYMBOL}-{int(time.time())}-A", side="LONG")
    main_loop()

if __name__ == "__main__":
    # Se vuoi aprire un trade manualmente all'avvio, abilita la riga sotto:
    # open_new_trade(open_sheet(), trade_id=f"{SYMBOL}-{int(time.time())}-A", side="LONG", qty=Decimal('1'))
    main_loop()
