import os
import json
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client
from twilio.rest import Client as TwilioClient
from datetime import datetime, timedelta

# ======= CONFIGURAZIONE =======
# Binance
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
DESTINATION_NUMBER = os.getenv("TWILIO_TO", "whatsapp:+393205616977")

# Google Sheets
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS") or os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_NAME = os.getenv("SHEET_NAME", "Foglio1")

# Trading
TRADE_SIZE = float(os.getenv("TRADE_SIZE", 1))
STOP_LOSS = float(os.getenv("STOP_LOSS", -0.5))
TAKE_PROFIT1 = float(os.getenv("TAKE_PROFIT1", 1))
TAKE_PROFIT2 = float(os.getenv("TAKE_PROFIT2", 2))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", -3))

# ======= CONNESSIONI =======
binance_client = Client(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDENTIALS), scope)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

# Crea foglio Report se non esiste
try:
    report_sheet = gc.open_by_key(SPREADSHEET_ID).worksheet("Report")
except gspread.exceptions.WorksheetNotFound:
    report_sheet = gc.open_by_key(SPREADSHEET_ID).add_worksheet(title="Report", rows=100, cols=10)

# ======= FUNZIONI =======
def invia_messaggio(messaggio):
    try:
        twilio_client.messages.create(
            body=messaggio,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=DESTINATION_NUMBER
        )
        print(f"[WHATSAPP] Messaggio inviato: {messaggio}")
    except Exception as e:
        print(f"[ERRORE WHATSAPP] {e}")

def scrivi_su_sheets(dati):
    try:
        sheet.append_row(dati)
        print(f"[SHEETS] Dati scritti: {dati}")
    except Exception as e:
        print(f"[ERRORE SHEETS] {e}")

def calcola_report(periodo="giornaliero"):
    records = sheet.get_all_records()
    oggi = datetime.now().date()
    inizio_settimana = oggi - timedelta(days=oggi.weekday())

    if periodo == "giornaliero":
        filtrati = [r for r in records if datetime.strptime(r['Data'], "%Y-%m-%d %H:%M:%S").date() == oggi]
    else:
        filtrati = [r for r in records if datetime.strptime(r['Data'], "%Y-%m-%d %H:%M:%S").date() >= inizio_settimana]

    if not filtrati:
        return {"operazioni": 0, "media": 0, "totale": 0}

    profitti = [float(r['Profitto (%)']) for r in filtrati if r.get('Profitto (%)')]
    totale = sum(profitti)
    media = totale / len(profitti)
    return {"operazioni": len(filtrati), "media": media, "totale": totale}

def aggiorna_report():
    giornaliero = calcola_report("giornaliero")
    settimanale = calcola_report("settimanale")

    report_sheet.clear()
    report_sheet.append_row(["Periodo", "Operazioni", "Media (%)", "Totale (%)"])
    report_sheet.append_row(["Giornaliero", giornaliero['operazioni'], giornaliero['media'], giornaliero['totale']])
    report_sheet.append_row(["Settimanale", settimanale['operazioni'], settimanale['media'], settimanale['totale']])

    invia_messaggio(
        f"📊 Report:\n"
        f"Giornaliero - Operazioni: {giornaliero['operazioni']}, Media: {giornaliero['media']:.2f}%, Totale: {giornaliero['totale']:.2f}%\n"
        f"Settimanale - Operazioni: {settimanale['operazioni']}, Media: {settimanale['media']:.2f}%, Totale: {settimanale['totale']:.2f}%"
    )

# ======= CICLO PRINCIPALE =======
def main():
    invia_messaggio(f"🤖 Bot Oro avviato correttamente!")
    while True:
        try:
            prezzo = float(binance_client.get_symbol_ticker(symbol="PAXGUSDT")["price"])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            profitto = round((TAKE_PROFIT1 + TAKE_PROFIT2) / 2, 2)  # simulazione
            scrivi_su_sheets([timestamp, "BOT ATTIVO", prezzo, STOP_LOSS, TAKE_PROFIT1, TAKE_PROFIT2, profitto, "Bot in esecuzione"])
            print(f"[BOT] Prezzo attuale: {prezzo}")
        except Exception as e:
            print(f"[ERRORE CICLO] {e}")
        time.sleep(300)  # ogni 5 minuti

if __name__ == "__main__":
    main()
