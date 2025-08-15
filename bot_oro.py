# bot_oro.py — v3.1 (Solo WebSocket, nessuna chiamata REST all'avvio)
import os, time, threading
from dataclasses import dataclass
from datetime import datetime
from binance import ThreadedWebsocketManager
from twilio.rest import Client as TwilioClient
from sheet_logger import SheetLogger

# ===== ENV =====
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY") or ""   # per stream pubblici non serve
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET") or ""
SYMBOL             = os.getenv("BINANCE_SYMBOL", "PAXGUSDT").upper()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
DESTINATION_NUMBER     = os.getenv("TWILIO_TO", "whatsapp:+393205616977")

# Strategia
ENTRY_DROP   = float(os.getenv("ENTRY_DROP", 0.005))
SL_PCT       = float(os.getenv("STOP_LOSS", 0.005))
TP1_PCT      = float(os.getenv("TAKE_PROFIT1", 0.004))
TP2_PCT      = float(os.getenv("TAKE_PROFIT2", 0.010))
RISK_PCT     = float(os.getenv("RISK_PCT", 0.005))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "15"))
PING_EVERY   = int(os.getenv("PING_EVERY_SEC", "60"))
REPORT_EVERY = int(os.getenv("REPORT_EVERY_SEC", "3600"))
EQUITY_START = float(os.getenv("EQUITY_START", 10000.0))

# ===== Servizi =====
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
sheet         = SheetLogger()

def invia_msg(msg: str):
    try:
        twilio_client.messages.create(body=msg, from_=TWILIO_WHATSAPP_NUMBER, to=DESTINATION_NUMBER)
    except Exception as e:
        print("[WHATSAPP][ERR]", e)

# ===== WebSocket Ticker =====
latest_price = None
latest_ts    = 0.0
ws_lock      = threading.Lock()
twm          = None

def _on_msg(msg):
    """Handler WS: aggiorna latest_price senza chiamare REST."""
    global latest_price, latest_ts
    try:
        # Formato python-binance: msg['c'] = last price (stringa)
        p = None
        if isinstance(msg, dict):
            if 'c' in msg:           # format standard
                p = msg['c']
            elif 'data' in msg and 'c' in msg['data']:  # a volte incapsulato
                p = msg['data']['c']
        if p is not None:
            with ws_lock:
                latest_price = float(p)
                latest_ts = time.time()
    except Exception as e:
        print("[WS][PARSE][ERR]", e)

