import pandas as pd
import time
import os
from datetime import datetime
import hmac
import hashlib
import base64
import requests
import json
from dotenv import load_dotenv 

load_dotenv()

# ==========================================
# 1. CONFIGURAZIONE CHIAVI API BITGET
# ==========================================
API_KEY = os.getenv('API_KEY') or 'LA_TUA_API_KEY'
SECRET_KEY = os.getenv('SECRET_KEY') or 'IL_TUO_SECRET_KEY'
PASSPHRASE = os.getenv('PASSPHRASE') or 'LA_TUA_PASSPHRASE'

# Colori per la console
C_GREEN = '\033[92m'
C_RED = '\033[91m'
C_CYAN = '\033[96m'
C_YELLOW = '\033[93m'
C_WHITE = '\033[97m'
C_RESET = '\033[0m'

# ==========================================
# 2. PARAMETRI DI BASE DEL BOT E PRECISIONE
# ==========================================
SYMBOL = 'SOLUSDT'           
PRODUCT_TYPE = 'USDT-FUTURES'
TIMEFRAME = '15m'            
CAPITAL_TO_USE = 100.0       

PRICE_DECIMALS = 4           
SIZE_DECIMALS = 1            

INITIAL_BALANCE = 0.0        

# ==========================================
# 3. PARAMETRI DI STRATEGIA "I 3 CORVI ROSSI"
# ==========================================
CRASH_DROP_PCT = 0.015       
TAKE_PROFIT_PCT = 0.012      
STOP_LOSS_PCT = 0.060        
MAX_HOLD_CANDLES = 16        

GRID_DROPS = [0.005, 0.015, 0.025]        
GRID_ALLOCATIONS = [0.30, 0.30, 0.40]     

# Variabili di Stato Globali
in_position = False
has_entered = False
candles_waited = 0
last_closed_candle_time = None
current_drop_pct = 0.0  

# ==========================================
# 4. MOTORE API BITGET
# ==========================================
def sign(message, secret_key):
    mac = hmac.new(
        bytes(secret_key, encoding='utf-8'),
        bytes(message, encoding='utf-8'),
        digestmod=hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode()

def bitget_request(method, endpoint, params=None, body=None):
    base_url = "https://api.bitget.com"
    timestamp = str(int(time.time() * 1000))
    
    query_str = ""
    if params and method.upper() == 'GET':
        sorted_params = []
        for k in sorted(params.keys()):
            sorted_params.append(f"{k}={params[k]}")
        query_str = "?" + "&".join(sorted_params)
        
    body_str = json.dumps(body) if body else ""
    
    prehash = timestamp + method.upper() + endpoint + query_str + body_str
    signature = sign(prehash, SECRET_KEY)
    
    headers = {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
        "locale": "en-US"
    }
    
    url = base_url + endpoint + query_str
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers)
        else:
            response = requests.post(url, headers=headers, data=body_str)
            
        data = response.json()
        if data.get('code') != '00000':
            if "Order not exist" not in str(data.get('msg')):
                pass # Silenziamo gli errori minori in dashboard per non rovinare la grafica
        return data
    except Exception as e:
        return None

def round_step(value, decimals):
    return f"{float(value):.{decimals}f}"

def get_market_data():
    try:
        url = f"https://api.bitget.com/api/v2/mix/market/candles?symbol={SYMBOL}&productType={PRODUCT_TYPE}&granularity={TIMEFRAME}&limit=60"
        response = requests.get(url).json()
        
        if response.get('code') == '00000':
            bars = response['data']
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'base_vol', 'quote_vol'])
            for col in ['open', 'high', 'low', 'close', 'base_vol']:
                df[col] = df[col].astype(float)
            df['timestamp'] = pd.to_datetime(df['timestamp'].astype(float), unit='ms')
            return df
        return None
    except Exception as e:
        return None

