# bot_oro.py
import os
import json
import time
from datetime import datetime, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from binance.client import Client
from twilio.rest import Client as TwilioClient

from sheet_logger import SheetLogger  # << usa il logger che separa Trade e Log

# =========================
# CONFIGURAZIONE / ENV
# =========================
# Binance
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("BINANCE_SYMBOL", "PAXGUSDT")  # usa PAXGUSDT (gold token su Binance) o quello che preferisci

# Twilio (WhatsApp)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
DESTINATION_NUMBER = os.getenv("TWILIO_TO", "whatsapp:+393205616977")

# Google Sheets
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS") or os.getenv("GOOGLE_CREDENTIALS_JSON")
TRADE_SHEET_NAME = "Trade"
LOG_SHEET_NAME = "Log"

# Trading (parametri base; qui il bot fa solo heartbeat finché non abiliti la parte segnali)
TRADE_SIZE = float(os.getenv("TRADE_SIZE", 1))
SL_PCT = float(os.getenv("STOP_LOSS", 0.005))      # 0.5%
TP1_PCT = float(os.getenv("TAKE_PROFIT1", 0.004))  # 0.4%
TP2_PCT = float(os.getenv("TAKE_PROFIT2", 0.010))  # 1.0%

# Timings
PING_EVERY_SEC = int(os.getenv("PING_EVERY_SEC", "60"))     # heartbeat → 1 riga su Log + cella in Trade
REPORT_EVERY_SEC = int(os.getenv("REPORT_EVERY_SEC", "3600"))  # aggiorna report ogni 60 min

# =========================
# CONNESSIONI
# =========================
binance_client = Client(api_key=BINANCE_API_KEY, api_secret=BINANCE_API_SECRET)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
sheet = SheetLogger()  # crea/valida fogli Trade e Log e la cella "Ultimo ping"

def _gc_client():
    """Client gspread per lettura report."""
    info = json.loads(GOOGLE_CREDENTIALS)
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
    return gspread.authorize(creds)

# =========================
# FUNZIONI UTILI
# =========================
def invia_messaggio(msg: str):
    try:
        twilio_client.messages.create(body=msg, from_=TWILIO_WHATSAPP_NUMBER, to=DESTINATION_NUMBER)
        print(f"[WHATSAPP] {msg}")
    except Exception as e:
        print(f"[WHATSAPP][ERRORE] {e}")

def prezzo_corrente() -> float:
    # Legge il last price dal ticker
    px = float(binance_client.get_symbol_ticker(symbol=SYMBOL)["price"])
    return px

def calcola_report(periodo="giornaliero"):
    """Legge dal foglio Trade e calcola conteggio operazioni e P&L medio/totale.
       Supporta colonne 'P&L %' (nuovo schema) e 'Profitto (%)' (vecchio)."""
    gc = _gc_client()
    trade_ws = gc.open_by_key(SPREADSHEET_ID).worksheet(TRADE_SHEET_NAME)
    records = trade_ws.get_all_records()

    if not records:
        return {"operazioni": 0, "media": 0.0, "totale": 0.0}

    oggi = datetime.now().date()
    inizio_settimana = oggi - timedelta(days=oggi.weekday())

    def parse_date(r):
        # accetta 'Data/Ora' (nuovo) o 'Data' (vecchio)
        key = "Data/Ora" if "Data/Ora" in r else "Data"
        try:
            return datetime.strptime(r[key], "%Y-%m-%d %H:%M:%S").date()
        except Exception:
            return None

    if periodo == "giornaliero":
        filtrati = [r for r in records if parse_date(r) == oggi and str(r.get("Stato", "")).upper() == "CHIUSO"]
    else:
        filtrati = [r for r in records if (d := parse_date(r)) and d >= inizio_settimana and str(r.get("Stato","")).upper() == "CHIUSO"]

    if not filtrati:
        return {"operazioni": 0, "media": 0.0, "totale": 0.0}

    prof_key = "P&L %" if "P&L %" in filtrati[0] else "Profitto (%)"
    profitti = []
    for r in filtrati:
        try:
            val = float(str(r.get(prof_key, "0")).replace("%", "").replace(",", "."))
            profitti.append(val)
        except Exception:
            pass

    if not profitti:
        return {"operazioni": len(filtrati), "media": 0.0, "totale": 0.0}

    totale = sum(profitti)
    media = totale / len(profitti)
    return {"operazioni": len(filtrati), "media": round(media, 4), "totale": round(totale, 4)}

def aggiorna_report_e_invia():
    g = calcola_report("giornaliero")
    s = calcola_report("settimanale")
    msg = (
        "📊 Report Bot Oro\n"
        f"• Oggi: operazioni {g['operazioni']}, media {g['media']:.2f}%, totale {g['totale']:.2f}%\n"
        f"• Settimana: operazioni {s['operazioni']}, media {s['media']:.2f}%, totale {s['totale']:.2f}%"
    )
    invia_messaggio(msg)

# =========================
# LOOP PRINCIPALE
# =========================
def main():
    invia_messaggio("🤖 Bot Oro avviato correttamente (modalità heartbeat + report).")

    last_ping = 0
    last_report = 0

    while True:
        try:
            now = time.time()

            # 1) Heartbeat (aggiorna UNA cella in Trade + append su Log)
            if now - last_ping >= PING_EVERY_SEC:
                px = prezzo_corrente()
                sheet.heartbeat(price=px, msg="loop ok")
                print(f"[HEARTBEAT] {datetime.now()}  {SYMBOL}={px}")
                last_ping = now

            # 2) Report periodico (legge SOLO il foglio Trade)
            if now - last_report >= REPORT_EVERY_SEC:
                aggiorna_report_e_invia()
                last_report = now

            # 3) (Spazio pronto per la logica segnali/ordini)
            #    Quando abiliti i trade:
            #    - usa sheet.log_open(...) all'apertura
            #    - usa sheet.log_close(...) alla chiusura (TP1/TP2/SL)
            #    La riga del trade verrà completata senza creare righe spazzatura.

        except Exception as e:
            print(f"[LOOP][ERRORE] {e}")

        time.sleep(1)  # tick leggero; il ritmo vero è gestito dai timer sopra


if __name__ == "__main__":
    main()
