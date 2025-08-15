# bot_oro.py  — v2 (heartbeat + trade simulati con TP1/TP2 separati)
import os, json, time
from dataclasses import dataclass
from datetime import datetime, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client
from twilio.rest import Client as TwilioClient

from sheet_logger import SheetLogger

# ============ ENV ============
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL             = os.getenv("BINANCE_SYMBOL", "PAXGUSDT")

TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
DESTINATION_NUMBER     = os.getenv("TWILIO_TO", "whatsapp:+393205616977")

SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

# Parametri strategia (modificabili da ENV)
ENTRY_DROP   = float(os.getenv("ENTRY_DROP", 0.005))   # entra se -0.5% dal massimo recente
SL_PCT       = float(os.getenv("STOP_LOSS", 0.005))    # 0.5%
TP1_PCT      = float(os.getenv("TAKE_PROFIT1", 0.004)) # 0.4%
TP2_PCT      = float(os.getenv("TAKE_PROFIT2", 0.010)) # 1.0%
RISK_PCT     = float(os.getenv("RISK_PCT", 0.005))     # 0.5% equity per trade
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "15"))    # minuti
PING_EVERY   = int(os.getenv("PING_EVERY_SEC", "60"))  # sec
PRICE_EVERY  = int(os.getenv("PRICE_EVERY_SEC", "5"))  # sec
REPORT_EVERY = int(os.getenv("REPORT_EVERY_SEC", "3600"))

EQUITY_START = float(os.getenv("EQUITY_START", 10000.0))

