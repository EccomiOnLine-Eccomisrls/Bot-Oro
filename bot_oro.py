import os, json, time, unicodedata, requests, re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException, BinanceRequestException
from twilio.rest import Client as TwilioClient

# ========= COSTANTI =========
BOT_VERSION = "oro-bot v1.6"

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
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "8"))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Rome")
AUTO_OPEN_ON_START = os.getenv("AUTO_OPEN_ON_START", "0") == "1"

# Auto-apertura continua per mantenere almeno N trade
MIN_OPEN_TRADES = int(os.getenv("MIN_OPEN_TRADES", "5"))
AUTO_TRADE_SIDE = os.getenv("AUTO_TRADE_SIDE", "LONG")
DEFAULT_QTY     = Decimal(os.getenv("DEFAULT_QTY", "1"))

# Debug / throttle log
DEBUG_HEADERS = os.getenv("DEBUG_HEADERS", "0") == "1"
HEARTBEAT_MIN_SECONDS = int(os.getenv("HEARTBEAT_MIN_SECONDS", "60"))
HEARTBEAT_PRICE_DELTA_BP = int(os.getenv("HEARTBEAT_PRICE_DELTA_BP", "2"))

# Riconciliazione meno frequente (per ridurre letture)
RECONCILE_MIN_SECONDS = int(os.getenv("RECONCILE_MIN_SECONDS", "180"))

# === Anti-clustering esistente ===
MIN_TRADE_GAP_SECONDS = int(os.getenv("MIN_TRADE_GAP_SECONDS", "180"))  # cooldown temporale
MIN_ENTRY_DISTANCE_BP = int(os.getenv("MIN_ENTRY_DISTANCE_BP", "12"))   # distanza minima da altri APERTI
GRID_STEP_BP          = int(os.getenv("GRID_STEP_BP", "15"))            # passo minimo vs ultimo aperto (0=off)

# === Nuove ENV anti rate-limit ===
PRICE_MIN_INTERVAL = int(os.getenv("PRICE_MIN_INTERVAL", "3"))
BANNED_FALLBACK_SLEEP = int(os.getenv("BANNED_FALLBACK_SLEEP", "30"))

# Stato interno
_LAST_HEADER_SIG = None
_LAST_HEARTBEAT_TS = 0
_LAST_HEARTBEAT_PRICE = None
_LAST_RECONCILE_TS = 0

# Throttle per log "nessuna chiusura"
_LAST_MISS_LOG_TS = 0
MISS_LOG_EVERY = 30  # secondi

# Cache header/mapping
_H_CACHE = None
_COL_PING_CACHE = None

# Stato per anti-clustering
_LAST_TRADE_TS = 0
_LAST_ENTRY_PRICE = None

# ====== Guard & cache Binance ======
_PRICE_CACHE = None
_PRICE_CACHE_TS = 0.0
_BINANCE_BANNED_UNTIL = 0.0   # epoch seconds


# ========= UTILS =========
def d(x) -> Decimal:
    if isinstance(x, Decimal): return x
    if x is None: return Decimal("0")
    s = str(x).strip()
    if not s: return Decimal("0")
    is_percent = False
    if s.endswith("%"):
        is_percent = True
        s = s[:-1].strip()
    s = s.replace(" ", "")
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        val = Decimal(s)
    except:
        return Decimal("0")
    if is_percent: val = val / Decimal("100")
    return val

def fmt_dec(x: Decimal, q="0.00001") -> str:
    return d(x).quantize(Decimal(q), rounding=ROUND_HALF_UP).normalize().to_eng_string()

def _zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIMEZONE)
    except Exception:
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
    global _LAST_HEADER_SIG
    if not DEBUG_HEADERS: return
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
    if not header: raise RuntimeError(f"La tab '{ws.title}' non ha intestazioni.")
    target = (header_name or "").strip().lower()
    for i, h in enumerate(header, start=1):
        if (h or "").strip().lower() == target:
            return i
    raise RuntimeError(f"Header '{header_name}' non trovato in '{ws.title}': {header}")


# ========= BINANCE =========
def binance_client():
    return BinanceClient(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)

