from twilio.rest import Client
import os, sys

SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
FROM  = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")  # sandbox di default
TO    = os.getenv("DESTINATION_NUMBER") or os.getenv("TWILIO_TO")      # supporta entrambi

def ensure_whatsapp_prefix(s: str) -> str:
    if not s:
        return s
    return s if s.startswith("whatsapp:") else f"whatsapp:{s}"

# normalizza i numeri con prefisso whatsapp:
FROM = ensure_whatsapp_prefix(FROM)
TO   = ensure_whatsapp_prefix(TO or "")

print("FROM:", FROM, "| TO:", TO, "| SID:", (SID[:6]+"…") if SID else "(manca)")

# validazioni rapide
errors = []
if not SID:   errors.append("TWILIO_ACCOUNT_SID mancante")
if not TOKEN: errors.append("TWILIO_AUTH_TOKEN mancante")
if not TO:    errors.append("DESTINATION_NUMBER / TWILIO_TO mancante")
if errors:
    print("[CONFIG ERROR]", " | ".join(errors))
    sys.exit(1)

try:
    client = Client(SID, TOKEN)
    msg = client.messages.create(
        body="🔔 Test WhatsApp da Bot Oro",
        from_=FROM,
        to=TO
    )
    print("[OK] Inviato. Message SID:", msg.sid)
except Exception as e:
    print("[TWILIO ERROR]", repr(e))
