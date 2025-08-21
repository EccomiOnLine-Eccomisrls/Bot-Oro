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

# Telegram (opzionale)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")  # -100... per canali/gruppi oppure ID utente

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "8"))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Rome")
AUTO_OPEN_ON_START = os.getenv("AUTO_OPEN_ON_START", "0") == "1"  # se 1, apre 1 trade all'avvio

# Auto-apertura continua per mantenere almeno N trade
MIN_OPEN_TRADES = int(os.getenv("MIN_OPEN_TRADES", "5"))   # minimo trades APERTI da mantenere
AUTO_TRADE_SIDE = os.getenv("AUTO_TRADE_SIDE", "LONG")      # lato predefinito (LONG/SHORT)
DEFAULT_QTY     = Decimal(os.getenv("DEFAULT_QTY", "1"))    # qty predefinito

# Debug / throttle log
DEBUG_HEADERS = os.getenv("DEBUG_HEADERS", "0") == "1"
HEARTBEAT_MIN_SECONDS = int(os.getenv("HEARTBEAT_MIN_SECONDS", "60"))  # scrivi heartbeat log ogni X s minimo
HEARTBEAT_PRICE_DELTA_BP = int(os.getenv("HEARTBEAT_PRICE_DELTA_BP", "2"))  # basis points (2 = 0.02%)

# Riconciliazione meno frequente (per ridurre letture)
RECONCILE_MIN_SECONDS = int(os.getenv("RECONCILE_MIN_SECONDS", "180"))

# Stato interno per throttling
_LAST_HEADER_SIG = None
_LAST_HEARTBEAT_TS = 0
_LAST_HEARTBEAT_PRICE = None
_LAST_RECONCILE_TS = 0

# Cache header/mapping
_H_CACHE = None
_COL_PING_CACHE = None


# ========= UTILS =========
def d(x) -> Decimal:
    """
    Converte stringhe stile IT/EN in Decimal:
    - '2345,67' -> 2345.67
    - '0,02%'   -> 0.0002
    """
    if isinstance(x, Decimal):
        return x
    if x is None:
        return Decimal("0")
    s = str(x).strip()
    if not s:
        return Decimal("0")

    is_percent = False
    if s.endswith("%"):
        is_percent = True
        s = s[:-1].strip()

    s = s.replace(" ", "")

    # se contiene sia . che , => interpreto , come decimale
    if "." in s and "," in s:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")

    try:
        val = Decimal(s)
    except:
        return Decimal("0")

    if is_percent:
        val = val / Decimal("100")
    return val

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

