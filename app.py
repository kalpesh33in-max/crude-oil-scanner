import asyncio
# --- CRITICAL PATCH FOR ASYNCIO/THREADING CONFLICT ---
# Define a policy that creates a new event loop for any thread if one is not present.
# This permanently resolves the 'Event loop is closed' crash in threaded environments (like Gunicorn).
class AnyThreadEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    def get_event_loop(self):
        try:
            # Try to get the existing loop (standard behavior)
            return super().get_event_loop()
        except RuntimeError:
            # If no loop is set (or if the main loop is closed), create a new one.
            loop = self.new_event_loop()
            self.set_event_loop(loop)
            return loop
        
# Apply the policy globally
asyncio.set_event_loop_policy(AnyThreadEventLoopPolicy())
# --------------------------------------------------------

from flask import Flask
import yfinance as yf
import pandas as pd
from datetime import datetime
import threading
import time
import os
import telegram
import random

app = Flask(__name__)

# Telegram setup
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# Initializing bot outside of async functions
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


# --- WRITER ACTIVITY ANALYSIS ---

def get_writer_activity(oi_change, iv_roc, strike_type, price_change):
    """
    Determines the Option Writer Activity based on OI, IV, and Price Change.
    """
    oi_rising = oi_change > 0
    iv_rising = iv_roc > 0
    
    # Use a small buffer to define price movement (0.05 or more)
    price_favors_call = price_change > 0.05 
    price_favors_put = price_change < -0.05
    
    # 1. New OI (OI Increase)
    if oi_rising:
        if iv_rising:
            # OI ^ + IV ^ (Hedging / Forced Writing)
            if (strike_type == "CE" and price_favors_call) or (strike_type == "PE" and price_favors_put):
                 return "Hedging / Forced Writing (Rising IV suggests high risk for sellers.)"
            return "Strong Accumulation / High Volatility Buy" 

        else: # IV falling
            # OI ^ + IV v (Fresh Writing / Position Building)
            if (strike_type == "CE" and price_favors_call) or (strike_type == "PE" and price_favors_put):
                return "Fresh Writing / Position Building (Writers are actively selling new contracts, high conviction.)"
            return "Strong Accumulation / Low Volatility Buy"

    # 2. Position Exit (OI Decrease)
    else:
        if iv_rising:
            # OI v + IV ^ (Unwinding / Position Exiting) 
            if (strike_type == "CE" and price_favors_call) or (strike_type == "PE" and price_favors_put):
                return "Unwinding / Position Exiting (Writers actively buying back due to higher risk/IV.)"
            return "Liquidation / Forced Exit by Buyers" 
            
        else: # IV falling
            # OI v + IV v (Profit Booking / Minor Exit)
            if (strike_type == "CE" and price_favors_call) or (strike_type == "PE" and price_favors_put):
                return "Profit Booking / Minor Exit (Writers closing positions as price moves slightly in their favor.)"
            return "Profit Booking by Buyers / Low Volatility Exit"

# --- ASYNC/STATUS HELPERS ---

async def async_send_message(cid, message, is_error):
    """Internal async helper to send a message."""
    prefix = "🚨 " if is_error else "🟢 "
    await bot.send_message(
        chat_id=cid.strip(), 
        text=f"{prefix}SCANNER STATUS:\n{message}", 
        parse_mode='HTML'
    )

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

async def async_send_alert(title, lots_label, side, strike_type, strike, price, oi_change, iv_roc, fut_price, fut_change, pct_change, strike_category, writer_activity):
    """
    Ensures writer_activity is included in the message.
    """
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
    msg += f"<b>Activity:</b> {writer_activity}\n"
    msg += f"<b>Time:</b> {datetime.now().strftime('%H:%M:%S IST')}"

    if bot and CHAT_ID:
        try:
            for cid in CHAT_ID.split(','):
                await bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML')
        except Exception as e:
            print(f"Telegram alert send error: {e}")


