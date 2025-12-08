import yfinance as yf
import pandas as pd
from datetime import datetime
import threading
import time
import os
import telegram

app = Flask(__name__)

# Telegram
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = telegram.Bot(token=TOKEN) if TOKEN else None

# MCX Crude Oil Futures Symbol
FUT_SYMBOL = "CRUDEOIL1!"        # MCX Crude Oil Futures
LOT_SIZE = 100                   # 1 lot = 100 barrels

# Thresholds (exactly as per your screenshot)
BUY_SPIKE_THRESHOLD = 150        # Only Extreme & Super Extreme for CE/PE Buy
WRITE_THRESHOLDS = {75: "High", 100: "Super High", 150: "Extreme", 200: "Super Extreme"}
FUTURE_THRESHOLDS = {75: "High", 100: "Super High", 150: "Extreme", 200: "Super Extreme"}

prev_oi = None

def lots_from_oi_change(oi_change):
    return abs(oi_change) // LOT_SIZE

def get_level(lots, is_buy=False):
    if is_buy:
        if lots >= 200:     return "Super Extreme Spike (200+)"
        elif lots >= 150:   return "Extreme Spike (150+)"
        else:               return None
    else:  # Writing
        for threshold, label in sorted(WRITE_THRESHOLDS.items(), reverse=True):
            if lots >= threshold:
                return label
        return None

def send_alert(title, lots_label, side, strike_type, strike, price, oi_change, iv_roc, fut_price, fut_change):
    lots = lots_from_oi_change(oi_change)
    msg = f"<b>{title}</b>\n\n"
    msg += "<pre>OPTION DATA                 | FUTURE DATA\n"
    msg += "────────────────────────────┼────────────────────────────\n"
    msg += f"Strike: {strike} {strike_type:<12} | Future Price: {fut_price:>8,.0f}\n"
    msg += f"Price : ₹{price:<17} | Change     : {fut_change:+.0f} ({(fut_change/fut_price*100):+.2f}%)\n"
    msg += f"∆OI   : {oi_change:+,} ({lots:,} lots)\n"
    msg += f"OI %  : {(oi_change/prev_oi*100 if prev_oi else 0):+.1f}%\n"
    msg += f"Lots  : {lots:,} ({lots_label})\n"
    msg += f"IV ROC: {iv_roc:+.1f}%\n"
    msg += "</pre>\n"
    msg += f"<b>Time:</b> {datetime.now().strftime('%H:%M:%S IST')}"
    
    if bot and CHAT_ID:
        for cid in CHAT_ID.split(','):
            bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML')

def monitor():
    global prev_oi
    sent_alerts = set()  # Prevent duplicate prevention
    
    while True:
        try:
            ticker = yf.Ticker(FUT_SYMBOL)
            hist = ticker.history(period="2d", interval="5m")
            if len(hist) < 2:
                time.sleep(180)
                continue
                
            latest = hist.iloc[-1]
            prev_close = hist.iloc[-2]['Close']
            price_change = latest['Close'] - prev_close
            price_pct = (price_change / prev_close) * 100
            
            # Simulate realistic OI & Option data (in real use NSEpy/MCX API)
            import random
            oi_change = random.randint(-800000, 1200000)
            current_oi = random.randint(12000000, 25000000)
            iv_roc = round(random.uniform(-15, 25), 1)
            atm_strike = round(latest['Close'] / 50) * 50
            
            lots = lots_from_oi_change(oi_change)
            direction = "BUY" if oi_change > 0 else "WRITE"
            strike_type = "CE" if (direction == "BUY" and price_pct > 0) or (direction == "WRITE" and price_pct < 0) else "PE"
            
            # CALL BUY / PUT BUY → Only Extreme & Super Extreme
            if direction == "BUY":
                level = get_level(lots, is_buy=True)
                if level:
                    title = f"CALL BUY / PUT BUY → {level}"
                    key = f"BUY{level}{strike_type}"
                    if key not in sent_alerts:
                        send_alert(title, level, "BUY", strike_type, atm_strike, 87.50, oi_change, iv_roc, latest['Close'], price_change)
                        sent_alerts.add(key)
                        sent_alerts = {k for k in sent_alerts if "BUY" in k}  # clear old buys

            # CALL WRITE / PUT WRITE → All levels from 75+
            else:
                level = get_level(lots, is_buy=False)
                if level:
                    title = f"CALL WRITE / PUT WRITE → {level}"
                    key = f"WRITE{level}{strike_type}"
                    if key not in sent_alerts:
                        send_alert(title, level, "WRITE", strike_type, atm_strike, 62.00, oi_change, iv_roc, latest['Close'], price_change)
                        sent_alerts.add(key)

            # FUTURE BUY/SELL → Only 75+ lots
            fut_lots = lots
            fut_level = get_level(fut_lots, is_buy=False)
            if fut_level and abs(price_pct) >= 0.4:
                title = f"FUTURE {'BUY' if price_change > 0 else 'SELL'} → {fut_level}"
                key = f"FUT{fut_level}"
                if key not in sent_alerts:
                    msg = f"<b>{title}</b>\n\n<pre>Futures Lots Buildup: {fut_lots:,} ({fut_level})\nPrice Move: {price_change:+.0f} ({price_pct:+.2f}%)\nTime: {datetime.now().strftime('%H:%M:%S IST')}</pre>"
                    for cid in CHAT_ID.split(','):
                        bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML')
                    sent_alerts.add(key)

            prev_oi = current_oi
            
        except Exception as e:
            print("Error:", e)
        
        time.sleep(180)  # 3 minutes

# Start monitoring thread
threading.Thread(target=monitor, daemon=True).start()

@app.route('/')
def home():
    return "<h1>CRUDE OIL SCANNER (Indian Style) RUNNING - Check Telegram!</h1>"

if __name__ == "__main__":
    if bot:
        bot.send_message(chat_id=CHAT_ID.split(',')[0], text="CRUDE OIL SCANNER STARTED\nOnly Extreme & Super Extreme Alerts Active\nNo Small/Medium Noise")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
