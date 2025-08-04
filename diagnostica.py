import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

SPREADSHEET_NAME = "BOT ORO – TEST"
print("Avvio diagnostica...")

try:
    # Leggi credenziali dai secrets
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise Exception("Variabile d'ambiente GOOGLE_CREDENTIALS_JSON mancante")
    creds_dict = json.loads(creds_json)

    # Usa le credenziali direttamente da variabile
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # Apertura foglio
    sheet = client.open(SPREADSHEET_NAME)
    print(f"Connessione riuscita al foglio: {SPREADSHEET_NAME}")
    print(f"Valore in A1: {sheet.sheet1.cell(1, 1).value}")

except Exception as e:
    print("ERRORE durante la diagnostica:")
    print(e)
