import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import gspread.utils

# --- AUTENTICAZIONE GOOGLE SHEETS ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
client = gspread.authorize(creds)

# --- APRE IL FOGLIO DI LAVORO ---
sheet = client.open("BOT ORO – TEST")
sheet_operations = sheet.sheet1  # Prima scheda

# --- ORA ATTUALE ---
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# --- TROVA LA PRIMA COLONNA VUOTA NELLA RIGA 1 ---
row_values = sheet_operations.row_values(1)  # Legge tutta la prima riga
next_col = len(row_values) + 1  # Prima colonna vuota
cell_address = gspread.utils.rowcol_to_a1(1, next_col)  # Converte in A1 (es. "K1")

# --- SCRIVE IL MESSAGGIO NELLA PRIMA COLONNA VUOTA ---
sheet_operations.update(cell_address, [[f"Bot attivo – {now}"]])

print(f"Valore scritto in {cell_address}: Bot attivo – {now}")
