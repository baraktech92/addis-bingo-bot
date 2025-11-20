# Addis (አዲስ) Bingo - V5: Complete Game Engine (Test Mode)
# Features: Lobby, Auto-Deduct, Bingo Cards, Number Calling, Win Check
# NOTE: MIN_PLAYERS is set to 1 for testing.

import os
import logging
import json
import base64
import asyncio
import random
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

# --- Global Game State (In-Memory) ---
LOBBY = set() # Stores user_ids waiting to play
ACTIVE_GAMES = {} # Stores game data
GAME_COST = 10
PRIZE_AMOUNT = 40 # 80% of 50

# --- TEST SETTING: START WITH 1 PLAYER ---
MIN_PLAYERS = 1 

# --- Database Setup ---
DB_STATUS = "Unknown"
ADMIN_USER_ID = None
db = None

try:
    if V2_SECRETS and '|' in V2_SECRETS:
        admin_id_str, firebase_b64 = V2_SECRETS.split('|', 1)
        ADMIN_USER_ID = int(admin_id_str)
        service_account_info = json.loads(base64.b64decode(firebase_b64).decode('utf-8'))
        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        DB_STATUS = "Connected"
    else:
        DB_STATUS = "Failed: Secrets Missing"
except Exception as e:
    DB_STATUS = f"Error: {e}"

# --- Database Helpers ---
USERS_COLLECTION = 'addis_bingo_users'

def get_user_data(user_id: int) -> dict:
    if not db: return {'balance': 0}
    doc = db.collection(USERS_COLLECTION).document(str(user_id)).get()
    if doc.exists:
        return doc.to_dict()
    return {'balance': 0, 'new_user': True}

def create_or_update_user(user_id: int, username: str, first_name: str):
    if not db: return
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    if not doc_ref.get().exists:
        doc_ref.set({
            'username': username,
            'first_name': first_name,
            'balance': 0.0,
            'created_at': firestore.SERVER_TIMESTAMP
        })

def update_balance(user_id: int, amount: float):
    if not db: return
    db.collection(USERS_COLLECTION).document(str(user_id)).update({
        'balance': firestore.Increment(amount)
    })

# --- Bingo Logic ---

def generate_card():
    """Generates a 5x5 Bingo card columns."""
    card = {
        'B': random.sample(range(1, 16), 5),
        'I': random.sample(range(16, 31), 5),
        'N': random.sample(range(31, 46), 5),
        'G': random.sample(range(46, 61), 5),
        'O': random.sample(range(61, 76), 5),
    }
    return card

def format_card_text(card):
    """Creates a text-based Bingo board string."""
    msg = "🎱 **B  I  N  G  O** 🎱\n"
    for i in range(5):
        row = [card['B'][i], card['I'][i], card['N'][i], card['G'][i], card['O'][i]]
        msg += f"{row[0]:02} {row[1]:02} {row[2]:02} {row[3]:02} {row[4]:02}\n"
    return msg

def check_win(card, called_numbers):
    """Checks if a card has a winning line."""
    called_set = set(called_numbers)
    
    # Convert card to grid 5x5
    grid = []
    for i in range(5):
        grid.append([card['B'][i], card['I'][i], card['N'][i], card['G'][i], card['O'][i]])

    # Check Rows & Cols
    for i in range(5):
        if all(grid[i][c] in called_set for c in range(5)): return True # Row
        if all(grid[r][i] in called_set for r in range(5)): return True # Col

    # Check Diagonals
    if all(grid[i][i] in called_set for i in range(5)): return True
    if all(grid[i][4-i] in called_set for i in range(5)): return True

    return False

# --- Game Loop (Async) ---

async def run_game_loop(context: ContextTypes.DEFAULT_TYPE, game_id, players):
    """The main heartbeat of the game. Calls numbers every 4 seconds."""
    cards = {pid: generate_card() for pid in players}
    called = []
    available_numbers = list(range(1, 76))
    random.shuffle(available_numbers)
    
    ACTIVE_GAMES[game_id] = {
        'players': players,
        'cards': cards,
        'called': called,
        'status': 'running'
    }

    # 1. Send Cards to Players
    for pid in players:
        card_txt = format_card_text(cards[pid])
        try:
            await context.bot.send_message(pid, f"ጨዋታው ተጀምሯል! (Game Started!)\n\n{card_txt}\n\nቁጥሮች ሲጠሩ ካርድዎን ያረጋግጡ! እርስዎ ብቻዎን እየተጫወቱ ነው። (Testing Mode)")
        except:
            pass 

    await asyncio.sleep(3)

    # 2. Call Numbers Loop
    for num in available_numbers:
        if game_id not in ACTIVE_GAMES or ACTIVE_GAMES[game_id]['status'] != 'running':
            break # Game ended

        called.append(num)
        ACTIVE_GAMES[game_id]['called'] = called
        
        # Broadcast Number
        msg = f"📣 ቁጥር (Number): **{num}**"
        for pid in players:
            try:
                await context.bot.send_message(pid, msg)
            except:
                pass
        
        # Wait before next number
        await asyncio.sleep(4) 