def get_last_price(client) -> Decimal:
    """
    Lettura prezzo con:
    - cache & throttle (PRICE_MIN_INTERVAL)
    - ban guard su errori -1003 con pausa fino a _BINANCE_BANNED_UNTIL
    """
    global _PRICE_CACHE, _PRICE_CACHE_TS, _BINANCE_BANNED_UNTIL

    now_ts = time.time()

    # se in ban, non chiamare l'API
    if now_ts < _BINANCE_BANNED_UNTIL:
        return d(_PRICE_CACHE) if _PRICE_CACHE is not None else Decimal("0")

    # throttle
    if _PRICE_CACHE is not None and (now_ts - _PRICE_CACHE_TS) < PRICE_MIN_INTERVAL:
        return d(_PRICE_CACHE)

    try:
        p = client.get_symbol_ticker(symbol=SYMBOL)
        price = d(p["price"])
        if price != 0:
            _PRICE_CACHE = price
            _PRICE_CACHE_TS = now_ts
        return price
    except BinanceAPIException as e:
        msg = str(e)
        m = re.search(r"banned until (\d+)", msg)
        if m:
            try:
                ban_ms = int(m.group(1))
                _BINANCE_BANNED_UNTIL = max(_BINANCE_BANNED_UNTIL, ban_ms / 1000.0)
            except Exception:
                _BINANCE_BANNED_UNTIL = now_ts + 300
        elif "-1003" in msg or "Too much request weight" in msg:
            _BINANCE_BANNED_UNTIL = now_ts + 60

        print(f"[BINANCE] {msg}")
        return d(_PRICE_CACHE) if _PRICE_CACHE is not None else Decimal("0")
    except (BinanceRequestException, KeyError, TypeError) as e:
        print(f"[BINANCE] {e}")
        return d(_PRICE_CACHE) if _PRICE_CACHE is not None else Decimal("0")


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
        msgs = ws_log.col_values(3)
        if len(msgs) <= 1: return set()
        return set(msgs[-max_rows:])
    except Exception:
        return set()

def start_already_notified(log_msgs: set, trade_id: str) -> bool:
    if not trade_id: return False
    key = f"Aperto trade {trade_id}"
    for m in log_msgs:
        if trade_id in m and "Aperto trade" in m: return True
        if key in m: return True
    return False

def gen_trade_id(symbol: str, row_index: int) -> str:
    return f"{symbol}-{int(time.time())}-R{row_index}"

def reconcile_and_notify_starts(ws_trade, ws_log, symbol: str):
    header = get_header(ws_trade)
    H = build_header_map(header)

    need = ["data/ora", "id trade", "lato", "stato", "prezzo ingresso", "ultimo ping"]
    for k in need:
        if k not in H:
            raise RuntimeError(f"Manca colonna '{k}' nella tab '{ws_trade.title}'.")

    L_ID = H["id trade"]; L_STATO = H["stato"]; L_LATO = H["lato"]
    L_ENTRY = H["prezzo ingresso"]; L_CLOSE = H.get("prezzo chiusura")
    L_TP1 = H.get("tp1 %"); L_TP2 = H.get("tp2 %"); L_SL = H.get("sl %")

    rows = ws_trade.get_all_values()
    if len(rows) <= 1: return

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

        if has_close and stato in ("", "APERTO"):
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_STATO), "values": [["CHIUSO"]]})
            stato = "CHIUSO"

        if entry_str and stato == "":
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_STATO), "values": [["APERTO"]]})
            stato = "APERTO"

        if stato == "APERTO" and not trade_id:
            trade_id = gen_trade_id(symbol, r)
            updates.append({"range": gspread.utils.rowcol_to_a1(r, L_ID), "values": [[trade_id]]})

        if stato == "APERTO" and entry > 0 and not start_already_notified(log_msgs, trade_id):
            TP1 = d(row[L_TP1 - 1]) if L_TP1 and len(row) >= L_TP1 and (row[L_TP1 - 1] or "").strip() else TP1_PCT
            TP2 = d(row[L_TP2 - 1]) if L_TP2 and len(row) >= L_TP2 and (row[L_TP2 - 1] or "").strip() else TP2_PCT
            SL  = d(row[L_SL  - 1]) if L_SL  and len(row) >= L_SL  and (row[L_SL  - 1]  or "").strip() else SL_PCT

            msg = (
                f"BOT ORO | {symbol}\n"
                f"Aperto trade {trade_id}\n"
                f"Side: {side} - Entry: {fmt_dec(entry)}\n"
                f"TP1 {fmt_dec(entry*(1+TP1))} - TP2 {fmt_dec(entry*(1+TP2))} - SL {fmt_dec(entry*(1-SL))}\n"
                f"{TIMEZONE}"
            )
            notify(msg)
            log(ws_log, "INFO", f"Aperto trade {trade_id} @ {fmt_dec(entry)} (riconosciuto)")

    if updates:
        ws_trade.spreadsheet.values_batch_update({
            "valueInputOption": "USER_ENTERED",
            "data": updates
        })


