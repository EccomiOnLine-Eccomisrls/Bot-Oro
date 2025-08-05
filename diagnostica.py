import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import json
import os

print("\nAvvio diagnostica...")

try:
    # Leggi credenziali dal secret su Render
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise Exception("Variabile GOOGLE_CREDENTIALS mancante.")
    creds_dict = json.loads(creds_json)

    # Autenticazione
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)

    # Nome del foglio
    SPREADSHEET_NAME = "BOT ORO – TEST"
    sh = gc.open(SPREADSHEET_NAME)
    worksheet = sh.sheet1

    # Leggi A1
    a1_value = worksheet.acell('A1').value
    print(f"Connessione riuscita al foglio: {SPREADSHEET_NAME}")
    print(f"Valore in A1: {a1_value}")

    # Scrivi in B1
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    worksheet.update_acell('B1', f"Connessione OK - {now}")
    print(f"Scritto in B1: Connessione OK - {now}")

except Exception as e:
    print("\nERRORE durante la diagnostica:")
    print(str(e))
