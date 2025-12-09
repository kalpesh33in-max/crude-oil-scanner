from flask import Flask
import yfinance as yf
import pandas as pd
from datetime import datetime
import threading
import time
import os
import telegram
import random
import asyncio # Ensure asyncio is imported globally for cleaner use

app = Flask(__name__)

# Telegram setup
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = telegram.Bot(token=TOKEN) if TOKEN else None

# WTI Crude Oil Futures (real ticker - MCX not on yfinance)
FUT_SYMBOL = "CL=F"
LOT_SIZE = 1000

# Thresholds
BUY_SPIKE_THRESHOLD = 150
WRITE_THRESHOLDS = {75: "High", 100: "Super High", 150: "Extreme", 200: "Super Extreme"}
FUTURE_THRESHOLDS = {75: "High", 100: "Super High", 150: "Extreme", 200: "Super Extreme"}

prev_oi = None
sent_alerts = set()


# --- NEW HELPER FUNCTION FOR STATUS ALERTS ---

def send_status_message(status_text, is_error=False):
    """Sends a simplified status or error message to Telegram."""
    if bot and CHAT_ID:
        try:
            # Use a distinctive emoji for status
            prefix = "🚨 " if is_error else "🟢 "
            
            for cid in CHAT_ID.split(','):
                # asyncio.run is required here because this function may be called from the main thread
                # or a monitoring thread, and telegram-bot V21+ is async.
                asyncio.run(bot.send_message(
                    chat_id=cid.strip(), 
                    text=f"{prefix}SCANNER STATUS:\n{status_text}", 
                    parse_mode='HTML'
                ))
        except Exception as e:
            # This is a fatal error if we can't even send the status message
            print(f"FATAL Telegram status send error: {e}")

# ---------------------------------------------


def lots_from_oi_change(oi_change):
    return abs(oi_change) // LOT_SIZE

def get_level(lots, is_buy=False):
    if is_buy:
        if lots >= 200:   return "Super Extreme Spike (200+)"
        elif lots >= 150: return "Extreme Spike (150+)"
        else:             return None
    else:
        for threshold, label in sorted(WRITE_THRESHOLDS.items(), reverse=True):
            if lots >= threshold:
                return label
        return None

