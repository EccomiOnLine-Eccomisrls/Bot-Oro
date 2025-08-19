import os, json, time, unicodedata, requests
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException, BinanceRequestException
from twilio.rest import Client as TwilioClient

# ========= ENV =========
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
SYMBOL = os.getenv("SYMBOL", "PAXGUSDT")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")  # JSON service account

SHEET_TAB_TRADE = os.getenv("SHEET_TAB_TRADE", "Trade")
SHEET_TAB_LOG   = os.getenv("SHEET_TAB_LOG", "Log")

TP1_PCT = Decimal(os.getenv("TP1_PCT", "0.0002"))
TP2_PCT = Decimal(os.getenv("TP2_PCT", "0.0003"))
SL_PCT  = Decimal(os.getenv("SL_PCT",  "0.0050"))
BASE_EQUITY = Decimal(os.getenv("BASE_EQUITY", "10000"))

# WhatsApp (opzionale)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
TWILIO_TO   = os.getenv("TWILIO_TO", "")

# Telegram (nuovo)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")  # -100... per canali/gruppi oppure ID utente

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "8"))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Rome")
AUTO_OPEN_ON_START = os.getenv("AUTO_OPEN_ON_START", "0") == "1"  # se 1, apre 1 trade all'avvio


# ========= UTILS =========
def d(x) -> Decimal:
    if isinstance(x, Decimal): return x
    try: return Decimal(str(x))
    except (InvalidOperation, TypeError): return Decimal("0")

def fmt_dec(x: Decimal, q="0.00001") -> str:
    return d(x).quantize(Decimal(q), rounding=ROUND_HALF_UP).normalize().to_eng_string()

def _zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIMEZONE)
    except Exception:
        # fallback: UTC se tz mancante
        return timezone.utc

def now_local_str() -> str:
    return datetime.now(_zone()).strftime("%Y-%m-%d %H:%M:%S")

def norm(s: str) -> str:
    s = (s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    for ch in " -_/.:;|%": s = s.replace(ch, " ")
    return " ".join(s.strip().lower().split())

def send_whatsapp(msg: str):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_TO): return
    try:
        TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN).messages.create(
            from_=TWILIO_FROM, to=TWILIO_TO, body=msg
        )
    except Exception as e:
        print(f"[TWILIO] {e}")

def send_telegram(msg: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID): return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except Exception as e:
        print(f"[TELEGRAM] {e}")

def notify(msg: str):
    # prima Telegram, poi WhatsApp (se abilitato)
    send_telegram(msg)
    send_whatsapp(msg)


# ========= SHEETS =========
def open_ws_by_title(sh, title: str):
    try: return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        raise RuntimeError(f"Tab '{title}' non trovata. Imposta SHEET_TAB_* correttamente.")

def open_sheets():
    if not GOOGLE_CREDENTIALS: raise RuntimeError("GOOGLE_CREDENTIALS mancante.")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(GOOGLE_CREDENTIALS),
        ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    return open_ws_by_title(sh, SHEET_TAB_TRADE), open_ws_by_title(sh, SHEET_TAB_LOG)

ALIAS = {
    "data/ora": ["data ora","timestamp","datetime","dataora","data"],
    "id trade": ["id","trade id","ordine id"],
    "lato": ["side","direzione"],
    "stato": ["status","state"],
    "prezzo ingresso": ["ingresso","entry","entry price","prezzo entry"],
    "qty": ["quantita","quantity","size"],
    "sl %": ["sl","stop loss","stoploss","sl pct"],
    "tp1 %": ["tp1","take profit 1","tp1 pct"],
    "tp2 %": ["tp2","take profit 2","tp2 pct"],
    "prezzo chiusura": ["close","exit","chiusura","prezzo close"],
    "ultimo ping": ["ping","ultimo prezzo","last price","last ping"],
    "p&l %": ["pl %","pnl %","profit %"],
    "p&l valore": ["pl","pnl","profit"],
    "equity post-trade": ["equity","saldo","balance","equity post trade"],
    "strategia": ["strategy","strat"],
    "note": ["notes","esito","tp/sl","esecuzione"],
}
def build_header_map(header_row):
    H={}
    for idx,name in enumerate(header_row, start=1):
        n=norm(name)
        for canon,alts in ALIAS.items():
            if n==norm(canon) or n in [norm(a) for a in alts]:
                H[canon]=idx; break
    return H
