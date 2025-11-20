# Addis (አዲስ) Bingo - V8.1: Adds /instructions command and includes rules on /start.
# This version features the Dynamic Card with automatic Green highlights and Card Selection.

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

# --- Constants ---
GAME_COST = 10
PRIZE_AMOUNT = 40 
MIN_PLAYERS = 1 # *** CHANGE THIS TO 5 BEFORE GOING LIVE! ***
COLUMNS = ['B', 'I', 'N', 'G', 'O']

# --- Emojis for Card State ---
EMOJI_UNMARKED = '🔵' # Not called, not marked
EMOJI_CALLED = '🟢'   # Called, not marked
EMOJI_MARKED = '✅'   # Called, and marked by player
EMOJI_FREE = '🌟'     # Free space

# --- Global Game State (In-Memory) ---
# Stores temporary data for card selection before the game starts
LOBBY = {} 
# Stores active game data, including player cards and called numbers
ACTIVE_GAMES = {}

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

# --- Bingo Card Logic ---

def generate_card():
    # 'data': Actual numbers, 'marked': (c, r) -> True (player marked it), 'called': (c, r) -> True (bot called it)
    card_data = {
        'data': {
            'B': random.sample(range(1, 16), 5),
            'I': random.sample(range(16, 31), 5),
            'N': random.sample(range(31, 46), 5),
            'G': random.sample(range(46, 61), 5),
            'O': random.sample(range(61, 76), 5),
        },
        'marked': {(2, 2): True}, # Free space is always marked
        'called': {} 
    }
    card_data['called'][(2, 2)] = True # Free space is also considered 'called'
    return card_data

def get_card_value(card, col_idx, row_idx):
    if col_idx == 2 and row_idx == 2:
        return "FREE"
    return card['data'][COLUMNS[col_idx]][row_idx]

def get_card_position(card, value):
    # Reverse lookup: find (col_idx, row_idx) for a number value
    for c_idx, col_letter in enumerate(COLUMNS):
        if col_letter == 'N': # Handle N column separately for the free space
            for r_idx, v in enumerate(card['data'][col_letter]):
                if r_idx == 2: continue # Skip the free space location
                if v == value:
                    return c_idx, r_idx
        else:
            try:
                r_idx = card['data'][col_letter].index(value)
                return c_idx, r_idx
            except ValueError:
                continue
    return None, None

async def refresh_all_player_cards(context: ContextTypes.DEFAULT_TYPE, game_id, players):
    game_data = ACTIVE_GAMES[game_id]
    
    for pid in players:
        card = game_data['cards'][pid]
        msg_id = game_data['card_messages'][pid]
        
        # Rebuild and update the card with new states (called/marked)
        new_keyboard = build_card_keyboard(card, -1, game_id, msg_id, is_selection=False)
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=pid,
                message_id=msg_id,
                reply_markup=new_keyboard
            )
        except Exception as e:
            logger.error(f"Error refreshing card for {pid}: {e}")


def build_card_keyboard(card, card_index, game_id=None, msg_id=None, is_selection=True):
    keyboard = []
    header = [InlineKeyboardButton(col, callback_data=f"ignore_header") for col in COLUMNS]
    keyboard.append(header)
    
    for r in range(5):
        row = []
        for c in range(5):
            pos = (c, r)
            value = get_card_value(card, c, r)
            is_marked = card['marked'].get(pos, False)
            is_called = card['called'].get(pos, False)

            if value == "FREE":
                label = f"{EMOJI_FREE} {value}"
                callback_data = f"ignore_free"
            elif is_marked:
                label = f"{EMOJI_MARKED} {value}" # Already marked by player
                callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" # Allow unmarking
            elif is_called:
                label = f"{EMOJI_CALLED} {value}" # Called but not marked by player
                callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" # Allow marking
            else:
                label = f"{EMOJI_UNMARKED} {value}" # Not called yet
                callback_data = f"ignore_not_called" # Cannot be marked
            
            # If selecting a card, all buttons are ignored (except the select button)
            if is_selection:
                row.append(InlineKeyboardButton(str(value), callback_data=f"ignore_select_card_num"))
            else:
                row.append(InlineKeyboardButton(label, callback_data=callback_data))
                
        keyboard.append(row)
    
    if is_selection:
        # Selection button for the initial choice phase
        keyboard.append([InlineKeyboardButton(f"✅ Card {card_index+1}: ይሄንን ይምረጡ (Select This)", callback_data=f"SELECT|{card_index}")])
    else:
        # BINGO button for the active game phase
        keyboard.append([InlineKeyboardButton("🚨 CALL BINGO! 🚨", callback_data=f"BINGO|{game_id}|{msg_id}")])
    
    return InlineKeyboardMarkup(keyboard)

