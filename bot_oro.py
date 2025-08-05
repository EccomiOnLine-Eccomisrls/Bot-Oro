import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from twilio.rest import Client
from datetime import datetime

# === FUNZIONE DI SUPPORTO PER LE VARIABILI ENV ===
def get_env_var(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"❌ Variabile d'ambiente mancante: {name}")
    return value

# === GOOGLE SHEETS ===
GOOGLE_CREDENTIALS_JSON = get_env_var("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID = get_env_var("SPREADSHEET_ID")

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials = ServiceAccountCredentials.from_json_keyfile_dict(eval(GOOGLE_CREDENTIALS_JSON), scope)
client = gspread.authorize(credentials)
sheet = client.open_by_key(SPREADSHEET_ID).sheet1  # Primo foglio

# === TWILIO ===
twilio_sid = get_env_var("TWILIO_ACCOUNT_SID")
twilio_token = get_env_var("TWILIO_AUTH_TOKEN")
twilio_from = get_env_var("TWILIO_WHATSAPP_NUMBER")
twilio_to = get_env_var("DESTINATION_NUMBER")

twilio_client = Client(twilio_sid, twilio_token)

# === FUNZIONE PRINCIPALE ===
def main():
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print(f"Bot avviato alle {now}")

    # Aggiorna una cella sul foglio (evita warning: ora valori prima e range dopo)
    first_empty_row = len(sheet.col_values(1)) + 1
    sheet.update([[f"Bot attivo – {now}"]], f'K{first_empty_row}')

    # Invia messaggio WhatsApp di notifica
    twilio_client.messages.create(
        from_=twilio_from,
        to=twilio_to,
        body=f"✅ Bot Oro attivo alle {now}"
    )

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Errore durante l'esecuzione: {e}")
