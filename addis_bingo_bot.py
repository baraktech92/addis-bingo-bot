# Addis (አዲስ) Bingo - V4: Debug Mode
# This version reports database errors directly to the Telegram Chat.

import os
import logging
import json
import base64
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import firebase_admin
from firebase_admin import credentials, firestore

# --- Configuration ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')
V2_SECRETS = os.environ.get('V2_SECRETS')

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Global Status Variables ---
DB_STATUS = "Unknown"
DB_ERROR = "None"
ADMIN_USER_ID = None
db = None

# --- Initialization Logic ---
try:
    if V2_SECRETS and '|' in V2_SECRETS:
        admin_id_str, firebase_b64 = V2_SECRETS.split('|', 1)
        ADMIN_USER_ID = int(admin_id_str)
        
        # Try to connect to Firebase
        service_account_info = json.loads(base64.b64decode(firebase_b64).decode('utf-8'))
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        DB_STATUS = "Connected"
    else:
        DB_STATUS = "Failed"
        DB_ERROR = "V2_SECRETS missing or no '|' separator found."
except Exception as e:
    DB_STATUS = "Error"
    DB_ERROR = str(e)

# --- Database Helpers ---
USERS_COLLECTION = 'addis_bingo_users'

def get_user_data(user_id: int) -> dict:
    if not db: return {'balance': 0, 'new_user': True}
    try:
        doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            data['new_user'] = False
            return data
        else:
            return {'balance': 0, 'new_user': True}
    except Exception as e:
        logger.error(f"DB Read Error: {e}")
        return {'balance': 0, 'new_user': True}

def create_new_user(user_id: int, username: str, first_name: str):
    if not db: return
    try:
        is_admin = (user_id == ADMIN_USER_ID)
        db.collection(USERS_COLLECTION).document(str(user_id)).set({
            'username': username,
            'first_name': first_name,
            'balance': 0.0,
            'is_admin': is_admin,
            'created_at': firestore.SERVER_TIMESTAMP
        })
    except Exception as e:
        logger.error(f"DB Create Error: {e}")

def update_user_balance(user_id: int, amount: float):
    if not db: return
    db.collection(USERS_COLLECTION).document(str(user_id)).update({
        'balance': firestore.Increment(amount)
    })

# --- Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start and reports DB status."""
    user_id = update.effective_user.id
    username = update.effective_user.username or "N/A"
    first_name = update.effective_user.first_name or "ተጫዋች"
    
    # Try to create user
    user_data = get_user_data(user_id)
    if user_data.get('new_user'):
        create_new_user(user_id, username, first_name)
    
    # Welcome Message
    msg = "እንኳን ወደ አዲስ ቢንጎ በደህና መጡ!\n"
    
    # DEBUG INFO (Only shows if there is a problem)
    if DB_STATUS != "Connected":
        msg += f"\n⚠️ SYSTEM WARNING: Database is NOT connected.\nReason: {DB_ERROR}"
    else:
        msg += "\n✅ System Online."

    await update.message.reply_text(msg)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows balance using plain text to avoid crashes."""
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    balance = user_data.get('balance', 0.0)
    
    # Using reply_text instead of Markdown to prevent parsing errors
    await update.message.reply_text(f"የእርስዎ የአሁኑ ቀሪ ሂሳብ: {balance:.2f} ብር ነው")

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"ወደ አዲስ ቢንጎ ገንዘብ ለማስገባት በቴሌብር አካውንት 0927922721 በማስገባት "
        f"ደረሰኁን በ ቴሌግራም ለ {ADMIN_USER_ID} ይላኩ።"
    )

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("ገንዘብ ለማውጣት ለአድሚን መልእክት ይላኩ።")

async def approve_deposit_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_USER_ID is None or update.effective_user.id != ADMIN_USER_ID:
        return

    if not context.args or len(context.args) != 2:
        await update.message.reply_text("Usage: /ap_dep [user_id] [amount]")
        return
    
    try:
        target_user_id = int(context.args[0])
        amount = float(context.args[1])
        update_user_balance(target_user_id, amount)
        await update.message.reply_text(f"✅ Success. Added {amount} to {target_user_id}")
        await context.bot.send_message(chat_id=target_user_id, text=f"Deposit received! Balance updated: +{amount}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# --- Main ---

def main() -> None:
    if not TOKEN:
        return
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("deposit", deposit_command))
    application.add_handler(CommandHandler("withdraw", withdraw_command))
    application.add_handler(CommandHandler("ap_dep", approve_deposit_admin))

    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}'
        )

if __name__ == '__main__':
    main()
