
import gspread
import json
import os
from google.oauth2.service_account import Credentials

GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME")

creds = json.loads(GOOGLE_CREDENTIALS)
scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
credentials = Credentials.from_service_account_info(creds, scopes=scopes)
gc = gspread.authorize(credentials)

sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
sheet.append_row(["TEST", "Funziona!", "Se vedi questa riga, l'integrazione è OK"])
print("✅ Riga scritta con successo!")
