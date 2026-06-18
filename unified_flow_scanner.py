import os
import re
import logging
import pytz
import json
import uuid
import requests
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

# ================= ENV =================
BOT_TOKEN = os.getenv("SUMMARIZER_BOT_TOKEN")
BOT_TOKEN_2 = os.getenv("SUMMARIZER_BOT_TOKEN_2")

TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")
SUMMARY_2MIN_CHAT_ID = os.getenv("SUMMARY_2MIN_CHAT_ID")
SUMMARY_5MIN_CHAT_ID = os.getenv("SUMMARY_5MIN_CHAT_ID")

# Matrix / Element X Credentials
MATRIX_HOMESERVER = os.getenv("MATRIX_HOMESERVER", "https://matrix.org")
MATRIX_ACCESS_TOKEN = os.getenv("MATRIX_ACCESS_TOKEN", "")
MATRIX_USER = os.getenv("MATRIX_USER", "")
MATRIX_PASS = os.getenv("MATRIX_PASS", "")
MATRIX_TOKEN_FILE = "matrix_access_token.txt"
MATRIX_ROOM_ID_2MIN = os.getenv("crude-oil-2min") or os.getenv("MATRIX_ROOM_ID_2MIN", "")
MATRIX_ROOM_ID_5MIN = os.getenv("crude-oil-5min") or os.getenv("MATRIX_ROOM_ID_5MIN", "")

bot2 = Bot(token=BOT_TOKEN_2) if BOT_TOKEN_2 else None

# ================= DATA =================
buffer_2min = []
buffer_5min = []

TRACK_SYMBOLS = ["BANKNIFTY","HDFCBANK","ICICIBANK","NIFTY","SENSEX"]

LOT_SIZES = {
    "BANKNIFTY": 30,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "NIFTY": 65,
    "SENSEX": 20
}

NEAR_ITM_RANGE = {
    "BANKNIFTY": 100,
    "HDFCBANK": 5,
    "ICICIBANK": 10,
    "NIFTY": 50,
    "SENSEX": 100
}

# ================= UTILS =================
def perform_matrix_login():
    if not MATRIX_USER or not MATRIX_PASS:
        return None
    
    login_url = f"{MATRIX_HOMESERVER}/_matrix/client/v3/login"
    payload = {
        "type": "m.login.password",
        "user": MATRIX_USER,
        "password": MATRIX_PASS,
        "initial_device_display_name": "InstitutionalScannerAuto"
    }
    
    try:
        response = requests.post(login_url, json=payload, timeout=15)
        if response.status_code == 200:
            token = response.json().get("access_token")
            if token:
                with open(MATRIX_TOKEN_FILE, "w") as f:
                    f.write(token)
                logger.info("Matrix auto-login successful.")
                return token
        else:
            logger.error(f"Matrix auto-login failed: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Matrix auto-login error: {e}")
    return None

def get_matrix_token():
    # 1. Try to read from file first
    token = None
    if os.path.exists(MATRIX_TOKEN_FILE):
        try:
            with open(MATRIX_TOKEN_FILE, "r") as f:
                token = f.read().strip()
        except Exception as e:
            logger.error(f"Error reading {MATRIX_TOKEN_FILE}: {e}")
    
    # 2. Fallback to environment variable
    if not token:
        token = MATRIX_ACCESS_TOKEN
        
    # 3. Auto-login if still no token
    if not token:
        token = perform_matrix_login()
        
    return token

async def send_matrix_message(message, room_id):
    token = get_matrix_token()
    if not (token and room_id):
        return
    try:
        # Strip HTML tags for Matrix body
        clean_msg = re.sub(r'<[^>]+>', '', message)
        txn_id = str(uuid.uuid4())
        url = f"{MATRIX_HOMESERVER}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "msgtype": "m.text",
            "body": clean_msg
        }

        def do_request(h):
            return requests.put(url, headers=h, data=json.dumps(payload), timeout=10)

        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: do_request(headers))

        if res.status_code == 401:
            logger.warning("Matrix token expired. Attempting auto-login...")
            new_token = perform_matrix_login()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                res = await loop.run_in_executor(None, lambda: do_request(headers))

        if res.status_code != 200:
            logger.error(f"Matrix Delivery Error: {res.status_code} - {res.text}")
    except Exception as e:
        logger.error(f"Matrix Exception: {e}")

