# Addis Bingo Bot - Stable Version for Render
# Author: Gemini
# Description: Telegram Bingo game bot using local JSON files.
# Compatibility: Updated for Python 3.13 and python-telegram-bot v21.9

import os
import json
import time
import random
import logging
from datetime import datetime, timedelta

# --- Python-Telegram-Bot Imports ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler
)

# --- Configuration ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# !!! REPLACE WITH YOUR ACTUAL NUMERIC ID !!!
ADMIN_USER_ID = 5887428731 

# Constants
GAME_PRICE = 50.0
INITIAL_BALANCE = 1000.0
MIN_PLAYERS = 2
GAME_INTERVAL_SECONDS = 300
CALL_INTERVAL_SECONDS = 15 # Call speed

# Environment Variables
TOKEN = os.environ.get("TELEGRAM_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")

# Files
STATE_FILE = "bingo_state.json"
USER_DATA_FILE = "user_data.json"

# Global State
global_state = {}
user_data_cache = {}

# --- Persistence Functions ---

def load_state():
    global global_state, user_data_cache
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f: global_state = json.load(f)
        else:
            global_state = {
                'current_game_id': 0, 'current_numbers': [], 'is_game_active': False,
                'last_game_time': time.time(), 'active_players': {}, 'total_prize_pool': 0.0
            }
        
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, 'r') as f: 
                raw_data = json.load(f)
                user_data_cache = {int(k): v for k, v in raw_data.items()}
        else:
            user_data_cache = {}
            
    except Exception as e:
        logger.error(f"Error loading state: {e}")

def save_state():
    try:
        with open(STATE_FILE, 'w') as f: json.dump(global_state, f)
        with open(USER_DATA_FILE, 'w') as f: 
            json.dump({str(k): v for k, v in user_data_cache.items()}, f)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

def get_user_data(user_id: int) -> dict:
    if user_id in user_data_cache: return user_data_cache[user_id]
    default = {'user_id': user_id, 'balance': INITIAL_BALANCE, 'cards': {}}
    user_data_cache[user_id] = default
    save_state()
    return default

# --- Game Logic ---

def generate_card():
    card = {
        'B': random.sample(range(1, 16), 5),
        'I': random.sample(range(16, 31), 5),
        'N': random.sample(range(31, 46), 4), # 4 numbers + free space
        'G': random.sample(range(46, 61), 5),
        'O': random.sample(range(61, 76), 5)
    }
    # Insert 0 for Free Space in N column
    card['N'].insert(2, 0) 
    
    # Create matrix
    matrix = []
    cols = ['B', 'I', 'N', 'G', 'O']
    for r in range(5):
        matrix.append([card[c][r] for c in cols])
    return matrix

def check_bingo(matrix, called):
    def covered(n): return n == 0 or n in called
    # Rows
    for r in matrix:
        if all(covered(n) for n in r): return True
    # Cols
    for c in range(5):
        if all(covered(matrix[r][c]) for r in range(5)): return True
    # Diagonals
    if all(covered(matrix[i][i]) for i in range(5)): return True
    if all(covered(matrix[i][4-i]) for i in range(5)): return True
    return False

# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user_data(user.id)
    await update.message.reply_html(
        f"ሰላም {user.mention_html()}!\nባላንስዎ: <b>{data['balance']} ብር</b>\n"
        f"ካርድ ለመግዛት: /buycard"
    )

async def buycard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = get_user_data(user.id)
    
    if data['balance'] < GAME_PRICE:
        await update.message.reply_text("በቂ ሒሳብ የለዎትም። /deposit ይጠቀሙ።")
        return

    data['balance'] -= GAME_PRICE
    
    # Register player for current game
    gid = global_state['current_game_id']
    if str(gid) not in data['cards']: data['cards'][str(gid)] = []
    
    card = generate_card()
    data['cards'][str(gid)].append(card)
    
    global_state['active_players'][str(user.id)] = True
    global_state['total_prize_pool'] += GAME_PRICE
    save_state()
    
    # Format card for display
    card_str = "B  I  N  G  O\n"
    for row in card:
        card_str += " ".join(f"{n:02d}" if n!=0 else "FB" for n in row) + "\n"
        
    await update.message.reply_text(f"ካርድ ተገዝቷል!\n\n`{card_str}`", parse_mode="Markdown")

async def deposit_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"እባክዎ ወደ 0927922721 ገንዘብ ያስገቡና ደረሰኝዎን ለአድሚን ይላኩ።\nየእርስዎ ID: `{update.effective_user.id}`", parse_mode="Markdown")

# --- Main Execution ---

def main():
    """Start the bot."""
    if not TOKEN:
        logger.critical("TELEGRAM_TOKEN not set.")
        return

    # Load data
    load_state()

    # Create Application
    app = Application.builder().token(TOKEN).build()

    # Add Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buycard", buycard))
    app.add_handler(CommandHandler("balance", start)) # Re-use start for balance
    app.add_handler(CommandHandler("deposit", deposit_request))
    
    # Webhook vs Polling logic
    if RENDER_URL:
        logger.info(f"Starting Webhook on Port {PORT}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{RENDER_URL}/{TOKEN}"
        )
    else:
        logger.info("Starting Polling...")
        app.run_polling()

if __name__ == "__main__":
    main()
