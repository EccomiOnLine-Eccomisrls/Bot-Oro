# wa_meta.py
import os
import json
import time
import requests

class MetaWhatsApp:
    def __init__(self,
                 token: str | None = None,
                 phone_id: str | None = None,
                 default_to: str | None = None,
                 timeout: int = 15):
        self.token     = token or os.getenv("WA_TOKEN", "")
        self.phone_id  = phone_id or os.getenv("WA_PHONE_ID", "")
        self.default_to = default_to or os.getenv("WA_TO", "")
        self.timeout   = timeout

        if not self.token or not self.phone_id:
            raise ValueError("WA_TOKEN o WA_PHONE_ID mancanti.")

        self.base_url = f"https://graph.facebook.com/v20.0/{self.phone_id}/messages"
        self.headers  = {"Authorization": f"Bearer {self.token}"}

    def send_text(self, body: str, to: str | None = None) -> dict:
        """Invia un semplice messaggio di testo."""
        to = (to or self.default_to or "").strip()
        if not to:
            raise ValueError("Numero destinatario mancante (WA_TO). Usa solo cifre, senza +.")

        payload = {
            "messaging_product": "whatsapp",
            "to": to,                # es: 393205616977 (no +)
            "type": "text",
            "text": {"preview_url": False, "body": body}
        }
        r = requests.post(self.base_url, headers=self.headers, json=payload, timeout=self.timeout)
        try:
            r.raise_for_status()
        except requests.HTTPError:
            raise RuntimeError(f"[WA META] {r.status_code} - {r.text}")
        return r.json()