def send_alert(title, lots_label, side, strike_type, strike, price, oi_change, iv_roc, fut_price, fut_change, pct_change, strike_category):
    """MODIFIED: Includes strike_category in the message format."""
    lots = lots_from_oi_change(oi_change)
    oi_pct = (oi_change / prev_oi * 100) if prev_oi and prev_oi != 0 else 0
    
    msg = f"<b>{title}</b>\n\n"
    msg += "<pre>OPTION DATA                       | FUTURE DATA\n"
    msg += "────────────────────────────┼────────────────────────────\n"
    msg += f"Strike: {strike} {strike_type:<12} | Future Price: {fut_price:>8,.2f}\n"
    msg += f"Price : ${price:<17} | Change      : {fut_change:+.2f} ({pct_change:+.2f}%)\n"
    msg += f"∆OI   : {oi_change:+,} ({lots:,} lots)\n"
    msg += f"OI %  : {oi_pct:+.1f}%\n"
    msg += f"Lots  : {lots:,} ({lots_label})\n"
    msg += f"IV ROC: {iv_roc:+.1f}%\n"
    msg += f"Type  : {strike_category}\n"
    msg += "</pre>\n"
    msg += f"<b>Time:</b> {datetime.now().strftime('%H:%M:%S IST')}"

    if bot and CHAT_ID:
        try:
            for cid in CHAT_ID.split(','):
                asyncio.run(bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML'))
        except Exception as e:
            print(f"Telegram alert send error: {e}")


def monitor():
    global prev_oi, sent_alerts
    
    while True:
        try:
            # 1. Fetch Data
            ticker = yf.Ticker(FUT_SYMBOL)
            hist = ticker.history(period="2d", interval="5m")
            if len(hist) < 2:
                time.sleep(180)
                continue
                
            latest = hist.iloc[-1]
            prev_close = hist.iloc[-2]['Close']
            fut_price = latest['Close']
            price_change = fut_price - prev_close
            price_pct = (price_change / prev_close) * 100
            
            # 2. Simulation
            sim_strike = round(fut_price * 2) / 2.0
            ce_oi_change = random.randint(-800000, 1200000)
            pe_oi_change = random.randint(-800000, 1200000)
            total_oi_change = ce_oi_change + pe_oi_change
            base_oi = random.randint(5000000, 15000000)
            current_oi = (prev_oi or base_oi) + total_oi_change 
            
            
            # 3. CE (CALL) LOGIC (Specific Alert Titles + ATM/ITM Filter)
            ce_lots = lots_from_oi_change(ce_oi_change)
            ce_iv_roc = round(random.uniform(-15, 25), 1)
            ce_option_price = round(random.uniform(0.50, 5.00), 2)
            
            if sim_strike < fut_price:
                ce_category = "ITM"
            elif abs(sim_strike - fut_price) < 0.1:
                ce_category = "ATM"
            else:
                ce_category = "OTM"
            
            if ce_category in ["ATM", "ITM"]:
                
                # CALL BUY (OI Increase > 0)
                if ce_oi_change > 0 and ce_lots >= BUY_SPIKE_THRESHOLD:
                    level = get_level(ce_lots, is_buy=True)
                    if level:
                        title = f"CALL BUY → {level} ({ce_category})"
                        key = f"BUY{level}CE{ce_category}"
                        if key not in sent_alerts:
                            send_alert(title, level, "BUY", "CE", sim_strike, ce_option_price, ce_oi_change, ce_iv_roc, fut_price, price_change, price_pct, ce_category)
                            sent_alerts.add(key)
                
                # CALL WRITE (OI Decrease < 0)
                elif ce_oi_change < 0 and ce_lots >= 75:
                    level = get_level(ce_lots, is_buy=False)
                    if level:
                        title = f"CALL WRITE → {level} ({ce_category})"
                        key = f"WRITE{level}CE{ce_category}"
                        if key not in sent_alerts:
                            send_alert(title, level, "WRITE", "CE", sim_strike, ce_option_price, ce_oi_change, ce_iv_roc, fut_price, price_change, price_pct, ce_category)
                            sent_alerts.add(key)


            # 4. PE (PUT) LOGIC (Specific Alert Titles + ATM/ITM Filter)
            pe_lots = lots_from_oi_change(pe_oi_change)
            pe_iv_roc = round(random.uniform(-15, 25), 1)
            pe_option_price = round(random.uniform(0.50, 5.00), 2)
            
            if sim_strike > fut_price:
                pe_category = "ITM"
            elif abs(sim_strike - fut_price) < 0.1:
                pe_category = "ATM"
            else:
                pe_category = "OTM"
            
            if pe_category in ["ATM", "ITM"]:
                
                # PUT BUY (OI Increase > 0)
                if pe_oi_change > 0 and pe_lots >= BUY_SPIKE_THRESHOLD:
                    level = get_level(pe_lots, is_buy=True)
                    if level:
                        title = f"PUT BUY → {level} ({pe_category})"
                        key = f"BUY{level}PE{pe_category}"
                        if key not in sent_alerts:
                            send_alert(title, level, "BUY", "PE", sim_strike, pe_option_price, pe_oi_change, pe_iv_roc, fut_price, price_change, price_pct, pe_category)
                            sent_alerts.add(key)
                            
                # PUT WRITE (OI Decrease < 0)
                elif pe_oi_change < 0 and pe_lots >= 75:
                    level = get_level(pe_lots, is_buy=False)
                    if level:
                        title = f"PUT WRITE → {level} ({pe_category})"
                        key = f"WRITE{level}PE{pe_category}"
                        if key not in sent_alerts:
                            send_alert(title, level, "WRITE", "PE", sim_strike, pe_option_price, pe_oi_change, pe_iv_roc, fut_price, price_change, price_pct, pe_category)
                            sent_alerts.add(key)

            # 5. FUTURE BUY/SELL
            fut_lots = lots_from_oi_change(total_oi_change)
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
                                asyncio.run(bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML'))
                        except Exception as e:
                            print(f"Telegram future alert error: {e}")
                    sent_alerts.add(key)

            # 6. Housekeeping
            if len(sent_alerts) > 100:
                sent_alerts = set()
            
            prev_oi = current_oi
            
        except Exception as e:
            # --- MODIFIED: Error Alert Sent to Telegram ---
            error_msg = f"ERROR: Monitor failed to fetch or process data. Retrying in 3 minutes.\nDetails: {e}"
            print(error_msg)
            send_status_message(error_msg, is_error=True)
            # The while loop will continue and retry after time.sleep(180)
        
        time.sleep(180)


# Start monitoring thread
threading.Thread(target=monitor, daemon=True).start()

@app.route('/')
def home():
    return "<h1>CRUDE OIL SCANNER (Indian Style) RUNNING - Check Telegram!</h1>"

if __name__ == "__main__":
    if bot and CHAT_ID:
        # --- MODIFIED: Initial Startup Alert Sent to Telegram ---
        send_status_message("Scanner Initializing...\nMonitoring CL=F for ATM/ITM Extreme/Super Extreme Spikes (150+ lots for Buy, 75+ for Write).")
        
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
