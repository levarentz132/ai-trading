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
Anda adalah asisten trading kripto yang ramah dan berpengalaman, membantu seorang pemula untuk menganalisis pasar BTC/USDT pada grafik 1 jam. Peran Anda adalah menjelaskan apa yang terjadi dengan jelas, tanpa menggunakan terlalu banyak istilah teknis, sambil tetap memberikan wawasan yang akurat.

🧾 Pertanyaan Pengguna:
"{question}"

📊 Ringkasan Pasar Saat Ini:
- Harga: ${price:.2f}
- RSI (Relative Strength Index): {rsi:.2f}
- EMA(9): {ema9:.2f}, EMA(21): {ema21:.2f}
- MACD: {macd:.4f}, Garis Sinyal: {macd_signal:.4f}
- StochRSI: {stochrsi:.4f}

💰 Ringkasan Saldo:
- USDT (Kas): ${usdt:.2f}
- BTC: {btc:.6f} (≈ ${btc * price:.2f})

🧠 Petunjuk untuk respon Anda:
- Pertama, jelaskan apakah pasar terlihat kuat (bullish), lemah (bearish), atau campuran.
- Jelaskan indikator-indikator dengan sederhana:
  • RSI: Apakah terlalu dibeli (overbought) atau terlalu dijual (oversold)? Apa artinya?
  • EMA: Apakah harga berada di atas atau di bawah rata-rata ini?
  • MACD: Apakah momentum meningkat atau menurun?
  • StochRSI: Apakah pasar menunjukkan tanda kelelahan atau kemungkinan bounce (pantulan)?

- Berikan prediksi realistis tentang apa yang bisa terjadi, menggunakan kalimat seperti “ini bisa berarti,” “ada kemungkinan,” atau “ini menunjukkan.”
- Jika pengguna memiliki modal yang terbatas, jelaskan bagaimana itu memengaruhi kemampuan mereka untuk trading.
- Hindari istilah teknis yang rumit seperti "konvergensi" atau "crossovers" kecuali diperlukan. Jaga agar tetap sederhana dan mendukung.

📰 **Sentimen Pasar Berdasarkan Berita Terbaru:**
{news}  # Masukkan berita yang diambil di sini

💡 **Dampak Berita Terhadap Tren Pasar:**
Berdasarkan berita terbaru, berikut ini yang bisa terjadi:
- **[Jelaskan dampak berita]:** Berita positif seperti adopsi Bitcoin oleh perusahaan besar bisa menyebabkan harga naik, sementara berita negatif seperti regulasi bisa membuat pasar turun.
- **Waktu Dampak:** Pertimbangkan apakah berita tersebut akan memengaruhi pasar dalam jangka pendek (beberapa jam ke depan) atau jangka panjang.

📌 **Kesimpulan:**
Berikut ini yang bisa terjadi dalam beberapa jam ke depan:
- **Jika pasar oversold:** Ada kemungkinan harga akan bounce (berbalik naik) dalam jangka pendek, tetapi tren keseluruhan masih turun kecuali ada perubahan positif.
- **Jika berita mempengaruhi pasar:** Berita positif bisa menyebabkan kenaikan harga, sementara berita negatif bisa menyebabkan penurunan lebih lanjut.
- **Dengan modal Anda yang terbatas:** Karena USDT Anda rendah, perubahan harga kecil bisa sangat mempengaruhi saldo Anda. Hati-hati dalam trading dan pertimbangkan untuk menunggu peluang yang lebih baik.

📌 Akhiri respons Anda dengan format ini (tanpa simbol atau markdown tambahan):
action=[buy|sell|hold]
percent=[XX]%
tp=[harga take-profit atau kosongkan]
sl=[harga stop-loss atau kosongkan]
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
    dp.add_handler(CommandHandler("askspec", telegram_askspec))

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

def ask_spec_gemini(price, rsi, ema9, ema21, macd, macd_signal, stochrsi, user_question):
    # Create a prompt based on user question and market data
    prompt = f"""
    📊 **Analisis Pasar BTC/USDT:**
    - Harga: ${price:.2f}
    - RSI (9): {rsi:.2f}
    - EMA(9): {ema9:.2f}, EMA(21): {ema21:.2f}
    - MACD: {macd:.4f}, Garis Sinyal: {macd_signal:.4f}
    - StochRSI: {stochrsi:.4f}
    
    💡 **Pertanyaan Pengguna:**
    "{user_question}"

    1. tolong berikan jawaban yang bisa di lakukan. 
    2. tidak perlu takut untuk memberikan arahan.
    3. jangan pernah jawab seperti " tidak dapat memprediksi pergerakan harga Bitcoin."
    4. terus terang dengan jawaban anda jangan beralasan. bila ada rekomendasi langsung berikan rekomendasi.
    """
    
    # Send the prompt to Gemini for analysis (this is where you'd forward the prompt to Gemini)
    response = model.generate_content(prompt)  # Gemini call
    return response.text.strip()


def escape_markdown(text):
    # Escape the Markdown special characters for Telegram
    return re.sub(r'([*_`\[\]()~|])', r'\\\1', text)

def telegram_askspec(update, context):
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
        # Fetch market data and indicators
        df = fetch_data(binance)
        df = add_indicators(df)
        last = df.iloc[-1]

        # Call the ask_spec_gemini function to generate the response
        response = ask_spec_gemini(
            price=last['close'],
            rsi=last['rsi'],
            ema9=last['ema9'],
            ema21=last['ema21'],
            macd=last['macd'],
            macd_signal=last['macd_signal'],
            stochrsi=last['stochrsi'],
            user_question=user_question
        )

        # Escape special characters in the response to ensure it's safe for Telegram
        escaped_response = escape_markdown(response)

        # Send the generated response from Gemini to the user
        update.message.reply_text(escaped_response, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        update.message.reply_text(f"⚠️ Error: {e}")

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
