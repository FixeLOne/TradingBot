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
C_RESET = '\033[0m'

# ==========================================
# 2. PARAMETRI DI BASE DEL BOT E PRECISIONE
# ==========================================
SYMBOL = 'SOLUSDT'           
PRODUCT_TYPE = 'USDT-FUTURES'
TIMEFRAME = '5m'
CAPITAL_TO_USE = 100.0       

PRICE_DECIMALS = 4           
SIZE_DECIMALS = 1            

INITIAL_BALANCE = 0.0        

# ==========================================
# 3. PARAMETRI DI STRATEGIA
# ==========================================
CRASH_DROP_PCT = 0.03       
VOL_SPIKE_MULT = 1.5        

TAKE_PROFIT_PCT = 0.025     
STOP_LOSS_PCT = 0.050       
MAX_HOLD_CANDLES = 12       

GRID_DROPS = [0.005, 0.015, 0.025]        
GRID_ALLOCATIONS = [0.30, 0.30, 0.40]     

# Variabili di Stato Globali
in_position = False
has_entered = False
candles_waited = 0
last_closed_candle_time = None

# ==========================================
# 4. MOTORE API BITGET (BARE-METAL)
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
            # Ignoriamo i warning silenziosi su ordini vuoti, stampiamo solo errori seri
            if "Order not exist" not in str(data.get('msg')):
                print(f"{C_RED}[API ERROR {endpoint}] {data.get('msg')}{C_RESET}")
        return data
    except Exception as e:
        print(f"{C_RED}[NETWORK ERROR] Impossibile contattare Bitget: {e}{C_RESET}")
        return None

def round_step(value, decimals):
    return f"{float(value):.{decimals}f}"

