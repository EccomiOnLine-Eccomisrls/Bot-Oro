import os
import sys
import time
import gspread
from twilio.rest import Client
from binance.client import Client as BinanceClient
from google.oauth2.service_account import Credentials

# === Funzione per leggere variabili in modo sicuro ===
def get_env_var(name, fallback=None, required=False):
    val = os.environ.get(name, fallback)
    if required and not val:
        print(f"[ERRORE] Variabile '{name}' non trovata!")
    return val

# === Lettura variabili d'ambiente ===
# Twilio
twilio_sid = get_env_var('TWILIO_ACCOUNT_SID', required=True)
twilio_token = get_env_var('TWILIO_AUTH_TOKEN', required=True)
twilio_to = get_env_var('TWILIO_TO') or get_env_var('DESTINATION_NUMBER')
twilio_whatsapp = get_env_var('TWILIO_WHATSAPP_NUMBER')

# Binance
binance_api_key = get_env_var('BINANCE_API_KEY', required=True)
binance_api_secret = get_env_var('BINANCE_API_SECRET', required=True)

# Altri parametri
daily_loss_limit = float(get_env_var('DAILY_LOSS_LIMIT', "0"))
stop_loss = float(get_env_var('STOP_LOSS', "0"))
take_profit1 = float(get_env_var('TAKE_PROFIT1', "0"))
take_profit2 = float(get_env_var('TAKE_PROFIT2', "0"))
trade_size = float(get_env_var('TRADE_SIZE', "0"))
spreadsheet_id = get_env_var('SPREADSHEET_ID')
google_credentials_json = get_env_var('GOOGLE_CREDENTIALS')

# === Debug: stampa variabili caricate (solo primi/ultimi caratteri per sicurezza) ===
print(f"[DEBUG] TWILIO_ACCOUNT_SID: {str(twilio_sid)[:4]}...{str(twilio_sid)[-4:]}")
print(f"[DEBUG] TWILIO_AUTH_TOKEN: {str(twilio_token)[:4]}...{str(twilio_token)[-4:]}")
print(f"[DEBUG] TWILIO_TO: {twilio_to}")
print(f"[DEBUG] TWILIO_WHATSAPP_NUMBER: {twilio_whatsapp}")
print(f"[DEBUG] BINANCE_API_KEY: {str(binance_api_key)[:4]}...{str(binance_api_key)[-4:]}")

# === Blocca avvio se mancano variabili critiche ===
if not all([twilio_sid, twilio_token, binance_api_key, binance_api_secret, google_credentials_json]):
    print("[ERRORE] Variabili d'ambiente critiche mancanti. Controlla le impostazioni su Render.")
    sys.exit(1)

print("[INFO] Variabili d'ambiente caricate correttamente.")

# === Inizializza client Twilio ===
twilio_client = Client(twilio_sid, twilio_token)

# === Inizializza client Binance ===
binance_client = BinanceClient(api_key=binance_api_key, api_secret=binance_api_secret)

# === Inizializza Google Sheets ===
try:
    import json
    creds_dict = json.loads(google_credentials_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(spreadsheet_id).sheet1 if spreadsheet_id else None
    print("[INFO] Connessione a Google Sheets riuscita.")
except Exception as e:
    print(f"[ERRORE] Impossibile connettersi a Google Sheets: {e}")
    sheet = None

# === Funzione per inviare un messaggio WhatsApp via Twilio ===
def invia_messaggio(messaggio):
    try:
        numero_from = f"whatsapp:{twilio_whatsapp}" if twilio_whatsapp else None
        numero_to = f"whatsapp:{twilio_to}" if twilio_to else None
        if not numero_from or not numero_to:
            print("[ERRORE] Numeri Twilio non configurati correttamente.")
            return
        twilio_client.messages.create(
            body=messaggio,
            from_=numero_from,
            to=numero_to
        )
        print(f"[INFO] Messaggio inviato a {numero_to}")
    except Exception as e:
        print(f"[ERRORE] Invio messaggio fallito: {e}")

# === Funzione principale ===
def main():
    print("[INFO] Bot avviato.")
    invia_messaggio("Bot Oro avviato correttamente 🚀")
    # Qui va la tua logica di trading / monitoraggio
    while True:
        # ESEMPIO: stampa saldo USDT ogni 60 secondi
        try:
            balance = binance_client.get_asset_balance(asset='USDT')
            print(f"[INFO] Saldo USDT: {balance}")
        except Exception as e:
            print(f"[ERRORE] Impossibile recuperare saldo: {e}")
        time.sleep(60)

if __name__ == "__main__":
    main()
