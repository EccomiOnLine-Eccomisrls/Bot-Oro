# bot_oro.py
# Bot Oro – v3 (WS Binance + Google Sheet + WhatsApp Cloud API)
# - WebSocket puro su Binance (niente REST quota)
# - Log su Google Sheets (SheetLogger)
# - Alert via WhatsApp Cloud API (wa_meta.py) con fallback interno
# - Strategia mock per test: apre/chiude operazioni su TP1/TP2/SL

import os
import json
import time
import asyncio
import threading
import websockets
from datetime import datetime
from decimal import Decimal

# ===== Sheets =====
from sheet_logger import SheetLogger  # deve essere nel repo

# ===== WhatsApp (Meta Cloud API) =====
try:
    from wa_meta import MetaWhatsApp  # preferito
except Exception:
    import requests

    class MetaWhatsApp:
        def __init__(self, token=None, phone_id=None, default_to=None, timeout=15):
            self.token = token or os.getenv("WA_TOKEN", "")
            self.phone_id = phone_id or os.getenv("WA_PHONE_ID", "")
            self.default_to = default_to or os.getenv("WA_TO", "")
            self.timeout = timeout
            if not self.token or not self.phone_id:
                raise ValueError("WA_TOKEN o WA_PHONE_ID mancanti per WhatsApp Cloud API.")
            self.base_url = f"https://graph.facebook.com/v20.0/{self.phone_id}/messages"
            self.headers = {"Authorization": f"Bearer {self.token}"}

        def send_text(self, body: str, to: str | None = None) -> dict:
            to = (to or self.default_to or "").strip()
            if not to:
                raise ValueError("Numero destinatario mancante (WA_TO). Usa solo cifre, senza +.")
            payload = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": body},
            }
            r = requests.post(self.base_url, headers=self.headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json()

# ================== CONFIG ==================
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "true").lower() == "true"
WA_PROVIDER = os.getenv("WA_PROVIDER", "meta").lower()  # meta | (altro: non usato)

SYMBOL = os.getenv("SYMBOL", "paxgusdt").lower()  # stream WS
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "60"))

TRADE_SIZE = Decimal(os.getenv("TRADE_SIZE", "0.001"))  # quantità fittizia per test
STOP_LOSS = Decimal(os.getenv("STOP_LOSS", "-0.5"))
TP1 = Decimal(os.getenv("TAKE_PROFIT1", "0.2"))
TP2 = Decimal(os.getenv("TAKE_PROFIT2", "0.4"))
STRATEGY_TAG = os.getenv("STRATEGY_TAG", "v1")

# ================== GLOBAL STATE ==================
latest_price: Decimal | None = None
price_ts: float | None = None
ws_thread: threading.Thread | None = None
ws_stop = threading.Event()

open_positions = []  # lista di dict: {id, side, entry, qty, sl_pct, tp1_pct, tp2_pct, state}

# ================== HELPERS ==================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_whatsapp(msg: str):
    if not ALERTS_ENABLED:
        return
    try:
        if WA_PROVIDER != "meta":
            print("[WA] Provider non supportato, salta invio.")
            return
        MetaWhatsApp().send_text(msg)
        print("[WA] OK:", msg[:100])
    except Exception as e:
        print("[WA] ERRORE:", e)


# ================== WEBSOCKET LISTENER ==================
async def price_stream(symbol: str):
    """Apre un WS su stream pubblici Binance e aggiorna latest_price."""
    global latest_price, price_ts
    stream_url = f"wss://stream.binance.com:9443/ws/{symbol}@trade"
    backoff = 1
    while not ws_stop.is_set():
        try:
            async with websockets.connect(stream_url, ping_interval=20, ping_timeout=20) as ws:
                print(f"[WS] Connesso a {stream_url}")
                backoff = 1
                async for msg in ws:
                    if ws_stop.is_set():
                        break
                    data = json.loads(msg)
                    # prezzo trade: "p" come stringa
                    p = Decimal(data.get("p"))
                    latest_price = p
                    price_ts = time.time()
        except Exception as e:
            print("[WS] Disconnesso/errore:", e)
            if ws_stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)  # exponential backoff max 30s


def start_ws():
    loop = asyncio.new_event_loop()
    def runner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(price_stream(SYMBOL))
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t

