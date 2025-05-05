import os
import ccxt
import pandas as pd
import ta
import time
import re
import requests
from datetime import datetime
from telegram.ext import Updater, CommandHandler
from telegram import ParseMode
from threading import Thread


import google.generativeai as genai
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Load API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Configure Binance (set sandbox mode to False to trade live)
binance = ccxt.binance({
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_SECRET_KEY,
    'enableRateLimit': True
})
binance.set_sandbox_mode(False)  # ❗Set to False to trade live

# Fetch market data
def fetch_data():
    bars = binance.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=50)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    return df

def add_indicators(df):
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=9).rsi()
    df['ema9'] = ta.trend.EMAIndicator(df['close'], window=9).ema_indicator()
    df['ema21'] = ta.trend.EMAIndicator(df['close'], window=21).ema_indicator()
    df['macd'] = ta.trend.MACD(df['close']).macd()
    df['macd_signal'] = ta.trend.MACD(df['close']).macd_signal()
    df['stochrsi'] = ta.momentum.StochRSIIndicator(df['close']).stochrsi()
    return df


# Ask Gemini for strategy
def ask_gemini(price, rsi, ema9, ema21, macd, macd_signal, stochrsi, df, question="What should I do?"):
    balance = binance.fetch_balance()
    usdt = balance['USDT']['free']
    btc = balance['BTC']['free']

    rsi_past = df['rsi'].iloc[-4:-1].tolist()
    price_past = df['close'].iloc[-4:-1].tolist()
    macd_past = df['macd'].iloc[-4:-1].tolist()

    rsi_trend = "rising" if rsi > rsi_past[-1] > rsi_past[-2] else "falling"
    price_change_pct = ((price - price_past[-3]) / price_past[-3]) * 100
    macd_trend = "rising" if macd > macd_past[-1] > macd_past[-2] else "falling"

    # btc_value = btc * price  # Compute BTC value in USD

    prompt = f"""
You are an expert crypto trading assistant analyzing BTC/USDT on a 1-hour chart. 
Your goal is to assist the user in making a strategic trading decision based on their wallet balance, technical indicators, and current market trend.

🧾 User Question: {question}

📈 Market Data:
- Current Price: ${price:.2f}
- RSI(9): {rsi:.2f} ({rsi_trend})
- EMA(9): {ema9:.2f}, EMA(21): {ema21:.2f}
- Price Change (last 3 candles): {price_change_pct:.2f}%
- MACD: {macd:.4f} ({macd_trend})
- MACD Signal: {macd_signal:.4f}
- StochRSI: {stochrsi:.4f}

💰 Wallet Balance:
- USDT: ${usdt:.2f}
- BTC: {btc:.6f} (≈ ${btc * price:.2f})

📌 Constraints:
- Only recommend BUY if USDT ≥ $5
- Only recommend SELL if BTC value ≥ $10
- If a trade is blocked due to low balance, return HOLD, even if signals support a trade.

🧠 Instructions:
1. Analyze the market indicators and wallet balance.
2. Recommend one action: BUY, SELL, or HOLD.
3. If recommending BUY or SELL, suggest:
   - TP (take-profit): a price above (for sell) or below (for buy) the current price.
   - SL (stop-loss): a safety price to exit if the trend goes the wrong way.
4. If recommending HOLD, specify whether it’s due to market indecision, low balance, or both.
5. Only recommend BUY or SELL if you have at least 60% confidence based on the indicators.

📌 Output Format (must appear at the bottom):
action="[buy|sell|hold]"
percent="[NN]%"  # Allocation percentage (if buy/sell)
tp="[TP price]"  # Target price to take profit
sl="[SL price]"  # Stop loss price
"""


    response = model.generate_content(prompt)
    return response.text.strip()

def extract_trade_details(response):
    action_match = re.search(r'action\s*=\s*"(buy|sell|hold)"', response, re.IGNORECASE)
    percent_match = re.search(r'percent\s*=\s*"(\d{1,3})\s*%"', response)

    if action_match:
        action = action_match.group(1).lower()
        percent = float(percent_match.group(1)) / 100 if percent_match else 0.0
        return action, percent

    return "hold", 0.0

def log_wallet(symbol="BTC/USDT"):
    market = binance.market(symbol)
    base_currency = market['base']   # BTC
    quote_currency = market['quote'] # USDT

    balance = binance.fetch_balance()

    usdt_free = balance[quote_currency]['free']
    btc_free = balance[base_currency]['free']

    print(f"💰 Wallet Balance:")
    print(f"   {quote_currency}: {usdt_free:.2f} USDT available")
    print(f"   {base_currency}: {btc_free:.6f} BTC available")


def place_trade(signal, symbol="BTC/USDT", percent=0.1):
    market = binance.market(symbol)
    base_currency = market['base']   # BTC
    quote_currency = market['quote'] # USDT
    price = binance.fetch_ticker(symbol)['last']

    try:
        if signal.lower().startswith("buy"):
            usdt_available = binance.fetch_balance()[quote_currency]['free']
            trade_amount_usdt = usdt_available * percent
            btc_amount = trade_amount_usdt / price
            print(f"🟢 Executing BUY: ~{btc_amount:.6f} BTC (~{trade_amount_usdt:.2f} USDT)")
            order = binance.create_market_buy_order(symbol, round(btc_amount, 6))

        elif signal.lower().startswith("sell"):
            btc_available = binance.fetch_balance()[base_currency]['free']
            trade_amount_btc = btc_available * percent
            print(f"🔴 Executing SELL: ~{trade_amount_btc:.6f} BTC")
            order = binance.create_market_sell_order(symbol, round(trade_amount_btc, 6))

        else:
            print("⏸ No trade executed.")
            return

        success_msg = f"✅ Trade executed: {signal.upper()} {percent * 100:.0f}% | Order ID: {order['id']}"
        print(success_msg)
        send_telegram(success_msg)

    except Exception as e:
        error_msg = f"❌ Trade failed: {e}"
        print(error_msg)
        send_telegram(error_msg)


