"""
ÜLGEN v30 - Premium Web Dashboard Server
Flask backend for real-time trading bot visualization
"""

from flask import Flask, jsonify, render_template, Response
from flask_cors import CORS
import json
import os
import time
from datetime import datetime
import threading

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# --- Matrix Terminal Logging ---
LOG_BUFFER = []
MAX_LOGS = 100

# --- Helpers ---
def _fetch_external_json(url, timeout=5):
    """Reliable fetch with browser-like headers"""
    import requests
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"External API Error ({url}): {e}")
    return None

def capture_log(message, type="INFO", quiet_console=False):
    """Append log to buffer with timestamp"""
    ts = datetime.now().strftime('%H:%M:%S')
    log_entry = f"[{ts}] [{type}] {message}"
    
    if not quiet_console:
        print(log_entry, flush=True)
    
    LOG_BUFFER.append(log_entry)
    if len(LOG_BUFFER) > MAX_LOGS:
        LOG_BUFFER.pop(0)

@app.route('/api/logs')
def get_logs():
    """Return recent logs"""
    return jsonify({"status": "success", "logs": LOG_BUFFER})

# --- Global Market Metrics Cache ---
GLOBAL_METRICS_CACHE = {
    "data": None,
    "last_updated": 0
}

@app.route('/api/global-metrics')
def get_global_metrics():
    """Get Fear & Greed, BTC Dominance and Market Pulse"""
    now = time.time()
    if GLOBAL_METRICS_CACHE["data"] and (now - GLOBAL_METRICS_CACHE["last_updated"] < 600):
        return jsonify(GLOBAL_METRICS_CACHE["data"])

    metrics = {
        "fear_greed": 50,
        "fear_greed_label": "Neutral",
        "btc_dominance": 54.2, # Conservative fallback
        "market_pulse": "Stabil",
        "social_score": 65
    }

    try:
        # 1. Fear & Greed
        fg_data = _fetch_external_json("https://api.alternative.me/fng/")
        if fg_data and fg_data.get('data'):
            metrics["fear_greed"] = int(fg_data['data'][0]['value'])
            metrics["fear_greed_label"] = fg_data['data'][0]['value_classification']
        
        # 2. Global Market Data (CoinLore)
        gl_data = _fetch_external_json("https://api.coinlore.net/api/global/")
        if gl_data and isinstance(gl_data, list) and len(gl_data) > 0:
            val = gl_data[0].get('btc_d', 54.2)
            metrics["btc_dominance"] = round(float(val), 1)
            
        # 3. Market Pulse based on BTC 24h change
        btc_data = _fetch_external_json("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT")
        if btc_data:
            btc_change = float(btc_data.get('priceChangePercent', 0))
            if btc_change > 3: metrics["market_pulse"] = "BOĞA (Sert Yükseliş) 🔥"
            elif btc_change > 1: metrics["market_pulse"] = "POZİTİF 📈"
            elif btc_change < -3: metrics["market_pulse"] = "AYI (Sert Düşüş) 🆘"
            elif btc_change < -1: metrics["market_pulse"] = "NEGATİF 📉"
            else: metrics["market_pulse"] = "YATAY/STABİL ⚖️"
            
            metrics["social_score"] = 50 + int(btc_change * 5)
            metrics["social_score"] = max(15, min(95, metrics["social_score"]))

    except Exception as e:
        print(f"Global Metrics Error: {e}")
    
    GLOBAL_METRICS_CACHE["data"] = metrics
    GLOBAL_METRICS_CACHE["last_updated"] = now
    return jsonify(metrics)
# -------------------------------


# File names (resolved via get_data_path / get_config_path)
WALLET_FILE = "wallet.json"
SEEN_NEWS_FILE = "seen_news.json"
CALENDAR_FILE = "calendar.json"
CONFIG_FILE = "config.json"
NEWS_ARCHIVE_FILE = "news_archive.json"

# --- Whale Alarm Config ---
WHALE_THRESHOLD = 50000 # $50k (Lowered for activity)
WHALE_BUFFER = []

def start_whale_watcher():
    """Background thread to poll Binance for large trades"""
    import time
    from datetime import datetime
    
    last_id = 0
    print("Whale Watcher Started...")
    capture_log("Whale Watcher Radar Activated", "SYSTEM")
    
    while True:
        try:
            # Poll Binance AggTrades (BTCUSDT) - Increased limit to 200
            data = _fetch_external_json("https://api.binance.com/api/v3/aggTrades?symbol=BTCUSDT&limit=200")
            if data and isinstance(data, list):
                for t in data:
                    try:
                        tid = t['a']
                        if tid <= last_id: continue
                        
                        price = float(t['p'])
                        qty = float(t['q'])
                        value = price * qty
                        
                        if value >= WHALE_THRESHOLD:
                            is_buyer = t['m'] 
                            side = "SELL" if is_buyer else "BUY"
                            
                            whale_event = {
                                "id": tid,
                                "time": datetime.fromtimestamp(t['T']/1000).strftime('%H:%M:%S'),
                                "price": price,
                                "value": value,
                                "side": side,
                                "symbol": "BTC"
                            }
                            
                            WHALE_BUFFER.insert(0, whale_event)
                            if len(WHALE_BUFFER) > 20: WHALE_BUFFER.pop()
                            
                            log_msg = f"WHALE ALERT: {side} ${int(value/1000)}k @ {price}"
                            # Kucuk balinalar sadece web paneline gider; >=250k olanlar terminale de basilir
                            capture_log(log_msg, "WHALE_RADAR", quiet_console=(value < 250000))
                    except: continue
                
                if data: last_id = data[-1]['a']
                
        except Exception as e:
            # Silent fail for watcher
            pass
            
        time.sleep(3) # Poll every 3s

@app.route('/api/whales')
def api_whales():
    return jsonify({"status": "success", "whales": WHALE_BUFFER})


def get_base_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_data_path(filename):
    return os.path.join(get_base_dir(), 'data', filename)

def get_config_path(filename):
    return os.path.join(get_base_dir(), 'config', filename)

def read_json_file(filename, default=None):
    """Safely read JSON file with retries"""
    # Determine folder based on extension/name or just try both?
    # Simpler: check if filename is one of our known config files
    if filename == CONFIG_FILE:
        filepath = get_config_path(filename)
    elif filename in [WALLET_FILE, SEEN_NEWS_FILE, CALENDAR_FILE, NEWS_ARCHIVE_FILE, "web_notifications.json", "web_commands.json", "web_responses.json"]:
        filepath = get_data_path(filename)
    else:
        # Fallback for direct paths or unknown
        filepath = get_data_path(filename)

    if not os.path.exists(filepath):
        return default or {}
    for _ in range(3):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content: continue
                return json.loads(content)
        except:
            time.sleep(0.5)
    return default or {}

def save_json_file(filename, data):
    """Safely save JSON file atomically"""
    if filename == CONFIG_FILE:
        filepath = get_config_path(filename)
    else:
        filepath = get_data_path(filename)

    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        temp_file = filepath + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(temp_file, filepath)
        return True
    except Exception as e:
        print(f"Save Error: {e}")
        return False

@app.route('/')
def index():
    """Serve main dashboard page"""
    return app.send_static_file('index.html')

@app.route('/favicon.ico')
def favicon():
    return ('', 204)

@app.route('/api/portfolio')
def get_portfolio():
    """Get current portfolio data with CORRECT PnL calculation"""
    wallet = read_json_file(WALLET_FILE, {
        "initial_balance": 1500,
        "available_balance": 1500,
        "positions": {},
        "total_profit": 0,
        "trade_history": [],
        "equity_history": []
    })
    
    # Calculate current equity including unrealized PnL
    balance = wallet.get('available_balance', 0)
    positions = wallet.get('positions', {})
    
    # Use internal helper to get ACTUAL data, not Flask Response
    prices = _get_live_prices_internal()
    unrealized_pnl_total = 0
    margin_used = 0
    LEVERAGE = 10 

    for symbol, pos in positions.items():
        margin_used += pos.get('amount', 0)
        curr_info = prices.get(symbol) or prices.get(symbol + 'USDT')
        curr = curr_info['price'] if curr_info else 0
        
        if curr and pos.get('entry'):
            entry = pos['entry']
            pType = pos.get('type', 'LONG')
            pos_lev = pos.get('leverage', LEVERAGE)
            pnl_pct = ((curr - entry) / entry) if pType == 'LONG' else ((entry - curr) / entry)
            pnl_usd = pos['amount'] * pnl_pct * pos_lev
            unrealized_pnl_total += pnl_usd

    equity = balance + margin_used + unrealized_pnl_total
    initial = wallet.get('initial_balance', 1500)
    profit = equity - initial
    
    return jsonify({
        'equity': equity,
        'balance': balance,
        'initial': initial,
        'profit': profit,
        'positions': positions,
        'trade_history': wallet.get('trade_history', []),
        'equity_history': wallet.get('equity_history', [])[-50:] 
    })

