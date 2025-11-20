# Addis (አዲስ) Bingo - V3: Single Secret for Free Tier
# NOTE: This code reads ADMIN_USER_ID and FIREBASE_CREDENTIALS_B64 from a single V2_SECRETS environment variable.

import os
import logging
import json
import base64
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import firebase_admin
from firebase_admin import credentials, firestore

# --- Configuration & Secrets ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')

# --- NEW: Extract secrets from the single combined variable ---
V2_SECRETS = os.environ.get('V2_SECRETS')
ADMIN_USER_ID = None
FIREBASE_CREDENTIALS_B64 = None

if V2_SECRETS and '|' in V2_SECRETS:
    try:
        # Split the string at the first '|' character
        admin_id_str, firebase_b64 = V2_SECRETS.split('|', 1)
        ADMIN_USER_ID = int(admin_id_str)
        FIREBASE_CREDENTIALS_B64 = firebase_b64
    except ValueError as e:
        print(f"Error parsing V2_SECRETS: {e}. Check format: ID|BASE64_STRING")
else:
    print("V2_SECRETS environment variable is missing or incorrectly formatted.")

# Editable Variables for Messages
TELEBIRR_ACCOUNT = "0927922721" 
ADMIN_USERNAME = "@your_admin_tg_username" 
MIN_DEPOSIT = 50
MIN_WITHDRAWAL = 50
GAME_COST = 10

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Firebase Initialization ---
db = None
if FIREBASE_CREDENTIALS_B64:
    try:
        # Decode the Base64 string back into the JSON content
        service_account_info = json.loads(base64.b64decode(FIREBASE_CREDENTIALS_B64).decode('utf-8'))
        
        # Initialize Firebase Admin SDK
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase connection successful.")
    except Exception as e:
        logger.error(f"Error initializing Firebase with V2_SECRETS: {e}")
else:
    logger.error("FIREBASE_CREDENTIALS_B64 part of V2_SECRETS is missing or invalid. Database disabled.")

# --- Database Helpers ---
USERS_COLLECTION = 'addis_bingo_users'

def get_user_data(user_id: int) -> dict:
    if not db: return {'balance': 0, 'new_user': True}
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict()
        data['new_user'] = False
        return data
    else:
        return {'balance': 0, 'new_user': True}

def create_new_user(user_id: int, username: str, first_name: str):
    if not db: return
    # Ensure ADMIN_USER_ID is an integer before comparison
    is_admin = False
    if ADMIN_USER_ID is not None:
        is_admin = user_id == ADMIN_USER_ID
        
    db.collection(USERS_COLLECTION).document(str(user_id)).set({
        'username': username,
        'first_name': first_name,
        'balance': 0.0,
        'is_admin': is_admin,
        'created_at': firestore.SERVER_TIMESTAMP
    })

def update_user_balance(user_id: int, amount: float):
    if not db: return
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    doc_ref.update({
        'balance': firestore.Increment(amount)
    })

# --- Amharic Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start, checks/creates user, sends welcome."""
    user_id = update.effective_user.id
    username = update.effective_user.username or "N/A"
    first_name = update.effective_user.first_name or "ተጫዋች"
    
    user_data = get_user_data(user_id)
    if user_data.get('new_user'):
        create_new_user(user_id, username, first_name)
        logger.info(f"New user created: {user_id}")
    
    welcome_message = "እንኳን ወደ አዲስ ቢንጎ በደህና መጡ!"
    await update.message.reply_text(welcome_message)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the user's current balance."""
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    balance = user_data.get('balance', 0.0)
    
    message = f"የእርስዎ የአሁኑ ቀሪ ሂሳብ: **{balance:.2f} ብር** ነው"
    await update.message.reply_markdown(message)

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends deposit instructions."""
    message = (
        f"ወደ አዲስ ቢንጎ ገንዘብ ለማስገባት በቴሌብር አካውንት `{TELEBIRR_ACCOUNT}` "
        f"በማስገባት ደረሰኁን በ ቴሌግራም **{ADMIN_USERNAME}** ስክሪንሻት አንስተው ይላኩ። "
        f"ትንሹ ለማስገባት የሚቻለው የገንዘብ መጠን **{MIN_DEPOSIT} ብር** ነዉ።"
    )
    await update.message.reply_markdown(message)

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends withdrawal instructions."""
    message = (
        f"ከአዲስ ቢንጎ ገንዘብ ለማስወጣት ቴሌግራም አካውንትዎን **{ADMIN_USERNAME}** በመጠቀም "
        f"የሚያወጡትን የገንዘብ መጠን ይላኩልን። "
        f"ትንሹ ለማስወጣት የሚቻለው የገንዘብ መጠን **{MIN_WITHDRAWAL} ብር** ነዉ።"
    )
    await update.message.reply_markdown(message)