def ws_start():
    """Avvia solo streaming pubblico; niente REST."""
    global twm
    twm = ThreadedWebsocketManager(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
    twm.start()
    twm.start_symbol_ticker_socket(callback=_on_msg, symbol=SYMBOL)

def ws_stop():
    global twm
    if twm:
        twm.stop()
        twm = None

def get_price_ws(timeout_stale=30):
    """Ritorna ultimo prezzo WS se non più vecchio di timeout_stale secondi."""
    now = time.time()
    with ws_lock:
        lp, ts = latest_price, latest_ts
    if lp is not None and (now - ts) <= timeout_stale:
        return lp
    return None

# ===== Modello posizione & logica =====
@dataclass
class Position:
    trade_id: str
    side: str
    entry: float
    qty: float
    tp_pct: float
    sl_pct: float
    open_ts: float

equity = EQUITY_START
open_positions: list[Position] = []
cooldown_until = 0.0
anchor_high = None

def position_size(entry: float, sl_pct: float, risk_pct: float) -> float:
    global equity
    risk_amount = equity * risk_pct
    per_unit_loss = entry * sl_pct
    if per_unit_loss <= 0:
        return 0.0
    return max(risk_amount / per_unit_loss, 0.0)

def log_open_pair(entry: float):
    global open_positions
    base_qty = position_size(entry, SL_PCT, RISK_PCT)
    if base_qty <= 0:
        return
    qty_half = round(base_qty / 2, 6)
    ts = int(time.time())

    trade_id_a = f"{SYMBOL}-{ts}-A"
    sheet.log_open(trade_id=trade_id_a, side="LONG", entry_price=entry, qty=qty_half,
                   sl_pct=SL_PCT, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT, strategy="v1", note="TP1")
    open_positions.append(Position(trade_id_a, "LONG", entry, qty_half, TP1_PCT, SL_PCT, time.time()))

    trade_id_b = f"{SYMBOL}-{ts}-B"
    sheet.log_open(trade_id=trade_id_b, side="LONG", entry_price=entry, qty=qty_half,
                   sl_pct=SL_PCT, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT, strategy="v1", note="TP2")
    open_positions.append(Position(trade_id_b, "LONG", entry, qty_half, TP2_PCT, SL_PCT, time.time()))

    invia_msg(f"🟢 APERTI {SYMBOL}\nEntry {entry:.2f}\nQty tot {qty_half*2:.6f}\nSL {SL_PCT*100:.2f}%  TP1 {TP1_PCT*100:.2f}%  TP2 {TP2_PCT*100:.2f}%")

def close_position(p: Position, close_price: float, reason: str):
    global equity, open_positions
    pnl_value = (close_price - p.entry) * p.qty
    pnl_pct   = ((close_price - p.entry) / p.entry) * 100.0
    equity   += pnl_value
    sheet.log_close(trade_id=p.trade_id, close_price=close_price, close_type=reason,
                    pnl_pct=round(pnl_pct, 4), pnl_value=round(pnl_value, 2),
                    equity_after=round(equity, 2), note=reason)
    invia_msg(f"⚪️ CHIUSO {p.trade_id} {reason}\nEntry {p.entry:.2f} → Close {close_price:.2f}\nP&L {pnl_pct:.3f}%  ({pnl_value:.2f})\nEquity {equity:.2f}")
    open_positions = [x for x in open_positions if x.trade_id != p.trade_id]

def maybe_open(price: float):
    global anchor_high
    if anchor_high is None:
        anchor_high = price
        return False
    if price > anchor_high:
        anchor_high = price
        return False
    trigger = anchor_high * (1.0 - ENTRY_DROP)
    if price <= trigger:
        log_open_pair(price)
        anchor_high = None
        return True
    return False

def manage_open_positions(price: float):
    to_close = []
    for p in open_positions:
        sl_price = p.entry * (1.0 - p.sl_pct)
        tp_price = p.entry * (1.0 + p.tp_pct)
        if price <= sl_price:
            to_close.append((p, "SL"))
        elif price >= tp_price:
            to_close.append((p, "TP"))
    for p, reason in to_close:
        close_position(p, price, reason)

# ===== MAIN =====
def main():
    global cooldown_until, anchor_high
    invia_msg("🤖 Bot Oro (WS only) avviato — nessuna chiamata REST.")
    ws_start()

    last_ping = 0.0
    last_report = 0.0

    try:
        while True:
            now = time.time()

            # Heartbeat con ultimo prezzo WS disponibile
            if now - last_ping >= PING_EVERY:
                px = get_price_ws()
                if px is not None:
                    sheet.heartbeat(price=px, msg="loop ok (ws-only)")
                    print(f"[HEARTBEAT] {datetime.now()}  {SYMBOL}={px}")
                else:
                    print("[HEARTBEAT] prezzo non disponibile (in attesa dati WS)")
                last_ping = now

            # Prezzo corrente da WS
            px = get_price_ws()
            if px is None:
                time.sleep(0.5)  # aspetta lo stream, niente REST
                continue

            # Cooldown / Operatività
            if now < cooldown_until:
                pass
            else:
                if not open_positions:
                    if maybe_open(px):
                        pass
                else:
                    manage_open_positions(px)

                if not open_positions and anchor_high is None:
                    cooldown_until = now + COOLDOWN_MIN * 60
                    anchor_high = px
                    invia_msg(f"⏸ Cooldown {COOLDOWN_MIN} min — equity {equity:.2f}")

            # Report leggero
            if now - last_report >= REPORT_EVERY:
                invia_msg(f"📊 Equity attuale: {equity:.2f} — posizioni aperte: {len(open_positions)}")
                last_report = now

            time.sleep(0.2)
    finally:
        ws_stop()

if __name__ == "__main__":
    main()