def format_money(v):
    if v >= 1e7: return f"{v/1e7:.2f}Cr"
    elif v >= 1e5: return f"{v/1e5:.2f}L"
    return str(int(v))

def format_future(act):
    return act.replace("FUTURE_","FUT_")

def bias_label(x):
    if x > 500: return "🔥 VERY STRONG BULLISH"
    if x > 150: return "🚀 STRONG BULLISH"
    if x > 0: return "🟢 Mild Bullish"
    if x < -500: return "🔥 VERY STRONG BEARISH"
    if x < -150: return "📉 STRONG BEARISH"
    if x < 0: return "🔴 Mild Bearish"
    return "⚖ Neutral"

# ================= STRIKE =================
def classify_strike(strike, option_type, future_price, symbol=None):
    try:
        strike = float(strike)
        future_price = float(future_price)
        near_range = NEAR_ITM_RANGE.get(symbol, 0)

        if abs(strike - future_price) <= near_range:
            return "ITM"

        if option_type == "CE":
            return "ITM" if strike < future_price else "OTM"
        elif option_type == "PE":
            return "ITM" if strike > future_price else "OTM"
    except: pass
    return None

# ================= PARSER =================
def parse_alert(text):
    text = text.upper()

    symbol = re.search(r"SYMBOL:\s*([^\n\r]+)", text)
    lots = re.search(r"LOTS:\s*(\d+)", text)
    price = re.search(r"PRICE:\s*([\d.]+)", text)
    fut = re.search(r"FUTURE PRICE:\s*([\d.]+)", text)

    if not symbol or not lots:
        return None

    symbol_full = symbol.group(1).strip()
    lots = int(lots.group(1))
    price = float(price.group(1)) if price else None
    fut_price = float(fut.group(1)) if fut else None

    base = next((s for s in TRACK_SYMBOLS if s in symbol_full), None)
    if not base:
        return None

    opt_match = re.search(r"(\d+)(CE|PE)$", symbol_full)
    zone = None
    option_type = None

    if opt_match and fut_price:
        strike = opt_match.group(1)
        option_type = opt_match.group(2)
        zone = classify_strike(strike, option_type, fut_price, base)

    is_future = (opt_match is None)

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
    elif "FUTURE SELL" in text or "SELL (SHORT)" in text:
        act = "FUTURE_SELL"
    else:
        return None

    return {
        "symbol": base,
        "lots": lots,
        "price": price,
        "future": fut_price,
        "action": act,
        "zone": zone,
        "timestamp": datetime.now(IST)
    }

# ================= HANDLER =================
async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post or update.message
    if msg and msg.text and str(msg.chat_id) == str(TARGET_CHANNEL_ID):
        data = parse_alert(msg.text)
        if data:
            buffer_2min.append(data)
            buffer_5min.append(data)

