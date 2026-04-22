import ccxt
import pandas as pd
import time
from datetime import datetime

# ==========================================
# 1. CONFIGURAZIONE CHIAVI API BITGET
# ==========================================
API_KEY = 'LA_TUA_API_KEY'
SECRET_KEY = 'IL_TUO_SECRET_KEY'
PASSPHRASE = 'LA_TUA_PASSPHRASE'

# Colori per la console
C_GREEN = '\033[92m'
C_RED = '\033[91m'
C_CYAN = '\033[96m'
C_YELLOW = '\033[93m'
C_RESET = '\033[0m'

exchange = ccxt.bitget({
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'password': PASSPHRASE,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap', # Usa i Futures Perpetui (UTA)
    }
})

# ==========================================
# 2. PARAMETRI DI BASE DEL BOT
# ==========================================
SYMBOL = 'SOL/USDT:USDT'
TIMEFRAME = '5m'
CAPITAL_TO_USE = 100.0  # USDT totali da destinare a questa operazione
INITIAL_BALANCE = 0.0   # Non toccare, valorizzato in automatico

# ==========================================
# 3. PARAMETRI DI STRATEGIA (TOTALMENTE MODIFICABILI)
# ==========================================

# -- Condizioni per scovare il Flash Crash --
CRASH_DROP_PCT = 0.03       # Crollo del 3% dal massimo recente
VOL_SPIKE_MULT = 1.5        # Volume superiore del 50% rispetto alla media a 50 periodi

# -- Gestione Rischio e Uscita --
TAKE_PROFIT_PCT = 0.025     # +2.5% di profitto per singolo ordine limite
STOP_LOSS_PCT = 0.050       # -5.0% di stop loss rigido per singolo ordine
MAX_HOLD_CANDLES = 12       # 12 candele da 5m = 1 Ora esatta prima di uscire

# -- Impostazioni Griglia DCA --
GRID_DROPS = [0.005, 0.015, 0.025]        # Distanza ordini limite: -0.5%, -1.5%, -2.5% dal prezzo attuale
GRID_ALLOCATIONS = [0.30, 0.30, 0.40]     # Divisione capitale: 30%, 30%, 40%

# Variabili di Stato Globali
in_position = False
has_entered = False
candles_waited = 0
last_closed_candle_time = None

# ==========================================
# 4. LOGICA DI ESTRAZIONE DATI E SEGNALI
# ==========================================
def get_market_data():
    try:
        bars = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=60)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"{C_RED}[!] Errore download dati candele: {e}{C_RESET}")
        return None

def check_flash_crash_signal(df):
    last_closed = df.iloc[-2] 
    recent_high = df['high'].iloc[-4:-1].max() 
    
    is_crash = last_closed['low'] < (recent_high * (1 - CRASH_DROP_PCT))
    is_green = last_closed['close'] > last_closed['open']
    
    vol_ma50 = df['volume'].iloc[-52:-2].mean()
    is_volume_spike = last_closed['volume'] > (VOL_SPIKE_MULT * vol_ma50)
    
    return is_crash and is_green and is_volume_spike

