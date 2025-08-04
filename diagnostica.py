
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from twilio.rest import Client
import os

# Variabili ambiente
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "BOT ORO – TEST")
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_WHATSAPP = os.getenv("TWILIO_WHATSAPP")
DESTINATARIO = os.getenv("DESTINATARIO")

# Connessione Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("google_credentials.json", scope)
client_gs = gspread.authorize(creds)

# Apri il foglio
sheet = client_gs.open(SPREADSHEET_NAME).sheet1
data = sheet.get_all_values()

# Stampa le prime 5 righe nei log
print("Prime 5 righe del foglio:")
for row in data[:5]:
    print(row)

# Connessione Twilio
client_twilio = Client(TWILIO_SID, TWILIO_TOKEN)

# Invia messaggio di conferma
msg = "✅ Connessione a Google Sheet e Twilio OK. Prime righe lette correttamente."
client_twilio.messages.create(
    from_=TWILIO_WHATSAPP,
    body=msg,
    to=DESTINATARIO
)

print("Messaggio WhatsApp inviato con successo!")
