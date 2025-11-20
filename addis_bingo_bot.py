# Addis (አዲስ) Bingo - V7.0: Adds Card Selection logic.
# Players can now choose from 3 unique cards after sending /play.
# This version maintains the Interactive Card (V6.0) features.
# NOTE: MIN_PLAYERS is still set to 1 for testing.

import os
import logging
import json
import base64
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler
)
import firebase_admin
from firebase_admin import credentials, firestore

# --- Configuration ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')
V2_SECRETS = os.environ.get('V2_SECRETS')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') 

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Global Game State (In-Memory) ---
LOBBY = {} # Stores {user_id: [message_ids of card options]}
ACTIVE_GAMES = {}
GAME_COST = 10
PRIZE_AMOUNT = 40 
MIN_PLAYERS = 1 # *** REMEMBER TO CHANGE THIS TO 5 BEFORE GOING LIVE! ***
COLUMNS = ['B', 'I', 'N', 'G', 'O']

# --- Database Setup (Unchanged) ---
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

# --- Database Helpers (Unchanged) ---
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
    # Card Structure: Dictionary of lists for numbers, and a 'marked' dict for state.
    card_data = {
        'B': random.sample(range(1, 16), 5),
        'I': random.sample(range(16, 31), 5),
        'N': random.sample(range(31, 46), 5),
        'G': random.sample(range(46, 61), 5),
        'O': random.sample(range(61, 76), 5),
        'marked': {} 
    }
    # Free space: N column (index 2), row 2
    card_data['marked'][(2, 2)] = True
    return card_data

def get_card_value(card, col_idx, row_idx):
    if col_idx == 2 and row_idx == 2:
        return "FREE"
    return card[COLUMNS[col_idx]][row_idx]

def build_card_keyboard(card, card_index, game_id=None, msg_id=None, is_selection=True):
    keyboard = []
    header = [InlineKeyboardButton(col, callback_data=f"ignore_header") for col in COLUMNS]
    keyboard.append(header)
    
    for r in range(5):
        row = []
        for c in range(5):
            value = get_card_value(card, c, r)
            is_marked = card['marked'].get((c, r), False)
            
            if is_marked:
                label = '✅' 
                callback_data = f"ignore_marked"
            else:
                label = str(value)
                # If the game has started (is_selection=False), use MARK action
                if not is_selection:
                    callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" 
                # If player is selecting, buttons are not clickable for marking
                else:
                    callback_data = f"ignore_select"
            
            row.append(InlineKeyboardButton(label, callback_data=callback_data))
        keyboard.append(row)
    
    if is_selection:
        # Selection button for the initial choice phase
        keyboard.append([InlineKeyboardButton(f"✅ Card {card_index}: ይሄንን ይምረጡ (Select This)", callback_data=f"SELECT|{card_index}")])
    else:
        # BINGO button for the active game phase
        keyboard.append([InlineKeyboardButton("🚨 CALL BINGO! 🚨", callback_data=f"BINGO|{game_id}|{msg_id}")])
    
    return InlineKeyboardMarkup(keyboard)

def check_win(card, game_data):
    # This check now uses the 'marked' dictionary.
    def is_marked(c, r):
        return card['marked'].get((c, r), False)

    # Check rows
    for r in range(5):
        if all(is_marked(c, r) for c in range(5)): return True

    # Check columns
    for c in range(5):
        if all(is_marked(c, r) for r in range(5)): return True

    # Check diagonals
    if all(is_marked(i, i) for i in range(5)): return True
    if all(is_marked(i, 4 - i) for i in range(5)): return True
    
    return False

# --- Game Loop ---
async def run_game_loop(context: ContextTypes.DEFAULT_TYPE, game_id, players):
    cards = ACTIVE_GAMES[game_id]['cards']
    called = []
    available_numbers = list(range(1, 76))
    random.shuffle(available_numbers)
    
    ACTIVE_GAMES[game_id]['status'] = 'running'

    await asyncio.sleep(2)

    for num in available_numbers:
        if game_id not in ACTIVE_GAMES or ACTIVE_GAMES[game_id]['status'] != 'running':
            break

        called.append(num)
        ACTIVE_GAMES[game_id]['called'] = called
        
        # Determine the column (e.g., B-12)
        col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)
        msg = f"📣 ቁጥር (Number): **{col_letter}-{num}**\n\n_If you have this number, please tap the button on your card now!_"
        
        for pid in players:
            try:
                # Send the called number message
                await context.bot.send_message(pid, msg, parse_mode='Markdown')
            except Exception as e:
                 logger.error(f"Error sending number call to {pid}: {e}")
        
        await asyncio.sleep(8) 