# ==========================================
# 5. LOGICA STRATEGICA
# ==========================================
def check_flash_crash_signal(df):
    global current_drop_pct, valid_red_pattern
    try:
        c1 = df.iloc[-4]
        c2 = df.iloc[-3]
        c3 = df.iloc[-2]
        
        is_c1_red = c1['close'] < c1['open']
        is_c2_red = c2['close'] < c2['open']
        is_c3_red = c3['close'] < c3['open']
        
        valid_red_pattern = is_c1_red and is_c2_red and is_c3_red
        
        # Calcoliamo SEMPRE il vero calo rispetto al picco massimo delle ultime 3 candele
        peak_high = max(c1['high'], c2['high'], c3['high'])
        current_close = c3['close']
        drop_pct = (peak_high - current_close) / peak_high
        
        # Salviamo la statistica reale per la dashboard
        current_drop_pct = drop_pct * 100 
        
        # Ma diamo il via libera al trade SOLO se il pattern è valido
        if not valid_red_pattern:
            return False
            
        return drop_pct >= CRASH_DROP_PCT
        
    except Exception as e:
        return False
    
def place_dca_grid(current_price):
    batch_order_list = []
    for i in range(len(GRID_DROPS)):
        order_price = current_price * (1 - GRID_DROPS[i])
        usd_amount = CAPITAL_TO_USE * GRID_ALLOCATIONS[i]
        sol_size = round_step(usd_amount / order_price, SIZE_DECIMALS)
        price_str = round_step(order_price, PRICE_DECIMALS)
        tp_str = round_step(order_price * (1 + TAKE_PROFIT_PCT), PRICE_DECIMALS)
        sl_str = round_step(order_price * (1 - STOP_LOSS_PCT), PRICE_DECIMALS)
        
        single_order = {
            "symbol": SYMBOL,
            "productType": PRODUCT_TYPE,
            "marginMode": "crossed", 
            "marginCoin": "USDT",
            "size": sol_size,
            "price": price_str,
            "side": "buy",
            "tradeSide": "open",
            "orderType": "limit",
            "force": "gtc",
            "presetTakeProfitPrice": tp_str,
            "presetStopLossPrice": sl_str,
            "clientOid": f"FC_{int(time.time()*1000)}_{i}"
        }
        batch_order_list.append(single_order)
        
    bitget_request('POST', '/api/v2/mix/order/batch-orders', body=batch_order_list)

# ==========================================
# 6. MODULI API E DASHBOARD (Clean Architecture)
# ==========================================
def format_candle(c, label):
    is_red = c['close'] < c['open']
    color = C_RED if is_red else C_GREEN
    icon = "🔴" if is_red else "🟢"
    move = ((c['close'] - c['open']) / c['open']) * 100
    return f"{label}: {icon} {color}{c['open']:.4f} ➔ {c['close']:.4f} ({move:+.2f}%){C_RESET}"

def get_account_balance():
    """Scarica il saldo USDT disponibile."""
    res = bitget_request('GET', '/api/v2/mix/account/accounts', params={'marginCoin': 'USDT', 'productType': PRODUCT_TYPE})
    if res and res.get('code') == '00000':
        for coin in res.get('data', []):
            if coin.get('marginCoin') == 'USDT':
                return float(coin.get('accountEquity', 0))
    return 0.0

def get_position_info():
    """Ritorna: Successo_API, Size, PnL, Prezzo_Entrata"""
    res = bitget_request('GET', '/api/v2/mix/position/single-position', params={'marginCoin': 'USDT', 'productType': PRODUCT_TYPE, 'symbol': SYMBOL})
    if res and res.get('code') == '00000':
        for p in res.get('data', []):
            if float(p.get('total', 0)) > 0:
                return True, float(p.get('total', 0)), float(p.get('unrealizedPL', 0)), float(p.get('averageOpenPrice', 0))
        return True, 0.0, 0.0, 0.0
    return False, 0.0, 0.0, 0.0

def get_open_orders_count():
    """Conta quanti ordini limite sono aperti."""
    res = bitget_request('GET', '/api/v2/mix/order/orders-pending', params={'productType': PRODUCT_TYPE, 'symbol': SYMBOL})
    if res and res.get('code') == '00000':
        return len((res.get('data') or {}).get('entrustedList') or [])
    return 0

