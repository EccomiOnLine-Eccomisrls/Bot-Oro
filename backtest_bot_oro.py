"""
BACKTEST BOT ORO (paper trading)
- Strumento: PAXGUSDT (cambia in XAUTUSDT se disponibile su Binance)
- Regole: SL -0.5%, TP1 +1% (chiude 50%), TP2 +2% (chiude il resto)
- Max posizioni contemporanee: 5
- Capitale per ingresso: 1 USDT (auto-adeguamento a minNotional simulato)
- Fee: 0.10% taker per lato (configurabile)
- Dati: candele storiche Binance (pubbliche), timeframe 5m per default
- Output: KPI in console + (opzionale) scrittura su Google Sheet (sheet "Report" + "Trade")
  Variabili d’ambiente per Google Sheet:
    - GOOGLE_CREDENTIALS  (JSON intero della service account)
    - SPREADSHEET_ID      (id del file)
  Variabili opzionali:
    - BACKTEST_DAYS       (default 30)
    - SYMBOL              (default "PAXGUSDT")
    - TIMEFRAME           (default "5m")  es. "1m","5m","15m"
    - BASE_NOTIONAL_USDT  (default 1.0)
"""

import os
import math
import json
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

import numpy as np
from binance.client import Client

# --- Parametri bot (puoi anche metterli via env) ---
SYMBOL = os.getenv("SYMBOL", "PAXGUSDT")
TIMEFRAME = os.getenv("TIMEFRAME", "5m")
BACKTEST_DAYS = int(os.getenv("BACKTEST_DAYS", "30"))

BASE_NOTIONAL_USDT = float(os.getenv("BASE_NOTIONAL_USDT", "1.0"))
MAX_OPEN_POS = 5

SL_PCT  = 0.005   # 0.5%
TP1_PCT = 0.010   # 1.0%
TP2_PCT = 0.020   # 2.0%
TP1_PARTIAL = 0.50
TAKER_FEE = 0.001  # 0.10% per lato

# Simuliamo un minNotional realistico di Binance sul simbolo (se 1 USDT < minNotional, alziamo)
# Nota: per semplicità lo stimiamo a 10 USDT; se vuoi leggere il vero filtro: client.get_symbol_info
SIM_MIN_NOTIONAL = 10.0

# --- Google Sheet (opzionale) ---
USE_SHEETS = False
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "")
if SPREADSHEET_ID and GOOGLE_CREDENTIALS:
    USE_SHEETS = True
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
    except Exception:
        USE_SHEETS = False
        print("[WARN] gspread non disponibile: niente output su Google Sheet.")

# ------------------ Utility Sheet ------------------
class SheetWriter:
    def __init__(self, creds_json_str: str, spreadsheet_id: str):
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(creds_json_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_id)
        # crea sheet se mancano
        self._ensure_ws("Report", [["KPI", "Valore"]])
        self._ensure_ws("Trade", [["Data/Ora", "Azione", "Prezzo", "Qty", "PNL_USDT", "Note"]])

    def _ensure_ws(self, title: str, header: List[List[Any]]):
        try:
            self.sh.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=2000, cols=20)
            ws.update("A1", header)

    def append_trade(self, row: List[Any]):
        ws = self.sh.worksheet("Trade")
        ws.append_row(row, value_input_option="USER_ENTERED")

    def write_kpis(self, kpis: Dict[str, Any]):
        ws = self.sh.worksheet("Report")
        data = [["KPI", "Valore"]]
        for k, v in kpis.items():
            data.append([k, v])
        ws.clear()
        ws.update("A1", data, value_input_option="USER_ENTERED")

# ------------------ Dati storici ------------------
def load_klines(symbol: str, interval: str, days: int):
    client = Client(api_key="", api_secret="")  # public endpoints
    kl = client.get_historical_klines(symbol, interval, f"{days} day ago UTC")
    # Binance ritorna: [open_time, open, high, low, close, volume, close_time, ...]
    arr = []
    for r in kl:
        arr.append({
            "ts": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        })
    return arr

# ------------------ Strategia d’ingresso ------------------
def ma_cross_signal(closes: np.ndarray, fast: int = 20, slow: int = 50) -> Optional[str]:
    """Ritorna 'BUY' quando avviene un incrocio fast>slow (cross up) nell’ultima barra."""
    if len(closes) < slow + 2:
        return None
    fast_ma_prev = np.mean(closes[-(fast+1):-1])
    slow_ma_prev = np.mean(closes[-(slow+1):-1])
    fast_ma_now  = np.mean(closes[-fast:])
    slow_ma_now  = np.mean(closes[-slow:])
    if fast_ma_prev <= slow_ma_prev and fast_ma_now > slow_ma_now:
        return "BUY"
    return None

