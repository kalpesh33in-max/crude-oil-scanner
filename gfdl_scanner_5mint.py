import os
import re
import logging
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ==============================
# LOGGING
# ==============================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_CHAT_ID = os.getenv("SUMMARY_CHAT_ID")

alerts_buffer = []

TRACK_SYMBOLS = ["BANKNIFTY","HDFCBANK","ICICIBANK","AXISBANK","SBIN"]

LOT_SIZES = {
    "BANKNIFTY":30,
    "HDFCBANK":550,
    "ICICIBANK":700,
    "AXISBANK":625,
    "SBIN":750
}

# ==============================
# MONEY FORMAT
# ==============================
def format_money(v):

    if v >= 1e7:
        return f"{v/1e7:.2f}Cr"

    if v >= 1e5:
        return f"{v/1e5:.2f}L"

    return f"{v:.0f}"

# ==============================
# ITM / OTM
# ==============================
def classify_strike(strike,opt,fut):

    strike=float(strike)
    fut=float(fut)

    if opt=="CE":
        return "ITM" if strike < fut else "OTM"

    if opt=="PE":
        return "ITM" if strike > fut else "OTM"

    return None

# ==============================
# BIAS
# ==============================
def get_bias_label(net):

    if net > 500:
        return "🔥 VERY STRONG BULLISH"

    if net >150:
        return "🚀 STRONG BULLISH"

    if net>0:
        return "🟢 Mild Bullish"

    if net<-500:
        return "🔥 VERY STRONG BEARISH"

    if net<-150:
        return "📉 STRONG BEARISH"

    if net<0:
        return "🔴 Mild Bearish"

    return "⚖ Neutral"

# ==============================
# PARSER
# ==============================
def parse_alert(text):

    t=text.upper()

    symbol_match=re.search(r"SYMBOL:\s*([\w-]+)",t)
    lot_match=re.search(r"LOTS:\s*(\d+)",t)
    price_match=re.search(r"PRICE:\s*([\d.]+)",t)
    fut_match=re.search(r"FUTURE:\s*([\d.]+)",t)

    if not(symbol_match and lot_match):
        return None

    symbol_full=symbol_match.group(1)
    lots=int(lot_match.group(1))
    price=float(price_match.group(1)) if price_match else None
    fut=float(fut_match.group(1)) if fut_match else None

    base=next((s for s in TRACK_SYMBOLS if s in symbol_full),None)

    if not base:
        return None

    opt_match=re.search(
        r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d+)(CE|PE)",
        symbol_full
    )

    zone=None
    opt=None

    if opt_match and fut:

        strike=opt_match.group(3)
        opt=opt_match.group(4)

        zone=classify_strike(strike,opt,fut)

    act=None

    if "PUT WRITER" in t:
        act="PUT_WR"

    elif "CALL WRITER" in t:
        act="CALL_WR"

    elif "SHORT COVERING" in t:

        if opt=="CE":
            act="CALL_SC"
        else:
            act="PUT_SC"

    elif "LONG UNWINDING" in t:

        if opt=="CE":
            act="CALL_UNW"
        else:
            act="PUT_UNW"

    elif "CALL BUY" in t:
        act="CALL_BUY"

    elif "PUT BUY" in t:
        act="PUT_BUY"

    elif "FUTURE BUY" in t:
        act="FUTURE_BUY"

    elif "FUTURE SELL" in t:
        act="FUTURE_SELL"

    if not act:
        return None

    return {
        "symbol":base,
        "lots":lots,
        "zone":zone,
        "act":act,
        "price":price,
        "future":fut
    }

# ==============================
# TELEGRAM HANDLER
# ==============================
async def message_handler(update:Update,context:ContextTypes.DEFAULT_TYPE):

    msg=update.channel_post or update.message

    if msg and msg.text and str(msg.chat_id)==str(TARGET_CHANNEL_ID):

        parsed=parse_alert(msg.text)

        if parsed:
            alerts_buffer.append(parsed)

