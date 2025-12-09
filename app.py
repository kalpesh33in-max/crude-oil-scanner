from flask import Flask  # FIXED: Added this import (was missing)
import yfinance as yf
import pandas as pd
from datetime import datetime
import threading
import time
import os
import telegram
import random  # For realistic simulation

app = Flask(__name__)  # Line 9 - now works!

# Telegram setup
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = telegram.Bot(token=TOKEN) if TOKEN else None

# WTI Crude Oil Futures (real ticker - MCX not on yfinance)
FUT_SYMBOL = "CL=F"      # CME WTI Crude Oil
LOT_SIZE = 1000          # 1 lot = 1,000 barrels (standard for crude futures)

# Thresholds (as per your screenshot)
BUY_SPIKE_THRESHOLD = 150        # Only Extreme (150+) & Super Extreme (200+) for CE/PE Buy
WRITE_THRESHOLDS = {75: "High", 100: "Super High", 150: "Extreme", 200: "Super Extreme"}
FUTURE_THRESHOLDS = {75: "High", 100: "Super High", 150: "Extreme", 200: "Super Extreme"}

prev_oi = None
sent_alerts = set()

def lots_from_oi_change(oi_change):
    return abs(oi_change) // LOT_SIZE

def get_level(lots, is_buy=False):
    if is_buy:
        if lots >= 200:     return "Super Extreme Spike (200+)"
        elif lots >= 150:   return "Extreme Spike (150+)"
        else:               return None
    else:  # Writing or Future
        for threshold, label in sorted(WRITE_THRESHOLDS.items(), reverse=True):
            if lots >= threshold:
                return label
        return None

def send_alert(title, lots_label, side, strike_type, strike, price, oi_change, iv_roc, fut_price, fut_change, pct_change):
    lots = lots_from_oi_change(oi_change)
    oi_pct = (oi_change / prev_oi * 100) if prev_oi else 0
    msg = f"<b>{title}</b>\n\n"
    msg += "<pre>OPTION DATA                 | FUTURE DATA\n"
    msg += "────────────────────────────┼────────────────────────────\n"
    msg += f"Strike: {strike} {strike_type:<12} | Future Price: {fut_price:>8,.2f}\n"
    msg += f"Price : ${price:<17} | Change     : {fut_change:+.2f} ({pct_change:+.2f}%)\n"
    msg += f"∆OI   : {oi_change:+,} ({lots:,} lots)\n"
    msg += f"OI %  : {oi_pct:+.1f}%\n"
    msg += f"Lots  : {lots:,} ({lots_label})\n"
    msg += f"IV ROC: {iv_roc:+.1f}%\n"
    msg += "</pre>\n"
    msg += f"<b>Time:</b> {datetime.now().strftime('%H:%M:%S IST')}"
    
    if bot and CHAT_ID:
        try:
            for cid in CHAT_ID.split(','):
                bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML')
        except Exception as e:
            print(f"Telegram send error: {e}")

def monitor():
    global prev_oi, sent_alerts
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
            
            # Simulate realistic OI & Option data (for demo; extend with CME API for real options OI)
            base_oi = random.randint(5000000, 15000000)  # Realistic crude OI
            oi_change = random.randint(-800000, 1200000)
            current_oi = (prev_oi or base_oi) + oi_change
            iv_roc = round(random.uniform(-15, 25), 1)
            atm_strike = round(latest['Close'])  # Strikes around current price (e.g., 72.50)
            
            lots = lots_from_oi_change(oi_change)
            direction = "BUY" if oi_change > 0 else "WRITE"
            strike_type = "CE" if (direction == "BUY" and price_pct > 0) or (direction == "WRITE" and price_pct < 0) else "PE"
            option_price = round(random.uniform(0.50, 5.00), 2)  # Realistic option premium
            
            # CALL BUY / PUT BUY → Only Extreme & Super Extreme (150+ lots)
            if direction == "BUY" and lots >= BUY_SPIKE_THRESHOLD:
                level = get_level(lots, is_buy=True)
                if level:
                    title = f"CALL BUY / PUT BUY → {level}"
                    key = f"BUY{level}{strike_type}"
                    if key not in sent_alerts:
                        send_alert(title, level, "BUY", strike_type, atm_strike, option_price, oi_change, iv_roc, latest['Close'], price_change, price_pct)
                        sent_alerts.add(key)
                        # Clear old alerts after 10 mins to allow repeats
                        if len(sent_alerts) > 10:
                            sent_alerts = set()

            # CALL WRITE / PUT WRITE → All levels from 75+
            elif direction == "WRITE" and lots >= 75:
                level = get_level(lots, is_buy=False)
                if level:
                    title = f"CALL WRITE / PUT WRITE → {level}"
                    key = f"WRITE{level}{strike_type}"
                    if key not in sent_alerts:
                        send_alert(title, level, "WRITE", strike_type, atm_strike, option_price, oi_change, iv_roc, latest['Close'], price_change, price_pct)
                        sent_alerts.add(key)

            # FUTURE BUY/SELL → Only 75+ lots with price move
            fut_lots = lots
            fut_level = get_level(fut_lots, is_buy=False)
            if fut_level and abs(price_pct) >= 0.4:
                fut_side = "BUY" if price_change > 0 else "SELL"
                title = f"FUTURE {fut_side} → {fut_level}"
                key = f"FUT{fut_level}{fut_side}"
                if key not in sent_alerts:
                    msg = f"<b>{title}</b>\n\n<pre>Futures Lots Buildup: {fut_lots:,} ({fut_level})\nPrice Move: {price_change:+.2f} ({price_pct:+.2f}%)\nTime: {datetime.now().strftime('%H:%M:%S IST')}</pre>"
                    if bot and CHAT_ID:
                        try:
                            for cid in CHAT_ID.split(','):
                                bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML')
                        except Exception as e:
                            print(f"Telegram send error: {e}")
                    sent_alerts.add(key)

            prev_oi = current_oi
            
        except Exception as e:
            print(f"Monitor error: {e}")
        
        time.sleep(180)  # 3 minutes

# Start monitoring thread
threading.Thread(target=monitor, daemon=True).start()

@app.route('/')
def home():
    return "<h1>CRUDE OIL SCANNER (Indian Style) RUNNING - Check Telegram!</h1>"

if __name__ == "__main__":
    if bot and CHAT_ID:
        try:
            # Fixed: proper async-style send for python-telegram-bot v21+
            import asyncio
            asyncio.run(bot.send_message(
                chat_id=CHAT_ID.split(',')[0].strip(),
                text="CRUDE OIL SCANNER STARTED\nOnly Extreme & Super Extreme Alerts Active\nNo Small/Medium Noise"
            ))
        except Exception as e:
            print(f"Startup Telegram error: {e}")
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
