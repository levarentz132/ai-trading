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
from supabase import create_client
from test import fetch_bitcoin_news  # or use the correct import path

import google.generativeai as genai
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Load API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# utils/supabase_client.py or somewhere near top
def get_user_binance_keys(chat_id):
    res = supabase.table("users").select("binance_key, binance_secret").eq("chat_id", chat_id).execute()
    if res.data and res.data[0]['binance_key'] and res.data[0]['binance_secret']:
        return res.data[0]['binance_key'], res.data[0]['binance_secret']
    return None, None

# Configure Binance (set sandbox mode to False to trade live)
def get_binance_client(api_key, secret):
    return ccxt.binance({
        'apiKey': api_key,
        'secret': secret,
        'enableRateLimit': True
    })

# Fetch market data
def fetch_data(binance):
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
def ask_gemini(price, rsi, ema9, ema21, macd, macd_signal, stochrsi, df, binance, question="What should I do?"):
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
    news = fetch_bitcoin_news()

    prompt = f"""
You are a friendly and experienced crypto trading assistant helping a beginner analyze the BTC/USDT market on a 1-hour chart. Your role is to explain what's happening clearly, without using too much technical jargon, while still giving accurate insights.

🧾 User's Question:
"{question}"

📊 Current Market Summary:
- Price: ${price:.2f}
- RSI (Relative Strength Index): {rsi:.2f}
- EMA(9): {ema9:.2f}, EMA(21): {ema21:.2f}
- MACD: {macd:.4f}, Signal Line: {macd_signal:.4f}
- StochRSI: {stochrsi:.4f}

💰 Wallet Overview:
- USDT (Cash): ${usdt:.2f}
- BTC: {btc:.6f} (≈ ${btc * price:.2f})

🧠 Instructions for your response:
- First, explain if the market is looking strong (bullish), weak (bearish), or mixed.
- Break down the indicators simply: 
  • RSI: Is it overbought or oversold? What does that mean?
  • EMA: Is the price above or below these averages?
  • MACD: Is momentum increasing or decreasing?
  • StochRSI: Is the market showing signs of exhaustion or bounce?

- Be realistic about what could happen, using phrases like “there’s a chance,” “this could mean,” or “it suggests.”
- If the user has limited capital, explain how that impacts their ability to trade.
- Avoid complicated terms like "convergence" or "crossovers" unless needed. Keep it simple and supportive.

📰 **Market Sentiment Based on Recent News:**
{news}  # Insert the news fetched here

💡 **Impact of the News on Market Trends:**
Based on recent news, here’s what could happen:
- **[Explain the news impact]:** Positive news like a major company adopting Bitcoin could cause the price to go up, while negative news like regulations could make the market go down.
- **Impact Timeframe:** Consider if the news might affect the market in the short term (next few hours) or over a longer period.

📌 **Conclusion:**
Here’s what could happen in the next few hours:
- **If the market is oversold:** The price might bounce back up, but the overall trend is still down unless positive changes happen.
- **If the news influences the market:** Positive news could lead to a rise in price, but negative news could cause more drops.
- **With your current capital:** Since your USDT is low, small price changes will have a big effect. Be careful with your trades and consider waiting for better opportunities.

📌 End your response with this format (no extra symbols or markdown):
action=[buy|sell|hold]
percent=[XX]%
tp=[take-profit price or leave blank]
sl=[stop-loss price or leave blank]
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

def log_wallet(binance, symbol="BTC/USDT"):
    market = binance.market(symbol)
    base_currency = market['base']   # BTC
    quote_currency = market['quote'] # USDT

    balance = binance.fetch_balance()
    usdt_free = balance[quote_currency]['free']
    btc_free = balance[base_currency]['free']

    print(f"💰 Wallet Balance:")
    print(f"   {quote_currency}: {usdt_free:.2f} USDT available")
    print(f"   {base_currency}: {btc_free:.6f} BTC available")

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
    chat_id = str(update.effective_chat.id)
    api_key, secret_key = get_user_binance_keys(chat_id)
    if not api_key:
        context.bot.send_message(chat_id=chat_id, text="❌ Binance API keys not found.")
        return

    binance = get_binance_client(api_key, secret_key)
    binance.set_sandbox_mode(False)

    df = fetch_data(binance)
    df = add_indicators(df)
    last = df.iloc[-1]
    price = last['close']
    rsi = last['rsi']
    ema = last['ema9']
    msg = f"📊 *BTC/USDT*\nPrice: ${price:.2f}\nRSI: {rsi:.2f}\nEMA(9): {ema:.2f}"
    context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)


def telegram_balance(update, context):
    chat_id = str(update.effective_chat.id)
    api_key, secret_key = get_user_binance_keys(chat_id)
    if not api_key:
        context.bot.send_message(chat_id=chat_id, text="❌ Binance API keys not found.")
        return

    binance = get_binance_client(api_key, secret_key)
    binance.set_sandbox_mode(False)

    balance = binance.fetch_balance()
    usdt = balance['USDT']['free']
    btc = balance['BTC']['free']
    msg = f"💰 *Your Balance:*\nUSDT: {usdt:.2f}\nBTC: {btc:.6f}"
    context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)

def fetch_price(binance, symbol="BTC/USDT"):
    return binance.fetch_ticker(symbol)['last']

def telegram_buy(update, context):
    chat_id = str(update.effective_chat.id)
    api_key, secret_key = get_user_binance_keys(chat_id)
    if not api_key:
        context.bot.send_message(chat_id=chat_id, text="❌ Binance API keys not found.")
        return

    binance = get_binance_client(api_key, secret_key)
    binance.set_sandbox_mode(False)

    try:
        percent = float(context.args[0]) / 100
        balance = binance.fetch_balance()['USDT']['free']
        price = fetch_price(binance)
        amount = (balance * percent) / price
        binance.create_market_buy_order('BTC/USDT', round(amount, 6))
        msg = f"🟢 Bought {amount:.6f} BTC (~{balance * percent:.2f} USDT)"
    except Exception as e:
        msg = f"❌ Buy failed: {e}"

    context.bot.send_message(chat_id=chat_id, text=msg)

def telegram_sell(update, context):
    chat_id = str(update.effective_chat.id)
    api_key, secret_key = get_user_binance_keys(chat_id)
    if not api_key:
        context.bot.send_message(chat_id=chat_id, text="❌ Binance API keys not found.")
        return

    binance = get_binance_client(api_key, secret_key)
    binance.set_sandbox_mode(False)

    try:
        if not context.args:
            raise ValueError("Missing percentage argument. Usage: /sell 10")

        percent = float(context.args[0]) / 100
        if percent <= 0 or percent > 1:
            raise ValueError("Invalid percentage. Use 1–100.")

        balance = binance.fetch_balance()['BTC']['free']
        price = fetch_price(binance)
        amount = balance * percent

        binance.create_market_sell_order('BTC/USDT', round(amount, 6))
        msg = f"🔴 Sold {amount:.6f} BTC (~${amount * price:.2f})"

    except ValueError as ve:
        msg = f"⚠️ Error: {ve}"
    except Exception as e:
        msg = f"❌ Sell failed: {e}"

    context.bot.send_message(chat_id=chat_id, text=msg)



def start_telegram_bot():
    updater = Updater(token=os.getenv("TELEGRAM_BOT_TOKEN"), use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("price", telegram_price))
    dp.add_handler(CommandHandler("balance", telegram_balance))
    dp.add_handler(CommandHandler("buy", telegram_buy))
    dp.add_handler(CommandHandler("sell", telegram_sell))
    dp.add_handler(CommandHandler("ask", telegram_ask))
    dp.add_handler(CommandHandler("register", telegram_register))


    updater.start_polling()

# Main bot loop
def main():
    # 🧪 Example: testing with your own Telegram chat_id
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    api_key, secret_key = get_user_binance_keys(chat_id)
    if not api_key:
        print("❌ Binance API keys not found.")
        return

    binance = get_binance_client(api_key, secret_key)
    binance.set_sandbox_mode(False)

    df = fetch_data(binance)
    df = add_indicators(df)
    last = df.iloc[-1]
    price = last['close']
    rsi = last['rsi']
    ema9 = last['ema9']
    ema21 = last['ema21']
    macd = last['macd']
    macd_signal = last['macd_signal']
    stochrsi = last['stochrsi']

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_wallet(binance)

    decision_text = ask_gemini(price, rsi, ema9, ema21, macd, macd_signal, stochrsi, df, binance)
    action, percent = extract_trade_details(decision_text)

    if action in ["buy", "sell"] and percent == 0:
        percent = 0.1

    message = (
        f"📊 [{timestamp}]\n"
        f"Price: ${price:.2f} | RSI: {rsi:.2f} | EMA(9): {ema9:.2f} | EMA(21): {ema21:.2f}\n"
        f"MACD: {macd:.4f} | Signal: {macd_signal:.4f} | StochRSI: {stochrsi:.4f}\n\n"
        f"🤖 Gemini Response:\n{decision_text}\n"
        f"Action Extracted: {action.upper()} {int(percent * 100)}%\n"
    )

    send_telegram(message)
    print(message)

def telegram_register(update, context):
    chat_id = str(update.effective_chat.id)
    args = context.args

    if len(args) != 2:
        update.message.reply_text("❌ Usage: /register <api_key> <secret_key>")
        return

    api_key, secret_key = args

    try:
        # Upsert to users table
        supabase.table("users").upsert({
            "chat_id": chat_id,
            "binance_key": api_key,
            "binance_secret": secret_key
        }).execute()

        update.message.reply_text("✅ Binance API keys registered successfully.")
        print(f"✅ Registered keys for chat_id: {chat_id}")
    except Exception as e:
        print(f"❌ Supabase error: {e}")
        update.message.reply_text(f"❌ Failed to register: {e}")


def telegram_ask(update, context):
    chat_id = str(update.effective_chat.id)
    user_question = ' '.join(context.args) or "What should I do now?"

    # 🔐 Load user's Binance API keys from Supabase
    api_key, secret_key = get_user_binance_keys(chat_id)

    if not api_key or not secret_key:
        context.bot.send_message(chat_id=chat_id, text="❌ Binance API keys not found. Please register first.")
        return

   # ✅ Correct way:
    binance = get_binance_client(api_key, secret_key)
    binance.set_sandbox_mode(False)

    try:
        df = fetch_data(binance)
        df = add_indicators(df)
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
            question=user_question,
            binance=binance  # pass client here
        )

        msg = (
            f"📊 *BTC/USDT Analysis*\n"
            f"Price: ${last['close']:.2f}\n"
            f"RSI(9): {last['rsi']:.2f}\n"
            f"EMA(9): {last['ema9']:.2f}, EMA(21): {last['ema21']:.2f}\n"
            f"MACD: {last['macd']:.4f}, Signal: {last['macd_signal']:.4f}\n"
            f"StochRSI: {last['stochrsi']:.4f}\n\n"
            f"💰 *Wallet:*\n"
            f"USDT: {binance.fetch_balance()['USDT']['free']:.2f}\n"
            f"BTC: {binance.fetch_balance()['BTC']['free']:.6f}\n\n"
            f"🤖 *Gemini Bot Suggestion:*\n{response}"
        )
        update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


    except Exception as e:
        update.message.reply_text(f"⚠️ Error: {e}")

if __name__ == "__main__":
    start_telegram_bot()  # ✅ Only start Telegram bot without loop