def get_header(ws):
    header=ws.row_values(1)
    if not header: raise RuntimeError(f"La tab '{ws.title}' non ha intestazioni.")
    return header


# ========= BINANCE =========
def binance_client():
    return BinanceClient(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
def get_last_price(client) -> Decimal:
    try:
        p=client.get_symbol_ticker(symbol=SYMBOL)
        return d(p["price"])
    except (BinanceAPIException, BinanceRequestException, KeyError, TypeError) as e:
        print(f"[BINANCE] {e}"); return Decimal("0")


# ========= LOGICA =========
def compute_targets(entry: Decimal):
    return (entry*(1+TP1_PCT), entry*(1+TP2_PCT), entry*(1-SL_PCT))
def pnl_values(side, entry, close, qty):
    if qty==0 or entry==0 or close==0: return (Decimal("0"), Decimal("0"))
    if side.upper()=="LONG":
        return ((close/entry-1)*100, (close-entry)*qty)
    else:
        return ((entry/close-1)*100, (entry-close)*qty)


# ========= SUPPORTO START/REPAIR =========
def log_get_messages(ws_log, max_rows=2000):
    try:
        msgs = ws_log.col_values(3)  # colonna 'msg'
        if len(msgs) <= 1:
            return set()
        return set(msgs[-max_rows:])
    except Exception:
        return set()

def start_already_notified(log_msgs: set, trade_id: str) -> bool:
    if not trade_id:
        return False
    key = f"Aperto trade {trade_id}"
    for m in log_msgs:
        if trade_id in m and "Aperto trade" in m:
            return True
        if key in m:
            return True
    return False

def gen_trade_id(symbol: str, row_index: int) -> str:
    return f"{symbol}-{int(time.time())}-R{row_index}"

def reconcile_and_notify_starts(ws_trade, ws_log, symbol: str):
    """
    - Se 'Prezzo chiusura' è pieno e 'Stato' è vuoto/APERTO -> CHIUSO.
    - Se 'Prezzo ingresso' è pieno e 'Stato' è vuoto -> APERTO.
    - Se APERTO ma manca 'ID trade' -> genera ID.
    - Se riga APERTA appena riconosciuta -> invia messaggio START (una sola volta).
    """
    header = get_header(ws_trade)
    H = build_header_map(header)

    need = ["data/ora", "id trade", "lato", "stato", "prezzo ingresso", "ultimo ping"]
    for k in need:
        if k not in H:
            raise RuntimeError(f"Manca colonna '{k}' nella tab '{ws_trade.title}'.")

    L_ID = H["id trade"]
    L_STATO = H["stato"]
    L_LATO = H["lato"]
    L_ENTRY = H["prezzo ingresso"]
    L_CLOSE = H.get("prezzo chiusura")
    L_TP1 = H.get("tp1 %")
    L_TP2 = H.get("tp2 %")
    L_SL  = H.get("sl %")

    rows = ws_trade.get_all_values()
    if len(rows) <= 1:
        return

    log_msgs = log_get_messages(ws_log)
    updates = []

    for r in range(2, len(rows)+1):
        row = rows[r-1]
        stato = (row[L_STATO-1] if len(row) >= L_STATO else "").strip().upper()
        trade_id = (row[L_ID-1] if len(row) >= L_ID else "").strip()
        side = (row[L_LATO-1] if len(row) >= L_LATO else "LONG").strip().upper()
        entry_str = (row[L_ENTRY-1] if len(row) >= L_ENTRY else "").strip()
        entry = d(entry_str) if entry_str else Decimal("0")
        close_str = (row[L_CLOSE-1] if L_CLOSE and len(row) >= L_CLOSE else "").strip()
        has_close = bool(close_str)

        # chiudi se incoerente
        if has_close and stato in ("", "APERTO"):
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_STATO), "values": [["CHIUSO"]]})
            stato = "CHIUSO"

        # apri se ha entry ma stato vuoto
        if entry > 0 and stato == "":
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_STATO), "values": [["APERTO"]]})
            stato = "APERTO"

        # genera ID se manca
        if stato == "APERTO" and not trade_id:
            trade_id = gen_trade_id(symbol, r)
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_ID), "values": [[trade_id]]})

        # notifica START una volta
        if stato == "APERTO" and entry > 0 and not start_already_notified(log_msgs, trade_id):
            TP1 = d(row[L_TP1-1]) if L_TP1 and len(row) >= L_TP1 and (row[L_TP1-1] or "").strip() else TP1_PCT
            TP2 = d(row[L_TP2-1]) if L_TP2 and len(row) >= L_TP2 and (row[L_TP2-1] or "").strip() else TP2_PCT
            SL  = d(row[L_SL-1])  if L_SL  and len(row) >= L_SL  and (row[L_SL-1]  or "").strip() else SL_PCT

            msg = (
                f"🚀 BOT ORO | {symbol}\n"
                f"Aperto trade {trade_id}\n"
                f"Side: {side} · Entry: {fmt_dec(entry)}\n"
                f"TP1 {fmt_dec(entry*(1+TP1))} · TP2 {fmt_dec(entry*(1+TP2))} · SL {fmt_dec(entry*(1-SL))}\n"
                f"{TIMEZONE}"
            )
            notify(msg)
            log(ws_log, "INFO", f"Aperto trade {trade_id} @ {fmt_dec(entry)} (riconosciuto)")

    if updates:
        ws_trade.spreadsheet.values_batch_update({"valueInputOption": "USER_ENTERED", "data": updates})


