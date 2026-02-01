import asyncio
import websockets
import json
import time
import requests
from datetime import datetime
import re
import functools
import os
import sys
from zoneinfo import ZoneInfo

# ==============================================================================
# ============================== CONFIGURATION =================================
# ==============================================================================

# --- Environment Variable Loading ---
# Load credentials securely from environment variables
API_KEY = os.environ.get("API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# --- Validation ---
# Ensure all required environment variables are set
required_vars = {
    "API_KEY": API_KEY,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
}

missing_vars = [key for key, value in required_vars.items() if value is None]

if missing_vars:
    print(f"❌ Critical Error: Missing required environment variables: {', '.join(missing_vars)}", flush=True)
    print("Please set these variables in your deployment environment and restart the application.", flush=True)
    sys.exit(1) # Exit the script with a non-zero status code to indicate failure

# --- API and Connection ---
WSS_URL = "wss://nimblewebstream.lisuns.com:4576/"

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# --- Symbol List (Options & Futures) ---
SYMBOLS_TO_MONITOR = [
    "BANKNIFTY24FEB2658900CE", "BANKNIFTY24FEB2658900PE", "BANKNIFTY24FEB2658800CE", "BANKNIFTY24FEB2658800PE",
    "BANKNIFTY24FEB2658700CE", "BANKNIFTY24FEB2658700PE", "BANKNIFTY24FEB2658600CE", "BANKNIFTY24FEB2658600PE",
    "BANKNIFTY24FEB2658500CE", "BANKNIFTY24FEB2658500PE", "BANKNIFTY24FEB2658400CE", "BANKNIFTY24FEB2658400PE",
    "BANKNIFTY24FEB2659000CE", "BANKNIFTY24FEB2659000PE", "BANKNIFTY24FEB2659100CE", "BANKNIFTY24FEB2659100PE",
    "BANKNIFTY24FEB2659200CE", "BANKNIFTY24FEB2659200PE", "BANKNIFTY24FEB2659300CE", "BANKNIFTY24FEB2659300PE",
    "BANKNIFTY24FEB2659400CE", "BANKNIFTY24FEB2659400PE",
    "HDFCBANK24FEB26930CE", "HDFCBANK24FEB26930PE", "HDFCBANK24FEB26925CE", "HDFCBANK24FEB26925PE",
    "HDFCBANK24FEB26920CE", "HDFCBANK24FEB26920PE", "HDFCBANK24FEB26915CE", "HDFCBANK24FEB26915PE",
    "HDFCBANK24FEB26910CE", "HDFCBANK24FEB26910PE", "HDFCBANK24FEB26905CE", "HDFCBANK24FEB26905PE",
    "HDFCBANK24FEB26935CE", "HDFCBANK24FEB26935PE", "HDFCBANK24FEB26940CE", "HDFCBANK24FEB26940PE",
    "HDFCBANK24FEB26945CE", "HDFCBANK24FEB26945PE", "HDFCBANK24FEB26950CE", "HDFCBANK24FEB26950PE",
    "HDFCBANK24FEB26955CE", "HDFCBANK24FEB26955PE",
    "SBIN24FEB261040CE", "SBIN24FEB261040PE", "SBIN24FEB261035CE", "SBIN24FEB261035PE",
    "SBIN24FEB261030CE", "SBIN24FEB261030PE", "SBIN24FEB261025CE", "SBIN24FEB261025PE",
    "SBIN24FEB261020CE", "SBIN24FEB261020PE", "SBIN24FEB261015CE", "SBIN24FEB261015PE",
    "SBIN24FEB261045CE", "SBIN24FEB261045PE", "SBIN24FEB261050CE", "SBIN24FEB261050PE",
    "SBIN24FEB261055CE", "SBIN24FEB261055PE", "SBIN24FEB261060CE", "SBIN24FEB261060PE",
    "SBIN24FEB261065CE", "SBIN24FEB261065PE",
    "ICICIBANK24FEB261350CE", "ICICIBANK24FEB261350PE", "ICICIBANK24FEB261340CE", "ICICIBANK24FEB261340PE",
    "ICICIBANK24FEB261330CE", "ICICIBANK24FEB261330PE", "ICICIBANK24FEB261320CE", "ICICIBANK24FEB261320PE",
    "ICICIBANK24FEB261310CE", "ICICIBANK24FEB261310PE", "ICICIBANK24FEB261300CE", "ICICIBANK24FEB261300PE",
    "ICICIBANK24FEB261360CE", "ICICIBANK24FEB261360PE", "ICICIBANK24FEB261370CE", "ICICIBANK24FEB261370PE",
    "ICICIBANK24FEB261380CE", "ICICIBANK24FEB261380PE", "ICICIBANK24FEB261390CE", "ICICIBANK24FEB261390PE",
    "ICICIBANK24FEB261400CE", "ICICIBANK24FEB261400PE",
    "ICICIBANK24FEB261410CE", "ICICIBANK24FEB261410PE",
    "ICICIBANK24FEB261420CE", "ICICIBANK24FEB261420PE",
    "BANKNIFTY24FEB26FUT",
    "HDFCBANK24FEB26FUT",
    "ICICIBANK24FEB26FUT",
    "SBIN24FEB26FUT",
]

# --- Logic & Thresholds ---
LOT_SIZES = {
    "BANKNIFTY": 30,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
}
DEFAULT_LOT_SIZE = 75 # For any other symbol
OI_ROC_THRESHOLD = 2.0 # Temporarily lowered for IV testing
MOMENTUM_WINDOW = 300 # 5 minutes in seconds

# ==============================================================================
# =============================== STATE & UTILITIES ============================
# ==============================================================================

# State now supports a history of ticks for momentum analysis
symbol_data_state = {
    symbol: {
        # State for single-tick OI RoC alerts
        "price": 0, "price_prev": 0,
        "oi": 0, "oi_prev": 0,
        # State for 5-minute momentum alerts
        "ticks": [],  # Stores (timestamp, price, oi) tuples
        "last_trend_alert_type": None, # e.g., "STRONG_UPTREND"
        "last_trend_alert_time": 0,    # time.time() of the last alert
    } for symbol in SYMBOLS_TO_MONITOR
}

# Dictionary to hold the latest price of the underlying futures
future_price_state = {
    "BANKNIFTY": {"ticks": []},
    "HDFCBANK": {"ticks": []},
    "ICICIBANK": {"ticks": []},
    "SBIN": {"ticks": []},
}

def now():
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M:%S")

async def send_telegram(msg: str):
    """Sends a message to the configured Telegram chat without blocking the event loop."""
    print(f"📦 [{now()}] Preparing to send Telegram message...", flush=True)
    loop = asyncio.get_running_loop()
    
    params = {'chat_id': TELEGRAM_CHAT_ID, 'text': msg}
    
    # Use functools.partial to prepare the blocking function with its arguments
    blocking_call = functools.partial(requests.post, TELEGRAM_API_URL, params=params, timeout=10)

    try:
        # Run the blocking call in a separate thread
        response = await loop.run_in_executor(None, blocking_call)
        response.raise_for_status() 
        print(f"✅ [{now()}] Telegram message sent successfully. Response: {response.text}", flush=True)
    except requests.exceptions.RequestException as e:
        print(f"❌ [{now()}] FAILED to send Telegram message: {e}", flush=True)
    except Exception as e:
        print(f"❌ [{now()}] An unexpected error occurred while sending Telegram message: {e}", flush=True)

async def send_alert(msg: str):
    """Dispatches the alert to the configured Telegram channel."""
    await send_telegram(msg)

# ==============================================================================
# =============================== CORE LOGIC ===================================
# ==============================================================================

def lots_from_oi_change(symbol, oi_change):
    """Calculates the number of lots from a change in Open Interest."""
    lot_size = DEFAULT_LOT_SIZE
    for name, size in LOT_SIZES.items():
        if name in symbol:
            lot_size = size
            break
    if lot_size == 0: return 0
    return int(abs(oi_change) / lot_size)

def lot_bucket(lots):
    """Classifies the number of lots into qualitative buckets."""
    if lots >= 200: return "EXTREME HIGH"
    if lots >= 150: return "EXTRA HIGH"
    if lots >= 100: return "HIGH"
    if lots >= 75: return "MEDIUM"
    if lots >= 1: return "LOW"
    return "IGNORE"

def classify_option(oi_change, price_change, symbol):
    if price_change == 0:
        if oi_change > 0:
            return "HEDGING"
        elif oi_change < 0:
            return "REMOVE FROM HEDGE"
    elif oi_change > 0:
        if price_change > 0:
            return "BUYER(LONG)"
        else:  # price_change < 0
            return "WRITER(SHORT)"
    elif oi_change < 0:
        if price_change > 0:
            return "REMOVE FROM SHORT"
        else:  # price_change < 0
            return "REMOVE FROM LONG"
    
    return "Indecisive Movement"

def get_option_moneyness(symbol, future_price_state):
    """
    Checks if an option is ITM, ATM, or OTM based on the latest future price.
    Returns a string: "ITM", "ATM", or "OTM".
    """
    # This filter does not apply to future contracts themselves
    if "FUT" in symbol:
        return "N/A"

    # Identify underlying and get its future price
    underlying = None
    if "HDFCBANK" in symbol: underlying = "HDFCBANK"
    elif "ICICIBANK" in symbol: underlying = "ICICIBANK"
    elif "SBIN" in symbol: underlying = "SBIN"
    elif "BANKNIFTY" in symbol: underlying = "BANKNIFTY"

    if not underlying: 
        return "N/A" # If it's not one of our known underlyings, don't block it

    future_price = future_price_state.get(underlying)
    if not future_price or future_price == 0:
        print(f"⏳ [{now()}] {symbol}: Waiting for future price of {underlying} to check moneyness.", flush=True)
        return "OTM" # Treat as OTM if we don't have the future price yet

    # Extract strike and type
    try:
        match = re.search(r'.*?(\d{2})(\d+)(CE|PE)$', symbol)
        strike_price = int(match.group(2))
        option_type = match.group(3)
    except (AttributeError, TypeError, ValueError):
        return "N/A" # If we can't parse the option, don't block it

    # Define ATM band (0.5% of future price)
    atm_band = future_price * 0.001
    
    # Check ATM first
    if abs(future_price - strike_price) <= atm_band:
        return "ATM"
    
    # Check ITM
    is_itm = False
    if option_type == 'CE' and strike_price < future_price:
        is_itm = True
    if option_type == 'PE' and strike_price > future_price:
        is_itm = True

    if is_itm:
        return "ITM"
    else:
        # If not ATM and not ITM, it must be OTM
        print(f"ℹ️ [{now()}] {symbol}: OTM (Future: {future_price:.2f}, Strike: {strike_price}), alert suppressed.", flush=True)
        return "OTM"

def check_momentum_trends(symbol, state, future_price_state):
    """
    Analyzes the 5-minute historical data for a symbol to detect one of four momentum trends.
    Returns a tuple of (trend_type, analysis_data) if a trend is detected, otherwise None.
    """
    ticks = state.get("ticks", [])
    
    # --- 1. Pre-computation and Validation ---
    
    # Ensure we have enough data to analyze
    if len(ticks) < 2:
        return None # Not enough data

    first_tick_time = ticks[0][0]
    last_tick_time = ticks[-1][0]
    window_duration = last_tick_time - first_tick_time

    # Ensure the data spans a meaningful amount of time (e.g., at least half the window)
    if window_duration < MOMENTUM_WINDOW / 2:
        return None

    # Get underlying name
    underlying = next((name for name in future_price_state if name in symbol), None)
    if not underlying:
        return None

    # Get future price ticks and find the corresponding start price
    future_ticks = future_price_state[underlying].get("ticks", [])
    if not future_ticks:
        return None
    
    # Find the future price at the start of the option's window
    start_future_price_tick = next((ft for ft in future_ticks if ft[0] >= first_tick_time), None)
    if not start_future_price_tick:
        return None # No matching future data for the period

    start_future_price = start_future_price_tick[1]
    end_future_price = future_ticks[-1][1]

    # --- 2. Calculate Deltas ---
    
    start_option_price = ticks[0][1]
    end_option_price = ticks[-1][1]
    start_oi = ticks[0][2]
    end_oi = ticks[-1][2]

    future_price_chg = end_future_price - start_future_price
    option_price_chg = end_option_price - start_option_price
    oi_chg = end_oi - start_oi

    # --- Add new lots threshold criteria (user requested) ---
    total_lots_in_window = lots_from_oi_change(symbol, oi_chg)
    if abs(total_lots_in_window) <= 300:
        return None # Ignore if lots are not above 300

    # --- Add OI ROC threshold criteria (user requested) ---
    try:
        oi_roc = (oi_chg / start_oi) * 100
    except ZeroDivisionError:
        oi_roc = 0.0
    
    if abs(oi_roc) <= 2.0:
        return None # Ignore if OI ROC is not above 2.0

    # Determine option type
    is_call = "CE" in symbol
    
    # --- 3. Trend Classification ---
    
    trend_type = None

    # Check for rising future price (uptrend)
    if future_price_chg > 0:
        price_condition_met = (is_call and option_price_chg > 0) or (not is_call and option_price_chg < 0)
        if price_condition_met:
            if oi_chg > 0:
                trend_type = "📈 STRONG UPTREND"
            elif oi_chg < 0:
                trend_type = "⚠️ WEAK UPTREND (Short Covering)"

    # Check for falling future price (downtrend)
    elif future_price_chg < 0:
        price_condition_met = (is_call and option_price_chg < 0) or (not is_call and option_price_chg > 0)
        if price_condition_met:
            if oi_chg > 0:
                trend_type = "📉 STRONG DOWNTREND"
            elif oi_chg < 0:
                trend_type = "⚠️ WEAK DOWNTREND (Long Unwinding)"

    # --- 4. Return Result ---

    if trend_type:
        analysis_data = {
            "start_time": first_tick_time,
            "end_time": last_tick_time,
            "duration": window_duration,
            "start_oi": start_oi,
            "end_oi": end_oi,
            "oi_chg": oi_chg,
            "start_option_price": start_option_price,
            "end_option_price": end_option_price,
            "option_price_chg": option_price_chg,
            "start_future_price": start_future_price,
            "end_future_price": end_future_price,
            "future_price_chg": future_price_chg,
        }
        return (trend_type, analysis_data)

    return None

def format_momentum_alert(symbol, trend_type, data):
    """
    Formats the 5-minute momentum alert message.
    """
    # --- Product & Strike Info ---
    product_name, strike_display, option_type, year = "UNKNOWN", "N/A", "", ""
    if "HDFCBANK" in symbol: product_name = "HDFCBANK"
    elif "ICICIBANK" in symbol: product_name = "ICICI"
    elif "SBIN" in symbol: product_name = "SBIN"
    elif "BANKNIFTY" in symbol: product_name = "BANKNIFTY"
    
    try:
        match = re.search(r'.*?(\d{2})(\d+)(CE|PE)$', symbol)
        if match:
            year, strike_display, option_type = match.groups()
    except Exception:
        pass

    # --- Calculations ---
    lots = lots_from_oi_change(symbol, data['oi_chg'])
    try:
        oi_roc = (data['oi_chg'] / data['start_oi']) * 100
    except ZeroDivisionError:
        oi_roc = 0.0
    
    try:
        future_price_roc = (data['future_price_chg'] / data['start_future_price']) * 100
    except ZeroDivisionError:
        future_price_roc = 0.0

    try:
        option_price_roc = (data['option_price_chg'] / data['start_option_price']) * 100
    except ZeroDivisionError:
        option_price_roc = 0.0

    # --- Formatting ---
    header = "- - - 5-Min Momentum Alert - - -"
    line1 = f"{product_name} | {strike_display}{option_type}"
    line2 = f"\n{trend_type} Confirmed\n"
    
    oi_line = f"OI Δ: {data['oi_chg']:+,.0f} ({lots} lots)"
    oi_roc_line = f"OI RoC: {oi_roc:+.2f}%"
    
    future_price_line = f"Future Price Δ: {data['future_price_chg']:+.2f} ({future_price_roc:+.2f}%)"
    option_price_line = f"Option Price Δ: {data['option_price_chg']:+.2f} ({option_price_roc:+.2f}%)"

    last_price_line = f"Last Option Price: {data['end_option_price']:.2f}"
    last_future_line = f"Last Future Price: {data['end_future_price']:.2f}"

    duration_minutes = int(data['duration'] // 60)
    duration_seconds = int(data['duration'] % 60)
    start_time_str = datetime.fromtimestamp(data['start_time']).strftime('%H:%M')
    end_time_str = datetime.fromtimestamp(data['end_time']).strftime('%H:%M')
    duration_line = f"Duration: {duration_minutes}m {duration_seconds}s ({start_time_str} -> {end_time_str})"
    
    footer = "- - - - - - - - - - - - - - - -"

    return "\n".join([
        header, line1, line2, oi_line, oi_roc_line, future_price_line, option_price_line,
        "", last_price_line, last_future_line, duration_line, footer
    ])


def format_alert_message(symbol, action, bucket, lots, state, oi_chg, oi_roc, moneyness, future_price_state):
    """Formats the alert message, showing N/A for missing IV."""
    price_chg = state['price'] - state['price_prev']
    if price_chg > 0:
        price_dir = "↑"
    elif price_chg < 0:
        price_dir = "↓"
    else:
        price_dir = "↔"

    # Dynamically determine product name
    product_name = "UNKNOWN" # Default
    if "HDFCBANK" in symbol:
        product_name = "HDFCBANK"
    elif "ICICIBANK" in symbol:
        product_name = "ICICI" # As requested by user
    elif "SBIN" in symbol:
        product_name = "SBIN"
    elif "BANKNIFTY" in symbol:
        product_name = "BANKNIFTY"

    year, strike_display, option_type_display = "", "", ""
    is_future = "FUT" in symbol # Determine if it's a future

    try:
        if is_future:
            match = re.search(r'.*?(\d{2})FUT$', symbol)
            if match:
                year = match.group(1)
            strike_display = "FUT" # For internal logic, but won't be displayed in main_message
            option_type_display = ""
        else: # For options
            match = re.search(r'.*?(\d{2})(\d+)(CE|PE)$', symbol)
            if match:
                year = match.group(1)
                strike_display = match.group(2)
                option_type_display = match.group(3)
            else: # Fallback for old format without date
                match = re.search(r'(\d+)(CE|PE)$', symbol)
                strike_display = match.group(1)
                option_type_display = match.group(2)
    except Exception:
        year, strike_display, option_type_display = "", "N/A", "" # Fallback if parsing completely fails

    if is_future:
        main_message = f"""{product_name} | FUTURE
ACTION: {action}
SIZE: {bucket} ({lots} lots)
EXISTING OI: {state['oi_prev']}
OI Δ: {oi_chg}
PRICE: {price_dir}
TIME: {now()}
"""
        added_section = f"""{year} {product_name} FUT

{state['price']:.2f}
"""
        return f"{main_message}\n\n{added_section}"
    else:
        # Ensure correct underlying key for future_prices lookup
        underlying_lookup_key = product_name
        if product_name == "ICICI":
            underlying_lookup_key = "ICICIBANK"
        future_price = future_price_state.get(underlying_lookup_key, 0)
        line1 = f"{product_name} | OPTION"
        line2 = f"STRIKE: {strike_display}{option_type_display} {moneyness}"
        line3 = f"ACTION: {action}"
        line4 = f"SIZE: {bucket} ({lots} lots)"
        line5 = f"EXISTING OI: {state['oi_prev']}"
        line6 = f"OI Δ: {oi_chg}"
        line7 = f"OI RoC: {oi_roc:.2f}%"
        line8 = f"PRICE: {price_dir}"
        line9 = f"TIME: {now()}"
        line10 = f"{year} {product_name} {strike_display}{option_type_display}"
        line11 = f"FUTURE PRICE: {future_price:.2f}"
        line12 = f"LAST PRICE: {state['price']:.2f}"

        return f"{line1}\n{line2}\n{line3}\n{line4}\n{line5}\n{line6}\n{line7}\n{line8}\n{line9}\n{line10}\n{line11}\n{line12}"

# ==============================================================================
# ============================ MAIN SCANNER & WEBSOCKET ========================
# ==============================================================================

async def process_data(data):
    """
    Processes a single data packet, updating future prices or sending option alerts.
    Option alerts are filtered to only include ITM/ATM strikes.
    """
    global symbol_data_state, future_price_state
    
    symbol = data.get("InstrumentIdentifier")
    if not symbol or symbol not in symbol_data_state:
        return

    new_price = data.get("LastTradePrice")
    if new_price is None:
        return

    # If the symbol is a future, update its price history and stop processing for alerts
    if "FUT" in symbol:
        underlying = next((name for name in future_price_state if name in symbol), None)
        
        if underlying and new_price > 0: # Ensure price is valid
            current_time = time.time()
            future_state = future_price_state[underlying]
            future_state["ticks"].append((current_time, new_price))
            # Prune old data points
            future_state["ticks"] = [tick for tick in future_state["ticks"] if current_time - tick[0] <= MOMENTUM_WINDOW]
        return # Stop processing here for futures

    # The rest of the function will only execute for OPTIONS
    state = symbol_data_state[symbol]
    new_oi = data.get("OpenInterest")

    if new_oi is None:
        return

    # --- History Management for Momentum Alerts ---
    current_time = time.time()
    # Add the new data point to the history if it's valid
    if new_price > 0 and new_oi > 0:
        state["ticks"].append((current_time, new_price, new_oi))
    # Prune old data points from the history
    state["ticks"] = [tick for tick in state["ticks"] if current_time - tick[0] <= MOMENTUM_WINDOW]

    # --- Standard processing for Option contracts ---

    state["price_prev"], state["oi_prev"] = state["price"], state["oi"]
    state["price"], state["oi"] = new_price, new_oi

    if state["oi_prev"] == 0:
        print(f"ℹ️ [{now()}] {symbol}: Initializing option data state.", flush=True)
        return

    oi_chg = state["oi"] - state["oi_prev"]
    if oi_chg == 0:
        return

    # --- Calculations ---
    price_chg = state["price"] - state["price_prev"]
    
    try:
        oi_roc = (oi_chg / state["oi_prev"]) * 100
    except ZeroDivisionError:
        oi_roc = 0.0

    # --- Alert Logic ---
    if abs(oi_roc) > OI_ROC_THRESHOLD:
        print(f"🚨 [{now()}] {symbol}: OI RoC {oi_roc:.2f}% > {OI_ROC_THRESHOLD}%. Potential Alert.", flush=True)
        
        lots = lots_from_oi_change(symbol, oi_chg)
        
        alert_condition_met = False
        moneyness = "N/A" 

        if lots > 100:
            moneyness = get_option_moneyness(symbol, future_price_state)
            if moneyness in ["ITM", "ATM"]:
                alert_condition_met = True
        
        if alert_condition_met:
            bucket = lot_bucket(lots)
            if bucket != "IGNORE":
                print(f"📊 [{now()}] {symbol}: {moneyness}, lots: {lots}, Bucket: {bucket}. TRIGGERING ALERT.", flush=True)
                action = classify_option(oi_chg, price_chg, symbol)
                alert_msg = format_alert_message(symbol, action, bucket, lots, state, oi_chg, oi_roc, moneyness, future_price_state)
                await send_alert(alert_msg)

    # --- Momentum Alert Logic ---
    # This is called on every tick to check for a developing trend.
    analysis_result = check_momentum_trends(symbol, state, future_price_state)
    if analysis_result:
        trend_type, trend_data = analysis_result
        
        current_time = time.time()
        last_alert_time = state.get("last_trend_alert_time", 0)
        last_alert_type = state.get("last_trend_alert_type", None)
        
        # Only alert if the trend type is new or if it's been more than 5 mins since the last alert of the same type
        if trend_type != last_alert_type or (current_time - last_alert_time) > MOMENTUM_WINDOW:
            print(f"📈 [{now()}] {symbol}: Momentum Trend Detected - {trend_type}. TRIGGERING ALERT.", flush=True)
            
            # Format the specific momentum alert message
            alert_msg = format_momentum_alert(symbol, trend_type, trend_data)
            await send_alert(alert_msg)
            
            # Update state to prevent re-alerting immediately
            state["last_trend_alert_type"] = trend_type
            state["last_trend_alert_time"] = current_time
        else:
            # This trend is ongoing, but we've already alerted recently. Suppress.
            print(f"📈 [{now()}] {symbol}: Ongoing momentum trend '{trend_type}'. Alert suppressed.", flush=True)

async def run_scanner():
    """The main function to connect, authenticate, subscribe, and process data."""
    while True:
        try:
            async with websockets.connect(WSS_URL, ping_interval=20, ping_timeout=20) as websocket:
                print(f"✅ [{now()}] Connected to WebSocket. Authenticating...", flush=True)
                
                auth_request = {"MessageType": "Authenticate", "Password": API_KEY}
                await websocket.send(json.dumps(auth_request))
                auth_response = json.loads(await websocket.recv())
                
                if not auth_response.get("Complete"):
                    print(f"❌ [{now()}] Authentication FAILED: {auth_response.get('Comment')}. Retrying in 30s.", flush=True)
                    await asyncio.sleep(30)
                    continue
                
                print(f"✅ [{now()}] Authentication successful. Subscribing to {len(SYMBOLS_TO_MONITOR)} symbols...", flush=True)

                for symbol in SYMBOLS_TO_MONITOR:
                    await websocket.send(json.dumps({
                        "MessageType": "SubscribeRealtime", "Exchange": "NFO",
                        "Unsubscribe": "false", "InstrumentIdentifier": symbol
                    }))
                print(f"✅ [{now()}] Subscriptions sent. Scanner is now live.", flush=True)
                await send_alert("✅ GFDL Scanner is LIVE and monitoring the market.")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                        if data.get("MessageType") == "RealtimeResult":
                            await process_data(data)                        
                    except json.JSONDecodeError:
                        print(f"⚠️ [{now()}] Warning: Received a non-JSON message.", flush=True)
                    except Exception as e:
                        print(f"❌ [{now()}] Error during message processing for {message}: {e}", flush=True)

        except websockets.exceptions.ConnectionClosed as e:
            print(f"⚠️ [{now()}] WebSocket connection closed: {e}. Reconnecting in 10 seconds...", flush=True)
            await asyncio.sleep(10)
        except Exception as e:
            print(f"❌ [{now()}] An unexpected error occurred in the main loop: {e}. Reconnecting in 30 seconds...", flush=True)
            await asyncio.sleep(30)

if __name__ == "__main__":
    print("🚀 GFDL Scanner Starting...", flush=True)
    try:
        asyncio.run(run_scanner())
    except KeyboardInterrupt:
        print("\n🛑 Scanner stopped by user.", flush=True)
        # In a real async app, you'd await this, but for shutdown it's okay to fire and forget
        asyncio.run(send_alert("🛑 GFDL Scanner was stopped manually."))
    except Exception as e:
        error_message = f"💥 GFDL Scanner CRASHED with a critical error: {e}"
        print(error_message, flush=True)
        asyncio.run(send_alert(error_message))