# ==========================================
# 5. ESECUZIONE ORDINI (ORDER PLACEMENT)
# ==========================================
def place_dca_grid(current_price):
    print(f"\n{C_GREEN}=========================================={C_RESET}")
    print(f"{C_GREEN}🚀 FLASH CRASH RILEVATO! LANCIO LA RETE! 🚀{C_RESET}")
    print(f"{C_GREEN}=========================================={C_RESET}")
    print(f"Prezzo di innesco: {current_price} USDT\n")
    
    for i in range(len(GRID_DROPS)):
        order_price = current_price * (1 - GRID_DROPS[i])
        usd_amount = CAPITAL_TO_USE * GRID_ALLOCATIONS[i]
        
        # Manteniamo le stringhe per rispettare al 100% la precisione richiesta da Bitget
        sol_amount_str = exchange.amount_to_precision(SYMBOL, usd_amount / order_price)
        price_str = exchange.price_to_precision(SYMBOL, order_price)
        
        tp_price_str = exchange.price_to_precision(SYMBOL, order_price * (1 + TAKE_PROFIT_PCT))
        sl_price_str = exchange.price_to_precision(SYMBOL, order_price * (1 - STOP_LOSS_PCT))
        
        # SINTASSI CCXT UNIFICATA PER TP/SL: Pienamente compatibile e sicura
        params = {
            'takeProfit': {
                'type': 'market',
                'triggerPrice': tp_price_str,
            },
            'stopLoss': {
                'type': 'market',
                'triggerPrice': sl_price_str,
            }
        }
        
        try:
            # Passiamo le STRINGHE direttamente a CCXT per evitare errori di floating point
            exchange.create_order(
                symbol=SYMBOL,
                type='limit',
                side='buy',
                amount=sol_amount_str,
                price=price_str,
                params=params
            )
            print(f"{C_CYAN}[+] Ordine {i+1} Piazzato:{C_RESET} {sol_amount_str} SOL a {price_str}$ | TP: {tp_price_str}$ | SL: {sl_price_str}$")
            time.sleep(0.3) 
        except Exception as e:
            print(f"{C_RED}[-] Errore invio ordine {i+1}: {e}{C_RESET}")

# ==========================================
# 6. DASHBOARD E LOGGING
# ==========================================
def update_dashboard():
    global in_position, has_entered, INITIAL_BALANCE
    
    try:
        balance = exchange.fetch_balance()
        usdt_balance = float(balance['total'].get('USDT', 0.0))
        if INITIAL_BALANCE == 0.0:
            INITIAL_BALANCE = usdt_balance
        session_profit = usdt_balance - INITIAL_BALANCE
        
        positions = exchange.fetch_positions([SYMBOL])
        open_orders = exchange.fetch_open_orders(SYMBOL)
        
        pos_size = 0.0
        unrealized_pnl = 0.0
        entry_price = 0.0
        
        if positions and len(positions) > 0:
            pos = positions[0]
            pos_size = float(pos.get('contracts', 0.0))
            if pos_size > 0:
                unrealized_pnl = float(pos.get('unrealizedPnl', 0.0))
                entry_price = float(pos.get('entryPrice', 0.0))
        
        # Logica Anti-Phantom Orders
        if in_position:
            if pos_size > 0:
                has_entered = True 
                
            if has_entered and pos_size == 0:
                print(f"\n{C_GREEN}[$] TAKE PROFIT O STOP LOSS COLPITO! Chiudo l'operazione.[$]{C_RESET}")
                exchange.cancel_all_orders(SYMBOL)
                print(f"{C_CYAN}[+] Ordini fantasma cancellati. Resetto il bot.{C_RESET}")
                in_position = False
                has_entered = False
                return 
        
        print(f"\n{C_YELLOW}--- BITGET UTA BOT | {datetime.now().strftime('%H:%M:%S')} ---{C_RESET}")
        print(f"💰 Saldo: {usdt_balance:.2f} USDT (PnL Sessione: {C_GREEN if session_profit >= 0 else C_RED}{session_profit:+.2f}${C_RESET})")
        
        if in_position:
            print(f"⏳ Stato: {C_CYAN}IN TRADE{C_RESET} (Candela {candles_waited}/{MAX_HOLD_CANDLES}) | 📋 Ordini attivi: {len(open_orders)}")
            if pos_size > 0:
                pnl_c = C_GREEN if unrealized_pnl >= 0 else C_RED
                print(f"🔥 Size: {pos_size} SOL @ {entry_price:.3f}$ | PnL: {pnl_c}{unrealized_pnl:+.3f}${C_RESET}")
            else:
                print("🕸️ Griglia piazzata. Nessun ordine colpito finora.")
        else:
            print(f"🎯 Stato: {C_GREEN}IN AGGUATO{C_RESET} | Nessuna operazione in corso.")
            
        print("-" * 35)
        
    except Exception as e:
        print(f"{C_RED}[!] Errore lettura dati account: {e}{C_RESET}")

