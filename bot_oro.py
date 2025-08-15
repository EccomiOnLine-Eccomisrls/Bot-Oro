# bot_oro.py
# Bot Oro – v3 (WS Binance + Google Sheet + Telegram + WhatsApp Cloud API)
# - Prezzo via WebSocket pubblico Binance (no REST, no ban)
# - Log su Google Sheets (SheetLogger)
# - Notifiche Telegram sempre disponibili (gratis)
# - Notifiche WhatsApp via Meta Cloud API opzionali (quando attive)
# - Strategia mock per test: apre/chiude operazioni con TP1/TP2/SL

import os
import json
import time
import asyncio
import threading
from datetime import datetime
from decimal import Decimal

import websockets
import requests

# ===== Google Sheets =====
from sheet_logger import SheetLogger  # Assicurati che sia presente nel repo

# ================== CONFIG ==================
# Symbol per stream WS (minuscolo, formato binance spot)
SYMBOL = os.getenv("SYMBOL", "paxgusdt").lower()

# Heartbeat (secondi fra un ciclo e l'altro)
HEARTBEAT_SEC = int(os.getenv("HEARTBEAT_SEC", "60"))

# Parametri strategia (mock per test)
TRADE_SIZE = Decimal(os.getenv("TRADE_SIZE", "0.001"))  # quantità fittizia
STOP_LOSS = Decimal(os.getenv("STOP_LOSS", "-0.5"))     # -0.5% default
TP1       = Decimal(os.getenv("TAKE_PROFIT1", "0.2"))   # +0.2% default
TP2       = Decimal(os.getenv("TAKE_PROFIT2", "0.4"))   # +0.4% default
STRATEGY_TAG = os.getenv("STRATEGY_TAG", "v1")
OPEN_EVERY_SEC = int(os.getenv("OPEN_EVERY_SEC", "0"))  # se >0 apre un trade fittizio ogni N sec

# Telegram (consigliato, gratis)
TELEGRAM_ENABLED   = os.getenv("TELEGRAM_ENABLED", "true").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# WhatsApp (Meta Cloud API) - opzionale
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "false").lower() == "true"  # abilita canale WA
WA_PROVIDER    = os.getenv("WA_PROVIDER", "meta").lower()                # per ora supportiamo solo "meta"
WA_TOKEN       = os.getenv("WA_TOKEN", "")
WA_PHONE_ID    = os.getenv("WA_PHONE_ID", "")
WA_TO          = (os.getenv("WA_TO", "") or "").strip()  # numero solo cifre (es. 39320...)

# ================== STATO GLOBALE ==================
latest_price: Decimal | None = None
price_ts: float | None = None
ws_thread: threading.Thread | None = None
ws_stop = threading.Event()

# lista di posizioni aperte (mock)
# ogni posizione: {id, side, entry, qty, sl_pct, tp1_pct, tp2_pct, state}
open_positions: list[dict] = []

# ================== UTILS ==================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ================== NOTIFIER: TELEGRAM ==================
def send_telegram_message(text: str):
    """Invia un messaggio su Telegram se configurato."""
    if not TELEGRAM_ENABLED:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Config mancante (BOT_TOKEN/CHAT_ID).")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[TELEGRAM ERROR] {r.status_code} - {r.text}")
        else:
            print("[TELEGRAM] OK")
    except Exception as e:
        print("[TELEGRAM EXC]", e)


# ================== NOTIFIER: WHATSAPP META (opzionale) ==================
def send_whatsapp_meta(text: str):
    """Invia un messaggio WhatsApp tramite Meta Cloud API (se abilitato)."""
    if not ALERTS_ENABLED:
        return
    if WA_PROVIDER != "meta":
        print("[WA] Provider non supportato (usa 'meta').")
        return
    if not WA_TOKEN or not WA_PHONE_ID or not WA_TO:
        print("[WA] Config mancante (WA_TOKEN / WA_PHONE_ID / WA_TO).")
        return

    # WA_TO deve essere solo cifre (senza +); Meta accetta anche con + ma teniamo standard solo cifre.
    to = WA_TO
    if to.startswith("+"):
        to = to[1:]

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code != 200:
            print(f"[WA META ERROR] {r.status_code} - {r.text}")
        else:
            print("[WA META] OK")
    except Exception as e:
        print("[WA META EXC]", e)


# ================== NOTIFY ALL (Telegram + WhatsApp) ==================
def notify_all(msg: str):
    """Invia il messaggio su tutti i canali attivi."""
    # Prima Telegram (affidabile e gratis)
    try:
        send_telegram_message(msg)
    except Exception as e:
        print("[NOTIFY][TG] ERR", e)
    # Poi WhatsApp (se abilitato e configurato)
    try:
        send_whatsapp_meta(msg)
    except Exception as e:
        print("[NOTIFY][WA] ERR", e)