# --- Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    create_or_update_user(user_id, update.effective_user.username, update.effective_user.first_name)
    await update.message.reply_text(f"እንኳን ወደ አዲስ ቢንጎ በደህና መጡ!\nSystem: {DB_STATUS}\n\nለመጫወት /play ይጫኑ (Cost: {GAME_COST} Birr).")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    data = get_user_data(user_id)
    await update.message.reply_text(f"💰 ቀሪ ሂሳብ (Balance): {data.get('balance', 0.0)} Br")

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Deposit to 0927922721 (Telebirr). Send receipt to {ADMIN_USER_ID}.")

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("ገንዘብ ለማውጣት ለአድሚን መልእክት ይላኩ።")

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    # 1. Check Balance
    data = get_user_data(user_id)
    if data.get('balance', 0) < GAME_COST:
        await update.message.reply_text(f"⛔ በቂ ሂሳብ የለዎትም (Not enough balance).\nያስፈልጋል: {GAME_COST} Br\nአለዎት: {data.get('balance', 0)} Br")
        return

    # 2. Check if already in lobby
    if user_id in LOBBY:
        await update.message.reply_text(f"⏳ ተራ ይጠብቁ (Already waiting). {len(LOBBY)}/{MIN_PLAYERS} players.")
        return

    # 3. Deduct Money & Join
    update_balance(user_id, -GAME_COST)
    LOBBY.add(user_id)
    
    await update.message.reply_text(f"✅ ተመዝግበዋል! (Joined). {len(LOBBY)}/{MIN_PLAYERS} players.")

    # 4. Check Start Condition
    if len(LOBBY) >= MIN_PLAYERS:
        game_players = list(LOBBY)
        LOBBY.clear()
        game_id = f"G{random.randint(1000,9999)}"
        
        # Notify Start
        for pid in game_players:
            await context.bot.send_message(pid, "🚀 ጨዋታው ሊጀምር ነው! (Game starting...)")
        
        # Start Game Loop Task
        asyncio.create_task(run_game_loop(context, game_id, game_players))

async def bingo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User claims Bingo!"""
    user_id = update.effective_user.id
    
    # Find user's active game
    found_game_id = None
    for gid, gdata in ACTIVE_GAMES.items():
        if user_id in gdata['players'] and gdata['status'] == 'running':
            found_game_id = gid
            break
    
    if not found_game_id:
        await update.message.reply_text("You are not in an active game.")
        return

    game_data = ACTIVE_GAMES[found_game_id]
    card = game_data['cards'][user_id]
    called = game_data['called']

    # Validate Win
    if check_win(card, called):
        # WINNER!
        game_data['status'] = 'finished' # Stop the loop
        update_balance(user_id, PRIZE_AMOUNT)
        
        win_msg = f"🎉 BINGO!!! 🎉\n\nአሸናፊ (Winner): {update.effective_user.first_name}\nPrize: {PRIZE_AMOUNT} Br Added!"
        for pid in game_data['players']:
            await context.bot.send_message(pid, win_msg)
        
        del ACTIVE_GAMES[found_game_id] # Cleanup
    else:
        # False Alarm
        await update.message.reply_text("❌ ውሸት! (Not a winner yet). Keep playing.")

# --- Admin ---
async def approve_deposit_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_USER_ID is None or update.effective_user.id != ADMIN_USER_ID: return
    try:
        tid = int(context.args[0])
        amt = float(context.args[1])
        update_balance(tid, amt)
        await update.message.reply_text(f"✅ Approved {amt} to {tid}")
        await context.bot.send_message(tid, f"Deposit Approved: +{amt} Br")
    except:
        await update.message.reply_text("Error. Usage: /ap_dep [id] [amt]")

# --- Main ---
def main():
    if not TOKEN: return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("withdraw", withdraw_command))
    app.add_handler(CommandHandler("play", play_command))
    app.add_handler(CommandHandler("bingo", bingo_command))
    app.add_handler(CommandHandler("ap_dep", approve_deposit_admin))

    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')

if __name__ == '__main__':
    main()
