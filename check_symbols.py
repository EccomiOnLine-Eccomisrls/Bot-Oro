from binance.client import Client
import os

# Legge le chiavi dalle variabili di ambiente
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

client = Client(api_key, api_secret)

# Recupera tutti i simboli
exchange_info = client.get_exchange_info()
symbols = [s['symbol'] for s in exchange_info['symbols']]

# Filtra i simboli che possono indicare l'oro
gold_symbols = [s for s in symbols if "GOLD" in s or "XAU" in s or "PAXG" in s]

print("Simboli trovati legati all'oro:")
for sym in gold_symbols:
    print(sym)