# ================== TRADING MOCK LOGIC ==================
def open_position(price: Decimal, side: str = "LONG"):
    """Apre un trade fittizio e lo registra su Sheet."""
    trade_id = f"PAXGUSDT-{int(time.time()*1000)}"
    pos = {
        "id": trade_id,
        "side": side,
        "entry": price,
        "qty": float(TRADE_SIZE),
        "sl_pct": float(STOP_LOSS),
        "tp1_pct": float(TP1),
        "tp2_pct": float(TP2),
        "state": "APERTO",
    }
    open_positions.append(pos)

    # log su sheet
    sheet.log_open(
        trade_id=trade_id,
        side=side,
        entry_price=float(price),
        qty=float(TRADE_SIZE),
        sl_pct=float(STOP_LOSS),
        tp1_pct=float(TP1),
        tp2_pct=float(TP2),
        strategy=STRATEGY_TAG,
        note="apertura mock"
    )
    send_whatsapp(f"🚀 APERTURA {side}\nID: {trade_id}\nEntry: {price}\nTP1 {TP1}% · TP2 {TP2}% · SL {STOP_LOSS}%")
    print("[TRADE] OPEN", pos)


def close_position(pos: dict, close_price: Decimal, reason: str):
    """Chiude trade fittizio e aggiorna Sheet."""
    if pos.get("state") != "APERTO":
        return
    pos["state"] = "CHIUSO"
    pnl_pct = float(((close_price - pos["entry"]) / pos["entry"]) * 100) if pos["side"] == "LONG" \
        else float(((pos["entry"] - close_price) / pos["entry"]) * 100)
    pnl_value = float(Decimal(pnl_pct) / Decimal(100) * Decimal(pos["qty"]) * close_price)

    sheet.log_close(
        trade_id=pos["id"],
        close_price=float(close_price),
        close_type=reason,
        pnl_pct=round(pnl_pct, 4),
        pnl_value=round(pnl_value, 2),
        equity_after="",  # opzionale: se vuoi calcolare l'equity cumulata
        note=reason
    )
    send_whatsapp(f"✅ CHIUSURA ({reason})\nID: {pos['id']}\nClose: {close_price}\nPnL: {pnl_pct:.3f}%")
    print("[TRADE] CLOSE", pos["id"], reason)


def manage_positions(current_price: Decimal):
    """Controlla TP/SL per tutte le posizioni aperte."""
    for pos in list(open_positions):
        if pos["state"] != "APERTO":
            continue
        entry = pos["entry"]
        # soglie
        tp1_level = entry * (1 + Decimal(pos["tp1_pct"]) / Decimal(100))
        tp2_level = entry * (1 + Decimal(pos["tp2_pct"]) / Decimal(100))
        sl_level = entry * (1 + Decimal(pos["sl_pct"]) / Decimal(100))

        if current_price <= sl_level:
            close_position(pos, current_price, "SL")
            open_positions.remove(pos)
        elif current_price >= tp2_level:
            close_position(pos, current_price, "TP2")
            open_positions.remove(pos)
        elif current_price >= tp1_level:
            # parziale (per semplicità: chiude tutto alla prima che scatta)
            close_position(pos, current_price, "TP1")
            open_positions.remove(pos)


# ================== MAIN LOOP ==================
def main():
    global ws_thread
    print("[BOOT] Bot Oro v3 – WS+Sheets+WA Meta")

    # init Sheets (crea/valida tabs & ping label)
    global sheet
    sheet = SheetLogger()

    # avvia websocket
    ws_thread = start_ws()
    send_whatsapp("🤖 Bot Oro avviato (WS attivo).")

    last_open_ts = 0
    open_every_sec = int(os.getenv("OPEN_EVERY_SEC", "0"))  # se >0 apertura periodica mock

    while True:
        try:
            # heartbeat ogni HEARTBEAT_SEC
            if latest_price is None:
                print("[HEARTBEAT] prezzo non disponibile (in attesa WS)")
            else:
                sheet.log_heartbeat(float(latest_price), msg="loop ok (ws-only)")
                sheet.set_last_ping(f"{now_str()} · {latest_price}")

                # gestione posizioni
                manage_positions(latest_price)

                # apertura mock periodica (solo per test)
                if open_every_sec > 0 and time.time() - last_open_ts > open_every_sec:
                    open_position(latest_price, "LONG")
                    last_open_ts = time.time()

            time.sleep(HEARTBEAT_SEC)
        except Exception as e:
            print("[LOOP] ERRORE:", e)
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    finally:
        ws_stop.set()
        if ws_thread and ws_thread.is_alive():
            ws_thread.join(timeout=2)