# ================== WEBSOCKET LISTENER (BINANCE) ==================
async def price_stream(symbol: str):
    """Apre WS pubblico Binance e aggiorna latest_price a ogni trade."""
    global latest_price, price_ts
    stream_url = f"wss://stream.binance.com:9443/ws/{symbol}@trade"
    backoff = 1
    while not ws_stop.is_set():
        try:
            async with websockets.connect(stream_url, ping_interval=20, ping_timeout=20) as ws:
                print(f"[WS] Connesso a {stream_url}")
                backoff = 1
                async for raw in ws:
                    if ws_stop.is_set():
                        break
                    data = json.loads(raw)
                    # Prezzo del trade in campo "p" (stringa)
                    p_str = data.get("p")
                    if p_str is None:
                        continue
                    latest_price = Decimal(p_str)
                    price_ts = time.time()
        except Exception as e:
            print("[WS] Disconnesso/errore:", e)
            if ws_stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)  # backoff max 30s


def start_ws():
    loop = asyncio.new_event_loop()
    def runner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(price_stream(SYMBOL))
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t


# ================== TRADING MOCK ==================
def open_position(price: Decimal, side: str = "LONG"):
    """Apre un trade fittizio e lo registra su Sheets."""
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

    # Log su Google Sheet
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

    notify_all(
        f"🚀 APERTURA {side}\n"
        f"ID: {trade_id}\n"
        f"Entry: {price}\n"
        f"TP1 {TP1}% · TP2 {TP2}% · SL {STOP_LOSS}%"
    )
    print("[TRADE] OPEN", pos)


def close_position(pos: dict, close_price: Decimal, reason: str):
    """Chiude trade fittizio e aggiorna Sheets."""
    if pos.get("state") != "APERTO":
        return
    pos["state"] = "CHIUSO"

    if pos["side"] == "LONG":
        pnl_pct = float(((close_price - pos["entry"]) / pos["entry"]) * 100)
    else:
        pnl_pct = float(((pos["entry"] - close_price) / pos["entry"]) * 100)

    pnl_value = float(Decimal(pnl_pct) / Decimal(100) * Decimal(pos["qty"]) * close_price)

    sheet.log_close(
        trade_id=pos["id"],
        close_price=float(close_price),
        close_type=reason,
        pnl_pct=round(pnl_pct, 4),
        pnl_value=round(pnl_value, 2),
        equity_after="",  # opzionale: puoi calcolare equity cumulata
        note=reason
    )

    notify_all(
        f"✅ CHIUSURA ({reason})\n"
        f"ID: {pos['id']}\n"
        f"Close: {close_price}\n"
        f"PnL: {pnl_pct:.3f}%"
    )
    print("[TRADE] CLOSE", pos["id"], reason)


def manage_positions(current_price: Decimal):
    """Controlla TP/SL per tutte le posizioni aperte."""
    for pos in list(open_positions):
        if pos["state"] != "APERTO":
            continue
        entry = pos["entry"]
        tp1_level = entry * (1 + Decimal(pos["tp1_pct"]) / Decimal(100))
        tp2_level = entry * (1 + Decimal(pos["tp2_pct"]) / Decimal(100))
        sl_level  = entry * (1 + Decimal(pos["sl_pct"])  / Decimal(100))

        if current_price <= sl_level:
            close_position(pos, current_price, "SL")
            open_positions.remove(pos)
        elif current_price >= tp2_level:
            close_position(pos, current_price, "TP2")
            open_positions.remove(pos)
        elif current_price >= tp1_level:
            # Parziale semplice: per il mock chiudiamo tutto al primo target
            close_position(pos, current_price, "TP1")
            open_positions.remove(pos)


# ================== MAIN LOOP ==================
def main():
    global ws_thread, sheet
    print("[BOOT] Bot Oro v3 – WS+Sheets+TG(+WA)")

    # Inizializza Google Sheets (crea/valida tabs & ping label)
    sheet = SheetLogger()

    # Avvia il WebSocket
    ws_thread = start_ws()

    notify_all("🤖 Bot Oro avviato (WS attivo).")

    last_open_ts = 0

    while True:
        try:
            if latest_price is None:
                print("[HEARTBEAT] prezzo non disponibile (in attesa WS)")
            else:
                # Log heartbeat su Sheet + label 'Ultimo ping'
                sheet.log_heartbeat(float(latest_price), msg="loop ok (ws-only)")
                sheet.set_last_ping(f"{now_str()} · {latest_price}")

                # Gestione posizioni (TP/SL)
                manage_positions(latest_price)

                # Apertura mock periodica per test (se configurata)
                if OPEN_EVERY_SEC > 0 and time.time() - last_open_ts > OPEN_EVERY_SEC:
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
