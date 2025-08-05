import os
import sys

def get_env_var(name, default=None, required=False):
    """Recupera una variabile d'ambiente e gestisce i valori mancanti."""
    value = os.environ.get(name, default)
    if required and value is None:
        print(f"[ERRORE] Variabile d'ambiente '{name}' non trovata!")
    return value

# === TWILIO ===
twilio_to = get_env_var('TWILTO_TO') or get_env_var('DESTINATION_NUMBER')  # fallback
twilio_sid = get_env_var('TWILIO_SID', required=True)
twilio_token = get_env_var('TWILIO_AUTH_TOKEN', required=True)

# === BINANCE ===
binance_api_key = get_env_var('BINANCE_API_KEY', required=True)
binance_api_secret = get_env_var('BINANCE_API_SECRET', required=True)

# === LIMITI ===
daily_loss_limit = get_env_var('DAILY_LOSS_LIMIT', default="0")  # valore di default 0

# === GOOGLE ===
google_credentials = get_env_var('GOOGLE_CREDENTIALS', required=True)

# Verifica finale: se mancano variabili critiche, esci
if not all([twilio_to, twilio_sid, twilio_token, binance_api_key, binance_api_secret, google_credentials]):
    print("[ERRORE] Variabili d'ambiente critiche mancanti. Controlla le impostazioni su Render.")
    sys.exit(1)

print("[INFO] Variabili d'ambiente caricate correttamente.")