def monitor():
    global prev_oi, sent_alerts
    
    try:
        # Loop creation is now safe due to the global patch
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except Exception as e:
        print(f"Failed to create new asyncio loop: {e}")
        return

    async def async_send_status(status_text, is_error):
        if bot and CHAT_ID:
            for cid in CHAT_ID.split(','):
                await async_send_message(cid, status_text, is_error)

    async def run_monitoring_logic(is_first_run):
        nonlocal prev_oi, sent_alerts
        
        if is_first_run:
            await async_send_status("Scanner Initializing...\nMonitoring CL=F for ATM/ITM Extreme/Super Extreme Spikes.", is_error=False)
        
        # 1. Fetch Data 
        ticker = yf.Ticker(FUT_SYMBOL)
        hist = ticker.history(period="2d", interval="5m") 
        if len(hist) < 2:
            return 
            
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
        
        
        # 3. CE (CALL) LOGIC
        ce_lots = lots_from_oi_change(ce_oi_change)
        ce_iv_roc = round(random.uniform(-15, 25), 1)
        
        # ITM/ATM Category Check
        ce_category = "ITM" if sim_strike < fut_price else ("ATM" if abs(sim_strike - fut_price) < 0.1 else "OTM")
        
        # PRICE SIMULATION FIX: Higher price for ITM options
        ce_option_price = round(random.uniform(0.50, 5.00), 2)
        if ce_category == "ITM":
            ce_option_price = round(random.uniform(10.00, 20.00), 2) 

        if ce_category in ["ATM", "ITM"]:
            
            ce_activity = get_writer_activity(ce_oi_change, ce_iv_roc, "CE", price_change) 

            # CALL BUY (OI Increase > 0)
            if ce_oi_change > 0 and ce_lots >= BUY_SPIKE_THRESHOLD:
                level = get_level(ce_lots, is_buy=True)
                if level:
                    title = f"CALL BUY → {level} ({ce_category})"
                    key = f"BUY{level}CE{ce_category}"
                    if key not in sent_alerts:
                        await async_send_alert(title, level, "BUY", "CE", sim_strike, ce_option_price, ce_oi_change, ce_iv_roc, fut_price, price_change, price_pct, ce_category, ce_activity)
                        sent_alerts.add(key)
            
            # CALL WRITE (OI Decrease < 0)
            elif ce_oi_change < 0 and ce_lots >= 75:
                level = get_level(ce_lots, is_buy=False)
                if level:
                    title = f"CALL WRITE → {level} ({ce_category})"
                    key = f"WRITE{level}CE{ce_category}"
                    if key not in sent_alerts:
                        await async_send_alert(title, level, "WRITE", "CE", sim_strike, ce_option_price, ce_oi_change, ce_iv_roc, fut_price, price_change, price_pct, ce_category, ce_activity)
                        sent_alerts.add(key)


        # 4. PE (PUT) LOGIC
        pe_lots = lots_from_oi_change(pe_oi_change)
        pe_iv_roc = round(random.uniform(-15, 25), 1)
        
        # ITM/ATM Category Check
        pe_category = "ITM" if sim_strike > fut_price else ("ATM" if abs(sim_strike - fut_price) < 0.1 else "OTM")

        # PRICE SIMULATION FIX: Higher price for ITM options
        pe_option_price = round(random.uniform(0.50, 5.00), 2)
        if pe_category == "ITM":
            pe_option_price = round(random.uniform(10.00, 20.00), 2) 

        if pe_category in ["ATM", "ITM"]:
            
            pe_activity = get_writer_activity(pe_oi_change, pe_iv_roc, "PE", price_change) 

            # PUT BUY (OI Increase > 0)
            if pe_oi_change > 0 and pe_lots >= BUY_SPIKE_THRESHOLD:
                level = get_level(pe_lots, is_buy=True)
                if level:
                    title = f"PUT BUY → {level} ({pe_category})"
                    key = f"BUY{level}PE{pe_category}"
                    if key not in sent_alerts:
                        await async_send_alert(title, level, "BUY", "PE", sim_strike, pe_option_price, pe_oi_change, pe_iv_roc, fut_price, price_change, price_pct, pe_category, pe_activity)
                        sent_alerts.add(key)
                        
            # PUT WRITE (OI Decrease < 0)
            elif pe_oi_change < 0 and pe_lots >= 75:
                level = get_level(pe_lots, is_buy=False)
                if level:
                    title = f"PUT WRITE → {level} ({pe_category})"
                    key = f"WRITE{level}PE{pe_category}"
                    if key not in sent_alerts:
                        await async_send_alert(title, level, "WRITE", "PE", sim_strike, pe_option_price, pe_oi_change, pe_iv_roc, fut_price, price_change, price_pct, pe_category, pe_activity)
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
                            await bot.send_message(chat_id=cid.strip(), text=msg, parse_mode='HTML')
                    except Exception as e:
                        print(f"Telegram future alert error: {e}")
                sent_alerts.add(key)

        # 6. Housekeeping
        if len(sent_alerts) > 100:
            sent_alerts = set()
        
        globals()['prev_oi'] = current_oi


    # Main monitoring loop (Synchronous part)
    is_first_run = True
    while True:
        try:
            loop.run_until_complete(run_monitoring_logic(is_first_run))
            is_first_run = False 
            
        except Exception as e:
            error_msg = f"ERROR: Monitor failed to fetch or process data. Retrying in 3 minutes.\nDetails: {e}"
            print(error_msg)
            loop.run_until_complete(async_send_status(error_msg, is_error=True)) 
            is_first_run = True 
        
        time.sleep(180) 


# --- GUNICORN COMPATIBLE STARTUP ---

# 1. Start monitoring thread immediately when the file is loaded by Gunicorn.
threading.Thread(target=monitor, daemon=True).start()

@app.route('/')
def home():
    return "<h1>CRUDE OIL SCANNER (Indian Style) RUNNING - Check Telegram!</h1>"

# 2. **DO NOT** include the 'if __name__ == "__main__":' block here.
# Gunicorn handles starting the app via the 'gunicorn app:app' command.