# ========= CHIUSURA IMMEDIATA SE 'PREZZO CHIUSURA' ESISTE =========
def close_rows_marked(ws_trade, ws_log, H):
    """
    Chiude immediatamente tutte le righe con Stato=APERTO e 'Prezzo chiusura' valorizzato,
    calcolando P&L ed Equity post-trade. Non usa Binance.
    """
    try:
        stato_col = ws_trade.col_values(H["stato"])
        close_col = ws_trade.col_values(H["prezzo chiusura"]) if "prezzo chiusura" in H else []
        if len(stato_col) <= 1:
            return

        side_col  = ws_trade.col_values(H["lato"]) if "lato" in H else []
        entry_col = ws_trade.col_values(H["prezzo ingresso"])
        qty_col   = ws_trade.col_values(H["qty"]) if "qty" in H else []

        plpct_col_idx  = H["p&l %"]
        plval_col_idx  = H["p&l valore"]
        equity_col_idx = H["equity post-trade"]
        note_col_idx   = H.get("note")
        stato_col_idx  = H["stato"]

        updates = []
        closed_count = 0

        for i in range(1, len(stato_col)):
            stato = (stato_col[i] or "").strip().upper()
            close_str = (close_col[i] if i < len(close_col) else "").strip()
            if stato != "APERTO" or not close_str:
                continue

            r = i + 1  # riga foglio (1-based)
            side  = (side_col[i] if i < len(side_col) else "LONG").strip().upper()
            entry = d(entry_col[i]) if i < len(entry_col) and entry_col[i] else Decimal("0")
            qty   = d(qty_col[i])   if i < len(qty_col)   and qty_col[i]   else Decimal("1")
            close = d(close_str)

            if entry == 0 or close == 0:
                continue

            pnl_pct, pnl_val = pnl_values(side, entry, close, qty)
            eq_prev = last_equity(ws_trade, equity_col_idx)
            eq_new  = eq_prev + pnl_val

            updates += [
                {"range": gspread.utils.rowcol_to_a1(r, stato_col_idx), "values": [["CHIUSO"]]},
                {"range": gspread.utils.rowcol_to_a1(r, plpct_col_idx), "values": [[fmt_dec(pnl_pct, "0.0001")]]},
                {"range": gspread.utils.rowcol_to_a1(r, plval_col_idx), "values": [[fmt_dec(pnl_val, "0.01")]]},
                {"range": gspread.utils.rowcol_to_a1(r, equity_col_idx), "values": [[fmt_dec(eq_new, "0.01")]]},
            ]
            if note_col_idx:
                updates.append({"range": gspread.utils.rowcol_to_a1(r, note_col_idx), "values": [["RESET"]]})

            log(ws_log, "INFO",
                f"Close MANUAL r{r} - side={side} entry={fmt_dec(entry)} close={fmt_dec(close)} "
                f"pnl%={fmt_dec(pnl_pct,'0.0001')} pnl=${fmt_dec(pnl_val,'0.01')} eq->{fmt_dec(eq_new,'0.01')}")
            notify(
                f"BOT ORO | {SYMBOL}\n"
                f"Chiusura manuale\n"
                f"Entry: {fmt_dec(entry)}  Close: {fmt_dec(close)}\n"
                f"P&L: {fmt_dec(pnl_val,'0.01')} USD  ({fmt_dec(pnl_pct,'0.0001')}%)\n"
                f"Equity: {fmt_dec(eq_new,'0.01')} - {TIMEZONE}"
            )
            closed_count += 1

        if updates:
            ws_trade.spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": updates}
            )
            log(ws_log, "INFO", f"Chiusure manuali processate: {closed_count}")
    except Exception as e:
        log(ws_log, "ERROR", f"close_rows_marked error: {e}")


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
    global _LAST_HEARTBEAT_TS, _LAST_HEARTBEAT_PRICE
    now_ts = time.time()
    if _LAST_HEARTBEAT_TS == 0 or _LAST_HEARTBEAT_PRICE is None:
        _LAST_HEARTBEAT_TS = now_ts; _LAST_HEARTBEAT_PRICE = price
        return True
    if now_ts - _LAST_HEARTBEAT_TS >= HEARTBEAT_MIN_SECONDS:
        _LAST_HEARTBEAT_TS = now_ts; _LAST_HEARTBEAT_PRICE = price
        return True
    try:
        if price > 0 and _LAST_HEARTBEAT_PRICE > 0:
            move_bp = abs((price - _LAST_HEARTBEAT_PRICE) / _LAST_HEARTBEAT_PRICE) * 10000
            if move_bp >= HEARTBEAT_PRICE_DELTA_BP:
                _LAST_HEARTBEAT_TS = now_ts; _LAST_HEARTBEAT_PRICE = price
                return True
    except Exception:
        pass
    return False

