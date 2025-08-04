import gspread
from oauth2client.service_account import ServiceAccountCredentials

# CONFIGURAZIONE
SPREADSHEET_NAME = "BOT ORO – TEST"

print("Avvio diagnostica...")

try:
    # Scope per Google Sheets
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_credentials.json", scope)
    client = gspread.authorize(creds)

    # Apertura foglio
    sheet = client.open(SPREADSHEET_NAME)
    print(f"Connessione riuscita al foglio: {SPREADSHEET_NAME}")

    # Legge la prima cella
    cella = sheet.sheet1.cell(1, 1).value
    print(f"Valore in A1: {cella}")

except Exception as e:
    print("ERRORE durante la diagnostica:")
    print(e)
