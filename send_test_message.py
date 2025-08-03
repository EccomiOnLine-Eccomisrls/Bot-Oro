
from twilio.rest import Client
import os

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
DESTINATION_NUMBER = os.getenv("DESTINATION_NUMBER")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
try:
    twilio_client.messages.create(
        body="🔄 Test manuale: WhatsApp da Bot ORO funzionante.",
        from_=TWILIO_WHATSAPP_NUMBER,
        to=DESTINATION_NUMBER
    )
    print("[OK] Messaggio di test inviato con successo.")
except Exception as e:
    print("[ERRORE] Invio messaggio di test fallito:", e)