def update_open_rows_light(ws_trade, ws_log, client, H, col_ping, lastp=None):
    nowloc = now_local_str()
    if lastp is None:
        lastp = get_last_price(client)
    if lastp == 0:
        log(ws_log, "WARN", "Prezzo 0 da Binance")
        return

    stato_col = ws_trade.col_values(H["stato"])
    if len(stato_col) <= 1:
        try:
            ws_trade.update_cell(2, col_ping, f"{nowloc} - {fmt_dec(lastp)}")
        except Exception as e:
            log(ws_log, "ERROR", f"[DEBUG] update_cell K2 fallito: {e}")
        return

    n_rows = len(stato_col) - 1
    start_row = 2
    end_row = start_row + n_rows - 1

    # Ping sintetico
    log(ws_log, "DEBUG", f"Ping @ {fmt_dec(lastp)} - righe={n_rows} - stato_col_len={len(stato_col)}")

    updates = []
    for r in range(start_row, end_row + 1):
        updates.append({
            "range": gspread.utils.rowcol_to_a1(r, col_ping),
            "values": [[f"{nowloc} - {fmt_dec(lastp)}"]],
        })

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
    delta_col_idx  = H.get("delta")

    global _LAST_MISS_LOG_TS

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
            pnl_pct, pnl_val = pnl_values(side, entry, lastp, qty)
            delta_price = (lastp - entry) if side == "LONG" else (entry - lastp)

            row_updates = [
                {"range": gspread.utils.rowcol_to_a1(r, plpct_col_idx), "values": [[fmt_dec(pnl_pct, "0.0001")]]},
                {"range": gspread.utils.rowcol_to_a1(r, plval_col_idx), "values": [[fmt_dec(pnl_val, "0.01")]]},
            ]
            if delta_col_idx:
                row_updates.append({
                    "range": gspread.utils.rowcol_to_a1(r, delta_col_idx),
                    "values": [[fmt_dec(delta_price, "0.01")]]
                })
            updates += row_updates

            now_ts = time.time()
            if now_ts - _LAST_MISS_LOG_TS >= MISS_LOG_EVERY:
                _LAST_MISS_LOG_TS = now_ts
                log(ws_log, "DEBUG",
                    f"Nessuna chiusura r{r}: side={side} entry={fmt_dec(entry)} last={fmt_dec(lastp)} "
                    f"tp1={fmt_dec(tp1)} tp2={fmt_dec(tp2)} sl={fmt_dec(sl)} qty={fmt_dec(qty,'0.00000001')}")
            continue

        # Chiusura automatica su TP/SL
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

        log(ws_log, "INFO",
            f"Close {hit} r{r} - side={side} entry={fmt_dec(entry)} close={fmt_dec(close_price)} "
            f"pnl%={fmt_dec(pnl_pct,'0.0001')} pnl=${fmt_dec(pnl_val,'0.01')} eq->{fmt_dec(eq_new,'0.01')}")

        notify(
            f"BOT ORO | {SYMBOL}\n"
            f"Trade chiuso: {hit}\n"
            f"Entry: {fmt_dec(entry)}  Close: {fmt_dec(close_price)}\n"
            f"P&L: {fmt_dec(pnl_val,'0.01')} USD  ({fmt_dec(pnl_pct,'0.0001')}%)\n"
            f"Equity: {fmt_dec(eq_new,'0.01')} - {TIMEZONE}"
        )

    if updates:
        ws_trade.spreadsheet.values_batch_update({"valueInputOption": "USER_ENTERED", "data": updates})


