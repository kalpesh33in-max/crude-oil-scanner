import os
import re
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# Setup Logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_CHAT_ID = os.getenv("SUMMARY_CHAT_ID")

alerts_buffer = []
TRACK_SYMBOLS = ["BANKNIFTY", "HDFCBANK", "ICICIBANK"]
ATM_RANGE = {"BANKNIFTY": 100, "HDFCBANK": 5, "ICICIBANK": 10}
last_future_prices = {s: 0.0 for s in TRACK_SYMBOLS}

def format_val(val):
    abs_v = abs(val)
    if abs_v >= 10000000: return f"{val/10000000:.2f} Cr"
    if abs_v >= 100000: return f"{val/100000:.2f} L"
    return f"{val:,.0f}"

def classify_strike(symbol, strike, opt_type, fut_price):
    width = ATM_RANGE.get(symbol, 0)
    if abs(strike - fut_price) <= width: return "ATM"
    if opt_type == "CE": return "ITM" if strike < (fut_price - width) else "OTM"
    if opt_type == "PE": return "ITM" if strike > (fut_price + width) else "OTM"
    return None

def parse_alert(text):
    t = text.upper()
    sym_m = re.search(r"SYMBOL:\s*([\w\s-]+)", t)
    lot_m = re.search(r"LOTS:\s*(\d+)", t)
    prc_m = re.search(r"PRICE:\s*([\d.]+)", t)
    oi_m = re.search(r"OI CHANGE:\s*([+-]?[\d,]+)", t)
    if not (sym_m and lot_m): return None

    raw_sym = sym_m.group(1).strip()
    base_sym = next((s for s in TRACK_SYMBOLS if s in raw_sym), None)
    if not base_sym: return None

    lots = int(lot_m.group(1))
    price = float(prc_m.group(1)) if prc_m else 0
    oi = abs(int(oi_m.group(1).replace(",",""))) if oi_m else 0
    
    # Update future price if alert is a FUT alert
    if "FUT" in raw_sym: last_future_prices[base_sym] = price

    # Categorization
    act = "OTHER"
    if "CALL WRITER" in t: act = "CALL_WRITER"
    elif "PUT WRITER" in t: act = "PUT_WRITER"
    elif "CALL BUY" in t: act = "CALL_BUY"
    elif "PUT BUY" in t: act = "PUT_BUY"
    elif "SHORT COVERING" in t: act = "CALL_SC" if "CE" in raw_sym else "PUT_SC" if "PE" in raw_sym else "FUT_SC"
    elif "UNWINDING" in t: act = "CALL_UNW" if "CE" in raw_sym else "PUT_UNW" if "PE" in raw_sym else "FUT_UNW"
    elif "FUTURE BUY" in t: act = "FUT_BUY"
    elif "FUTURE SELL" in t: act = "FUT_SELL"

    # Zone Classification
    zone = "TOTAL"
    strike_m = re.search(r"(\d{4,6})", raw_sym)
    if strike_m and last_future_prices[base_sym] > 0:
        opt_type = "CE" if "CE" in raw_sym else "PE" if "PE" in raw_sym else None
        if opt_type: zone = classify_strike(base_sym, int(strike_m.group(1)), opt_type, last_future_prices[base_sym])

    val = (oi * price) if "FUT" not in act else (lots * 100000)
    return {"sym": base_sym, "act": act, "lots": lots, "val": val, "zone": zone}

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message
    if msg and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        p = parse_alert(msg.text)
        if p: alerts_buffer.append(p)

async def send_flow_report(context: ContextTypes.DEFAULT_TYPE):
    global alerts_buffer
    if not alerts_buffer: return
    batch, alerts_buffer = list(alerts_buffer), []
    
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for a in batch:
        data[a['sym']][a['act']][a['zone']] += a['lots']
        data[a['sym']][a['act']]["VAL"] += a['val']

    # Using HTML/Pre for table alignment like in your photo
    msg = "<b>📊 2 MIN FLOW REPORT</b>\n\n<pre>"
    total_bull_v = total_bear_v = total_bull_l = total_bear_l = 0

    for sym in TRACK_SYMBOLS:
        if sym not in data: continue
        f_prc = last_future_prices[sym]
        msg += f"🔹 {sym} (FUT: {f_prc})\n"
        msg += f"{'TYPE':14} {'ITM':>5} {'OTM':>5} {'TOT':>5}\n"
        msg += "-" * 32 + "\n"
        
        acts = ["CALL_WRITER", "PUT_WRITER", "CALL_BUY", "PUT_BUY", "CALL_SC", "PUT_SC"]
        for a in acts:
            itm, otm = data[sym][a]["ITM"], data[sym][a]["OTM"]
            tot = itm + otm + data[sym][a]["ATM"]
            msg += f"{a.replace('_',' '):14} {itm:5} {otm:5} {tot:5}\n"
        
        msg += "-" * 32 + "\n"
        msg += f"{'FUT BUY':14} {data[sym]['FUT_BUY']['TOTAL']:>17}\n"
        msg += f"{'FUT SELL':14} {data[sym]['FUT_SELL']['TOTAL']:>17}\n\n"

        # Math for Summary Logic
        b_v = data[sym]['PUT_WRITER']['VAL'] + data[sym]['CALL_BUY']['VAL'] + data[sym]['FUT_BUY']['VAL']
        r_v = data[sym]['CALL_WRITER']['VAL'] + data[sym]['PUT_BUY']['VAL'] + data[sym]['FUT_SELL']['VAL']
        total_bull_v += b_v; total_bear_v += r_v

    # Bottom Logic from your Photo
    net_v = total_bull_v - total_bear_v
    bias = "🚀 BULLISH" if net_v > 0 else "📉 BEARISH" if net_v < 0 else "⚖️ NEUTRAL"
    
    msg += "</pre>\n<b>📈 TOTAL SUMMARY</b>\n"
    msg += f"Bullish Value: {format_val(total_bull_v)}\n"
    msg += f"Bearish Value: {format_val(total_bear_v)}\n"
    msg += f"Net Dominance: <b>{format_val(net_v)}</b>\n"
    msg += f"Market Bias  : <b>{bias}</b>\n\n"
    msg += "⏳ <i>Validity: Next 2 Minutes Only</i>"

    await context.bot.send_message(chat_id=SUMMARY_CHAT_ID, text=msg, parse_mode="HTML")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))
    app.job_queue.run_repeating(send_flow_report, interval=120, first=10)
    app.run_polling()

if __name__ == "__main__":
    main()
