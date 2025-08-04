
import time
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from twilio.rest import Client

# CONFIGURAZIONE
GOOGLE_CREDENTIALS_FILE = 'google_credentials.json'  # Percorso del file credenziali
SPREADSHEET_NAME = "BOT ORO – TEST"  # Nome del foglio Google
TWILIO_SID = 'YOUR_TWILIO_SID'
TWILIO_TOKEN = 'YOUR_TWILIO_TOKEN'
TWILIO_WHATSAPP = 'whatsapp:+14155238886'
DESTINATARIO = 'whatsapp:+39XXXXXXXXXX'

# Configurazione logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Connessione a Twilio
client = Client(TWILIO_SID, TWILIO_TOKEN)

# Funzione per invio messaggio WhatsApp
def invia_messaggio(testo):
    try:
        client.messages.create(
            from_=TWILIO_WHATSAPP,
            body=testo,
            to=DESTINATARIO
        )
        logging.info(f"Messaggio inviato: {testo}")
    except Exception as e:
        logging.error(f"Errore invio messaggio: {e}")

# Connessione a Google Sheet
def connessione_google():
    try:
        scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, scope)
        client_g = gspread.authorize(creds)
        sheet = client_g.open(SPREADSHEET_NAME).sheet1
        logging.info("Connessione a Google Sheet riuscita.")
        return sheet
    except Exception as e:
        logging.error(f"Errore connessione Google Sheet: {e}")
        invia_messaggio("⚠️ Errore connessione Google Sheet!")
        return None

# Ciclo principale
def main():
    invia_messaggio("🚀 Bot ORO avviato e funzionante.")
    last_checked_row = 1
    while True:
        try:
            sheet = connessione_google()
            if not sheet:
                time.sleep(30)
                continue
            dati = sheet.get_all_records()
            for idx, riga in enumerate(dati, start=2):
                if idx <= last_checked_row:
                    continue
                esito = riga.get('G')
                if esito in ['WIN', 'LOSS']:
                    prezzo = riga.get('C')
                    invia_messaggio(f"📈 Nuova operazione: {esito} - Prezzo: {prezzo}")
                last_checked_row = idx
            time.sleep(60)  # Controlla ogni 60 secondi
        except Exception as e:
            logging.error(f"Errore nel ciclo principale: {e}")
            invia_messaggio("⚠️ Bot ORO riavviato per errore.")
            time.sleep(10)

if __name__ == '__main__':
    main()