# === Alias più tolleranti ===
ALIAS = {
    "data/ora": ["data ora","timestamp","datetime","dataora","data"],
    "id trade": ["id","trade id","ordine id"],
    "lato": ["side","direzione"],
    "stato": ["status","state"],
    "prezzo ingresso": ["ingresso","entry","entry price","prezzo entry","prezzo d'ingresso","prezzo di ingresso"],
    "qty": ["quantita","quantity","size","q.tà","quantità"],
    "sl %": ["sl","stop loss","stoploss","sl pct","sl percentuale"],
    "tp1 %": ["tp1","take profit 1","tp1 pct","tp1 percentuale"],
    "tp2 %": ["tp2","take profit 2","tp2 pct","tp2 percentuale"],
    "prezzo chiusura": ["close","exit","chiusura","prezzo close","prezzo di chiusura"],
    "ultimo ping": ["ping","ultimo prezzo","last price","last ping","heartbeat","ultimo aggiornamento"],
    "delta": ["differenza", "delta prezzo", "diff", "delta $", "delta value"],
    "p&l %": ["pl %","pnl %","profit %","p e l %"],
    "p&l valore": ["pl","p l","pnl","profit","pl valore","pnl valore","p e l valore","p & l valore"],
    "equity post-trade": ["equity","saldo","balance","equity post trade","equity post trade","equity post − trade","equity post – trade","equity post — trade"],
    "strategia": ["strategy","strat"],
    "note": ["notes","esito","tp/sl","esecuzione","nota"],
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

def header_signature(header_row):
    return "|".join([h.strip().lower() for h in header_row])

def dump_headers_once(ws_trade, ws_log):
    """Logga gli header solo se cambiano e solo se DEBUG_HEADERS=1."""
    global _LAST_HEADER_SIG
    if not DEBUG_HEADERS:
        return
    header = ws_trade.row_values(1)
    sig = header_signature(header)
    if sig != _LAST_HEADER_SIG:
        _LAST_HEADER_SIG = sig
        try:
            ws_log.append_row([now_local_str(), "INFO", f"[DEBUG] Header Trade raw: {header}", "bot"], value_input_option="USER_ENTERED")
            H = build_header_map(header)
            ws_log.append_row([now_local_str(), "INFO", f"[DEBUG] Header mappati: {sorted(list(H.keys()))}", "bot"], value_input_option="USER_ENTERED")
        except Exception as e:
            print("[DEBUG] dump_headers_once error:", e)

def find_col_by_header(ws, header_name: str) -> int:
    header = ws.row_values(1)
    if not header:
        raise RuntimeError(f"La tab '{ws.title}' non ha intestazioni.")
    target = (header_name or "").strip().lower()
    for i, h in enumerate(header, start=1):
        if (h or "").strip().lower() == target:
            return i
    raise RuntimeError(f"Header '{header_name}' non trovato in '{ws.title}': {header}")


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
    - Se 'Prezzo ingresso' (cella NON vuota) e 'Stato' è vuoto -> APERTO.
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

    for r in range(2, len(rows) + 1):
        row = rows[r - 1]
        stato = (row[L_STATO - 1] if len(row) >= L_STATO else "").strip().upper()
        trade_id = (row[L_ID - 1] if len(row) >= L_ID else "").strip()
        side = (row[L_LATO - 1] if len(row) >= L_LATO else "LONG").strip().upper()
        entry_str = (row[L_ENTRY - 1] if len(row) >= L_ENTRY else "").strip()
        entry = d(entry_str) if entry_str else Decimal("0")
        close_str = (row[L_CLOSE - 1] if L_CLOSE and len(row) >= L_CLOSE else "").strip()
        has_close = bool(close_str)

        # Se ha un prezzo di chiusura ma non è segnato chiuso -> chiudi
        if has_close and stato in ("", "APERTO"):
            updates.append({
                "range": gspread.utils.rowcol_to_a1(r, L_STATO),
                "values": [["CHIUSO"]]
            })
            stato = "CHIUSO"

        # Se la cella entry NON è vuota e lo stato è vuoto -> APERTO
        if entry_str and stato == "":
            updates.append({
                "range": gspread.utils.rowcol_to_a1(r, L_STATO),
                "values": [["APERTO"]]
            })
            stato = "APERTO"

        # Genera ID se manca su riga APERTA
        if stato == "APERTO" and not trade_id:
            trade_id = gen_trade_id(symbol, r)
            updates.append({
                "range": gspread.utils.rowcol_to_a1(r, L_ID),
                "values": [[trade_id]]
            })

        # START una sola volta
        if stato == "APERTO" and entry > 0 and not start_already_notified(log_msgs, trade_id):
            TP1 = d(row[L_TP1 - 1]) if L_TP1 and len(row) >= L_TP1 and (row[L_TP1 - 1] or "").strip() else TP1_PCT
            TP2 = d(row[L_TP2 - 1]) if L_TP2 and len(row) >= L_TP2 and (row[L_TP2 - 1] or "").strip() else TP2_PCT
            SL  = d(row[L_SL  - 1]) if L_SL  and len(row) >= L_SL  and (row[L_SL  - 1]  or "").strip() else SL_PCT

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
        ws_trade.spreadsheet.values_batch_update({
            "valueInputOption": "USER_ENTERED",
            "data": updates
        })


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

def should_log_heartbeat(price: Decimal) -> bool:
    """Decide se scrivere la riga 'Heartbeat OK - price' per evitare spam."""
    global _LAST_HEARTBEAT_TS, _LAST_HEARTBEAT_PRICE
    now_ts = time.time()
    if _LAST_HEARTBEAT_TS == 0 or _LAST_HEARTBEAT_PRICE is None:
        _LAST_HEARTBEAT_TS = now_ts
        _LAST_HEARTBEAT_PRICE = price
        return True
    # tempo
    if now_ts - _LAST_HEARTBEAT_TS >= HEARTBEAT_MIN_SECONDS:
        _LAST_HEARTBEAT_TS = now_ts
        _LAST_HEARTBEAT_PRICE = price
        return True
    # delta prezzo in basis points
    try:
        if price > 0 and _LAST_HEARTBEAT_PRICE > 0:
            move_bp = abs((price - _LAST_HEARTBEAT_PRICE) / _LAST_HEARTBEAT_PRICE) * 10000
            if move_bp >= HEARTBEAT_PRICE_DELTA_BP:
                _LAST_HEARTBEAT_TS = now_ts
                _LAST_HEARTBEAT_PRICE = price
                return True
    except Exception:
        pass
    return False

def update_open_rows_light(ws_trade, ws_log, client, H, col_ping):
    """
    Versione "leggera": legge solo le colonne necessarie.
    - 1 read per 'stato' per capire quante righe ci sono
    - se nessuna riga -> aggiorna solo K2
    - altrimenti, legge solo le colonne richieste (entry/lato/qty/close) una volta per tutte
    - aggiorna Delta (se la colonna 'Delta' esiste), P&L % e P&L valore ad ogni ping
    """
    nowloc = now_local_str()
    lastp = get_last_price(client)
    if lastp == 0:
        log(ws_log, "WARN", "Prezzo 0 da Binance")
        return

    # 1) leggi solo colonna STATO (riduce letture)
    stato_col = ws_trade.col_values(H["stato"])  # include header in posizione 1
    if len(stato_col) <= 1:
        # solo header -> aggiorna K2 e stop
        try:
            ws_trade.update_cell(2, col_ping, f"{nowloc} - {fmt_dec(lastp)}")
        except Exception as e:
            log(ws_log, "ERROR", f"[DEBUG] update_cell K2 fallito: {e}")
        return

    n_rows = len(stato_col) - 1  # escludi header
    start_row = 2
    end_row = start_row + n_rows - 1

    # Aggiorna 'Ultimo ping' per TUTTE le righe presenti (scrittura batch)
    updates = []
    for r in range(start_row, end_row + 1):
        updates.append({
            "range": gspread.utils.rowcol_to_a1(r, col_ping),
            "values": [[f"{nowloc} - {fmt_dec(lastp)}"]],
        })

    # 2) leggi colonne minime per P&L/chiusure (UNA LETTURA per colonna)
    side_col  = ws_trade.col_values(H["lato"]) if "lato" in H else []
    entry_col = ws_trade.col_values(H["prezzo ingresso"])
    qty_col   = ws_trade.col_values(H["qty"])  if "qty" in H  else []
    close_col = ws_trade.col_values(H["prezzo chiusura"]) if "prezzo chiusura" in H else []

    plpct_col_idx  = H["p&l %"]
    plval_col_idx  = H["p&l valore"]
    equity_col_idx = H["equity post-trade"]
    note_col_idx   = H.get("note")
    stato_col_idx  = H["stato"]
    close_col_idx  = H.get("prezzo chiusura")
    delta_col_idx  = H.get("delta")  # opzionale

    # Loop sulle righe dati
    for i in range(n_rows):
        r = start_row + i
        stato = (stato_col[i+1] if i+1 < len(stato_col) else "").strip().upper()
        side  = (side_col[i+1]  if i+1 < len(side_col)  else "LONG").strip().upper()
        entry_str = (entry_col[i+1] if i+1 < len(entry_col) else "").strip()
        qty_str   = (qty_col[i+1]   if i+1 < len(qty_col)   else "").strip()
        close_str = (close_col[i+1] if i+1 < len(close_col) else "").strip()

        entry = d(entry_str) if entry_str else Decimal("0")
        qty   = d(qty_str) if qty_str else Decimal("1")

        if stato == "CHIUSO" or entry == 0:
            continue

        tp1, tp2, sl = compute_targets(entry)
        hit = None
        close_price = None
        if side == "LONG":
            if lastp >= tp2: hit, close_price = "TP2", tp2
            elif lastp >= tp1: hit, close_price = "TP1", tp1
            elif lastp <= sl:  hit, close_price = "SL",  sl
        else:
            if lastp <= tp2: hit, close_price = "TP2", tp2
            elif lastp <= tp1: hit, close_price = "TP1", tp1
            elif lastp >= sl:  hit, close_price = "SL",  sl

        if not hit:
            # P&L % e P&L valore (guadagno/perdita istantanei)
            pnl_pct, pnl_val = pnl_values(side, entry, lastp, qty)

            # Delta prezzo istantaneo (Last - Entry per LONG, Entry - Last per SHORT)
            delta_price = (lastp - entry) if side == "LONG" else (entry - lastp)

            row_updates = [
                {"range": gspread.utils.rowcol_to_a1(r, plpct_col_idx), "values": [[fmt_dec(pnl_pct, "0.0001")]]},
                {"range": gspread.utils.rowcol_to_a1(r, plval_col_idx), "values": [[fmt_dec(pnl_val, "0.01")]]},
            ]

            # Se esiste la colonna Delta, aggiorna anche la differenza di prezzo
            if delta_col_idx:
                row_updates.append({
                    "range": gspread.utils.rowcol_to_a1(r, delta_col_idx),
                    "values": [[fmt_dec(delta_price, "0.01")]]
                })

            updates += row_updates
            continue

        # Qui: colpito TP/SL -> CHIUSURA
        pnl_pct, pnl_val = pnl_values(side, entry, close_price, qty)
        eq_prev = last_equity(ws_trade, equity_col_idx)
        eq_new  = eq_prev + pnl_val

        if close_col_idx:
            updates.append({"range": gspread.utils.rowcol_to_a1(r, close_col_idx),
                            "values": [[fmt_dec(close_price)]]})
        updates.append({"range": gspread.utils.rowcol_to_a1(r, stato_col_idx),
                        "values": [["CHIUSO"]]})
        if note_col_idx:
            updates.append({"range": gspread.utils.rowcol_to_a1(r, note_col_idx),
                            "values": [[hit]]})
        updates += [
            {"range": gspread.utils.rowcol_to_a1(r, plpct_col_idx), "values": [[fmt_dec(pnl_pct, "0.0001")]]},
            {"range": gspread.utils.rowcol_to_a1(r, plval_col_idx), "values": [[fmt_dec(pnl_val, "0.01")]]},
            {"range": gspread.utils.rowcol_to_a1(r, equity_col_idx), "values": [[fmt_dec(eq_new, "0.01")]]},
        ]

        notify(
            f"⛏️ BOT ORO | {SYMBOL}\n"
            f"Trade chiuso: {hit}\n"
            f"Entry: {fmt_dec(entry)}  Close: {fmt_dec(close_price)}\n"
            f"P&L: {fmt_dec(pnl_val,'0.01')} USD  ({fmt_dec(pnl_pct,'0.0001')}%)\n"
            f"Equity: {fmt_dec(eq_new,'0.01')} · {TIMEZONE}"
        )

    if updates:
        ws_trade.spreadsheet.values_batch_update({"valueInputOption": "USER_ENTERED", "data": updates})


def open_new_trade(ws_trade, ws_log, trade_id: str, side="LONG", qty=Decimal("1"), H=None, col_ping=None):
    if H is None:
        header = get_header(ws_trade); H = build_header_map(header)
    need=["data/ora","id trade","lato","stato","prezzo ingresso","qty","sl %","tp1 %","tp2 %","ultimo ping"]
    for k in need:
        if k not in H: raise RuntimeError(f"Colonna '{k}' mancante per aprire un trade.")
    price=get_last_price(binance_client())
    if price==0: raise RuntimeError("Prezzo non disponibile per apertura trade.")

    row=[""]*max(H.values())
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
    if col_ping is None:
        col_ping = find_col_by_header(ws_trade, "Ultimo ping")
    row[col_ping-1] = f"{now_local_str()} - {fmt_dec(price)}"

    ws_trade.append_row(row, value_input_option="USER_ENTERED")
    msg = (f"🚀 BOT ORO | {SYMBOL}\n"
           f"Trade APERTO: {trade_id}\n"
           f"Side: {side}  Entry: {fmt_dec(price)}\n"
           f"TP1 {fmt_dec(price*(1+TP1_PCT))} · TP2 {fmt_dec(price*(1+TP2_PCT))} · SL {fmt_dec(price*(1-SL_PCT))}\n"
           f"{TIMEZONE}")
    log(ws_log,"INFO",f"Aperto trade {trade_id} @ {fmt_dec(price)}")
    notify(msg)

def ensure_min_open_trades(ws_trade, ws_log, H, col_ping,
                           min_trades=5, side="LONG", qty=Decimal("1")):
    """
    Conta quanti trade sono APERTI e, se < min_trades, ne apre subito di nuovi
    usando open_new_trade(...) con prezzo corrente.
    """
    try:
        # Legge solo la colonna STATO (meno quote)
        stato_col = ws_trade.col_values(H["stato"])  # include header
        n_open = 0
        if len(stato_col) > 1:
            n_open = sum(1 for s in stato_col[1:] if (s or "").strip().upper() == "APERTO")

        to_open = max(0, min_trades - n_open)
        if to_open <= 0:
            return

        # Apre i mancanti
        for i in range(to_open):
            trade_id = f"{SYMBOL}-{int(time.time())}-AUTO{i}"
            try:
                open_new_trade(ws_trade, ws_log,
                               trade_id=trade_id,
                               side=side,
                               qty=qty,
                               H=H,
                               col_ping=col_ping)
                log(ws_log, "INFO",
                    f"Aperto trade automatico {trade_id} per mantenere minimo {min_trades}")
            except Exception as e:
                log(ws_log, "ERROR",
                    f"Apertura trade automatico fallita ({trade_id}): {e}")
                # continua comunque ad aprire gli altri
    except Exception as e:
        log(ws_log, "ERROR", f"ensure_min_open_trades error: {e}")

def main_loop():
    global _H_CACHE, _COL_PING_CACHE, _LAST_RECONCILE_TS
    ws_trade, ws_log = open_sheets()
    client = binance_client()
    log(ws_log,"INFO",f"Bot Oro avviato · Trade='{ws_trade.title}', Log='{ws_log.title}' · TZ={TIMEZONE}")

    # Header/mapping una sola volta
    header = get_header(ws_trade)
    _H_CACHE = build_header_map(header)
    _COL_PING_CACHE = find_col_by_header(ws_trade, "Ultimo ping")

    dump_headers_once(ws_trade, ws_log)  # solo se DEBUG_HEADERS=1

    # Riconcilia una volta all'avvio
    reconcile_and_notify_starts(ws_trade, ws_log, SYMBOL)
    _LAST_RECONCILE_TS = time.time()

    if AUTO_OPEN_ON_START:
        try:
            open_new_trade(ws_trade, ws_log, trade_id=f"{SYMBOL}-{int(time.time())}-A", side="LONG", H=_H_CACHE, col_ping=_COL_PING_CACHE)
        except Exception as e:
            log(ws_log,"ERROR",f"Apertura automatica fallita: {e}")

    while True:
    try:
        # Riconcilia SOLO ogni RECONCILE_MIN_SECONDS
        if time.time() - _LAST_RECONCILE_TS >= RECONCILE_MIN_SECONDS:
            reconcile_and_notify_starts(ws_trade, ws_log, SYMBOL)
            _LAST_RECONCILE_TS = time.time()

        # Aggiornamento leggero sulle righe (colonne minime)
        update_open_rows_light(ws_trade, ws_log, client, _H_CACHE, _COL_PING_CACHE)

        # Mantieni almeno MIN_OPEN_TRADES trade APERTI
        ensure_min_open_trades(
            ws_trade, ws_log,
            H=_H_CACHE,
            col_ping=_COL_PING_CACHE,
            min_trades=MIN_OPEN_TRADES,   # es. 5
            side=AUTO_TRADE_SIDE.upper(), # es. LONG
            qty=DEFAULT_QTY               # es. 1
        )

        # Heartbeat log (throttled)
        lastp = get_last_price(client)
        if lastp != 0 and should_log_heartbeat(lastp):
            log(ws_log, "INFO", f"Heartbeat OK - {fmt_dec(lastp)}")

    except Exception as e:
        log(ws_log, "ERROR", str(e))

    time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main_loop()