def open_new_trade(ws_trade, ws_log, trade_id: str, side="LONG", qty=Decimal("1"),
                   H=None, col_ping=None, entry_price: Decimal | None = None) -> Decimal:
    """
    Apre una riga trade e ritorna il prezzo di ingresso usato.
    Se entry_price è passato, usa quello; altrimenti preleva da Binance.
    """
    if H is None:
        header = get_header(ws_trade); H = build_header_map(header)
    need=["data/ora","id trade","lato","stato","prezzo ingresso","qty","sl %","tp1 %","tp2 %","ultimo ping"]
    for k in need:
        if k not in H: raise RuntimeError(f"Colonna '{k}' mancante per aprire un trade.")

    if entry_price is None:
        price = get_last_price(binance_client())
    else:
        price = d(entry_price)

    if price == 0:
        raise RuntimeError("Prezzo non disponibile per apertura trade.")

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
    msg = (f"BOT ORO | {SYMBOL}\n"
           f"Trade APERTO: {trade_id}\n"
           f"Side: {side}  Entry: {fmt_dec(price)}\n"
           f"TP1 {fmt_dec(price*(1+TP1_PCT))} - TP2 {fmt_dec(price*(1+TP2_PCT))} - SL {fmt_dec(price*(1-SL_PCT))}\n"
           f"{TIMEZONE}")
    log(ws_log,"INFO",f"Aperto trade {trade_id} @ {fmt_dec(price)}")
    notify(msg)
    return price


def ensure_min_open_trades(ws_trade, ws_log, client, H, col_ping,
                           min_trades=5, side="LONG", qty=Decimal("1"),
                           last_price: Decimal | None = None):
    """
    Mantiene almeno min_trades APERTI, applicando:
    - Cooldown temporale MIN_TRADE_GAP_SECONDS
    - Distanza minima dai trade APERTI in bps (MIN_ENTRY_DISTANCE_BP)
    - Grid step rispetto all'ultimo aperto (GRID_STEP_BP)
    Usa 'last_price' se fornito dal loop; altrimenti legge da Binance.
    """
    global _LAST_TRADE_TS, _LAST_ENTRY_PRICE
    try:
        stato_col = ws_trade.col_values(H["stato"])  # include header
        n_open = 0
        if len(stato_col) > 1:
            n_open = sum(1 for s in stato_col[1:] if (s or "").strip().upper() == "APERTO")

        to_open = max(0, min_trades - n_open)
        if to_open <= 0:
            return

        now_ts = time.time()
        if now_ts - _LAST_TRADE_TS < MIN_TRADE_GAP_SECONDS:
            log(ws_log, "DEBUG", f"Skip open: cooldown attivo {int(now_ts - _LAST_TRADE_TS)}s < {MIN_TRADE_GAP_SECONDS}s")
            return

        lastp = d(last_price) if last_price else get_last_price(client)
        if lastp == 0:
            log(ws_log, "WARN", "Prezzo 0 da Binance (skip open)")
            return

        # Distanza minima da altri APERTI
        open_entries = []
        entry_col = ws_trade.col_values(H["prezzo ingresso"])
        if len(stato_col) > 1:
            for i in range(1, len(stato_col)):
                if (stato_col[i] or "").strip().upper() == "APERTO":
                    v = (entry_col[i] if i < len(entry_col) else "").strip()
                    if v:
                        e = d(v)
                        if e > 0:
                            open_entries.append(e)

        if MIN_ENTRY_DISTANCE_BP > 0 and open_entries:
            too_close = any(abs((lastp - e) / e) * 10000 < MIN_ENTRY_DISTANCE_BP for e in open_entries)
            if too_close:
                log(ws_log, "DEBUG",
                    f"Skip open: distanza < {MIN_ENTRY_DISTANCE_BP}bp da un entry aperto (last={fmt_dec(lastp)})")
                return

        # Grid step rispetto all'ultimo aperto
        if GRID_STEP_BP > 0 and _LAST_ENTRY_PRICE:
            move_bp = abs((lastp - _LAST_ENTRY_PRICE) / _LAST_ENTRY_PRICE) * 10000
            if move_bp < GRID_STEP_BP:
                log(ws_log, "DEBUG",
                    f"Skip open: grid step {move_bp:.1f}bp < {GRID_STEP_BP}bp (ultimo={fmt_dec(_LAST_ENTRY_PRICE)} last={fmt_dec(lastp)})")
                return

        # Apri i mancanti (di fatto passerà 1/giro grazie ai filtri)
        for i in range(to_open):
            trade_id = f"{SYMBOL}-{int(time.time())}-AUTO{i}"
            try:
                used_price = open_new_trade(ws_trade, ws_log,
                                            trade_id=trade_id,
                                            side=side,
                                            qty=qty,
                                            H=H,
                                            col_ping=col_ping,
                                            entry_price=lastp)
                _LAST_TRADE_TS = now_ts
                _LAST_ENTRY_PRICE = used_price
                log(ws_log, "INFO",
                    f"Aperto trade automatico {trade_id} (min={min_trades}) - price={fmt_dec(used_price)}")
                break
            except Exception as e:
                log(ws_log, "ERROR", f"Apertura trade automatico fallita ({trade_id}): {e}")
    except Exception as e:
        log(ws_log, "ERROR", f"ensure_min_open_trades error: {e}")


