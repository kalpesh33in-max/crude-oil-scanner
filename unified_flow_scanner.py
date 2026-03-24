import os
import re
import logging
from collections import defaultdict
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)

# ================= CONFIGURATION =================
# Ensure these are set in your Environment Variables
BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
BOT_TOKEN_2 = os.getenv("SUMMARIZER_BOT_TOKEN_2") # Optional secondary bot

TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_2MIN_CHAT_ID = os.getenv("SUMMARY_2MIN_CHAT_ID")
SUMMARY_5MIN_CHAT_ID = os.getenv("SUMMARY_5MIN_CHAT_ID")

# Initialize secondary bot if token exists
bot2 = Bot(token=BOT_TOKEN_2) if BOT_TOKEN_2 else None

# ================= ASSET DATA =================
TRACK_SYMBOLS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN"]

LOT_SIZES = {
    "BANKNIFTY": 30,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "AXISBANK": 625,
    "SBIN": 750
}

# Buffers to hold incoming data
buffer_2min = []
buffer_5min = []

# ================= UTILITY FUNCTIONS =================
def format_money(v):
    if v >= 1e7: return f"{v/1e7:.2f}Cr"
    elif v >= 1e5: return f"{v/1e5:.2f}L"
    return str(int(v))

def bias_label(x):
    if x > 500: return "🔥 VERY STRONG BULLISH"
    if x > 150: return "🚀 STRONG BULLISH"
    if x > 0: return "🟢 Mild Bullish"
    if x < -500: return "🔥 VERY STRONG BEARISH"
    if x < -150: return "📉 STRONG BEARISH"
    if x < 0: return "🔴 Mild Bearish"
    return "⚖ Neutral"

def classify_strike(strike, option_type, future_price):
    try:
        strike, future_price = float(strike), float(future_price)
        if option_type == "CE":
            return "ITM" if strike < future_price else "OTM"
        if option_type == "PE":
            return "ITM" if strike > future_price else "OTM"
    except: pass
    return None

# ================= PARSE LOGIC =================
def parse_alert(text):
    text = text.upper()
    
    # Extract core fields
    symbol_match = re.search(r"SYMBOL:\s*([\w:-]+)", text)
    lots_match = re.search(r"LOTS:\s*(\d+)", text)
    price_match = re.search(r"PRICE:\s*([\d.]+)", text)
    fut_match = re.search(r"FUTURE PRICE:\s*([\d.]+)", text)

    if not symbol_match or not lots_match:
        return None

    symbol_full = symbol_match.group(1)
    lots = int(lots_match.group(1))
    price = float(price_match.group(1)) if price_match else None
    fut_price = float(fut_match.group(1)) if fut_match else None

    # Identify base symbol
    base = next((s for s in TRACK_SYMBOLS if s in symbol_full), None)
    if not base: return None

    # Determine if Future or Option
    is_future = "FUT" in symbol_full or "FUTURE" in text
    opt_match = re.search(r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}(\d+)(?:CE|PE)$", symbol_full)
    
    zone = None
    option_type = None
    act = None

    if opt_match and fut_price:
        strike = opt_match.group(1)
        option_type = "CE" if symbol_full.endswith("CE") else "PE"
        zone = classify_strike(strike, option_type, fut_price)

    # Action Logic Mapping
    if "WRITER" in text:
        act = "CALL_WRITER" if option_type == "CE" else "PUT_WRITER"
    elif "CALL BUY" in text:
        act = "CALL_BUY"
    elif "PUT BUY" in text:
        act = "PUT_BUY"
    elif "SHORT COVERING" in text:
        act = "FUTURE_SC" if is_future else ("CALL_SC" if option_type=="CE" else "PUT_SC")
    elif "LONG UNWINDING" in text:
        act = "FUTURE_UNW" if is_future else ("CALL_UNW" if option_type=="CE" else "PUT_UNW")
    elif "FUTURE BUY" in text or "BUY (LONG)" in text:
        act = "FUTURE_BUY"
    elif "FUTURE SELL" in text:
        act = "FUTURE_SELL"

    if not act: return None

    return {
        "symbol": base, "lots": lots, "price": price, 
        "future": fut_price, "action": act, "zone": zone
    }