def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")

def telegram_price(update, context):
    df = fetch_data()
    df = add_indicators(df)  # ✅ Add this line
    last = df.iloc[-1]
    price = last['close']
    rsi = last['rsi']
    ema = last['ema']
    msg = f"📊 *BTC/USDT*\nPrice: ${price:.2f}\nRSI: {rsi:.2f}\nEMA(14): {ema:.2f}"
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode=ParseMode.MARKDOWN)


def telegram_balance(update, context):
    balance = binance.fetch_balance()
    usdt = balance['USDT']['free']
    btc = balance['BTC']['free']
    msg = f"💰 *Your Balance:*\nUSDT: {usdt:.2f}\nBTC: {btc:.6f}"
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode=ParseMode.MARKDOWN)

def fetch_price(symbol="BTC/USDT"):
    return binance.fetch_ticker(symbol)['last']

def telegram_buy(update, context):
    try:
        percent = float(context.args[0]) / 100
        balance = binance.fetch_balance()['USDT']['free']
        price = fetch_price()
        amount = (balance * percent) / price
        binance.create_market_buy_order('BTC/USDT', round(amount, 6))
        msg = f"🟢 Bought {amount:.6f} BTC (~{balance * percent:.2f} USDT)"
    except Exception as e:
        msg = f"❌ Buy failed: {e}"
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

def telegram_sell(update, context):
    try:
        if not context.args:
            raise ValueError("Missing percentage argument. Usage: /sell 10")

        percent = float(context.args[0]) / 100
        if percent <= 0 or percent > 1:
            raise ValueError("Invalid percentage. Use 1–100.")

        balance = binance.fetch_balance()['BTC']['free']
        price = binance.fetch_ticker('BTC/USDT')['last']
        amount = balance * percent

        binance.create_market_sell_order('BTC/USDT', round(amount, 6))
        msg = f"🔴 Sold {amount:.6f} BTC (~${amount * price:.2f})"


    except ValueError as ve:
        msg = f"⚠️ Error: {ve}"
    except Exception as e:
        msg = f"❌ Sell failed: {e}"

    context.bot.send_message(chat_id=update.effective_chat.id, text=msg)


def start_telegram_bot():
    updater = Updater(token=os.getenv("TELEGRAM_BOT_TOKEN"), use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("price", telegram_price))
    dp.add_handler(CommandHandler("balance", telegram_balance))
    dp.add_handler(CommandHandler("buy", telegram_buy))
    dp.add_handler(CommandHandler("sell", telegram_sell))
    dp.add_handler(CommandHandler("ask", telegram_ask))

    updater.start_polling()

# Main bot loop
def main():
    df = fetch_data()
    df = add_indicators(df)
    last = df.iloc[-1]
    price = last['close']
    rsi = last['rsi']
    ema9 = last['ema9']
    ema21 = last['ema21']

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_wallet()

    macd = last['macd']
    macd_signal = last['macd_signal']
    stochrsi = last['stochrsi']

    decision_text = ask_gemini(price, rsi, ema9, ema21, macd, macd_signal, stochrsi, df)
    action, percent = extract_trade_details(decision_text)

    if action in ["buy", "sell"] and percent == 0:
        percent = 0.1

    # Log text for Telegram
    message = (
    f"📊 [{timestamp}]\n"
    f"Price: ${price:.2f} | RSI: {rsi:.2f} | EMA(9): {ema9:.2f} | EMA(21): {ema21:.2f}\n"
    f"MACD: {macd:.4f} | Signal: {macd_signal:.4f} | StochRSI: {stochrsi:.4f}\n\n"
    f"🤖 Gemini Response:\n{decision_text}\n"
    f"Action Extracted: {action.upper()} {int(percent * 100)}%\n"
)


    send_telegram(message)
    print(message)

    place_trade(action, percent=percent)

def telegram_ask(update, context):
    user_question = ' '.join(context.args) or "What should I do now?"
    df = add_indicators(fetch_data())
    last = df.iloc[-1]
    response = ask_gemini(
        price=last['close'],
        rsi=last['rsi'],
        ema9=last['ema9'],
        ema21=last['ema21'],
        macd=last['macd'],
        macd_signal=last['macd_signal'],
        stochrsi=last['stochrsi'],
        df=df,
        question=user_question
    )
    update.message.reply_text(f"🤖 Gemini Bot:\n{response}")


def main_loop(interval_minutes=15):
    print("🚀 AI Trading Bot Started. Press Ctrl+C to stop.\n")
    while True:
        try:
            main()
            print(f"⏳ Waiting {interval_minutes} minutes before next run...\n")
            time.sleep(interval_minutes * 60)
        except Exception as e:
            print(f"⚠️ Error: {e}")
            print("Retrying in 1 minute...")
            time.sleep(60)

if __name__ == "__main__":
    start_telegram_bot()  # ✅ Only start Telegram bot without loop
