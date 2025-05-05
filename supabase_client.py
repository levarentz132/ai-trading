from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def save_binance_keys(chat_id, api_key, secret_key):
    try:
        supabase.table("users").update({
            "binance_key": api_key,
            "binance_secret": secret_key
        }).eq("chat_id", chat_id).execute()
        print("✅ Binance keys saved")
    except Exception as e:
        print(f"❌ Failed to save Binance keys: {e}")


def add_user(chat_id, username):
    try:
        supabase.table("users").insert({
            "chat_id": chat_id,
            "username": username
        }).execute()
    except Exception as e:
        print(f"🛑 Supabase insert error: {e}")

def user_exists(chat_id):
    res = supabase.table("users").select("*").eq("chat_id", chat_id).execute()
    return len(res.data) > 0
