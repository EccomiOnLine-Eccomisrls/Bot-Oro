from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

class SheetLogger:
    def __init__(self, creds_json, spreadsheet_id):
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_json, scope)
        client = gspread.authorize(creds)
        self.sheet = client.open_by_key(spreadsheet_id)

    def log_trade(self, data):
        try:
            ws = self.sheet.worksheet("Log")
            ws.append_row(data, value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[ERRORE] Scrittura log trade: {e}")

    def log_error(self, message):
        try:
            ws = self.sheet.worksheet("Errori")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ws.append_row([now, message], value_input_option="USER_ENTERED")
        except Exception as e:
            print(f"[ERRORE] Scrittura log errore: {e}")

    def log_heartbeat(self):
        """Registra un segnale di 'battito' per indicare che il bot è vivo"""
        try:
            ws = self.sheet.worksheet("Log")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ws.append_row([now, "Heartbeat OK"], value_input_option="USER_ENTERED")
            print(f"[HEARTBEAT] Registrato su Google Sheet alle {now}")
        except Exception as e:
            print(f"[ERRORE] Scrittura log heartbeat: {e}")
