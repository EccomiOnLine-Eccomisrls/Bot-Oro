import os
import json
import time
import asyncio
import traceback
from datetime import datetime

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import websockets
import json as jsonlib


# ==============================
# Config & Helpers
# ==============================

SPREADSHEET_ID      = os.getenv("SPREADSHEET_ID", "").strip()
ALERTS_ENABLED      = os.getenv("ALERTS_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SYMBOL              = "PAXGUSDT"
BINANCE_WS_URL      = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@trade"

# colonne del foglio "Trade"
COL_STATO           = 4   # D
COL_ULTIMO_PING     = 11  # K

HEARTBEAT_SECS      = 60


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_telegram(text: str):
    if not ALERTS_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}")


# ==============================
# Google Sheets
# ==============================

def make_gs_client():
    raw = os.getenv("GOOGLE_CREDENTIALS", "")
    if not raw:
        raise RuntimeError("GOOGLE_CREDENTIALS mancante.")
    try:
        creds_dict = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("GOOGLE_CREDENTIALS non è JSON valido (controlla gli \\n della private_key).")

    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)


class SheetIO:
    def __init__(self, spreadsheet_id: str):
        if not spreadsheet_id:
            raise RuntimeError("SPREADSHEET_ID mancante.")
        self.gc = make_gs_client()
        self.sh = self.gc.open_by_key(spreadsheet_id)
        # Worksheet handle
        self.ws_trade = self.sh.worksheet("Trade")
        self.ws_log   = self.sh.worksheet("Log")

    def append_log(self, stato: str, prezzo: float | str, messaggio: str, extra: str = "", fonte: str = "bot"):
        """
        Log: A Data/Ora | B Stato | C Prezzo | D Messaggio | E Extra | F Fonte
        """
        try:
            row = [now_str(), stato, prezzo, messaggio, extra, fonte]
            self.ws_log.append_row(row, value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[LOG][ERRORE] {e}")

    def update_ultimo_ping_open_rows(self, price: float):
        """
        Scansiona la colonna D (Stato) e aggiorna K (Ultimo ping)
        per tutte le righe con 'APERTO'.
        Fa una sola batch_update per efficienza.
        """
        try:
            # Leggo tutte le righe presenti (limitando l'intervallo per performance se serve)
            values = self.ws_trade.get_all_values()
            if len(values) <= 1:
                return  # solo header

            updates = []
            ping_value = f"{now_str()} · {price}"

            # values[0] è header; le righe reali partono da index=1
            for idx, row in enumerate(values[1:], start=2):
                try:
                    stato = (row[COL_STATO - 1] or "").strip().upper()
                except IndexError:
                    continue
                if stato == "APERTO":
                    # cella K{idx}
                    a1 = f"{self.col_to_a1(COL_ULTIMO_PING)}{idx}"
                    updates.append({
                        "range": f"Trade!{a1}",
                        "values": [[ping_value]]
                    })

            if updates:
                self.ws_trade.batch_update(updates, value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[PING][ERRORE] {e}")

    @staticmethod
    def col_to_a1(col_idx: int) -> str:
        """
        1->A, 2->B ... 26->Z, 27->AA etc.
        """
        s = ""
        while col_idx > 0:
            col_idx, r = divmod(col_idx - 1, 26)
            s = chr(65 + r) + s
        return s


# ==============================
# WebSocket loop
# ==============================

async def binance_ws_loop(sheet: SheetIO):
    last_hb = 0.0
    async with websockets.connect(BINANCE_WS_URL, ping_interval=20, ping_timeout=20) as ws:
        print("[WS] connesso a Binance")
        send_telegram("🤖 Bot Oro avviato (WS attivo).")

        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                data = jsonlib.loads(msg)

                # trade message → prezzo è in 'p'
                price = float(data.get("p") or data.get("P") or 0.0)
                if price > 0:
                    # 1) aggiorno Ultimo ping per TUTTI i trade aperti
                    sheet.update_ultimo_ping_open_rows(price)

                # 2) heartbeat cadenzato
                now = time.time()
                if now - last_hb >= HEARTBEAT_SECS:
                    last_hb = now
                    sheet.append_log("Heartbeat OK", price, "ws alive", "", "bot")
                    print(f"[HEARTBEAT] {now_str()}  {SYMBOL}={price}")

            except asyncio.TimeoutError:
                # se non arrivano msg per un po', mando solo heartbeat
                now = time.time()
                if now - last_hb >= HEARTBEAT_SECS:
                    last_hb = now
                    sheet.append_log("Heartbeat OK", "", "no ticks (timeout)", "", "bot")
                    print(f"[HEARTBEAT] {now_str()}  no ticks (timeout)")
            except websockets.ConnectionClosed:
                print("[WS] chiuso. Riconnessione tra 3s…")
                await asyncio.sleep(3)
                return  # esco e lascio il main riaprire
            except Exception as e:
                err = "".join(traceback.format_exception_only(type(e), e)).strip()
                print(f"[WS][ERRORE] {err}")
                sheet.append_log("ERRORE", "", f"WS exception: {err}", "", "bot")
                send_telegram(f"⚠️ Bot Oro: errore WS\n{err}")
                await asyncio.sleep(2)


async def main_async():
    sheet = SheetIO(SPREADSHEET_ID)
    # loop di riconnessione perpetua
    while True:
        try:
            await binance_ws_loop(sheet)
        except Exception as e:
            err = "".join(traceback.format_exception_only(type(e), e)).strip()
            print(f"[MAIN][ERRORE] {err}")
            send_telegram(f"⚠️ Bot Oro crash loop:\n{err}")
            await asyncio.sleep(3)  # backoff e riprovo


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("Stop richiesto.")


if __name__ == "__main__":
    main()