# --- Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    create_or_update_user(user_id, update.effective_user.username, update.effective_user.first_name)
    await update.message.reply_text(f"እንኳን ወደ አዲስ ቢንጎ በደህና መጡ!\nSystem: {DB_STATUS}\n\nለመጫወት /play ይጫኑ (Cost: {GAME_COST} Birr).")

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # V5.4 Logic: Only shows balance
    user_id = update.effective_user.id
    data = get_user_data(user_id)
    balance = data.get('balance', 0.0)
    
    message = (
        f"**💰 ቀሪ ሂሳብ (Balance) 💰**\n\n"
        f"ሂሳብዎ: **{balance} Br**\n\n"
        f"_ገንዘብ ለማስገባት /deposit ይጫኑ።_"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # V5.4 Logic: ID is shown directly in deposit instructions
    user_id = update.effective_user.id
    telebirr_number = "0927922721"
    
    contact_info = ADMIN_USERNAME if ADMIN_USERNAME else str(ADMIN_USER_ID)
    
    if ADMIN_USERNAME and ADMIN_USERNAME.startswith('@'):
        link_name = f"Admin ({ADMIN_USERNAME})"
        link_message = f"[Send Receipt to {link_name}](https://t.me/{ADMIN_USERNAME.lstrip('@')})"
    else:
        link_message = f"Send receipt to Admin: {contact_info}"

    message = (
        f"**🏦 የገንዘብ ማስገቢያ (Deposit Instructions) 🏦**\n\n"
        f"1. Telebirr ቁጥር: **{telebirr_number}** ይጠቀሙ።\n"
        f"2. የእርስዎ መለያ ቁጥር (Telegram ID):\n"
        f"   **{user_id}**\n\n"
        f"3. የላኩበትን ደረሰኝ (Screenshot) እና **ID ቁጥርዎን** ወዲያውኑ ለኛ ይላኩ:\n"
        f"{link_message}\n\n"
        f"_ገንዘብዎ በአንድ ደቂቃ ውስጥ ወደ ሂሳብዎ ይገባል!_"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("ገንዘብ ለማውጣት ለአድሚን መልእክት ይላኩ።")

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    data = get_user_data(user_id)
    if data.get('balance', 0) < GAME_COST:
        await update.message.reply_text(f"⛔ በቂ ሂሳብ የለዎትም (Not enough balance).\nያስፈልጋል: {GAME_COST} Br\nአለዎት: {data.get('balance', 0)} Br")
        return

    # Check if user is already in a game or selecting a card
    if user_id in LOBBY or any(user_id in g['players'] for g in ACTIVE_GAMES.values()):
        await update.message.reply_text("⏳ ተራ ይጠብቁ (Already waiting or in a game).")
        return

    # Deduct cost immediately
    update_balance(user_id, -GAME_COST)
    
    # Generate 3 card options
    card_options = [generate_card() for i in range(3)]
    
    # Store message IDs for cleanup later
    card_message_ids = []

    # Send the 3 options
    for i, card in enumerate(card_options):
        keyboard = build_card_keyboard(card, i, is_selection=True)
        message_text = f"🃏 **Card Option {i+1}** 🃏\n\n_ይህን ካርድ ከመምረጥዎ በፊት ቁጥሮቹን በጥንቃቄ ይመልከቱ።_"
        msg = await context.bot.send_message(user_id, message_text, reply_markup=keyboard, parse_mode='Markdown')
        card_message_ids.append(msg.message_id)

    # Add user to lobby with the card options and message IDs
    LOBBY[user_id] = {
        'cards': card_options,
        'message_ids': card_message_ids,
        'status': 'selecting_card'
    }
    
    # Notification to wait for choice
    await context.bot.send_message(user_id, f"✅ {GAME_COST} Br ተቀንሷል። (Deducted {GAME_COST} Br).\n\n**እባክዎ ከላይ ካሉት 3 ካርዶች አንዱን ይምረጡ።**")

    # Check if game can start (for testing, it's always 1/1)
    if len(LOBBY) >= MIN_PLAYERS:
        # Since we are using MIN_PLAYERS = 1 for testing, the game will be queued 
        # but won't start until the player selects a card.
        pass # Game starts after SELECT callback

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data.split('|')
    action = data[0]

    if action == 'SELECT':
        # Handles the player selecting one of the 3 cards
        if user_id not in LOBBY or LOBBY[user_id]['status'] != 'selecting_card':
            await query.answer("Invalid card selection or session expired.")
            return

        card_index = int(data[1])
        lobby_data = LOBBY.pop(user_id) # Remove from lobby
        
        # Get the selected card and the list of message IDs for cleanup
        selected_card = lobby_data['cards'][card_index]
        all_message_ids = lobby_data['message_ids']
        
        # 1. Clean up the other 2 card options
        for msg_id in all_message_ids:
            try:
                if msg_id != query.message.message_id:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                else:
                    # Edit the selected card's message to become the FINAL game card
                    await context.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=f"✅ Card Selected! ጨዋታው ሊጀምር ነው! (Game starting...)\n\n_Tap the numbers on the card below to mark them._",
                        reply_markup=None # Remove the old selection button
                    )
            except Exception as e:
                logger.error(f"Error cleaning up card messages: {e}")

        # 2. Start the Game!
        game_id = f"G{random.randint(1000,9999)}"
        # Store the game data: one player for now, but ready for multiplayer structure
        
        # Send the final interactive card for the game
        final_keyboard = build_card_keyboard(selected_card, card_index, game_id, query.message.message_id, is_selection=False)

        final_msg = await context.bot.edit_message_reply_markup(
            chat_id=user_id,
            message_id=query.message.message_id,
            reply_markup=final_keyboard
        )

        ACTIVE_GAMES[game_id] = {
            'players': [user_id], 
            'cards': {user_id: selected_card}, 
            'called': [], 
            'status': 'starting', 
            'card_messages': {user_id: query.message.message_id}
        }
        
        await query.answer("Card selected! Get ready to play!")
        asyncio.create_task(run_game_loop(context, game_id, [user_id]))
        return

    # --- MARK and BINGO (Active Game Logic) ---
    
    # Check if the player is in the active game
    if game_id not in ACTIVE_GAMES or user_id not in ACTIVE_GAMES[game_id]['players']:
        await query.answer("This game has ended or you are not a participant.")
        return

    game_data = ACTIVE_GAMES[game_id]
    card = game_data['cards'][user_id]
    
    if action == 'MARK':
        # MARK|GameID|MsgID|ColIndex|RowIndex
        if len(data) < 5: return
        c, r = int(data[3]), int(data[4])
        
        called_numbers = game_data['called']
        value = get_card_value(card, c, r)
        
        if value == 'FREE':
            await query.answer("Free space is already marked.")
            return

        if value not in called_numbers:
            await query.answer("That number has not been called yet!")
            return

        # Toggle the marked state
        pos = (c, r)
        card['marked'][pos] = not card['marked'].get(pos, False)
        
        # Re-render the card
        new_keyboard = build_card_keyboard(card, -1, game_id, msg_id, is_selection=False)
        
        try:
            await query.edit_message_reply_markup(reply_markup=new_keyboard)
            await query.answer(f"Number {value} {'marked' if card['marked'][pos] else 'unmarked'}")
        except Exception as e:
            logger.error(f"Error editing message reply markup: {e}")
            await query.answer("Error updating card. Is the message too old?")

    elif action == 'BINGO':
        # Check for Win
        if check_win(card, game_data):
            game_data['status'] = 'finished'
            update_balance(user_id, PRIZE_AMOUNT)
            
            winner_name = query.from_user.first_name
            win_msg = f"🎉 BINGO!!! 🎉\n\nአሸናፊ (Winner): {winner_name}\nPrize: {PRIZE_AMOUNT} Br Added!"
            
            # Notify all players
            for pid in game_data['players']:
                await context.bot.send_message(pid, win_msg)
            
            # Clean up the winner's card
            try:
                 await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=f"WINNER! Game Over.\nPrize: {PRIZE_AMOUNT} Br",
                    reply_markup=None
                )
            except:
                pass
            
            del ACTIVE_GAMES[game_id]
        else:
            await query.answer("❌ ውሸት! (Not a winner yet). Keep playing.")

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
        await update.message.reply_text("Error. Usage: /ap_dep [id] [amt] (Both must be numbers)")

# --- Main ---
def main():
    if not TOKEN: return
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("withdraw", withdraw_command))
    app.add_handler(CommandHandler("play", play_command))
    app.add_handler(CommandHandler("ap_dep", approve_deposit_admin))
    
    app.add_handler(CallbackQueryHandler(handle_callback))

    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')

if __name__ == '__main__':
    main()