# ========= OPERATIVA PRINCIPALE =========
def last_equity(ws, idx_equity) -> Decimal:
    col = ws.col_values(idx_equity)
    for v in reversed(col[1:]):
        v=(v or "").strip()
        if v:
            try: return d(v)
            except: continue
    return BASE_EQUITY

def log(ws_log, level, msg):
    try:
        ws_log.append_row([now_local_str(), level, msg, "bot"], value_input_option="USER_ENTERED")
    except Exception as e:
        print(f"[LOG] {level}: {msg} ({e})")

def update_open_rows(ws_trade, ws_log, client):
    # ripara stati + notifica START per nuove righe
    reconcile_and_notify_starts(ws_trade, ws_log, SYMBOL)

    header = get_header(ws_trade)
    H = build_header_map(header)

    need = ["prezzo ingresso","ultimo ping","p&l %","p&l valore","equity post-trade"]
    for k in need:
        if k not in H: raise RuntimeError(f"Manca colonna '{k}' in '{ws_trade.title}'.")

    L_STATO = H.get("stato"); L_LATO = H.get("lato"); L_QTY = H.get("qty")
    L_NOTE = H.get("note"); L_CLOSE = H.get("prezzo chiusura")

    rows = ws_trade.get_all_values()
    if len(rows)<=1: return

    nowloc = now_local_str()
    lastp = get_last_price(client)
    if lastp==0: log(ws_log,"WARN","Prezzo 0 da Binance"); return

    updates=[]
    for r in range(2, len(rows)+1):
        row = rows[r-1]
        stato = (row[L_STATO-1] if L_STATO and len(row)>=L_STATO else "").strip().upper()
        side  = (row[L_LATO-1]  if L_LATO  and len(row)>=L_LATO  else "LONG").strip().upper()
        entry = d(row[H["prezzo ingresso"]-1]) if len(row)>=H["prezzo ingresso"] else Decimal("0")
        qty   = d(row[L_QTY-1]) if L_QTY and len(row)>=L_QTY else Decimal("1")

        # Ping (ora locale)
        updates.append({"range": gspread.utils.rowcol_to_a1(r, H["ultimo ping"]),
                        "values":[[f"{nowloc} - {fmt_dec(lastp)}"]]})

        if stato == "CHIUSO" or entry == 0:
            continue

        tp1,tp2,sl=compute_targets(entry)
        hit=None; close_price=None
        if side=="LONG":
            if lastp>=tp2: hit,close_price="TP2",tp2
            elif lastp>=tp1: hit,close_price="TP1",tp1
            elif lastp<=sl: hit,close_price="SL",sl
        else:
            if lastp<=tp2: hit,close_price="TP2",tp2
            elif lastp<=tp1: hit,close_price="TP1",tp1
            elif lastp>=sl: hit,close_price="SL",sl

        if not hit:
            pnl_pct,pnl_val = pnl_values(side, entry, lastp, qty)
            updates += [
                {"range": gspread.utils.rowcol_to_a1(r, H["p&l %"]), "values":[[fmt_dec(pnl_pct,"0.0001")]]},
                {"range": gspread.utils.rowcol_to_a1(r, H["p&l valore"]), "values":[[fmt_dec(pnl_val,"0.01")]]},
            ]
            continue

        # CHIUSURA
        pnl_pct,pnl_val = pnl_values(side, entry, close_price, qty)
        eq_prev = last_equity(ws_trade, H["equity post-trade"])
        eq_new  = eq_prev + pnl_val

        if L_CLOSE:
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_CLOSE),"values":[[fmt_dec(close_price)]]})
        if L_STATO:
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_STATO),"values":[["CHIUSO"]]})
        if L_NOTE:
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_NOTE),"values":[[hit]]})
        updates += [
            {"range": gspread.utils.rowcol_to_a1(r, H["p&l %"]), "values":[[fmt_dec(pnl_pct,"0.0001")]]},
            {"range": gspread.utils.rowcol_to_a1(r, H["p&l valore"]), "values":[[fmt_dec(pnl_val,"0.01")]]},
            {"range": gspread.utils.rowcol_to_a1(r, H["equity post-trade"]), "values":[[fmt_dec(eq_new,"0.01")]]},
        ]

        notify(
            f"⛏️ BOT ORO | {SYMBOL}\n"
            f"Trade chiuso: {hit}\n"
            f"Entry: {fmt_dec(entry)}  Close: {fmt_dec(close_price)}\n"
            f"P&L: {fmt_dec(pnl_val,'0.01')} USD  ({fmt_dec(pnl_pct,'0.0001')}%)\n"
            f"Equity: {fmt_dec(eq_new,'0.01')} · {TIMEZONE}"
        )

    if updates:
        ws_trade.spreadsheet.values_batch_update({"valueInputOption":"USER_ENTERED","data":updates})