# ------------------ Simulazione posizioni ------------------
@dataclass
class Position:
    open_time: int
    entry: float
    qty: float
    remaining_qty: float
    tp1: float
    tp2: float
    sl: float
    closed: bool = False
    close_time: Optional[int] = None
    pnl_usdt: float = 0.0
    took_tp1: bool = False

def round_step(value: float, step: float) -> float:
    return math.floor(value / step) * step

def simulate_backtest(candles: List[Dict[str, float]],
                      base_notional: float,
                      max_open: int,
                      sl_pct: float,
                      tp1_pct: float,
                      tp2_pct: float,
                      tp1_partial: float,
                      taker_fee: float) -> Dict[str, Any]:

    closes = np.array([c["close"] for c in candles], dtype=float)
    positions: List[Position] = []
    trade_log: List[Dict[str, Any]] = []

    # Stima step size/precisione (semplificata): 1e-5 di qty
    QTY_STEP = 1e-5

    # KPI trackers
    realized_pnl = 0.0
    equity = 0.0
    peak_equity = 0.0
    max_drawdown = 0.0
    wins = 0
    losses = 0

    for i, c in enumerate(candles):
        px = c["close"]

        # 1) Gestione posizioni aperte (SL/TP)
        for p in positions:
            if p.closed:
                continue
            # TP1
            if not p.took_tp1 and px >= p.tp1:
                qty_close = p.remaining_qty * tp1_partial
                qty_close = round_step(qty_close, QTY_STEP)
                if qty_close > 0:
                    gross = qty_close * (px - p.entry)
                    fees = (p.entry + px) * qty_close * taker_fee
                    pnl = gross - fees
                    realized_pnl += pnl
                    p.remaining_qty -= qty_close
                    p.took_tp1 = True
                    trade_log.append({
                        "ts": c["ts"], "action": "TP1 partial",
                        "price": px, "qty": qty_close, "pnl": pnl
                    })
            # SL (sul restante)
            if not p.closed and px <= p.sl:
                qty_close = p.remaining_qty
                qty_close = round_step(qty_close, QTY_STEP)
                if qty_close > 0:
                    gross = qty_close * (px - p.entry)
                    fees = (p.entry + px) * qty_close * taker_fee
                    pnl = gross - fees
                    realized_pnl += pnl
                    p.remaining_qty = 0.0
                    p.closed = True
                    p.close_time = c["ts"]
                    p.pnl_usdt += pnl
                    trade_log.append({
                        "ts": c["ts"], "action": "SL close",
                        "price": px, "qty": qty_close, "pnl": pnl
                    })
            # TP2 (sul restante)
            if not p.closed and px >= p.tp2:
                qty_close = p.remaining_qty
                qty_close = round_step(qty_close, QTY_STEP)
                if qty_close > 0:
                    gross = qty_close * (px - p.entry)
                    fees = (p.entry + px) * qty_close * taker_fee
                    pnl = gross - fees
                    realized_pnl += pnl
                    p.remaining_qty = 0.0
                    p.closed = True
                    p.close_time = c["ts"]
                    p.pnl_usdt += pnl
                    trade_log.append({
                        "ts": c["ts"], "action": "TP2 close",
                        "price": px, "qty": qty_close, "pnl": pnl
                    })

        # 2) Segnale ingresso (una nuova posizione per barra se segnale e cap non superato)
        open_count = sum(0 if p.closed else 1 for p in positions)
        signal = ma_cross_signal(closes[:i+1])
        if signal == "BUY" and open_count < max_open:
            notional = base_notional
            # adegua al minNotional simulato
            if notional < SIM_MIN_NOTIONAL:
                notional = SIM_MIN_NOTIONAL
            qty = notional / px
            qty = round_step(qty, QTY_STEP)
            if qty > 0:
                tp1 = px * (1 + tp1_pct)
                tp2 = px * (1 + tp2_pct)
                sl  = px * (1 - sl_pct)
                # fee ingresso (solo per calcolo PnL cumulato)
                entry_fee = px * qty * taker_fee
                realized_pnl -= entry_fee  # contabilizzo costo d’ingresso
                new_pos = Position(
                    open_time=c["ts"], entry=px, qty=qty, remaining_qty=qty,
                    tp1=tp1, tp2=tp2, sl=sl
                )
                positions.append(new_pos)
                trade_log.append({
                    "ts": c["ts"], "action": "OPEN", "price": px, "qty": qty, "pnl": -entry_fee
                })

        # 3) Equity / drawdown su base PnL realizzato
        equity = realized_pnl
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity)
        max_drawdown = max(max_drawdown, dd)

    # Chiudi eventuali posizioni rimaste alla fine al prezzo dell’ultima barra (mark-to-market)
    last_px = candles[-1]["close"]
    for p in positions:
        if not p.closed and p.remaining_qty > 0:
            qty_close = round_step(p.remaining_qty, QTY_STEP)
            gross = qty_close * (last_px - p.entry)
            fees = (p.entry + last_px) * qty_close * taker_fee
            pnl = gross - fees
            realized_pnl += pnl
            p.closed = True
            p.remaining_qty = 0.0
            p.close_time = candles[-1]["ts"]
            p.pnl_usdt += pnl
            trade_log.append({
                "ts": candles[-1]["ts"], "action": "FORCE CLOSE",
                "price": last_px, "qty": qty_close, "pnl": pnl
            })

    # KPI finali
    closed = [p for p in positions if p.closed]
    for p in closed:
        if p.pnl_usdt >= 0:
            wins += 1
        else:
            losses += 1

    total_trades = len([t for t in trade_log if t["action"] == "OPEN"])
    win_rate = (wins / max(1, len(closed))) * 100.0
    avg_win = np.mean([p.pnl_usdt for p in closed if p.pnl_usdt > 0]) if wins else 0.0
    avg_loss = np.mean([p.pnl_usdt for p in closed if p.pnl_usdt < 0]) if losses else 0.0
    expectancy = (win_rate/100.0) * avg_win + (1 - win_rate/100.0) * avg_loss

    kpis = {
        "Symbol": SYMBOL,
        "Timeframe": TIMEFRAME,
        "Giorni": BACKTEST_DAYS,
        "Trade aperti": total_trades,
        "Posizioni chiuse": len(closed),
        "Win rate %": round(win_rate, 2),
        "PNL totale (USDT)": round(realized_pnl, 2),
        "Avg win (USDT)": round(avg_win, 3),
        "Avg loss (USDT)": round(avg_loss, 3),
        "Expectancy per trade (USDT)": round(expectancy, 3),
        "Max drawdown (USDT)": round(max_drawdown, 2),
        "Regole": f"SL {SL_PCT*100:.1f}%, TP1 {TP1_PCT*100:.1f}% ({int(TP1_PARTIAL*100)}%), TP2 {TP2_PCT*100:.1f}%, MaxPos {MAX_OPEN_POS}, Fee {TAKER_FEE*100:.2f}%",
    }

    return {
        "kpis": kpis,
        "positions": positions,
        "trade_log": trade_log,
    }