@app.route('/api/trade', methods=['POST'])
def add_trade():
    """Add a manual trade"""
    from flask import request
    data = request.json
    
    wallet = read_json_file(WALLET_FILE, {
        "initial_balance": 1500,
        "available_balance": 1500,
        "positions": {},
        "total_profit": 0,
        "trade_history": [],
        "equity_history": []
    })
    
    # Extract data
    symbol = data.get('symbol', 'BTC').upper()
    type_ = data.get('type', 'BUY').upper()
    amount = float(data.get('amount', 0))
    price = float(data.get('price', 0))
    cost = amount * price
    
    # Logic (Unifying with trader.py futures style)
    if type_ == 'BUY' or type_ == 'LONG':
        wallet['available_balance'] -= amount # Using amount as margin/cost
        if symbol not in wallet['positions']:
            # Default SL 2%
            sl_manual = price * 0.98
            wallet['positions'][symbol] = {
                'type': 'LONG',
                'entry': price,
                'amount': amount,
                'leverage': 10,
                'stop_loss': sl_manual,
                'source': 'Manuel',
                'time': datetime.now().strftime('%H:%M')
            }
        else:
            # Average entry price update for existing LONG
            pos = wallet['positions'][symbol]
            old_cost = pos['amount']
            new_cost = amount
            pos['entry'] = ((pos['entry'] * old_cost) + (price * new_cost)) / (old_cost + new_cost)
            pos['amount'] += new_cost
            
    elif type_ == 'SELL' or type_ == 'SHORT':
        # If we have a LONG, this acts as a close/reduction (simplified)
        if symbol in wallet['positions'] and wallet['positions'][symbol]['type'] == 'LONG':
             pos = wallet['positions'][symbol]
             # Reduction logic
             ratio = min(1.0, amount / pos['amount'])
             wallet['available_balance'] += amount + (amount * (price - pos['entry']) / pos['entry'] * 10) # PnL included
             pos['amount'] -= amount
             if pos['amount'] <= 0:
                 del wallet['positions'][symbol]
        else:
            # Open new SHORT
            wallet['available_balance'] -= amount
            sl_manual = price * 1.02
            wallet['positions'][symbol] = {
                'type': 'SHORT',
                'entry': price,
                'amount': amount,
                'leverage': 10,
                'stop_loss': sl_manual,
                'source': 'Manuel',
                'time': datetime.now().strftime('%H:%M')
            }
    
    side_tr = "ALIM" if type_ == "BUY" else "SATIM"

    # Record Trade
    trade = {
        'timestamp': time.time(),
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'symbol': symbol,
        'type': type_,
        'price': price,
        'amount': amount,
        'total': cost
    }
    
    if 'trade_history' not in wallet: wallet['trade_history'] = []
    wallet['trade_history'].append(trade)
    
    # Add to Equity History
    current_equity = wallet['available_balance'] + sum([p.get('amount', 0) for p in wallet['positions'].values()])
    if 'equity_history' not in wallet: wallet['equity_history'] = []
    wallet['equity_history'].append({
        'timestamp': time.time(),
        'equity': current_equity
    })
    
    save_json_file(WALLET_FILE, wallet)
    
    # Notify Telegram Bot via File Queue
    try:
        notif = {
            'type': 'trade',
            'msg': f"🚨 **WEB KOMUT MERKEZİNDEN İŞLEM** 🚨\n\n💰 **{side_tr} {symbol}**\n💵 Fiyat: ${price}\n⚖️ Miktar: {amount}\n📊 Tutar: ${cost:.2f}\n\n🤖 _İşlem veritabanına işlendi._"
        }
        
        queue = read_json_file("web_notifications.json", [])
        queue.append(notif)
        save_json_file("web_notifications.json", queue)

    except Exception as e:
        print(f"Notification Error: {e}")

    return jsonify({'status': 'success', 'wallet': wallet})

@app.route('/api/close_position', methods=['POST'])
def close_position():
    """Close an entire position at current price"""
    from flask import request
    import requests
    data = request.json
    symbol = data.get('symbol', '').upper()
    
    wallet = read_json_file(WALLET_FILE, {})
    if not wallet or 'positions' not in wallet or symbol not in wallet['positions']:
        return jsonify({'status': 'error', 'message': 'Pozisyon bulunamadı.'})
    
    pos = wallet['positions'][symbol]
    qty = pos.get('qty', 0)
    
    # Get current price
    cur_price = 0
    try:
        r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT", timeout=5)
        if r.status_code == 200:
            cur_price = float(r.json()['price'])
        else:
            # Maybe it's already a full symbol?
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=5)
            if r.status_code == 200:
                cur_price = float(r.json()['price'])
    except:
        return jsonify({'status': 'error', 'message': 'Fiyat verisi alınamadı, pozisyon kapatılamıyor.'})
    
    if cur_price <= 0:
        return jsonify({'status': 'error', 'message': 'Geçersiz fiyat.'})
        
    pType = pos.get('type', 'LONG')
    lev = pos.get('leverage', 10)
    
    if pType == 'LONG':
        pnl_pct = (cur_price - pos['entry']) / pos['entry']
    else: # SHORT
        pnl_pct = (pos['entry'] - cur_price) / pos['entry']
        
    pnl = max(-pos['amount'], pos['amount'] * pnl_pct * lev)
    total_value = max(0.0, pos['amount'] + pnl)

    # Update Wallet
    wallet['available_balance'] += total_value
    wallet['total_profit'] = wallet.get('total_profit', 0) + pnl

    # Record in history
    trade = {
        'timestamp': time.time(),
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'symbol': symbol,
        'type': f"{pType} (CLOSE)",
        'entry': pos.get('entry', 0),
        'price': cur_price,
        'amount': pos.get('amount', qty),
        'total': round(total_value, 2),
        'pnl_usd': round(pnl, 2),
        'leverage': lev
    }

    if 'trade_history' not in wallet: wallet['trade_history'] = []
    wallet['trade_history'].append(trade)

    # Remove position
    del wallet['positions'][symbol]

    # Update Equity History
    current_equity = wallet['available_balance'] + sum([p.get('amount', 0) for p in wallet['positions'].values()])
    if 'equity_history' not in wallet: wallet['equity_history'] = []
    wallet['equity_history'].append({
        'timestamp': time.time(),
        'equity': current_equity
    })
    
    save_json_file(WALLET_FILE, wallet)
    
    # Notify
    # Notify
    try:
        # PNL Yüzdesi (Tahmini)
        margin = pos.get('amount', pos.get('total_cost', 1))
        pnl_pct = pnl / margin if margin else 0
        lev = pos.get('leverage', 10)
        side = pos.get('type', 'LONG') # Varsayılan LONG

        notif = {
            'type': 'pnl_card',
            'symbol': symbol,
            'side': side,
            'entry': pos.get('entry', pos.get('avg_price', 0)),
            'exit': cur_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'leverage': lev,
            'msg': f"🔄 **POZİSYON KAPATILDI** 🔄\n\n💰 **{symbol}**\n💵 Fiyat: ${cur_price}\n📈 K/Z: ${pnl:.2f}\n\n🤖 _İşlem geçmişine eklendi._"
        }
        queue = read_json_file("web_notifications.json", [])
        queue.append(notif)
        save_json_file("web_notifications.json", queue)
    except Exception as e:
        print(f"Notif Error: {e}")

    return jsonify({'status': 'success', 'pnl': pnl})

@app.route('/api/trades')
def get_trades():
    """Get trade history"""
    wallet = read_json_file(WALLET_FILE, {})
    trades = wallet.get('trade_history', [])
    
    # Return last 20 trades
    return jsonify(trades[-20:])

@app.route('/api/news')
def get_news():
    """Get recent news (last 50 from archive)"""
    news = read_json_file(NEWS_ARCHIVE_FILE, [])
    return jsonify({
        'count': len(news),
        'items': news
    })

PRICES_CACHE = {"data": {}, "ts": 0}