# ================= SUMMARY =================
def build_summary(batch, mode):
    opt = defaultdict(lambda: defaultdict(lambda: {"ITM":0,"OTM":0}))
    opt_turn = defaultdict(lambda: defaultdict(lambda: {"ITM":0.0,"OTM":0.0}))
    fut = defaultdict(lambda: defaultdict(int))
    fut_turn = defaultdict(lambda: defaultdict(float))
    last_price = {}

    for a in batch:
        s, act, lots, price, zone = a["symbol"], a["action"], a["lots"], a["price"], a["zone"]
        if a["future"]:
            last_price[s] = a["future"]

        if "FUTURE" in act:
            fut[s][act] += lots
            fut_turn[s][act] += lots * 175000
        else:
            if zone:
                opt[s][act][zone] += lots
                # DIFFERENT OUTPUT LOGIC: 2min uses fixed 1.25L for Writing, 5min uses Price*Lots
                if mode == "2min" and ("WRITER" in act or "_SC" in act):
                    opt_turn[s][act][zone] += lots * 125000
                else:
                    if price:
                        opt_turn[s][act][zone] += lots * price * LOT_SIZES[s]

    title = "2 MIN" if mode=="2min" else "5 MIN"
    msg = f"<pre>\n📊 {title} INSTITUTIONAL FLOW REPORT\n\n"

    for s in TRACK_SYMBOLS:
        if s not in opt and s not in fut:
            continue

        msg += f"💎 {s} (FUT: {last_price.get(s,'N/A')})\n\n"

        if s in opt:
            msg += "--- OPTIONS FLOW ---\n"
            msg += f"{'TYPE':8}{'ITM':>14}{'OTM':>14}{'TOT':>14}\n"
            msg += "-"*50 + "\n"

            bull=bear=0
            bull_t=bear_t=0

            for act in opt[s]:
                itm_l = opt[s][act]["ITM"]
                otm_l = opt[s][act]["OTM"]
                itm_t = opt_turn[s][act]["ITM"]
                otm_t = opt_turn[s][act]["OTM"]
                tot_l = itm_l + otm_l
                tot_t = itm_t + otm_t

                name = act.replace("CALL_WRITER","CALL_WR").replace("PUT_WRITER","PUT_WR")
                msg += f"{name[:8]:8}{f'{itm_l}({format_money(itm_t)})':>14}{f'{otm_l}({format_money(otm_t)})':>14}{f'{tot_l}({format_money(tot_t)})':>14}\n"

                if act in ["PUT_WRITER", "CALL_BUY", "CALL_SC", "PUT_UNW"]:
                    bull += tot_l; bull_t += tot_t
                elif act in ["CALL_WRITER", "PUT_BUY", "PUT_SC", "CALL_UNW"]:
                    bear += tot_l; bear_t += tot_t

            msg += "-"*50 + "\n"
            msg += f"Option Bias: {bias_label(bull-bear)}\n"
            msg += f"Bullish Turn: {format_money(bull_t)}\n"
            msg += f"Bearish Turn: {format_money(bear_t)}\n\n"

        if s in fut:
            msg += "---- FUTURES FLOW ----\n"
            f_bull=f_bear=0
            f_bt=f_bt2=0
            for act in fut[s]:
                l = fut[s][act]
                t = fut_turn[s][act]
                name = format_future(act)
                msg += f"{name:10} : {l} lots ({format_money(t)})\n"
                if act in ["FUTURE_BUY","FUTURE_SC"]:
                    f_bull += l; f_bt += t
                else:
                    f_bear += l; f_bt2 += t

            msg += f"\nFuture Bias: {bias_label(f_bull-f_bear)}\n"
            msg += f"Bullish Turn: {format_money(f_bt)}\n"
            msg += f"Bearish Turn: {format_money(f_bt2)}\n"

        msg += "\n========================================\n\n"

    msg += f"Validity: Next {title}\n</pre>"
    return msg

# ================= JOBS =================
async def process_2min(context):
    global buffer_2min
    now = datetime.now(IST)
    
    current_time_int = now.hour * 100 + now.minute
    if current_time_int < 915 or current_time_int > 1530:
        return

    if not buffer_2min: return

    # Back to original 1 minute logic
    batch = [a for a in buffer_2min if a["timestamp"] >= now - timedelta(minutes=1)]
    buffer_2min = [a for a in buffer_2min if a["timestamp"] >= now - timedelta(minutes=1)]

    if not batch: return

    msg = build_summary(batch,"2min")
    # Send to Telegram
    try:
        await context.bot.send_message(chat_id=SUMMARY_2MIN_CHAT_ID,text=msg,parse_mode="HTML")
    except Exception as e:
        logger.error(f"Telegram 2min Send Error: {e}")

    # Send to Matrix
    await send_matrix_message(msg, MATRIX_ROOM_ID_2MIN)

async def process_5min(context):
    global buffer_5min
    now = datetime.now(IST)

    current_time_int = now.hour * 100 + now.minute
    if current_time_int < 915 or current_time_int > 1530:
        return

    if not buffer_5min: return

    # Back to original 1 minute logic
    batch = [a for a in buffer_5min if a["timestamp"] >= now - timedelta(minutes=1)]
    buffer_5min = [a for a in buffer_5min if a["timestamp"] >= now - timedelta(minutes=1)]

    if not batch: return

    msg = build_summary(batch,"5min")
    # Send to Telegram
    try:
        target = bot2 if bot2 else context.bot
        await target.send_message(chat_id=SUMMARY_5MIN_CHAT_ID,text=msg,parse_mode="HTML")
    except Exception as e:
        logger.error(f"Telegram 5min Send Error: {e}")

    # Send to Matrix
    await send_matrix_message(msg, MATRIX_ROOM_ID_5MIN)

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handler))

    app.job_queue.run_repeating(process_2min, interval=60, first=10)
    app.job_queue.run_repeating(process_5min, interval=60, first=20)

    app.run_polling()

if __name__ == "__main__":
    main()
