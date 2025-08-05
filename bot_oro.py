import os
import json
import time
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from twilio.rest import Client
from binance.client import Client as BinanceClient

# === CONFIGURAZIONE ===
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

# Legge le credenziali dal JSON in ENV (non dal file)
service_account_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)

# Connessione a Google Sheets
client = gspread.authorize(creds)
sheet = client.open_by_key(os.environ["SPREADSHEET_ID"])  # <-- ID del foglio da ENV
sheet_operations = sheet.sheet1

# Twilio (per invio messaggi)
twilio_sid = os.environ["TWILIO_SID"]
twilio_token = os.environ["TWILIO_TOKEN"]
twilio_from = os.environ["TWILIO_FROM"]
twilio_to = os.environ["TWILIO_TO"]
twilio_client = Client(twilio_sid, twilio_token)

# Binance (se serve)
binance_api_key = os.environ["BINANCE_API_KEY"]
binance_api_secret = os.environ["BINANCE_API_SECRET"]
binance_client = BinanceClient(binance_api_key, binance_api_secret)

# === LOOP BOT ===
def main_loop():
    while True:
        try:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # TROVA PRIMA RIGA VUOTA IN COLONNA K (11)
            existing_values = sheet_operations.col_values(11)
            first_empty_row = len(existing_values) + 1

            # SCRIVE LO STATO NELLA PRIMA RIGA VUOTA DELLA COLONNA K
            sheet_operations.update(f'K{first_empty_row}', [[f"Bot attivo – {now}"]])

            print(f"[{now}] Stato aggiornato su Google Sheets (riga {first_empty_row})")

            # Esempio: invio notifica su WhatsApp via Twilio
            twilio_client.messages.create(
                body=f"Bot attivo – {now}",
                from_=twilio_from,
                to=twilio_to
            )

            # Aspetta 60 secondi prima del prossimo ciclo
            time.sleep(60)

        except Exception as e:
            print(f"Errore: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main_loop()