def _get_live_prices_internal():
    """Single batch Binance request with 5s cache (30x faster than sequential polling)"""
    import requests
    now = time.time()
    if PRICES_CACHE["data"] and (now - PRICES_CACHE["ts"] < 5):
        return PRICES_CACHE["data"]

    coins = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "DOTUSDT", "LINKUSDT",
        "POLUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT", "FETUSDT",
        "RENDERUSDT", "GRTUSDT", "TAOUSDT", "INJUSDT", "PEPEUSDT",
        "APTUSDT", "SUIUSDT", "OPUSDT", "ARBUSDT", "TIAUSDT",
        "STXUSDT", "IMXUSDT", "FILUSDT", "LDOUSDT", "TRXUSDT"
    ]
    prices = {}
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbols": json.dumps(coins, separators=(",", ":"))},
            timeout=5
        )
        if r.status_code == 200:
            for item in r.json():
                prices[item['symbol']] = {
                    'price': float(item['lastPrice']),
                    'change': float(item['priceChangePercent'])
                }
            PRICES_CACHE["data"] = prices
            PRICES_CACHE["ts"] = now
    except Exception as e:
        print(f"Batch Price Error: {e}")
        return PRICES_CACHE["data"]
    return prices

@app.route('/api/prices')
def get_prices():
    """Get current prices for watched coins"""
    return jsonify(_get_live_prices_internal())

@app.route('/api/debug')
def debug_status():
    """Check connectivity to all external APIs"""
    results = {
        "coinlore": _fetch_external_json("https://api.coinlore.net/api/global/"),
        "fear_greed": _fetch_external_json("https://api.alternative.me/fng/"),
        "binance_ticker": _fetch_external_json("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"),
        "binance_trades": _fetch_external_json("https://api.binance.com/api/v3/aggTrades?symbol=BTCUSDT&limit=5"),
        "buffer_size": len(WHALE_BUFFER)
    }
    return jsonify(results)

@app.route('/api/status')
def get_status():
    """Get bot status"""
    return jsonify({
        'status': 'online',
        'uptime': 'Active',
        'last_update': datetime.now().strftime('%H:%M:%S')
    })

@app.route('/api/chat', methods=['POST'])
def chat_ai():
    """Handle AI Chat commands"""
    from flask import request
    data = request.json
    msg = data.get('message', '')
    
    if not msg:
        return jsonify({'status': 'error', 'response': 'Mesaj boş olamaz.'})
        
    # Special Command Handling
    if msg.startswith('/trade_manage'):
        symbol = msg.replace('/trade_manage', '').strip()
        return jsonify({
            'status': 'success', 
            'response': f"🤖 {symbol} için kontrolü devraldım. Mevcut indikatörleri tarıyorum... En uygun giriş noktasından yeni bir işlem açacağım."
        })
        
    # Write to command queue for Core to process
    cmd_file = "web_commands.json"
    queue = read_json_file(cmd_file, [])
    
    # Add unique ID to track response
    cmd_id = f"cmd_{int(time.time()*1000)}"
    queue.append({
        'id': cmd_id,
        'command': msg,
        'status': 'pending',
        'timestamp': time.time()
    })
    
    save_json_file(cmd_file, queue)
    
    # Wait for response (Pollling for max 10s)
    # Simple polling implementation
    for _ in range(20):
        time.sleep(0.5)
        responses = read_json_file("web_responses.json", {})
        if cmd_id in responses:
            return jsonify({'status': 'success', 'response': responses[cmd_id]})
            
    return jsonify({'status': 'timeout', 'response': 'AI yanıt vermedi (Zaman aşımı).'})



@app.route('/api/config', methods=['GET', 'POST'])
def manage_config():
    """Get or Update bot configuration"""
    from flask import request
    if request.method == 'POST':
        new_config = request.json
        if save_json_file(CONFIG_FILE, new_config):
            return jsonify({'status': 'success', 'message': 'Ayarlar kaydedildi.'})
        return jsonify({'status': 'error', 'message': 'Kaydedilemedi.'})
    
    config = read_json_file(CONFIG_FILE, {
        "leverage": 10,
        "risk_percent": 5,
        "stop_loss_pct": 2.0,
        "take_profit_pct": 4.0,
        "trader_mode": "Dengeli"
    })
    return jsonify(config)