# Placeholder for the future game logic
async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """V3 Placeholder: Checks balance before playing."""
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    balance = user_data.get('balance', 0.0)

    if balance < GAME_COST:
        message = (
            f"ለመጫወት በቂ ቀሪ ሂሳብ የለዎትም። ለመጫወት **{GAME_COST} ብር** ያስፈልጋል። "
            f"የእርስዎ ቀሪ ሂሳብ **{balance:.2f} ብር** ነው።"
        )
    else:
        message = "እባክዎ ትንሽ ይጠብቁ። ጨዋታው በ5 ተጫዋቾች ይጀምራል።" # Waiting for game message
        
    await update.message.reply_text(message)

# --- Admin Functions (SECURE) ---

async def approve_deposit_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin command to approve a deposit. Usage: /ap_dep [user_id] [amount]
    """
    if ADMIN_USER_ID is None or update.effective_user.id != ADMIN_USER_ID:
        return # Silently ignore non-admin attempts or if ID is missing

    if not context.args or len(context.args) != 2:
        await update.message.reply_text("Usage: /ap_dep [user_id] [amount]")
        return
    
    try:
        target_user_id = int(context.args[0])
        amount = float(context.args[1])
        
        if amount <= 0:
            await update.message.reply_text("Amount must be positive.")
            return

        update_user_balance(target_user_id, amount)
        user_data = get_user_data(target_user_id)
        new_balance = user_data.get('balance', 0.0)
        
        # Send confirmation to the Admin
        await update.message.reply_text(f"✅ Deposit Approved: Added {amount:.2f} ETB to User ID {target_user_id}. New Balance: {new_balance:.2f} ETB.")
        
        # Send confirmation to the User
        user_message = (
            f"ተሳክቷል! የ {amount:.2f} ብር ተቀማጭ ገንዘብዎ ተረጋግጧል። "
            f"አዲሱ ቀሪ ሂሳብዎ **{new_balance:.2f} ብር** ነው።"
        )
        await context.bot.send_message(chat_id=target_user_id, text=user_message)

    except Exception as e:
        await update.message.reply_text(f"Error processing deposit: {e}")
        logger.error(f"Admin Deposit Error: {e}")

# --- Main Bot Initialization ---

def main() -> None:
    """Start the bot."""
    if not TOKEN:
        print("Bot failed to start: TELEGRAM_TOKEN missing.")
        return

    application = Application.builder().token(TOKEN).build()

    # Public Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("deposit", deposit_command))
    application.add_handler(CommandHandler("withdraw", withdraw_command))
    application.add_handler(CommandHandler("play", play_command)) 

    # Admin Handlers (Secured by ADMIN_USER_ID)
    application.add_handler(CommandHandler("ap_dep", approve_deposit_admin)) 
    
    # --- Deployment Configuration for Render ---
    PORT = int(os.environ.get('PORT', '8080'))
    
    if RENDER_EXTERNAL_URL:
        logger.info(f"Starting bot in production on port {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN, 
            webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}' 
        )
    else:
        logger.error("Bot cannot start without RENDER_EXTERNAL_URL in production.")

if __name__ == '__main__':
    main()