# ================= SUMMARY BUILDER =================
def build_summary(batch, mode):
    opt = defaultdict(lambda: defaultdict(lambda: {"ITM":0,"OTM":0}))
    opt_turn = defaultdict(lambda: defaultdict(lambda: {"ITM":0.0,"OTM":0.0}))
    fut = defaultdict(lambda: defaultdict(int))
    fut_turn = defaultdict(lambda: defaultdict(float))
    last_price = {}

    for a in batch:
        s, act, lots, price, zone = a["symbol"], a["action"], a["lots"], a["price"], a["zone"]
        if a["future"]: last_price[s] = a["future"]

        if "FUTURE" in act:
            fut[s][act] += lots
            fut_turn[s][act] += lots * (a["future"] or 0) * LOT_SIZES.get(s, 1) # Calculation adjustment
        else:
            if zone:
                opt[s][act][zone] += lots
                # Standardized turnover logic
                val = (lots * 125000) if mode == "2min" else (lots * (price or 0) * LOT_SIZES.get(s, 0))
                opt_turn[s][act][zone] += val

    title = "2 MIN" if mode=="2min" else "5 MIN"
    msg = f"<b>📊 {title} INSTITUTIONAL FLOW REPORT</b>\n<pre>"

    for s in TRACK_SYMBOLS:
        if s not in opt and s not in fut: continue
        
        msg += f"\n💎 {s} (FUT: {last_price.get(s,'N/A')})\n"
        
        if s in opt:
            msg += "-"*35 + "\n"
            bull_l = bear_l = bull_t = bear_t = 0
            for act, zones in opt[s].items():
                itm_l, otm_l = zones["ITM"], zones["OTM"]
                itm_t, otm_t = opt_turn[s][act]["ITM"], opt_turn[s][act]["OTM"]
                
                tot_l = itm_l + otm_l
                tot_t = itm_t + otm_t
                
                label = act.replace("_WRITER", "_WR").replace("FUTURE_", "F_")
                msg += f"{label[:7]:7} | I:{itm_l} O:{otm_l} T:{format_money(tot_t)}\n"

                if act in ["PUT_WRITER","CALL_BUY","PUT_SC","CALL_UNW"]:
                    bull_l += tot_l; bull_t += tot_t
                else:
                    bear_l += tot_l; bear_t += tot_t
            
            msg += f"Bias: {bias_label(bull_l - bear_l)}\n"

    msg += "</pre>"
    return msg

# ================= TELEGRAM HANDLERS =================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message
    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        data = parse_alert(msg.text)
        if data:
            buffer_2min.append(data)
            buffer_5min.append(data)

async def job_2min(context: ContextTypes.DEFAULT_TYPE):
    global buffer_2min
    if not buffer_2min: return
    text = build_summary(buffer_2min.copy(), "2min")
    buffer_2min.clear()
    await context.bot.send_message(chat_id=SUMMARY_2MIN_CHAT_ID, text=text, parse_mode="HTML")

async def job_5min(context: ContextTypes.DEFAULT_TYPE):
    global buffer_5min
    if not buffer_5min: return
    text = build_summary(buffer_5min.copy(), "5min")
    buffer_5min.clear()
    target = bot2 if bot2 else context.bot
    await target.send_message(chat_id=SUMMARY_5MIN_CHAT_ID, text=text, parse_mode="HTML")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.Chat(int(TARGET_CHANNEL_ID)), message_handler))
    
    # Schedule Jobs
    app.job_queue.run_repeating(job_2min, interval=120, first=10)
    app.job_queue.run_repeating(job_5min, interval=300, first=20)

    print("Scanner is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