def render_dashboard_ui(usdt_balance, session_profit, df, current_price, pos_size, unrealized_pnl, entry_price, open_orders_count):
    """Gestisce ESCLUSIVAMENTE la stampa a schermo della grafica (UI)."""
    os.system('cls' if os.name == 'nt' else 'clear') 
    
    print(f"{C_CYAN}╭────────────────────────────────────────────────────────────╮{C_RESET}")
    print(f"{C_CYAN}│{C_WHITE} 🤖 BITGET QUANT: 3 CORVI ROSSI                 {C_YELLOW}⏱️ {datetime.now().strftime('%H:%M:%S')}{C_CYAN} │{C_RESET}")
    print(f"{C_CYAN}├────────────────────────────────────────────────────────────┤{C_RESET}")
    
    pnl_str = f"{C_GREEN if session_profit >= 0 else C_RED}{session_profit:+.2f} USDT{C_RESET}"
    print(f"{C_CYAN}│{C_RESET} 💰 CONTO : {C_WHITE}{usdt_balance:.2f} USDT{C_RESET}   |  PnL: {pnl_str}")
    print(f"{C_CYAN}│{C_RESET} 📊 ASSET : {C_WHITE}{SYMBOL}{C_RESET}        |  PREZZO: {C_WHITE}{current_price:.4f} ${C_RESET}")
    
    print(f"{C_CYAN}├────────────────────────────────────────────────────────────┤{C_RESET}")
    print(f"{C_CYAN}│{C_YELLOW} 🔍 ANALISI ULTIME 3 CANDELE ({TIMEFRAME}){C_RESET}")
    
    if df is not None and len(df) >= 4:
        print(f"{C_CYAN}│{C_RESET}    {format_candle(df.iloc[-4], 'T-3')}")
        print(f"{C_CYAN}│{C_RESET}    {format_candle(df.iloc[-3], 'T-2')}")
        print(f"{C_CYAN}│{C_RESET}    {format_candle(df.iloc[-2], 'T-1')}")
        print(f"{C_CYAN}│{C_RESET}")
        
        peak_high_ui = max(df.iloc[-4]['high'], df.iloc[-3]['high'], df.iloc[-2]['high'])
        
        if valid_red_pattern:
            drop_color = C_RED if current_drop_pct >= (CRASH_DROP_PCT*100) else C_YELLOW
            print(f"{C_CYAN}│{C_RESET}  🔥 SETUP : {C_RED}ATTIVO (3 Corvi Allineati){C_RESET}")
            print(f"{C_CYAN}│{C_RESET}  🏔️ PICCO : {C_WHITE}{peak_high_ui:.4f} ${C_RESET} (Inizio crollo)")
            print(f"{C_CYAN}│{C_RESET}  📉 DROP  : {drop_color}{-current_drop_pct:.2f}%{C_RESET} / Target: -{CRASH_DROP_PCT*100:.2f}%")
        else:
            print(f"{C_CYAN}│{C_RESET}  ❌ SETUP : {C_WHITE}Interrotto (Attesa 3 rosse consecutive){C_RESET}")
            print(f"{C_CYAN}│{C_RESET}  🏔️ PICCO : {C_WHITE}{peak_high_ui:.4f} ${C_RESET} (Massimo locale)")
            print(f"{C_CYAN}│{C_RESET}  📊 DIST. : {C_WHITE}{-current_drop_pct:.2f}%{C_RESET} / Target: -{CRASH_DROP_PCT*100:.2f}%")
    else:
        print(f"{C_CYAN}│{C_RESET}  Caricamento dati candele in corso...")
        
    print(f"{C_CYAN}├────────────────────────────────────────────────────────────┤{C_RESET}")
    
    if in_position:
        print(f"{C_CYAN}│{C_RESET} 🎯 STATO : {C_CYAN}IN TRADE ({candles_waited}/{MAX_HOLD_CANDLES}){C_RESET} | Ordini Book: {open_orders_count}")
        if pos_size > 0:
            pnl_c = C_GREEN if unrealized_pnl >= 0 else C_RED
            print(f"{C_CYAN}│{C_RESET} 🔥 POSIZ : {C_WHITE}{pos_size} SOL @ {entry_price:.4f}{C_RESET} | Live PnL: {pnl_c}{unrealized_pnl:+.3f} ${C_RESET}")
    else:
        print(f"{C_CYAN}│{C_RESET} 🎯 STATO : {C_GREEN}🟢 IN AGGUATO...{C_RESET}")
        
    print(f"{C_CYAN}╰────────────────────────────────────────────────────────────╯{C_RESET}\n")