def main_loop():
    global _H_CACHE, _COL_PING_CACHE, _LAST_RECONCILE_TS, _BINANCE_BANNED_UNTIL

    ws_trade, ws_log = open_sheets()
    client = binance_client()

    # Startup log con versione e parametri principali
    log(ws_log, "INFO",
        f"{BOT_VERSION} - SYMBOL={SYMBOL} - TZ={TIMEZONE} - "
        f"TABS=({ws_trade.title},{ws_log.title}) - "
        f"TP1={fmt_dec(TP1_PCT,'0.0000001')} TP2={fmt_dec(TP2_PCT,'0.0000001')} SL={fmt_dec(SL_PCT,'0.0000001')} - "
        f"MIN_OPEN_TRADES={MIN_OPEN_TRADES} POLL={POLL_SECONDS}s - "
        f"COOLDOWN={MIN_TRADE_GAP_SECONDS}s DIST_BP={MIN_ENTRY_DISTANCE_BP} GRID_BP={GRID_STEP_BP}")

    header = get_header(ws_trade)
    _H_CACHE = build_header_map(header)
    _COL_PING_CACHE = find_col_by_header(ws_trade, "Ultimo ping")

    dump_headers_once(ws_trade, ws_log)

    reconcile_and_notify_starts(ws_trade, ws_log, SYMBOL)
    _LAST_RECONCILE_TS = time.time()

    if AUTO_OPEN_ON_START:
        try:
            open_new_trade(ws_trade, ws_log,
                           trade_id=f"{SYMBOL}-{int(time.time())}-A",
                           side="LONG", H=_H_CACHE, col_ping=_COL_PING_CACHE)
        except Exception as e:
            log(ws_log, "ERROR", f"Apertura automatica fallita: {e}")

    while True:
        try:
            # Se in ban, pausa gentile e riprova
            if time.time() < _BINANCE_BANNED_UNTIL:
                ts = datetime.fromtimestamp(_BINANCE_BANNED_UNTIL).strftime('%Y-%m-%d %H:%M:%S')
                log(ws_log, "WARN", f"Binance bannato fino a {ts}. Sleep {BANNED_FALLBACK_SLEEP}s")
                time.sleep(BANNED_FALLBACK_SLEEP)
                continue

            # 1) Chiudi SUBITO le righe con 'Prezzo chiusura' compilato (no Binance)
            close_rows_marked(ws_trade, ws_log, _H_CACHE)

            # 2) Una sola lettura prezzo per ciclo (throttlata e con cache)
            lastp = get_last_price(client)

            # 3) Riconciliazione saltuaria
            if time.time() - _LAST_RECONCILE_TS >= RECONCILE_MIN_SECONDS:
                reconcile_and_notify_starts(ws_trade, ws_log, SYMBOL)
                _LAST_RECONCILE_TS = time.time()

            # 4) Aggiorna P&L / ping
            update_open_rows_light(ws_trade, ws_log, client, _H_CACHE, _COL_PING_CACHE, lastp=lastp)

            # 5) Mantieni minimo aperti (con anti-clustering)
            ensure_min_open_trades(
                ws_trade, ws_log, client,
                H=_H_CACHE,
                col_ping=_COL_PING_CACHE,
                min_trades=MIN_OPEN_TRADES,
                side=AUTO_TRADE_SIDE.upper(),
                qty=DEFAULT_QTY,
                last_price=lastp
            )

            # 6) Heartbeat
            if lastp != 0 and should_log_heartbeat(lastp):
                log(ws_log, "INFO", f"Heartbeat OK - {fmt_dec(lastp)}")

        except Exception as e:
            log(ws_log, "ERROR", str(e))

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main_loop()