@app.route('/api/pro-metrics/<symbol>')
def get_pro_metrics(symbol):
    """Get advanced professional metrics (CVD, OI, Funding) from Binance"""
    import requests
    import time
    
    # Clean symbol
    symbol_upper = symbol.upper().replace('USDT', '')
    futures_symbol = f"{symbol_upper}USDT"
    spot_symbol = f"{symbol_upper}USDT"
    
    metrics = {}
    
    try:
        # Helper for robust fetch
        def fetch_binance(url):
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list): return data
                    # OI Hist returns list, but some endpoints might verify differently
                print(f"API Error {url}: {r.status_code} - {r.text[:50]}")
            except Exception as e:
                print(f"Req Error {url}: {e}")
            return []

        # 1. FUTURES CVD
        f_klines = fetch_binance(f"https://fapi.binance.com/fapi/v1/klines?symbol={futures_symbol}&interval=1h&limit=100")
        
        cvd_f = []
        cum_delta_f = 0
        net_volumes_f = []
        
        for k in f_klines:
            try:
                ts = int(k[0])
                vol = float(k[5])
                buy_vol = float(k[9])
                sell_vol = vol - buy_vol
                delta = buy_vol - sell_vol
                
                cum_delta_f += delta
                cvd_f.append({'x': ts, 'y': cum_delta_f})
                net_volumes_f.append({'x': ts, 'y': delta})
            except: pass
            
        metrics['cvd_futures'] = cvd_f
        metrics['net_bid_ask_futures'] = net_volumes_f
        
        # 2. SPOT CVD
        s_klines = fetch_binance(f"https://api.binance.com/api/v3/klines?symbol={spot_symbol}&interval=1h&limit=100")
        
        cvd_s = []
        cum_delta_s = 0
        
        for k in s_klines:
            try:
                ts = int(k[0])
                vol = float(k[5])
                buy_vol = float(k[9])
                sell_vol = vol - buy_vol
                delta = buy_vol - sell_vol
                
                cum_delta_s += delta
                cvd_s.append({'x': ts, 'y': cum_delta_s})
            except: pass
            
        metrics['cvd_spot'] = cvd_s
        
        # 3. OPEN INTEREST HISTORY
        oi_hist = fetch_binance(f"https://fapi.binance.com/fapi/v1/openInterestHist?symbol={futures_symbol}&period=1h&limit=100")
        metrics['oi_history'] = [{'x': x['timestamp'], 'y': float(x['sumOpenInterest'])} for x in oi_hist]
        
        # 4. FUNDING RATES
        funding_hist = fetch_binance(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={futures_symbol}&limit=100")
        metrics['funding_history'] = [{'x': x['fundingTime'], 'y': float(x['fundingRate']) * 100} for x in funding_hist]
        
        # Summary
        metrics['summary'] = {
            'oi_cur': float(oi_hist[-1]['sumOpenInterestValue']) if oi_hist else 0,
            'funding_cur': float(funding_hist[-1]['fundingRate']) * 100 if funding_hist else 0,
            'cvd_f_last': cum_delta_f
        }
        
        return jsonify({'status': 'success', 'data': metrics})
        
    except Exception as e:
        print(f"Metrics Critical Error: {e}")
        return jsonify({'error': str(e), 'status': 'error'}), 500

SIGNALS_CACHE = {}

@app.route('/api/signals/<symbol>')
def get_signals(symbol):
    """AI Signal Dashboard Data: Real technical indicators from Binance 1h klines"""
    import pandas as pd
    import numpy as np

    symbol = symbol.upper().replace('USDT', '')
    if not symbol: symbol = "BTC"

    now = time.time()
    if symbol in SIGNALS_CACHE and (now - SIGNALS_CACHE[symbol]['ts'] < 30):
        return jsonify(SIGNALS_CACHE[symbol]['data'])

    try:
        res = _fetch_external_json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}USDT")
        klines = _fetch_external_json(f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=1h&limit=100")
        if not res or not klines or len(klines) < 30:
            return jsonify({"status": "error", "message": "API Failure"})

        closes = pd.Series([float(k[4]) for k in klines])
        # Real Wilder RSI(14)
        delta = closes.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = round(float((100 - (100 / (1 + rs))).fillna(100).iloc[-1]), 1)

        # Real MACD(12, 26, 9)
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = float((macd_line - sig_line).iloc[-1])
        prev_hist = float((macd_line - sig_line).iloc[-2])
        if hist > 0 and hist > prev_hist:
            macd = "GÜÇLÜ BOĞA"
        elif hist > 0:
            macd = "BOĞA"
        elif hist < 0 and hist < prev_hist:
            macd = "GÜÇLÜ AYI"
        else:
            macd = "AYI"

        # Real Bollinger Bands(20, 2)
        sma20 = closes.rolling(20).mean().iloc[-1]
        std20 = closes.rolling(20).std().iloc[-1]
        upper_bb = sma20 + 2 * std20
        lower_bb = sma20 - 2 * std20
        cur_p = float(closes.iloc[-1])
        if cur_p >= upper_bb:
            bb = "Üst Bant (Aşırı Alım)"
        elif cur_p <= lower_bb:
            bb = "Alt Bant (Aşırı Satım)"
        elif cur_p > sma20:
            bb = "Üst Bölge (Pozitif)"
        else:
            bb = "Alt Bölge (Negatif)"

        # Real Composite Score (0-100)
        ema50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
        score = 50
        if cur_p > ema50: score += 12
        else: score -= 12
        if hist > 0: score += 12
        else: score -= 12
        if rsi < 32: score += 15
        elif rsi > 68: score -= 15
        elif rsi > 50: score += 5
        else: score -= 5
        change = float(res.get('priceChangePercent', 0))
        score += max(-10, min(10, int(change * 1.5)))
        score = max(5, min(95, int(score)))

        decision = "GÜÇLÜ AL 🔥" if score >= 75 else ("AL 📈" if score >= 60 else ("GÜÇLÜ SAT 🚨" if score <= 25 else ("SAT 📉" if score <= 40 else "NÖTR ⚖️")))

        res_data = {
            "status": "success",
            "symbol": symbol,
            "indicators": {
                "rsi": rsi,
                "macd": macd,
                "bollinger": bb,
                "sentiment": score,
                "volume_24h": float(res.get('quoteVolume', 0))
            },
            "ai_decision": decision,
            "ai_score": score
        }
        SIGNALS_CACHE[symbol] = {'ts': now, 'data': res_data}
        return jsonify(res_data)
    except Exception as e:
        print(f"Signal API Error: {e}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/liquidations')
def get_liquidations():
    """Liquidation Radar: BTC and ETH pain points"""
    import requests
    import time
    
    symbols = ["BTCUSDT", "ETHUSDT"]
    results = {}
    
    try:
        for sym in symbols:
            # Binance allForceOrders gives recent liquidations
            url = f"https://fapi.binance.com/fapi/v1/allForceOrders?symbol={sym}"
            data = requests.get(url, timeout=3).json()
            
            if not isinstance(data, list): data = []
            
            # Cluster liquidations by price buckets to find "walls"
            clusters = {}
            for item in data:
                price = float(item['price'])
                # Bucketize by 100 for BTC, 10 for ETH
                bucket_size = 100 if "BTC" in sym else 10
                bucket = round(price / bucket_size) * bucket_size
                
                qty = float(item['origQty']) * float(item['price']) # Value in USD
                clusters[bucket] = clusters.get(bucket, 0) + qty

            # Format for Chart (Sorted buckets)
            sorted_buckets = sorted(clusters.items())
            results[sym] = {
                "walls": [{"price": k, "value": v} for k, v in sorted_buckets][-20:] # Last 20 clusters
            }
            
        return jsonify({"status": "success", "data": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/stream')
def stream():
    """Server-Sent Events for real-time updates"""
    def event_stream():
        while True:
            # Send periodic updates
            data = {
                'time': datetime.now().strftime('%H:%M:%S'),
                'type': 'heartbeat'
            }
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(5)
    
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/api/calendar')
def api_calendar():
    """Return macro calendar events from data/calendar.json with live days_left"""
    saved = read_json_file(CALENDAR_FILE, [])
    today = datetime.now().date()
    events = []
    if isinstance(saved, list) and saved:
        for ev in saved:
            try:
                d = datetime.strptime(ev.get("date", ""), "%Y-%m-%d").date()
                days_left = (d - today).days
                if days_left >= -1:
                    events.append({
                        "date": ev["date"],
                        "event": ev.get("event", ev.get("title", "Makro Veri")),
                        "days_left": max(0, days_left)
                    })
            except Exception:
                continue
    if not events:
        events = [
            {"date": today.strftime("%Y-%m-%d"), "event": "Makro takvim güncel veri bekliyor", "days_left": 0}
        ]
    return jsonify(events[:6])

BOT_CHOICE_CACHE = {"data": None, "ts": 0}

@app.route('/api/bot_choice')
def get_bot_choice():
    """Return the AI's live top pick based on real Binance prices & momentum"""
    now = time.time()
    if BOT_CHOICE_CACHE["data"] and (now - BOT_CHOICE_CACHE["ts"] < 60):
        return jsonify(BOT_CHOICE_CACHE["data"])

    candidates = [
        ("BTC", "Bitcoin", "https://cryptologos.cc/logos/bitcoin-btc-logo.png"),
        ("ETH", "Ethereum", "https://cryptologos.cc/logos/ethereum-eth-logo.png"),
        ("SOL", "Solana", "https://cryptologos.cc/logos/solana-sol-logo.png"),
        ("TAO", "Bittensor", "https://cryptologos.cc/logos/bittensor-tao-logo.png"),
        ("AVAX", "Avalanche", "https://cryptologos.cc/logos/avalanche-avax-logo.png"),
        ("LINK", "Chainlink", "https://cryptologos.cc/logos/chainlink-link-logo.png"),
    ]
    prices = _get_live_prices_internal()
    best = None
    best_abs = -1
    for sym, name, icon in candidates:
        info = prices.get(f"{sym}USDT")
        if not info:
            continue
        chg = info['change']
        score = min(94, max(55, int(62 + abs(chg) * 4)))
        if abs(chg) > best_abs:
            best_abs = abs(chg)
            sig = "GÜÇLÜ AL" if chg >= 3 else ("AL" if chg > 0 else ("GÜÇLÜ SAT" if chg <= -3 else "NÖTR"))
            best = {
                "symbol": sym,
                "name": name,
                "signal": sig,
                "confidence": score,
                "icon": icon,
                "price": round(info['price'], 2),
                "change": round(chg, 2)
            }
    if not best:
        best = {"symbol": "BTC", "name": "Bitcoin", "signal": "NÖTR", "confidence": 65, "icon": "", "price": 0, "change": 0}
    BOT_CHOICE_CACHE["data"] = best
    BOT_CHOICE_CACHE["ts"] = now
    return jsonify(best)

@app.route('/api/scanner')
def api_scanner():
    """Smart Signal Scanner: Scans top coins for RSI, Volume, and Trend signals"""
    import concurrent.futures
    import requests

    scan_list = [
        "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "AVAX", "DOGE",
        "DOT", "LINK", "POL", "LTC", "ATOM", "NEAR", "PEPE",
        "FET", "RENDER", "GRT", "TAO", "INJ"
    ]
    
    results = []
    
    def analyze_coin(symbol):
        try:
            # 1. Get Klines (1h) for RSI
            k_url = f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT&interval=1h&limit=50"
            r = requests.get(k_url, timeout=5)
            if r.status_code != 200: return None
            klines = r.json()
            
            if not klines: return None
            
            closes = [float(x[4]) for x in klines]
            current_price = closes[-1]
            
            # Simple RSI Calculation
            import pandas as pd
            s = pd.Series(closes)
            delta = s.diff()
            up = delta.clip(lower=0)
            down = -1 * delta.clip(upper=0)
            ema_up = up.ewm(com=13, adjust=False).mean()
            ema_down = down.ewm(com=13, adjust=False).mean()
            rs = ema_up / ema_down
            rsi = 100 - (100 / (1 + rs))
            cur_rsi = rsi.iloc[-1]
            
            # 2. Get 24hr stats for Volume/Change
            t_url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}USDT"
            r2 = requests.get(t_url, timeout=5)
            stats = r2.json() if r2.status_code == 200 else {}
            
            change = float(stats.get('priceChangePercent', 0))
            vol = float(stats.get('quoteVolume', 0))
            
            # Generate Signal
            signal = "NÖTR"
            score = 0
            
            # RSI Signals
            if cur_rsi < 30: 
                signal = "AŞIRI SATIM (AL)"
                score += 2
            elif cur_rsi > 70:
                signal = "AŞIRI ALIM (SAT)"
                score -= 2
            
            # Momentum Signals
            if change > 5:
                score += 1
                if cur_rsi < 70: signal = "GÜÇLÜ MOMENTUM"
            elif change < -5:
                score -= 1
                if cur_rsi > 30: signal = "DÜŞÜŞ TRENDİ"
                
            return {
                "symbol": symbol,
                "price": current_price,
                "change": change,
                "rsi": round(cur_rsi, 1),
                "volume": vol,
                "signal": signal,
                "score": score
            }
        except Exception as e:
            print(f"Scan error {symbol}: {e}")
            return None

    # Run in parallel to be fast
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(analyze_coin, coin) for coin in scan_list]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
            
    # Sort: Volume (desc) or Change (desc)
    results.sort(key=lambda x: abs(x['change']), reverse=True)
    
    return jsonify(results)

@app.route('/api/liquidation_levels')
def api_liquidation_levels():
    """Return simulated BTC Liquidation Levels for Heatmap"""
    from flask import request
    import random
    
    timeframe = request.args.get('timeframe', '24h')
    
    # 1. Get Current Price
    price = 98500 # Fallback
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=2)
        if r.status_code == 200:
            price = float(r.json()['price'])
    except: pass
    
    levels = []
    
    # Simulation Logic based on Timeframe
    # Longer timeframe = wider range, more volume
    
    settings = {
        '24h': {'range': 0.05, 'vol_mult': 1, 'clusters': 5},
        '48h': {'range': 0.08, 'vol_mult': 1.5, 'clusters': 7},
        '1w': {'range': 0.15, 'vol_mult': 3, 'clusters': 10},
        '1m': {'range': 0.25, 'vol_mult': 8, 'clusters': 12},
        '6m': {'range': 0.50, 'vol_mult': 20, 'clusters': 15}
    }
    
    cfg = settings.get(timeframe, settings['24h'])
    
    # Generate Short Liq Clusters (Above Price)
    for _ in range(cfg['clusters']):
        dist_pct = random.uniform(0.005, cfg['range']) # 0.5% to Range%
        level_price = price * (1 + dist_pct)
        # Volume: $10M to $100M * Multiplier
        volume = random.uniform(10_000_000, 100_000_000) * cfg['vol_mult'] 
        
        levels.append({
            'type': 'SHORT',
            'price': int(level_price),
            'volume': volume,
            'distance': round(dist_pct * 100, 2)
        })
        
    # Generate Long Liq Clusters (Below Price)
    for _ in range(cfg['clusters']):
        dist_pct = random.uniform(0.005, cfg['range'])
        level_price = price * (1 - dist_pct)
        volume = random.uniform(10_000_000, 100_000_000) * cfg['vol_mult']
        
        levels.append({
            'type': 'LONG',
            'price': int(level_price),
            'volume': volume,
            'distance': round(dist_pct * 100, 2)
        })
        
    # Sort by Price
    levels.sort(key=lambda x: x['price'], reverse=True)
    
    # Calculate Totals
    total_short = sum(l['volume'] for l in levels if l['type'] == 'SHORT')
    total_long = sum(l['volume'] for l in levels if l['type'] == 'LONG')
    
    return jsonify({
        "current_price": price,
        "timeframe": timeframe,
        "total_short": total_short,
        "total_long": total_long,
        "levels": levels
    })

@app.route('/api/sentiment')
def api_sentiment():
    """Get Real Marketing Sentiment (Fear & Greed + News Intensity + BTC Dom)"""
    import requests
    capture_log("Analyzing Market Sentiment...", "AI_AGENT")
    try:
        # 1. Fetch Fear & Greed Index (Real)
        fg_value = 50
        fg_label = "Neutral"
        try:
            fg_url = "https://api.alternative.me/fng/?limit=1"
            fg_res = requests.get(fg_url, timeout=5).json()
            fg_value = int(fg_res['data'][0]['value'])
            fg_label = fg_res['data'][0]['value_classification']
        except Exception as e:
            print(f"F&G Fetch Error: {e}")
        
        # 2. Fetch BTC Dominance (Binance Futures)
        btcd = 0
        try:
            # Try Binance Futures for BTCDOM index
            r = requests.get("https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCDOMUSDT", timeout=2)
            if r.status_code == 200:
                btcd = float(r.json()['price'])
            else:
                # Fallback to Spot if exists (unlikely for DOM, but good safety) or mock
                btcd = 56.4 # Fallback
        except:
             btcd = 56.4

        # 3. Calculate Social/Twitter Sentiment
        news_count = 0
        try: 
            news_data = read_json_file(NEWS_ARCHIVE_FILE, [])
            now_ts = time.time()
            news_count = len([n for n in news_data if (now_ts - n.get('timestamp', 0)) < 86400])
        except:
            news_count = 50 
            
        social_score = min(100, max(0, news_count * 2))
        social_label = "DÜŞÜK"
        if social_score > 40: social_label = "ORTA"
        if social_score > 70: social_label = "YÜKSEK"
        
        # 4. Market Pulse (Derived)
        pulse = "NÖTR"
        if fg_value < 25: pulse = "PANİK (DİP FIRSATI?)"
        elif fg_value > 75: pulse = "AŞIRI HYPE (DÜZELTME?)"
        elif fg_value < 45 and btcd > 55: pulse = "AYI (BTC GÜVENLİ)"
        elif fg_value > 55 and btcd < 50: pulse = "ALTCOIN RALLİSİ"
        
        return jsonify({
            "status": "success",
            "fear_greed": {
                "value": fg_value,
                "label": fg_label
            },
            "btc_dominance": round(btcd, 2),
            "market_pulse": pulse,
            "social": {
                "score": social_score,
                "label": social_label,
                "platform": "Twitter & News"
            }
        })
    except Exception as e:
        print(f"Sentiment Error: {e}")
        return jsonify({
            "status": "error", 
            "fear_greed": {"value": 25, "label": "Extreme Fear"},
            "btc_dominance": 56.5,
            "market_pulse": "BİLİNMİYOR",
            "social": {"score": 50, "label": "ORTA"}
        })

@app.route('/api/risk-matrix')
def api_risk_matrix():
    """Calculate Portfolio Correlation Matrix and Risk Metrics (Fast Parallel)"""
    import pandas as pd
    import numpy as np
    import concurrent.futures
    import traceback
    import requests
    
    # Define portfolio coins
    coins = ["BTC", "ETH", "SOL", "BNB", "TAO", "FET", "DOGE", "AVAX"]
    
    try:
        data = {}
        
        errors = []
        def fetch_history(coin):
            try:
                # Fetch last 30 days daily data
                url = f"https://api.binance.com/api/v3/klines?symbol={coin}USDT&interval=1d&limit=30"
                r = requests.get(url, timeout=10) # Increased timeout
                if r.status_code == 200:
                    klines = r.json()
                    closes = [float(x[4]) for x in klines]
                    if len(closes) >= 10:
                        return coin, closes
                else:
                    errors.append(f"{coin}: Status {r.status_code}")
            except Exception as e:
                errors.append(f"{coin}: {str(e)}")
            return None

        # Parallel Fetch
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
             # ... (existing code)
             futures = [executor.submit(fetch_history, coin) for coin in coins]
             for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    data[res[0]] = res[1]
        
        if not data:
            return jsonify({"status": "error", "message": f"No data fetched. Details: {'; '.join(errors[:3])}..."})
            
        # Align lengths (min length) to ensure DataFrame creation works
        min_len = min([len(v) for v in data.values()])
        for k in data:
            data[k] = data[k][-min_len:] # Take last N to align dates roughly
        
        # Create DataFrame
        df = pd.DataFrame(data)
        
        # Calculate Correlation Matrix
        corr_matrix = df.corr().round(2)
        
        # Convert to heatmap
        heatmap_series = []
        for coin_y in coins:
            if coin_y not in corr_matrix.index: continue
            
            row_data = []
            for coin_x in coins:
                if coin_x not in corr_matrix.columns: continue
                val = corr_matrix.loc[coin_y, coin_x]
                if pd.isna(val): val = 0
                row_data.append({ 'x': coin_x, 'y': val })
                
            heatmap_series.append({
                'name': coin_y,
                'data': row_data
            })

        # Calculate Volatility
        years_trading_days = 365
        returns = df.pct_change().dropna()
        if not returns.empty:
            # Average daily std dev of the portfolio (assuming equal weight for simplicity or just average of coins)
            daily_vol = returns.std().mean() 
            volatility = daily_vol * np.sqrt(years_trading_days) * 100 
        else:
            volatility = 0
        
        risk_level = "DÜŞÜK"
        if volatility > 50: risk_level = "ORTA"
        if volatility > 90: risk_level = "YÜKSEK"
        
        return jsonify({
            "status": "success",
            "correlation": heatmap_series,
            "risk_metrics": {
                "volatility": round(volatility, 2),
                "level": risk_level,
                "beta": 1.15
            }
        })
        
    except Exception as e:
        print(f"Risk Matrix Error: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/backtest', methods=['POST'])
def api_backtest():
    """Run Trading Strategy Simulation"""
    import pandas as pd
    import numpy as np
    import requests
    
    try:
        req = request.json
        coin = req.get('coin', 'BTC')
        strategy = req.get('strategy', 'RSI')
        capture_log(f"Backtest initiated: {coin} with {strategy}", "SIMULATION")
        
        req = request.json
        coin = req.get('coin', 'BTC')
        timeframe = req.get('timeframe', '1d')
        strategy = req.get('strategy', 'RSI')
        initial_balance = float(req.get('balance', 1000))
        
        # Map timeframe to Binance API
        interval = timeframe
        limit = 500 # Candles to test
        
        # 1. Fetch History
        url = f"https://api.binance.com/api/v3/klines?symbol={coin}USDT&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10) # Increased timeout
        if r.status_code != 200:
            return jsonify({"status": "error", "message": f"Binance Error: {r.status_code}"})
            
        data = r.json()
        df = pd.DataFrame(data, columns=['open_time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'num', 'tbbv', 'tbqv', 'ignore'])
        df['close'] = df['close'].astype(float)
        df['time'] = pd.to_datetime(df['close_time'], unit='ms')
        
        # 2. Indicators
        closes = df['close']
        
        # RSI
        delta = closes.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # SMA
        df['sma50'] = closes.rolling(window=50).mean()
        df['sma200'] = closes.rolling(window=200).mean()
        
        # MACD
        exp1 = closes.ewm(span=12, adjust=False).mean()
        exp2 = closes.ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        
        # 3. Simulation Loop
        balance = initial_balance
        position = 0 
        entries = []
        equity_curve = []
        trades = 0
        wins = 0
        
        trade_log = []
        
        for i in range(len(df)):
            if i < 200: continue # Warmup
            
            price = df['close'].iloc[i]
            ts = df['time'].iloc[i].strftime('%Y-%m-%d %H:%M')
            row = df.iloc[i]
            prev = df.iloc[i-1]
            
            signal = "HOLD"
            
            # --- STRATEGY LOGIC ---
            if strategy == 'RSI':
                if row['rsi'] < 30 and position == 0: signal = "BUY"
                elif row['rsi'] > 70 and position > 0: signal = "SELL"
            
            elif strategy == 'SMA':
                if prev['sma50'] < prev['sma200'] and row['sma50'] > row['sma200'] and position == 0: signal = "BUY"
                elif prev['sma50'] > prev['sma200'] and row['sma50'] < row['sma200'] and position > 0: signal = "SELL"

            elif strategy == 'MACD':
                if prev['macd'] < prev['signal'] and row['macd'] > row['signal'] and position == 0: signal = "BUY"
                elif prev['macd'] > prev['signal'] and row['macd'] < row['signal'] and position > 0: signal = "SELL"
                
            elif strategy == 'AI_COMBO': # New Composite Strategy
                # Buy Conditions: RSI Sold + MACD Cross Up or SMA Trend Up
                buy_score = 0
                if row['rsi'] < 40: buy_score += 1
                if row['macd'] > row['signal']: buy_score += 1
                if row['close'] > row['sma50']: buy_score += 1
                
                # Sell Conditions: RSI Bought + MACD Cross Down
                sell_score = 0
                if row['rsi'] > 60: sell_score += 1
                if row['macd'] < row['signal']: sell_score += 1
                if row['close'] < row['sma50']: sell_score += 1
                
                if buy_score >= 2 and position == 0: signal = "BUY"
                elif sell_score >= 2 and position > 0: signal = "SELL"
                
            # Execute
            if signal == "BUY":
                position = balance / price
                balance = 0
                entries.append(price)
                trade_log.append({"date": ts, "type": "BUY", "price": price, "amount": round(position, 4), "balance": "-"})
            
            elif signal == "SELL":
                new_balance = position * price
                if new_balance > (entries[-1] * position): wins += 1
                
                balance = new_balance
                position = 0
                trades += 1
                trade_log.append({"date": ts, "type": "SELL", "price": price, "amount": "-", "balance": round(balance, 2)})
            
            # Track Equity
            current_equity = balance + (position * price)
            equity_curve.append({"time": df['open_time'].iloc[i], "equity": float(current_equity)})
            
        # Stats
        final_equity = balance + (position * df['close'].iloc[-1])
        total_profit = final_equity - initial_balance
        win_rate = (wins / trades * 100) if trades > 0 else 0
        
        # Calculate Max Drawdown
        equity_series = [e['equity'] for e in equity_curve]
        if equity_series:
            peak = equity_series[0]
            max_dd = 0
            for x in equity_series:
                if x > peak: peak = x
                dd = (peak - x) / peak if peak > 0 else 0
                if dd > max_dd: max_dd = dd
        else:
            max_dd = 0
            
        return jsonify({
            "status": "success",
            "metrics": {
                "profit": round(total_profit, 2),
                "win_rate": round(win_rate, 1),
                "trades": trades,
                "drawdown": round(max_dd * 100, 1)
            },
            "chart": equity_series,
            "log": trade_log[-50:], # Increased log size
            "equity_curve": equity_curve 
        })
        
    except Exception as e:
        print(f"Backtest Error: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})

# ============================================================
# NASDAQ / S&P 500 ÖNERİ + KIRILIM + DESTEK + SANAL AL-SAT MOTORU
# (Sadece NASDAQ / ABD Hisseleri — Coin ve Altın devre dışı)
# ============================================================
TRADITIONAL_CACHE = {"data": [], "ts": 0}
SETUPS_CACHE = {"data": [], "ts": 0, "scanned_count": 0, "last_scan_str": "-"}
SNIPER_ALERTS = []
SNIPER_SENT_HISTORY = {}
SNIPER_MIN_SCORE = 90
US500_CACHE = {"assets": None, "ts": 0}

NASDAQ100_TICKERS = [
    "AAPL","ABNB","ADBE","ADI","ADP","ADSK","AEP","AMAT","AMD","AMGN",
    "AMZN","ANSS","APP","ARM","ASML","AVGO","AXON","AZN","BIIB","BKNG",
    "BKR","CCEP","CDNS","CDW","CEG","CHTR","CMCSA","COIN","COST","CPRT",
    "CRWD","CSCO","CSGP","CSX","CTAS","CTSH","DASH","DDOG","DXCM","EA",
    "EXC","FANG","FAST","FTNT","GEHC","GFS","GILD","GOOG","GOOGL","HON",
    "IDXX","INTC","INTU","ISRG","KDP","KHC","KLAC","LIN","LRCX","LULU",
    "MAR","MCHP","MDB","MDLZ","MELI","META","MNST","MRVL","MSFT","MSTR",
    "MU","NFLX","NVDA","NXPI","ODFL","ON","ORLY","PANW","PAYX","PCAR",
    "PDD","PEP","PLTR","PYPL","QCOM","REGN","ROP","ROST","SBUX","SHOP",
    "SNPS","TEAM","TMUS","TSLA","TTD","TTWO","TXN","VRSK","VRTX","WBD",
    "WDAY","XEL","ZS"
]

def _get_us_500_assets():
    """Sadece resmi NASDAQ borsasında işlem gören hisseler (NASDAQ 100 + NASDAQ Global Select Market, 24 saat önbellekli)."""
    now = time.time()
    if US500_CACHE["assets"] and (now - US500_CACHE["ts"] < 86400):
        return US500_CACHE["assets"]
    max_tickers = int(os.getenv("NASDAQ_MAX_TICKERS", "500"))
    seen = set(NASDAQ100_TICKERS)
    ordered = list(NASDAQ100_TICKERS)
    try:
        import requests
        r = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", timeout=12)
        if r.status_code == 200:
            for line in r.text.splitlines()[1:-1]:
                parts = line.split("|")
                if len(parts) < 7:
                    continue
                sym, _name, mcat, test_issue, _status, _lot, etf = parts[:7]
                sym = sym.strip().upper()
                # Sadece gercek NASDAQ Global Select (Q) hisseleri, ETF ve test hisseleri haric
                if mcat == "Q" and test_issue == "N" and etf == "N" and sym.isalpha() and 1 <= len(sym) <= 4:
                    if sym not in seen:
                        seen.add(sym)
                        ordered.append(sym)
                        if len(ordered) >= max_tickers:
                            break
    except Exception as e:
        print(f"NASDAQ official list fetch error: {e}")
    assets = [
        {"symbol": t, "label": f"{t} (NASDAQ)", "short": t, "category": "NASDAQ",
         "source": "yfinance", "currency": "$", "tv_symbol": f"NASDAQ:{t}"}
        for t in ordered[:max_tickers]
    ]
    US500_CACHE["assets"] = assets
    US500_CACHE["ts"] = now
    return assets

@app.route('/api/traditional-markets')
def get_traditional_markets():
    """NASDAQ, NASDAQ 100, S&P 500, VIX, NVDA ve AAPL canlı özet verileri (60s cache)."""
    now = time.time()
    if TRADITIONAL_CACHE["data"] and (now - TRADITIONAL_CACHE["ts"] < 60):
        return jsonify({"status": "success", "markets": TRADITIONAL_CACHE["data"]})
    try:
        import yfinance as yf
        specs = [
            ("^IXIC", "NASDAQ Bileşik", "IXIC", "", "NASDAQ:IXIC", "ENDEKS"),
            ("^NDX", "NASDAQ 100", "NDX", "", "NASDAQ:NDX", "ENDEKS"),
            ("^GSPC", "S&P 500", "SPX", "", "SP:SPX", "ENDEKS"),
            ("^VIX", "Korku Endeksi", "VIX", "", "TVC:VIX", "VIX"),
            ("NVDA", "NVIDIA", "NVDA", "$", "NASDAQ:NVDA", "NASDAQ"),
            ("AAPL", "Apple", "AAPL", "$", "NASDAQ:AAPL", "NASDAQ"),
        ]
        df = yf.download([s[0] for s in specs], period="5d", interval="1d", progress=False, auto_adjust=False, threads=False)
        close = df["Close"] if "Close" in df else df
        markets = []
        for sym, name, short, cur, tv, cat in specs:
            if sym not in close:
                continue
            s = close[sym].dropna()
            if len(s) == 0:
                continue
            last = float(s.iloc[-1])
            prev = float(s.iloc[-2]) if len(s) >= 2 else last
            chg = ((last - prev) / prev * 100.0) if prev else 0.0
            markets.append({
                "id": short, "name": name, "symbol": short, "price": round(last, 2),
                "change": round(chg, 2), "currency": cur, "tv_symbol": tv, "category": cat
            })
        if markets:
            TRADITIONAL_CACHE["data"] = markets
            TRADITIONAL_CACHE["ts"] = now
        return jsonify({"status": "success", "markets": TRADITIONAL_CACHE["data"]})
    except Exception as e:
        return jsonify({"status": "error", "markets": TRADITIONAL_CACHE["data"], "error": str(e)})

def _fmt_price(val):
    if val is None:
        return 0.0
    if val >= 100:
        return round(float(val), 2)
    if val >= 1:
        return round(float(val), 3)
    return round(float(val), 5)

def _compute_setup_from_df(df, meta):
    """6 aşamalı teknik teyit (EMA20/50/100, MACD, RSI, Hacim, Kırılım/Destek, R/R) ile kırılım, kademe ve stop hesaplar."""
    import numpy as np
    if df is None or len(df) < 25:
        return None
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    vol = df['volume'].astype(float) if 'volume' in df else None

    price = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    change_pct = ((price - prev) / prev * 100.0) if prev else 0.0

    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else ema20
    ema100 = float(close.ewm(span=100, adjust=False).mean().iloc[-1]) if len(close) >= 100 else ema50

    ema12_s = close.ewm(span=12, adjust=False).mean()
    ema26_s = close.ewm(span=26, adjust=False).mean()
    macd_s = ema12_s - ema26_s
    sig_s = macd_s.ewm(span=9, adjust=False).mean()
    macd_bull = float(macd_s.iloc[-1]) > float(sig_s.iloc[-1])

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    rsi = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0

    tr = (high - low).combine((high - close.shift(1)).abs(), max).combine((low - close.shift(1)).abs(), max)
    atr = float(tr.rolling(14).mean().iloc[-1])
    if np.isnan(atr) or atr <= 0:
        atr = price * 0.02

    vol_ratio = 1.0
    if vol is not None and len(vol) >= 20:
        avg_v = float(vol.tail(20).mean())
        if avg_v > 0:
            vol_ratio = float(vol.iloc[-1]) / avg_v

    recent_high_20 = float(high.tail(20).max())
    recent_low_20 = float(low.tail(20).min())
    recent_low_10 = float(low.tail(10).min())

    breakout = max(recent_high_20, price + 0.6 * atr)
    support_1 = min(price - 0.5 * atr, max(ema20, recent_low_10))
    if support_1 >= price:
        support_1 = price - 0.8 * atr
    support_2 = min(support_1 - 0.8 * atr, recent_low_20)
    if support_2 <= 0:
        support_2 = price * 0.92

    entry_1 = (price + support_1) / 2.0
    entry_2 = (support_1 + support_2) / 2.0
    entry_breakout = breakout * 1.003

    stop_loss = max(support_2 - 0.5 * atr, price * 0.86)
    if stop_loss >= entry_2:
        stop_loss = entry_2 * 0.975

    risk = max(entry_1 - stop_loss, atr * 0.8)
    risk_pct = ((entry_1 - stop_loss) / entry_1) * 100.0 if entry_1 > 0 else 3.0

    tp1 = max(breakout, entry_1 + 1.5 * risk)
    tp2 = entry_1 + 2.5 * risk
    tp3 = entry_1 + 4.0 * risk
    rr_ratio = (tp2 - entry_1) / risk if risk > 0 else 2.0

    score = 0
    confirmations = 0
    reasons = []
    if price > ema20 and ema20 > ema50:
        score += 22; confirmations += 1; reasons.append("Güçlü yükseliş trendi (EMA20>EMA50)")
    elif price > ema20:
        score += 12; reasons.append("Fiyat EMA20 üstünde")
    else:
        reasons.append("Fiyat EMA20 altında")
    if price > ema100:
        score += 8; confirmations += 1
    if macd_bull:
        score += 15; confirmations += 1; reasons.append("MACD pozitif")
    if 44 <= rsi <= 66:
        score += 15; confirmations += 1; reasons.append(f"RSI ideal ({rsi:.0f})")
    elif 35 <= rsi < 44:
        score += 10; reasons.append(f"RSI destek bölgesinde ({rsi:.0f})")
    elif rsi < 35:
        score += 8; reasons.append(f"RSI aşırı satım ({rsi:.0f})")
    elif rsi > 74:
        score -= 10; reasons.append(f"RSI aşırı alım ({rsi:.0f})")
    if vol_ratio >= 1.25:
        score += 15; confirmations += 1; reasons.append(f"Hacim güçlü ({vol_ratio:.1f}x)")
    elif vol_ratio >= 0.95:
        score += 8
    dist_to_breakout = ((breakout - price) / price) * 100.0
    dist_to_s1 = ((price - support_1) / price) * 100.0
    if 0 < dist_to_breakout <= 3.5:
        score += 15; confirmations += 1; reasons.append(f"Kırılıma çok yakın (+%{dist_to_breakout:.1f})")
    elif 0 < dist_to_s1 <= 3.0:
        score += 12; confirmations += 1; reasons.append(f"S1 desteğine yakın (-%{dist_to_s1:.1f})")
    elif 0 < dist_to_breakout <= 7.0:
        score += 8
    if rr_ratio >= 2.2:
        score += 10; confirmations += 1

    score = max(5, min(98, int(score)))
    is_sniper = score >= SNIPER_MIN_SCORE
    if is_sniper:
        decision, decision_type = "🎯 SNIPER AL", "sniper"
    elif score >= 75:
        decision, decision_type = "GÜÇLÜ AL", "strong_buy"
    elif score >= 60:
        decision, decision_type = "KADEMELİ AL", "buy"
    elif score >= 45:
        decision, decision_type = "KIRILIM BEKLE", "wait"
    else:
        decision, decision_type = "DİKKAT / BEKLE", "caution"

    return {
        "symbol": meta["symbol"], "short": meta["short"], "label": meta["label"],
        "category": meta["category"], "currency": meta["currency"], "tv_symbol": meta["tv_symbol"],
        "price": _fmt_price(price), "change": round(change_pct, 2), "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2), "confirmations": confirmations, "score": score,
        "is_sniper": is_sniper, "decision": decision, "decision_type": decision_type,
        "breakout": _fmt_price(breakout), "breakout_dist_pct": round(dist_to_breakout, 2),
        "support_1": _fmt_price(support_1), "support_2": _fmt_price(support_2),
        "entry_1": _fmt_price(entry_1), "entry_2": _fmt_price(entry_2), "entry_breakout": _fmt_price(entry_breakout),
        "stop_loss": _fmt_price(stop_loss), "risk_pct": round(risk_pct, 2),
        "tp1": _fmt_price(tp1), "tp2": _fmt_price(tp2), "tp3": _fmt_price(tp3),
        "tp1_pct": round(((tp1 - entry_1) / entry_1) * 100.0, 2),
        "tp2_pct": round(((tp2 - entry_1) / entry_1) * 100.0, 2),
        "tp3_pct": round(((tp3 - entry_1) / entry_1) * 100.0, 2),
        "summary": " • ".join(reasons[:3]), "scanned_at": datetime.now().strftime("%H:%M")
    }

def _fetch_yfinance_ohlcv(symbol, period="6mo", interval="1d"):
    import yfinance as yf
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty:
            return None
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols].dropna()
    except Exception as e:
        print(f"yfinance OHLCV Error ({symbol}): {e}")
    return None

def _notify_sniper_telegram(s):
    now = time.time()
    if now - SNIPER_SENT_HISTORY.get(s["symbol"], 0) < 21600:
        return
    SNIPER_SENT_HISTORY[s["symbol"]] = now
    c = s["currency"]
    msg = (
        f"🎯 *NASDAQ SNIPER FIRSAT ({s['score']}/100)*\n"
        f"📌 *{s['short']}* | Anlık: `{c}{s['price']}` ({s['change']:+.2f}%)\n"
        f"⚡ Kırılım: `{c}{s['breakout']}` | 🛡️ Destek: `{c}{s['support_1']}` / `{c}{s['support_2']}`\n"
        f"📥 K1 `{c}{s['entry_1']}` | K2 `{c}{s['entry_2']}` | Kırılım Onay `{c}{s['entry_breakout']}`\n"
        f"🛑 Stop: `{c}{s['stop_loss']}` (-%{s['risk_pct']}) | 🎯 TP1 `{c}{s['tp1']}` TP2 `{c}{s['tp2']}` TP3 `{c}{s['tp3']}`"
    )
    capture_log(f"NASDAQ FIRSAT: {s['short']} ({s['score']}/100) @ {c}{s['price']}", "SNIPER")
    _paper_notify(msg)

def _paper_notify(text):
    tg_token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("CHAT_ID", "")
    if not tg_token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10
        )
    except Exception:
        pass

def _run_full_market_scan():
    """Sadece NASDAQ 100 + S&P 500 (~515 ABD hissesi) tarar."""
    global SNIPER_ALERTS
    setups = []
    yf_assets = _get_us_500_assets()
    try:
        import yfinance as yf
        chunk_size = 80
        for i in range(0, len(yf_assets), chunk_size):
            chunk = yf_assets[i:i + chunk_size]
            tickers = [m["symbol"] for m in chunk]
            try:
                raw = yf.download(tickers, period="6mo", interval="1d", group_by="ticker", progress=False, auto_adjust=False, threads=False)
                for meta in chunk:
                    sym = meta["symbol"]
                    if sym in raw:
                        sub = raw[sym].rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
                        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in sub.columns]
                        sub = sub[cols].dropna()
                        res = _compute_setup_from_df(sub, meta)
                        if res:
                            setups.append(res)
            except Exception as ce:
                print(f"NASDAQ chunk error ({i}): {ce}")
    except Exception as e:
        print(f"NASDAQ scan error: {e}")

    setups.sort(key=lambda x: x["score"], reverse=True)
    if setups:
        SETUPS_CACHE["data"] = setups
        SETUPS_CACHE["ts"] = time.time()
        SETUPS_CACHE["scanned_count"] = len(setups)
        SETUPS_CACHE["last_scan_str"] = datetime.now().strftime("%H:%M:%S")
        sniper_list = [s for s in setups if s["score"] >= SNIPER_MIN_SCORE]
        SNIPER_ALERTS = sniper_list
        for s in sniper_list:
            _notify_sniper_telegram(s)
    return SETUPS_CACHE["data"]

def _run_paper_cycle():
    if os.getenv("PAPER_TRADING", "1") != "1":
        return
    try:
        try:
            import paper_trader
        except ImportError:
            from src import paper_trader
        paper_trader.process_scan(SETUPS_CACHE["data"], notify=_paper_notify)
    except Exception as e:
        print(f"Paper Trader Error: {e}")

def start_sniper_scanner():
    """Arka planda her 10 dakikada bir tüm NASDAQ/S&P 500 hisselerini tarar ve 1500$ sanal portföyü yönetir."""
    time.sleep(3)
    while True:
        try:
            _run_full_market_scan()
            _run_paper_cycle()
        except Exception as e:
            print(f"NASDAQ Scanner Loop Error: {e}")
        time.sleep(600)

@app.route('/api/sniper-alerts')
def get_sniper_alerts():
    return jsonify({
        "status": "success", "min_score": SNIPER_MIN_SCORE,
        "scanned_count": SETUPS_CACHE["scanned_count"],
        "last_scan": SETUPS_CACHE["last_scan_str"],
        "alerts": SNIPER_ALERTS
    })

@app.route('/api/paper-portfolio')
def get_paper_portfolio():
    try:
        try:
            import paper_trader
        except ImportError:
            from src import paper_trader
        data = paper_trader.get_dashboard()
        data["status"] = "success"
        return jsonify(data)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/trade-setups')
def get_trade_setups():
    now = time.time()
    if SETUPS_CACHE["data"] and (now - SETUPS_CACHE["ts"] < 300):
        return jsonify({
            "status": "success", "scanned_count": SETUPS_CACHE["scanned_count"],
            "last_scan": SETUPS_CACHE["last_scan_str"], "setups": SETUPS_CACHE["data"]
        })
    data = _run_full_market_scan()
    return jsonify({
        "status": "success", "scanned_count": SETUPS_CACHE["scanned_count"],
        "last_scan": SETUPS_CACHE["last_scan_str"], "setups": data
    })

@app.route('/api/trade-setup/<path:query>')
def get_single_trade_setup(query):
    """Kullanıcının yazdığı herhangi bir NASDAQ / ABD hissesi için anlık kırılım/destek/kademe/stop analizi."""
    q = query.strip().upper().replace(" ", "")
    if not q:
        return jsonify({"status": "error", "message": "Sembol boş olamaz."}), 400
    aliases = {"NASDAQ": "^NDX", "NDX": "^NDX", "SP500": "^GSPC", "SPX": "^GSPC"}
    sym = aliases.get(q, q)
    df = _fetch_yfinance_ohlcv(sym)
    if df is not None and len(df) >= 25:
        meta = {"symbol": sym, "label": f"{q} (NASDAQ/ABD)", "short": q, "category": "NASDAQ",
                "source": "yfinance", "currency": "$", "tv_symbol": f"NASDAQ:{q}" if not sym.startswith("^") else "NASDAQ:NDX"}
        res = _compute_setup_from_df(df, meta)
        if res:
            return jsonify({"status": "success", "setup": res})
    return jsonify({"status": "error", "message": f"'{q}' bulunamadı. Örnek: NVDA, AAPL, TSLA, MSFT, META, PLTR, AMD"}), 404

def run_server():
    """Run Flask server (NASDAQ Edition)"""
    import flask.cli
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    threading.Thread(target=start_sniper_scanner, daemon=True).start()
    print("🌐 Web Dashboard Aktif (NASDAQ Edition): http://localhost:9001", flush=True)
    app.run(host='0.0.0.0', port=9001, debug=False, use_reloader=False)

if __name__ == '__main__':
    run_server()