def update_dashboard(df, current_price):
    """Funzione principale (Orchestratore) che unisce i dati e chiama la grafica."""
    global in_position, has_entered, INITIAL_BALANCE
    
    try:
        # 1. Raccolta Dati
        usdt_balance = get_account_balance()
        if INITIAL_BALANCE == 0.0 and usdt_balance > 0: 
            INITIAL_BALANCE = usdt_balance
        session_profit = usdt_balance - INITIAL_BALANCE
        
        pos_api_success, pos_size, unrealized_pnl, entry_price = get_position_info()
        open_orders_count = get_open_orders_count()
        
        # 2. Controllo Chiusura Ordini (Reset TP/SL)
        if in_position and pos_api_success:
            if pos_size > 0: 
                has_entered = True 
            if has_entered and pos_size == 0:
                print(f"\n{C_GREEN}╭─────────────────────────────────────────────────────────────╮{C_RESET}")
                print(f"{C_GREEN}│ [$$$] TARGET COLPITO! Reset ordini...                       │{C_RESET}")
                print(f"{C_GREEN}╰─────────────────────────────────────────────────────────────╯{C_RESET}")
                bitget_request('POST', '/api/v2/mix/order/cancel-all-orders', body={'symbol': SYMBOL, 'productType': PRODUCT_TYPE, 'marginCoin': 'USDT'})
                in_position, has_entered = False, False
                return 

        # 3. Chiamata Grafica
        render_dashboard_ui(usdt_balance, session_profit, df, current_price, pos_size, unrealized_pnl, entry_price, open_orders_count)
        
    except Exception as e:
        print(f"{C_RED}[!] Errore nell'aggiornamento della Dashboard: {e}{C_RESET}")
        
# ==========================================
# 7. CICLO PRINCIPALE
# ==========================================
def run_bot():
    global in_position, has_entered, candles_waited, last_closed_candle_time
    time.sleep(2)
    
    while True:
        try:
            df = get_market_data()
            if df is None:
                print(f"{C_YELLOW}[!] In attesa di ricevere i dati da Bitget...{C_RESET}")
                time.sleep(5)
                continue
            
            current_price = df['close'].iloc[-1]
            signal_triggered = check_flash_crash_signal(df)
            
            # Chiamata alla nuova dashboard grafica passando il Dataframe per leggere le candele
            update_dashboard(df, current_price)
            
            current_closed_time = df.iloc[-2]['timestamp']
            
            if last_closed_candle_time is None or last_closed_candle_time != current_closed_time:
                last_closed_candle_time = current_closed_time
                
                if not in_position:
                    if signal_triggered:
                        place_dca_grid(current_price)
                        in_position = True
                        has_entered = False
                        candles_waited = 0
                
                else:
                    candles_waited += 1
                    if candles_waited >= MAX_HOLD_CANDLES:
                        bitget_request('POST', '/api/v2/mix/order/cancel-all-orders', body={'symbol': SYMBOL, 'productType': PRODUCT_TYPE, 'marginCoin': 'USDT'})
                        pos_res = bitget_request('GET', '/api/v2/mix/position/single-position', params={'marginCoin': 'USDT', 'productType': PRODUCT_TYPE, 'symbol': SYMBOL})
                        
                        if pos_res and pos_res.get('code') == '00000' and pos_res.get('data'):
                            for p in pos_res['data']:
                                size_to_close = float(p.get('total', 0))
                                if size_to_close > 0:
                                    close_side = 'sell' if p.get('holdSide', 'long') == 'long' else 'buy'
                                    bitget_request('POST', '/api/v2/mix/order/place-order', body={
                                        "symbol": SYMBOL, "productType": PRODUCT_TYPE, "marginMode": "crossed",
                                        "marginCoin": "USDT", "size": round_step(size_to_close, SIZE_DECIMALS),
                                        "side": close_side, "tradeSide": "close", "orderType": "market"
                                    })
                        in_position = False
                        has_entered = False
                        candles_waited = 0
            
            time.sleep(15)
            
        except Exception as e:
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
