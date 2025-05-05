import os
import ccxt
import ta
import pandas as pd
from dotenv import load_dotenv
from telegram.ext import Updater, CommandHandler
from telegram import ParseMode
from threading import Thread


load_dotenv()

# Set up API keys and Binance
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

binance = ccxt.binance({
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_SECRET_KEY,
    'enableRateLimit': True
})
binance.set_sandbox_mode(False)


def fetch_price():
    ticker = binance.fetch_ticker('BTC/USDT')
    return ticker['last']

def fetch_data():
    bars = binance.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=50)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    df['ema'] = ta.trend.EMAIndicator(df['close'], window=14).ema_indicator()
    return df.iloc[-1]


def show_price(update, context):
    last = fetch_data()
    price = last['close']
    rsi = last['rsi']
    ema = last['ema']
    msg = f"📊 *BTC/USDT Price*: ${price:.2f}\nRSI: {rsi:.2f}\nEMA(14): {ema:.2f}"
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode=ParseMode.MARKDOWN)


def show_balance(update, context):
    balance = binance.fetch_balance()
    usdt = balance['USDT']['free']
    btc = balance['BTC']['free']
    msg = f"💰 *Your Balance*:\nUSDT: {usdt:.2f}\nBTC: {btc:.6f}"
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode=ParseMode.MARKDOWN)


def buy(update, context):
    try:
        percent = float(context.args[0]) / 100
        balance = binance.fetch_balance()['USDT']['free']
        usdt_to_spend = balance * percent
        price = fetch_price()
        btc_amount = usdt_to_spend / price
        order = binance.create_market_buy_order('BTC/USDT', round(btc_amount, 6))
        msg = f"🟢 Bought {btc_amount:.6f} BTC (~${usdt_to_spend:.2f})"
    except Exception as e:
        msg = f"❌ Buy error: {e}"
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg)


def sell(update, context):
    try:
        percent = float(context.args[0]) / 100
        balance = binance.fetch_balance()['BTC']['free']
        btc_to_sell = balance * percent
        order = binance.create_market_sell_order('BTC/USDT', round(btc_to_sell, 6))
        msg = f"🔴 Sold {btc_to_sell:.6f} BTC"
    except Exception as e:
        msg = f"❌ Sell error: {e}"
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

def telegram_price(update, context):
    last = fetch_data()
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

def telegram_buy(update, context):
    try:
        percent = float(context.args[0]) / 100
        balance = binance.fetch_balance()['USDT']['free']
        price = fetch_price()
        amount = (balance * percent) / price
        binance.create_market_buy_order('BTC/USDT', round(amount, 6))
        msg = f"🟢 Bought {amount:.6f} BTC"
    except Exception as e:
        msg = f"❌ Buy failed: {e}"
    context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

def telegram_sell(update, context):
    try:
        percent = float(context.args[0]) / 100
        balance = binance.fetch_balance()['BTC']['free']
        amount = balance * percent
        binance.create_market_sell_order('BTC/USDT', round(amount, 6))
        msg = f"🔴 Sold {amount:.6f} BTC"
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

    updater.start_polling()

def main():
    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("price", show_price))
    dp.add_handler(CommandHandler("balance", show_balance))
    dp.add_handler(CommandHandler("buy", buy))
    dp.add_handler(CommandHandler("sell", sell))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