# ------------------ Main ------------------
def main():
    print(f"[INFO] Scarico candele {SYMBOL} {TIMEFRAME} ultimi {BACKTEST_DAYS} giorni…")
    candles = load_klines(SYMBOL, TIMEFRAME, BACKTEST_DAYS)
    if len(candles) < 100:
        raise RuntimeError("Pochi dati storici recuperati.")

    print("[INFO] Avvio simulazione…")
    result = simulate_backtest(
        candles=candles,
        base_notional=BASE_NOTIONAL_USDT,
        max_open=MAX_OPEN_POS,
        sl_pct=SL_PCT,
        tp1_pct=TP1_PCT,
        tp2_pct=TP2_PCT,
        tp1_partial=TP1_PARTIAL,
        taker_fee=TAKER_FEE,
    )

    kpis = result["kpis"]
    print("\n=== KPI Backtest ===")
    for k, v in kpis.items():
        print(f"- {k}: {v}")

    # Output su Google Sheet (opzionale)
    if USE_SHEETS:
        try:
            sw = SheetWriter(GOOGLE_CREDENTIALS, SPREADSHEET_ID)
            sw.write_kpis(kpis)
            # Trade log sintetico
            rows = []
            for t in result["trade_log"]:
                rows.append([t["ts"], t["action"], t["price"], t["qty"], round(t["pnl"], 4), "backtest"])
            # append batch in blocchi per non saturare
            ws = sw.sh.worksheet("Trade")
            cell_start = ws.row_count + 1
            # Se il foglio è appena creato abbiamo header in A1, append in fondo
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            print("[OK] Report e trade log scritti su Google Sheet.")
        except Exception as e:
            print(f"[WARN] Scrittura su Google Sheet fallita: {e}")

if __name__ == "__main__":
    main()