# ==============================
# SUMMARY
# ==============================
async def process_summary(context:ContextTypes.DEFAULT_TYPE):

    global alerts_buffer

    if not alerts_buffer:
        return

    batch=list(alerts_buffer)
    alerts_buffer.clear()

    opt_data=defaultdict(lambda:defaultdict(lambda:defaultdict(int)))
    opt_turn=defaultdict(lambda:defaultdict(lambda:defaultdict(float)))

    fut_data=defaultdict(lambda:defaultdict(int))
    fut_turn=defaultdict(lambda:defaultdict(float))

    last_future={}

    for a in batch:

        sym=a["symbol"]
        act=a["act"]
        zone=a["zone"]
        lots=a["lots"]
        price=a["price"]

        lot_size=LOT_SIZES.get(sym,1)

        if a["future"]:
            last_future[sym]=a["future"]

        if zone:

            opt_data[sym][act][zone]+=lots

            if price:
                opt_turn[sym][act][zone]+=lots*price*lot_size

        else:

            fut_data[sym][act]+=lots
            fut_turn[sym][act]+=lots*175000

    msg="<pre>\n📊 2 MIN INSTITUTIONAL FLOW REPORT\n\n"

    for sym in TRACK_SYMBOLS:

        if sym not in opt_data and sym not in fut_data:
            continue

        msg+=f"💎 {sym} (FUT: {last_future.get(sym,'N/A')})\n"

        # OPTIONS
        if sym in opt_data:

            msg+="--- OPTIONS FLOW ---\n"

            msg+=f"{'TYPE':10}{'ITM':>13}{'OTM':>13}{'TOT':>13}\n"
            msg+="-""*"*49+"\n"

            bull=0
            bear=0
            bull_turn=0
            bear_turn=0

            for act in opt_data[sym]:

                itm_l=opt_data[sym][act]["ITM"]
                otm_l=opt_data[sym][act]["OTM"]

                itm_t=opt_turn[sym][act]["ITM"]
                otm_t=opt_turn[sym][act]["OTM"]

                tot_l=itm_l+otm_l
                tot_t=itm_t+otm_t

                itm_str=f"{itm_l}({format_money(itm_t)})"
                otm_str=f"{otm_l}({format_money(otm_t)})"
                tot_str=f"{tot_l}({format_money(tot_t)})"

                msg+=f"{act:10}{itm_str:>13}{otm_str:>13}{tot_str:>13}\n"

                if act in ["PUT_WR","CALL_BUY","CALL_SC","PUT_UNW"]:
                    bull+=tot_l
                    bull_turn+=tot_t
                else:
                    bear+=tot_l
                    bear_turn+=tot_t

            msg+="-""*"*49+"\n"

            net=bull-bear

            msg+=f"Option Bias: {get_bias_label(net)}\n"
            msg+=f"Bullish Turn: {format_money(bull_turn)}\n"
            msg+=f"Bearish Turn: {format_money(bear_turn)}\n\n"

        # FUTURES
        if sym in fut_data:

            msg+="---- FUTURES FLOW ----\n"

            bull=0
            bear=0
            bull_turn=0
            bear_turn=0

            for act in fut_data[sym]:

                lots=fut_data[sym][act]
                turn=fut_turn[sym][act]

                msg+=f"{act:10} : {lots}({format_money(turn)})\n"

                if act in ["FUTURE_BUY"]:
                    bull+=lots
                    bull_turn+=turn
                else:
                    bear+=lots
                    bear_turn+=turn

            msg+=f"Future Bias: {get_bias_label(bull-bear)}\n"
            msg+=f"Bullish Turn: {format_money(bull_turn)}\n"
            msg+=f"Bearish Turn: {format_money(bear_turn)}\n"

        msg+="="*49+"\n\n"

    msg+="Validity: Next 2 Minutes\n"
    msg+="</pre>"

    await context.bot.send_message(
        chat_id=SUMMARY_CHAT_ID,
        text=msg,
        parse_mode="HTML"
    )

# ==============================
# MAIN
# ==============================
def main():

    app=Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND),message_handler)
    )

    if app.job_queue:
        app.job_queue.run_repeating(process_summary,interval=60,first=10)

    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
