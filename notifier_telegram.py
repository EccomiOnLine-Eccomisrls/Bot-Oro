# notifier_telegram.py
import os
import time
import json
import requests
from typing import Optional

class TelegramNotifier:
    """
    Invio notifiche Telegram con rate-limit di sicurezza.
    Se TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non sono impostati, va in no-op (stampa a console).
    Variabili d'ambiente:
      - TELEGRAM_BOT_TOKEN
      - TELEGRAM_CHAT_ID
      - ALERTS_ENABLED=true/false  (default: true)
    """

    def __init__(self, min_interval_sec: int = 2):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = (os.getenv("ALERTS_ENABLED", "true").lower() == "true")
        self.min_interval = max(0, int(min_interval_sec))
        self._last_sent_ts = 0.0

    def _can_send(self) -> bool:
        if not self.enabled:
            return False
        return bool(self.token and self.chat_id)

    def send(self, text: str, disable_web_page_preview: bool = True) -> Optional[dict]:
        """
        Invia un messaggio. Se non configurato, stampa su console e ritorna None.
        Applica un piccolo rate-limit (min_interval_sec) per evitare flood.
        """
        if not self._can_send():
            print(f"[NOTIFY/DRY] {text}")
            return None

        # rate-limit
        now = time.time()
        if now - self._last_sent_ts < self.min_interval:
            time.sleep(self.min_interval - (now - self._last_sent_ts))

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
            "parse_mode": "HTML",
        }
        try:
            r = requests.post(url, json=payload, timeout=10)
            r.raise_for_status()
            self._last_sent_ts = time.time()
            resp = r.json()
            print(f"[NOTIFY/OK] {json.dumps(resp, ensure_ascii=False)}")
            return resp
        except Exception as e:
            print(f"[NOTIFY/ERR] {e}  // testo='{text[:120]}'")
            return None

    # Helper “semantici”
    def startup(self):
        return self.send("🤖 <b>Bot Oro</b> avviato correttamente.")

    def heartbeat(self, price: Optional[float] = None):
        txt = "💓 Heartbeat"
        if price is not None:
            txt += f" – Prezzo: <b>{price}</b>"
        return self.send(txt)

    def trade_open(self, trade_id: str, side: str, qty: float, price: float, strategy: str):
        return self.send(
            f"🟢 <b>APERTURA</b> #{trade_id}\n"
            f"Lato: <b>{side}</b>  Qty: <b>{qty}</b>\n"
            f"Prezzo ingresso: <b>{price}</b>\n"
            f"Strategia: <i>{strategy}</i>"
        )

    def trade_close(self, trade_id: str, reason: str, price: float,
                    pnl_pct: float, pnl_value: float, equity_after: float):
        return self.send(
            f"🔴 <b>CHIUSURA</b> #{trade_id}\n"
            f"Motivo: <b>{reason}</b>\n"
            f"Prezzo uscita: <b>{price}</b>\n"
            f"P&L %: <b>{pnl_pct:.4f}</b>  P&L $: <b>{pnl_value:.2f}</b>\n"
            f"Equity post-trade: <b>{equity_after:.2f}</b>"
        )

    def info(self, msg: str):
        return self.send(f"ℹ️ {msg}")

    def warn(self, msg: str):
        return self.send(f"⚠️ {msg}")

    def error(self, msg: str):
        return self.send(f"❌ {msg}")