# ============ Connessioni ============
binance_client = Client(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
twilio_client  = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
sheet = SheetLogger()

def _gc_client():
    info = json.loads(GOOGLE_CREDENTIALS)
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
    return gspread.authorize(creds)

def invia_msg(msg: str):
    try:
        twilio_client.messages.create(body=msg, from_=TWILIO_WHATSAPP_NUMBER, to=DESTINATION_NUMBER)
    except Exception as e:
        print("[WHATSAPP][ERR]", e)

def get_price() -> float:
    return float(binance_client.get_symbol_ticker(symbol=SYMBOL)["price"])

# ============ Modello posizione ============
@dataclass
class Position:
    trade_id: str
    side: str       # "LONG"
    entry: float
    qty: float
    tp_pct: float   # TP specifico per questa “metà” (TP1 o TP2)
    sl_pct: float   # SL
    open_ts: float

equity = EQUITY_START
open_positions: list[Position] = []
cooldown_until = 0.0
anchor_high = None  # massimo da cui misuriamo il drop per l'entry

def position_size(entry: float, sl_pct: float, risk_pct: float) -> float:
    """Qty tale che la perdita a SL sia = equity * risk_pct"""
    global equity
    risk_amount = equity * risk_pct
    per_unit_loss = entry * sl_pct
    if per_unit_loss <= 0:
        return 0.0
    return max(risk_amount / per_unit_loss, 0.0)

def log_open_pair(entry: float):
    """Apre due 'sotto-trade' (50% + 50%) per gestire il TP parziale come due righe distinte."""
    global open_positions
    base_qty = position_size(entry, SL_PCT, RISK_PCT)
    qty_half = round(base_qty / 2, 6)
    ts = int(time.time())

    # Trade A (TP1)
    trade_id_a = f"{SYMBOL}-{ts}-A"
    sheet.log_open(trade_id=trade_id_a, side="LONG", entry_price=entry, qty=qty_half,
                   sl_pct=SL_PCT, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT, strategy="v1", note="TP1")
    open_positions.append(Position(trade_id_a, "LONG", entry, qty_half, TP1_PCT, SL_PCT, time.time()))

    # Trade B (TP2)
    trade_id_b = f"{SYMBOL}-{ts}-B"
    sheet.log_open(trade_id=trade_id_b, side="LONG", entry_price=entry, qty=qty_half,
                   sl_pct=SL_PCT, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT, strategy="v1", note="TP2")
    open_positions.append(Position(trade_id_b, "LONG", entry, qty_half, TP2_PCT, SL_PCT, time.time()))

    invia_msg(f"🟢 APERTI {SYMBOL}\nEntry {entry:.2f}\nQty tot {qty_half*2:.6f}\nSL {SL_PCT*100:.2f}%  TP1 {TP1_PCT*100:.2f}%  TP2 {TP2_PCT*100:.2f}%")

def close_position(p: Position, close_price: float, reason: str):
    """Chiude una posizione singola e logga P&L su Trade."""
    global equity
    direction = 1  # LONG
    pnl_value = (close_price - p.entry) * p.qty * direction
    pnl_pct   = ((close_price - p.entry) / p.entry) * 100.0 * direction
    equity   += pnl_value
    sheet.log_close(trade_id=p.trade_id, close_price=close_price, close_type=reason,
                    pnl_pct=round(pnl_pct, 4), pnl_value=round(pnl_value, 2),
                    equity_after=round(equity, 2), note=reason)
    invia_msg(f"⚪️ CHIUSO {p.trade_id} {reason}\nEntry {p.entry:.2f} → Close {close_price:.2f}\nP&L {pnl_pct:.3f}%  ({pnl_value:.2f})\nEquity {equity:.2f}")

def maybe_open(price: float):
    """Logica d’ingresso: entra se il prezzo è sceso di ENTRY_DROP dal massimo recente."""
    global anchor_high
    if anchor_high is None:
        anchor_high = price
        return
    # aggiorna il massimo
    if price > anchor_high:
        anchor_high = price
        return
    # condizione di ingresso
    trigger = anchor_high * (1.0 - ENTRY_DROP)
    if price <= trigger:
        log_open_pair(price)
        # reset anchor così non rientra subito; verrà settata dopo le chiusure
        return True
    return False

def manage_open_positions(price: float):
    """Controlla SL e TP di ogni 'sotto-trade' e chiude quando necessario."""
    global open_positions
    to_close = []
    for p in open_positions:
        sl_price = p.entry * (1.0 - p.sl_pct)
        tp_price = p.entry * (1.0 + p.tp_pct)
        if price <= sl_price:
            to_close.append((p, "SL"))
        elif price >= tp_price:
            to_close.append((p, "TP"))
    # chiudi fuori dal loop
    for p, reason in to_close:
        close_position(p, price, reason)
        open_positions = [x for x in open_positions if x.trade_id != p.trade_id]

def main():
    global cooldown_until, anchor_high, open_positions

    invia_msg("🤖 Bot Oro avviato — modalità simulazione con TP1/TP2 separati.")
    last_ping = 0.0
    last_px_ts = 0.0
    last_report = 0.0

    while True:
        try:
            now = time.time()

            # Heartbeat ogni PING_EVERY
            if now - last_ping >= PING_EVERY:
                px = get_price()
                sheet.heartbeat(price=px, msg="loop ok")
                print(f"[HEARTBEAT] {datetime.now()}  {SYMBOL}={px}")
                last_ping = now

            # Lettura prezzo ogni PRICE_EVERY (riduce peso su API)
            if now - last_px_ts >= PRICE_EVERY:
                price = get_price()
                last_px_ts = now

                # Se in cooldown, aspetta
                if now < cooldown_until:
                    pass
                else:
                    # Se non abbiamo posizioni aperte → valuta ingresso
                    if not open_positions:
                        if maybe_open(price):
                            # appena entrato: imposta un'ancora alta molto bassa per non rientrare finché non chiudiamo
                            anchor_high = None
                    else:
                        # Gestisci posizioni aperte
                        manage_open_positions(price)

                    # Se abbiamo chiuso tutto → imposta cooldown e resetta ancora
                    if not open_positions and anchor_high is None:
                        cooldown_until = now + COOLDOWN_MIN * 60
                        anchor_high = price  # riparti da qui
                        invia_msg(f"⏸ Cooldown {COOLDOWN_MIN} min — equity {equity:.2f}")

            # Report periodico
            if now - last_report >= REPORT_EVERY:
                # Report “leggero” via WhatsApp
                invia_msg(f"📊 Equity attuale: {equity:.2f} — posizioni aperte: {len(open_positions)}")
                last_report = now

        except Exception as e:
            print("[LOOP][ERR]", e)

        time.sleep(1)

if __name__ == "__main__":
    main()