def check_win(card):
    # This check uses the 'marked' dictionary.
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
    # Cards are now loaded from ACTIVE_GAMES[game_id]['cards']
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
        
        # 1. Update all cards with the new 'called' number for the green highlight
        for pid in players:
            card = ACTIVE_GAMES[game_id]['cards'][pid]
            c, r = get_card_position(card, num)
            if c is not None and r is not None:
                card['called'][(c, r)] = True

        # Refresh all player cards to show the green highlight
        await refresh_all_player_cards(context, game_id, players)

        # 2. Send the number call message
        col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)
        msg = f"📣 ቁጥር (Number): **{col_letter}-{num}**\n\n_If you have this number, please tap the green button on your card now!_"
        
        for pid in players:
            try:
                await context.bot.send_message(pid, msg, parse_mode='Markdown')
            except Exception as e:
                 logger.error(f"Error sending number call to {pid}: {e}")
        
        await asyncio.sleep(8) 
    
    # Game ended without a winner (very unlikely with 75 numbers)
    if game_id in ACTIVE_GAMES:
        for pid in players:
            await context.bot.send_message(pid, "💔 ጨዋታው ተጠናቀቀ (Game Over). ሁሉም ቁጥሮች ተጠርተዋል።")
        del ACTIVE_GAMES[game_id]


# --- Handlers ---