# ==========================================
# 7. CICLO PRINCIPALE
# ==========================================
def run_bot():
    global in_position, has_entered, candles_waited, last_closed_candle_time
    
    print(f"\n{C_CYAN}[*] Inizializzazione Bot su Bitget UTA...{C_RESET}")
    exchange.load_markets()
    update_dashboard() 
    
    while True:
        try:
            df = get_market_data()
            if df is None:
                time.sleep(5)
                continue
            
            current_closed_time = df.iloc[-2]['timestamp']
            
            if last_closed_candle_time != current_closed_time:
                last_closed_candle_time = current_closed_time
                current_price = df['close'].iloc[-1]
                
                if not in_position:
                    if check_flash_crash_signal(df):
                        place_dca_grid(current_price)
                        in_position = True
                        has_entered = False
                        candles_waited = 0
                
                else:
                    candles_waited += 1
                    
                    if candles_waited >= MAX_HOLD_CANDLES:
                        print(f"\n{C_YELLOW}[!] Tempo scaduto ({MAX_HOLD_CANDLES * 5} min). Chiusura forzata.{C_RESET}")
                        exchange.cancel_all_orders(SYMBOL)
                        
                        positions = exchange.fetch_positions([SYMBOL])
                        if positions and len(positions) > 0:
                            size_to_close = float(positions[0].get('contracts', 0.0))
                            if size_to_close > 0:
                                # Approccio ultra-sicuro per Bitget (supporta Hedge e One-Way)
                                pos_side = positions[0].get('side', 'long')
                                side_to_close = 'sell' if pos_side == 'long' else 'buy'
                                
                                exchange.create_order(
                                    symbol=SYMBOL, 
                                    type='market', 
                                    side=side_to_close, 
                                    amount=size_to_close,
                                    params={'reduceOnly': True}
                                )
                                print(f"{C_CYAN}[+] Posizione chiusa a mercato (Size: {size_to_close}).{C_RESET}")
                        
                        in_position = False
                        has_entered = False
                        candles_waited = 0
            
            update_dashboard()
            time.sleep(15)
            
        except Exception as e:
            print(f"{C_RED}[!] Errore critico nel Loop: {e}{C_RESET}")
            time.sleep(10)

            # ==========================================
# FUNZIONE DI TEST IMMEDIATO (RICHIESTA)
# ==========================================
def test_api_connection():
    print(f"\n{C_YELLOW}[!] AVVIO TEST DI CONNESSIONE E ORDINE IMMEDIATO...{C_RESET}")
    try:
        test_size = 0.1 
        print(f"[*] Apertura ordine di test: {test_size} SOL a mercato...")
        
        # Apertura Long a Mercato
        order = exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='buy',
            amount=test_size
        )
        print(f"{C_GREEN}[+] Ordine aperto con successo! ID: {order['id']}{C_RESET}")
        
        print(f"[*] Attesa di 60 secondi prima della chiusura...")
        time.sleep(60)
        
        # Chiusura a Mercato
        print(f"[*] Chiusura ordine di test...")
        exchange.create_order(
            symbol=SYMBOL,
            type='market',
            side='sell',
            amount=test_size,
            params={'reduceOnly': True}
        )
        print(f"{C_GREEN}[+] Test completato. Ordine chiuso correttamente.{C_RESET}")
        print(f"{C_CYAN}[*] Il bot entrerà ora in modalità 'Cacciatore'.{C_RESET}\n")
        
    except Exception as e:
        print(f"{C_RED}[!] ERRORE DURANTE IL TEST API: {e}{C_RESET}")
        print(f"{C_RED}[!] Verifica permessi API (Futures/UTA) e Saldo USDT.{C_RESET}")
        exit() # Ferma il bot se il test fallisce

if __name__ == "__main__":
    run_bot()
