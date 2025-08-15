import os, requests, sys

WA_TOKEN    = os.getenv("WA_TOKEN", "")
WA_PHONE_ID = os.getenv("WA_PHONE_ID", "")
WA_TO       = os.getenv("WA_TO", "")

if not (WA_TOKEN and WA_PHONE_ID and WA_TO):
    print("[ERR] Manca WA_TOKEN o WA_PHONE_ID o WA_TO")
    sys.exit(1)

url = f"https://graph.facebook.com/v20.0/{WA_PHONE_ID}/messages"
payload = {
    "messaging_product": "whatsapp",
    "to": WA_TO,  # es. 393205616977 (senza +)
    "type": "text",
    "text": {"body": "✅ Test WhatsApp Cloud API (Meta) dal Bot Oro."}
}
headers = {"Authorization": f"Bearer {WA_TOKEN}"}

r = requests.post(url, json=payload, headers=headers, timeout=15)
print("Status:", r.status_code)
print("Body:", r.text)
r.raise_for_status()
print("[OK] Messaggio inviato via Meta Cloud API.")