async def instructions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "**📜 የመጫወቻ ህጎች (Game Rules) 📜**\n\n"
        "1. **ክፍያ (Cost):** እያንዳንዱ ጨዋታ ለመጫወት **10 Br** ያስከፍላል።\n"
        "2. **አሸናፊ (Winner):** 5 ተጫዋቾች ሲመዘገቡ ጨዋታው ይጀምራል (Testing: 1 ተጫዋች).\n"
        "3. **ሽልማት (Prize):** ያሸነፉ ተጫዋቾች **40 Br** ወዲያውኑ ወደ ሂሳባቸው ይገባል!\n\n"
        
        "**🕹️ እንዴት እንጫወታለን? (How to Play) 🕹️**\n"
        "1. **/play** ይጫኑ እና 10 Br ይከፍላሉ።\n"
        "2. **3 የተለያዩ ካርዶች** ቀርበውልዎታል፤ የመረጡትን **'Select This'** የሚለውን ይጫኑ።\n"
        "3. ቁጥሮች መጥራት ሲጀምሩ:\n"
        "   - **🟢 አረንጓዴ ቁጥር (Green Button):** ይህ ቁጥር ተጠርቷል ማለት ነው።\n"
        "   - **✅ ተጭነው ምልክት ያድርጉ (Tap to Mark):** ቁጥሩን በካርድዎ ላይ ምልክት ለማድረግ አረንጓዴውን ቁጥር ይጫኑ። ወደ **✅** ይቀየራል።\n"
        "4. **ቢንጎ (BINGO):** 5 ምልክት የተደረገባቸው ቁጥሮች (✅) በአንድ ቀጥተኛ መስመር (አግድም፣ ቁመታዊ፣ ወይም ዲያጎናል) ሲገጥሙ:\n"
        "   - **🚨 CALL BINGO! 🚨** የሚለውን ቁልፍ ይጫኑ።\n"
        
        "**እድለኛ ይሁኑ! (Good Luck!)**"
    )
    await update.message.reply_text(message, parse_mode='Markdown')

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    create_or_update_user(user_id, update.effective_user.username, update.effective_user.first_name)
    
    await update.message.reply_text(
        f"**👋 እንኳን ወደ አዲስ ቢንጎ በደህና መጡ!**\nSystem: {DB_STATUS}\n\n"
        f"ለመጫወት /play ይጫኑ (Cost: {GAME_COST} Birr).\n\n"
        f"**👉 እባክዎ ከመጀመርዎ በፊት ህጎችን ያንብቡ:**"
    , parse_mode='Markdown')
    
    # Display the full instructions immediately after the welcome message
    await instructions_command(update, context)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if user_id in LOBBY or any(user_id in g['players'] for g in ACTIVE_GAMES.values()):
        await update.message.reply_text("⏳ ተራ ይጠብቁ (Already waiting or in a game).")
        return

    update_balance(user_id, -GAME_COST)
    
    card_options = [generate_card() for i in range(3)]
    card_message_ids = []

    for i, card in enumerate(card_options):
        # Build initial selection keyboard without emojis/dynamic state
        keyboard = build_card_keyboard(card, i, is_selection=True)
        
        # Format the card numbers clearly for selection
        card_layout_text = ""
        for r in range(5):
            row_numbers = [str(get_card_value(card, c, r)) for c in range(5)]
            card_layout_text += " ".join(row_numbers) + "\n"
        
        message_text = (
            f"🃏 **Card Option {i+1}** 🃏\n\n"
            f"```\n{COLUMNS[0]}  {COLUMNS[1]}  {COLUMNS[2]}  {COLUMNS[3]}  {COLUMNS[4]}\n{card_layout_text}```\n"
            f"_ይህን ካርድ ከመምረጥዎ በፊት ቁጥሮቹን በጥንቃቄ ይመልከቱ።_"
        )
        
        msg = await context.bot.send_message(user_id, message_text, reply_markup=keyboard, parse_mode='Markdown')
        card_message_ids.append(msg.message_id)

    LOBBY[user_id] = {
        'cards': card_options,
        'message_ids': card_message_ids,
        'status': 'selecting_card'
    }
    
    await context.bot.send_message(user_id, f"✅ {GAME_COST} Br ተቀንሷል። (Deducted {GAME_COST} Br).\n\n**እባክዎ ከላይ ካሉት 3 ካርዶች አንዱን ይምረጡ።**")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data.split('|')
    action = data[0]

    # --- Card Selection Phase ---
    if action == 'SELECT':
        if user_id not in LOBBY or LOBBY[user_id]['status'] != 'selecting_card':
            await query.answer("Invalid card selection or session expired.")
            return

        card_index = int(data[1])
        lobby_data = LOBBY.pop(user_id) 
        selected_card = lobby_data['cards'][card_index]
        all_message_ids = lobby_data['message_ids']
        
        # 1. Clean up the other 2 card options
        for msg_id in all_message_ids:
            try:
                # Edit the selected card's message to become the FINAL game card
                if msg_id == query.message.message_id:
                    await context.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=f"✅ Card Selected! ጨዋታው ሊጀምር ነው! (Game starting...)\n\n_Tap the numbers on the card below to mark them._",
                        reply_markup=None 
                    )
                else:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
            except Exception as e:
                logger.error(f"Error cleaning up card messages: {e}")

        # 2. Start the Game Setup
        game_id = f"G{random.randint(1000,9999)}"
        
        # Send the final interactive card for the game
        final_keyboard = build_card_keyboard(selected_card, card_index, game_id, query.message.message_id, is_selection=False)

        final_msg = await context.bot.send_message(
            user_id, 
            "**🃏 የእርስዎ ቢንጎ ካርድ (Your Bingo Card) 🃏**\n\n_አረንጓዴ ቁጥር ሲመጣ ይጫኑ!_", 
            reply_markup=final_keyboard, 
            parse_mode='Markdown'
        )
        
        # Remove the previous confirmation message, if possible
        try: await context.bot.delete_message(chat_id=user_id, message_id=query.message.message_id)
        except: pass

        ACTIVE_GAMES[game_id] = {
            'players': [user_id], 
            'cards': {user_id: selected_card}, 
            'called': [], 
            'status': 'starting', 
            'card_messages': {user_id: final_msg.message_id}
        }
        
        # 3. Start the game loop (for testing, MIN_PLAYERS=1 makes it start immediately)
        asyncio.create_task(run_game_loop(context, game_id, [user_id]))
        await query.answer("Card selected! Get ready to play!")
        return

    # --- MARK and BINGO (Active Game Logic) ---
    
    # Check if the player is in the active game
    game_id = data[1] if len(data) > 1 else None
    msg_id = int(data[2]) if len(data) > 2 else None

    if game_id not in ACTIVE_GAMES or user_id not in ACTIVE_GAMES[game_id]['players']:
        await query.answer("This game has ended or you are not a participant.")
        return

    game_data = ACTIVE_GAMES[game_id]
    card = game_data['cards'][user_id]
    
    if action == 'MARK':
        # MARK|GameID|MsgID|ColIndex|RowIndex
        if len(data) < 5: 
            await query.answer("Invalid MARK data.")
            return
        c, r = int(data[3]), int(data[4])
        pos = (c, r)
        value = get_card_value(card, c, r)
        
        is_already_marked = card['marked'].get(pos, False)

        if not card['called'].get(pos, False) and value != 'FREE':
            await query.answer("That number has not been called yet (Wait for the Green)! ⛔")
            return

        # Toggle the marked state (if marked, unmark; if unmarked, mark)
        card['marked'][pos] = not is_already_marked
        
        # Re-render the card
        new_keyboard = build_card_keyboard(card, -1, game_id, msg_id, is_selection=False)
        
        try:
            await query.edit_message_reply_markup(reply_markup=new_keyboard)
            await query.answer(f"Number {value} {'Marked ✅' if card['marked'][pos] else 'Unmarked 🟢'}")
        except Exception as e:
            logger.error(f"Error editing message reply markup: {e}")
            await query.answer("Error updating card. Is the message too old?")

    elif action == 'BINGO':
        # BINGO|GameID|MsgID
        
        # 1. Check for Win
        if check_win(card):
            game_data['status'] = 'finished'
            update_balance(user_id, PRIZE_AMOUNT)
            
            winner_name = query.from_user.first_name
            win_msg = f"🎉 BINGO!!! 🎉\n\nአሸናፊ (Winner): **{winner_name}**\n**Prize: {PRIZE_AMOUNT} Br Added!**"
            
            # 2. Notify all players and remove game
            for pid in game_data['players']:
                await context.bot.send_message(pid, win_msg, parse_mode='Markdown')
            
            # 3. Final cleanup of the winner's card
            try:
                 await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=f"**🎉 WINNER! Game Over. 🎉**\nPrize: {PRIZE_AMOUNT} Br",
                    reply_markup=None,
                    parse_mode='Markdown'
                )
            except: pass
            
            del ACTIVE_GAMES[game_id]
            await query.answer("BINGO! You Win! 🎉")
        else:
            await query.answer("❌ ውሸት! (False Bingo). Keep playing. ❌")

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
    app.add_handler(CommandHandler("instructions", instructions_command))
    app.add_handler(CommandHandler("ap_dep", approve_deposit_admin))
    
    app.add_handler(CallbackQueryHandler(handle_callback))

    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')

if __name__ == '__main__':
    main()