# ==========================================
# 5. LOGICA STRATEGICA
# ==========================================
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
            df.rename(columns={'base_vol': 'volume'}, inplace=True)
            return df
        return None
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
# 5. ESECUZIONE ORDINI (BATCH ORDER PLACEMENT)
# ==========================================
def place_dca_grid(current_price):
    print(f"\n{C_GREEN}=============================================================={C_RESET}")
    print(f"{C_GREEN}🚀 FLASH CRASH RILEVATO! LANCIO LA RETE IN BATCH! 🚀{C_RESET}")
    print(f"{C_GREEN}=============================================================={C_RESET}")
    print(f"Prezzo di innesco: {current_price} USDT\n")
    
    # 1. Prepariamo la scatola vuota che conterrà tutti gli ordini
    batch_order_list = []
    
    # 2. Riempiamo la scatola
    for i in range(len(GRID_DROPS)):
        order_price = current_price * (1 - GRID_DROPS[i])
        usd_amount = CAPITAL_TO_USE * GRID_ALLOCATIONS[i]
        
        sol_size = round_step(usd_amount / order_price, SIZE_DECIMALS)
        price_str = round_step(order_price, PRICE_DECIMALS)
        tp_str = round_step(order_price * (1 + TAKE_PROFIT_PCT), PRICE_DECIMALS)
        sl_str = round_step(order_price * (1 - STOP_LOSS_PCT), PRICE_DECIMALS)
        
        # Creiamo il singolo ordine e lo aggiungiamo alla lista
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
        print(f"{C_CYAN}[*] Preparato Ordine {i+1}:{C_RESET} {sol_size} SOL a {price_str}$")
        
    # 3. Spariamo tutta la scatola a Bitget in un millisecondo
    print(f"📡 Invio pacchetto Batch a Bitget...")
    res = bitget_request('POST', '/api/v2/mix/order/batch-orders', body=batch_order_list)
    
    if res and res.get('code') == '00000':
        print(f"{C_GREEN}[+] Rete DCA piazzata simultaneamente con successo!{C_RESET}")
    else:
        print(f"{C_RED}[-] Errore nell'invio del Batch Order.{C_RESET}")    
        print(f"\n{C_GREEN}=============================================================={C_RESET}")
    print(f"{C_GREEN}🚀 FLASH CRASH RILEVATO! LANCIO LA RETE DCA! 🚀{C_RESET}")
    print(f"{C_GREEN}=============================================================={C_RESET}")
    print(f"Prezzo di innesco: {current_price} USDT\n")
    
    for i in range(len(GRID_DROPS)):
        order_price = current_price * (1 - GRID_DROPS[i])
        usd_amount = CAPITAL_TO_USE * GRID_ALLOCATIONS[i]
        
        sol_size = round_step(usd_amount / order_price, SIZE_DECIMALS)
        price_str = round_step(order_price, PRICE_DECIMALS)
        tp_str = round_step(order_price * (1 + TAKE_PROFIT_PCT), PRICE_DECIMALS)
        sl_str = round_step(order_price * (1 - STOP_LOSS_PCT), PRICE_DECIMALS)
        
        body = {
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
        
        res = bitget_request('POST', '/api/v2/mix/order/place-order', body=body)
        if res and res.get('code') == '00000':
            print(f"{C_CYAN}[+] Ordine {i+1} Piazzato:{C_RESET} {sol_size} SOL a {price_str}$ | TP: {tp_str}$ | SL: {sl_str}$")
        time.sleep(0.2) 

# ==========================================
# 6. GESTIONE DASHBOARD ULTRA-DETTAGLIATA
# ==========================================
def update_dashboard(current_price, recent_high, current_drop_pct):
    global in_position, has_entered, INITIAL_BALANCE
    
    try:
        # 1. Recupero Saldo (Estremamente blindato contro i crash)
        acc_res = bitget_request('GET', '/api/v2/mix/account/accounts', params={'marginCoin': 'USDT', 'productType': PRODUCT_TYPE})
        usdt_balance = 0.0
        
        if acc_res and acc_res.get('code') == '00000':
            data_list = acc_res.get('data', [])
            if isinstance(data_list, list):
                for coin in data_list:
                    if coin.get('marginCoin') == 'USDT':
                        usdt_balance = float(coin.get('accountEquity', 0))
                        break
                    
        if INITIAL_BALANCE == 0.0 and usdt_balance > 0:
            INITIAL_BALANCE = usdt_balance
        session_profit = usdt_balance - INITIAL_BALANCE
        
        # 2. Recupero Posizioni
        pos_res = bitget_request('GET', '/api/v2/mix/position/single-position', 
                                 params={'marginCoin': 'USDT', 'productType': PRODUCT_TYPE, 'symbol': SYMBOL})
        pos_size = 0.0
        unrealized_pnl = 0.0
        entry_price = 0.0
        
        if pos_res and pos_res.get('code') == '00000':
            data_list = pos_res.get('data', [])
            if isinstance(data_list, list):
                for p in data_list:
                    size = float(p.get('total', 0))
                    if size > 0:
                        pos_size = size
                        unrealized_pnl = float(p.get('unrealizedPL', 0))
                        entry_price = float(p.get('averageOpenPrice', 0))
                        break 
        
        # 3. Recupero Ordini Aperti
        ord_res = bitget_request('GET', '/api/v2/mix/order/orders-pending', params={'productType': PRODUCT_TYPE, 'symbol': SYMBOL})
        open_orders_count = 0
        
        if ord_res and ord_res.get('code') == '00000':
            data_dict = ord_res.get('data', {})
            if isinstance(data_dict, dict):
                entrusted = data_dict.get('entrustedList', [])
                if isinstance(entrusted, list):
                    open_orders_count = len(entrusted)
        
        # 4. Logica Anti-Phantom Orders
        if in_position:
            if pos_size > 0:
                has_entered = True 
                
            if has_entered and pos_size == 0:
                print(f"\n{C_GREEN}[$$$] TAKE PROFIT O STOP LOSS COLPITO! Chiudo l'operazione. [$$$]{C_RESET}")
                
                bitget_request('POST', '/api/v2/mix/order/cancel-all-orders', 
                               body={'symbol': SYMBOL, 'productType': PRODUCT_TYPE, 'marginCoin': 'USDT'})
                
                print(f"{C_CYAN}[+] Ordini in eccesso cancellati. Resetto il bot in attesa.{C_RESET}")
                in_position = False
                has_entered = False
                return 
        
        # 5. STAMPA DEL TERMINALE GRAFICO
        os.system('cls' if os.name == 'nt' else 'clear') # Pulisce lo schermo per fare l'effetto "Dashboard live"
        
        print(f"{C_CYAN}=============================================================={C_RESET}")
        print(f"🤖 {C_YELLOW}BITGET FLASH-CRASH BOT{C_RESET} | ⏱️ Orario Server: {datetime.now().strftime('%H:%M:%S')}")
        print(f"{C_CYAN}=============================================================={C_RESET}")
        print(f"💰 {C_GREEN}IL TUO CONTO{C_RESET}")
        print(f"   Saldo Attuale : {usdt_balance:.2f} USDT")
        pnl_session_color = C_GREEN if session_profit >= 0 else C_RED
        print(f"   PnL Sessione  : {pnl_session_color}{session_profit:+.2f} USDT{C_RESET}")
        print(f"{C_CYAN}--------------------------------------------------------------{C_RESET}")
        print(f"📊 {C_GREEN}ANALISI MERCATO [{SYMBOL}]{C_RESET}")
        print(f"   Prezzo Attuale: {current_price:.4f}$")
        print(f"   Max Recente   : {recent_high:.4f}$ (Ultime candele)")
        
        drop_color = C_RED if current_drop_pct < -1.5 else C_YELLOW
        print(f"   Drop Valutato : {drop_color}{current_drop_pct:.2f}%{C_RESET} (Innesco al -{CRASH_DROP_PCT*100:.1f}%)")
        print(f"{C_CYAN}--------------------------------------------------------------{C_RESET}")
        
        if in_position:
            print(f"⏳ {C_CYAN}STATO: IN TRADE{C_RESET} (Minuti Trascorsi: {candles_waited*5}/{MAX_HOLD_CANDLES*5})")
            print(f"   Ordini Limite Attivi nel Book: {open_orders_count}")
            if pos_size > 0:
                pnl_c = C_GREEN if unrealized_pnl >= 0 else C_RED
                print(f"🔥 Size Aperta  : {pos_size} SOL a {entry_price:.4f}$")
                print(f"💵 Profitto Live: {pnl_c}{unrealized_pnl:+.3f} USDT{C_RESET}")
            else:
                print("🕸️ Rete di ordini piazzata. In attesa che il mercato ci colpisca...")
        else:
            print(f"🎯 {C_GREEN}STATO: IN AGGUATO{C_RESET} (Cerco candela di crollo...)")
        print(f"{C_CYAN}=============================================================={C_RESET}\n")
        
    except Exception as e:
        print(f"{C_RED}[!] Errore critico nella Dashboard. Il bot continua a funzionare. Dettagli: {e}{C_RESET}")

# ==========================================
# 7. CICLO PRINCIPALE
# ==========================================
def run_bot():
    global in_position, has_entered, candles_waited, last_closed_candle_time
    
    print(f"\n{C_CYAN}[*] Inizializzazione Motore Quantitativo Bitget...{C_RESET}")
    time.sleep(2)
    
    while True:
        try:
            df = get_market_data()
            if df is None:
                time.sleep(5)
                continue
            
            # Calcoli live per la Dashboard
            current_price = df['close'].iloc[-1]
            recent_high = df['high'].iloc[-4:-1].max() 
            current_drop_pct = ((current_price - recent_high) / recent_high) * 100
            
            # Aggiornamento Dashboard grafica
            update_dashboard(current_price, recent_high, current_drop_pct)
            
            current_closed_time = df.iloc[-2]['timestamp']
            
            if last_closed_candle_time is None or last_closed_candle_time != current_closed_time:
                last_closed_candle_time = current_closed_time
                
                if not in_position:
                    if check_flash_crash_signal(df):
                        place_dca_grid(current_price)
                        in_position = True
                        has_entered = False
                        candles_waited = 0
                
                else:
                    candles_waited += 1
                    
                    if candles_waited >= MAX_HOLD_CANDLES:
                        print(f"\n{C_YELLOW}[!] Tempo scaduto ({MAX_HOLD_CANDLES * 5} min). Forzo la chiusura.{C_RESET}")
                        
                        bitget_request('POST', '/api/v2/mix/order/cancel-all-orders', 
                                       body={'symbol': SYMBOL, 'productType': PRODUCT_TYPE, 'marginCoin': 'USDT'})
                        
                        pos_res = bitget_request('GET', '/api/v2/mix/position/single-position', 
                                                 params={'marginCoin': 'USDT', 'productType': PRODUCT_TYPE, 'symbol': SYMBOL})
                        
                        if pos_res and pos_res.get('code') == '00000' and pos_res.get('data'):
                            for p in pos_res['data']:
                                size_to_close = float(p.get('total', 0))
                                if size_to_close > 0:
                                    hold_side = p.get('holdSide', 'long')
                                    close_side = 'sell' if hold_side == 'long' else 'buy'
                                    
                                    close_body = {
                                        "symbol": SYMBOL,
                                        "productType": PRODUCT_TYPE,
                                        "marginMode": "crossed",
                                        "marginCoin": "USDT",
                                        "size": round_step(size_to_close, SIZE_DECIMALS),
                                        "side": close_side,
                                        "tradeSide": "close",
                                        "orderType": "market"
                                    }
                                    res_close = bitget_request('POST', '/api/v2/mix/order/place-order', body=close_body)
                                    if res_close and res_close.get('code') == '00000':
                                        print(f"{C_CYAN}[+] Posizione salvata a mercato (Size: {size_to_close}).{C_RESET}")
                                        time.sleep(2)
                        
                        in_position = False
                        has_entered = False
                        candles_waited = 0
            
            # Attende 15 secondi prima del prossimo refresh
            time.sleep(15)
            
        except Exception as e:
            print(f"{C_RED}[!] Errore nel Loop (Ritento tra 10s): {e}{C_RESET}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