def open_new_trade(ws_trade, ws_log, trade_id: str, side="LONG", qty=Decimal("1")):
    header=get_header(ws_trade); H=build_header_map(header)
    need=["data/ora","id trade","lato","stato","prezzo ingresso","qty","sl %","tp1 %","tp2 %","ultimo ping"]
    for k in need:
        if k not in H: raise RuntimeError(f"Colonna '{k}' mancante per aprire un trade.")
    price=get_last_price(binance_client())
    if price==0: raise RuntimeError("Prezzo non disponibile per apertura trade.")

    row=[""]*len(header)
    def setv(k,v): row[H[k]-1]=v

    setv("data/ora", now_local_str())
    setv("id trade", trade_id)
    setv("lato", side.upper())
    setv("stato", "APERTO")
    setv("prezzo ingresso", fmt_dec(price))
    setv("qty", fmt_dec(qty,"0.00000001"))
    setv("sl %", fmt_dec(SL_PCT,"0.0000001"))
    setv("tp1 %", fmt_dec(TP1_PCT,"0.0000001"))
    setv("tp2 %", fmt_dec(TP2_PCT,"0.0000001"))
    setv("ultimo ping", f"{now_local_str()} - {fmt_dec(price)}")

    ws_trade.append_row(row, value_input_option="USER_ENTERED")
    msg = (f"🚀 BOT ORO | {SYMBOL}\n"
           f"Trade APERTO: {trade_id}\n"
           f"Side: {side}  Entry: {fmt_dec(price)}\n"
           f"TP1 {fmt_dec(price*(1+TP1_PCT))} · TP2 {fmt_dec(price*(1+TP2_PCT))} · SL {fmt_dec(price*(1-SL_PCT))}\n"
           f"{TIMEZONE}")
    log(ws_log,"INFO",f"Aperto trade {trade_id} @ {fmt_dec(price)}")
    notify(msg)


def main_loop():
    ws_trade, ws_log = open_sheets()
    client = binance_client()
    log(ws_log,"INFO",f"Bot Oro avviato · Trade='{ws_trade.title}', Log='{ws_log.title}' · TZ={TIMEZONE}")

    # ripara subito e invia START per righe APERTE non ancora notificate
    reconcile_and_notify_starts(ws_trade, ws_log, SYMBOL)

    if AUTO_OPEN_ON_START:
        try:
            open_new_trade(ws_trade, ws_log, trade_id=f"{SYMBOL}-{int(time.time())}-A", side="LONG")
        except Exception as e:
            log(ws_log,"ERROR",f"Apertura automatica fallita: {e}")

    while True:
        try:
            update_open_rows(ws_trade, ws_log, client)
            log(ws_log,"INFO",f"Heartbeat OK - {fmt_dec(get_last_price(client))}")
        except Exception as e:
            log(ws_log,"ERROR",str(e))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main_loop()
