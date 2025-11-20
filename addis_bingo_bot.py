# Addis (አዲስ) Bingo - V9.1: Implements Compact UX Flow
# Optimizes the Called Numbers Board to use less vertical space, ensuring the Card and Board are visible together.

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
LOBBY = {} 
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
    card_data = {
        'data': {
            'B': random.sample(range(1, 16), 5),
            'I': random.sample(range(16, 31), 5),
            'N': random.sample(range(31, 46), 5),
            'G': random.sample(range(46, 61), 5),
            'O': random.sample(range(61, 76), 5),
        },
        'marked': {(2, 2): True}, 
        'called': {} 
    }
    card_data['called'][(2, 2)] = True
    return card_data

def get_card_value(card, col_idx, row_idx):
    if col_idx == 2 and row_idx == 2:
        return "FREE"
    return card['data'][COLUMNS[col_idx]][row_idx]

def get_card_position(card, value):
    for c_idx, col_letter in enumerate(COLUMNS):
        if col_letter == 'N':
            for r_idx, v in enumerate(card['data'][col_letter]):
                if r_idx == 2: continue
                if v == value:
                    return c_idx, r_idx
        else:
            try:
                r_idx = card['data'][col_letter].index(value)
                return c_idx, r_idx
            except ValueError:
                continue
    return None, None

def format_called_numbers_compact(called_numbers):
    # V9.1: Compact, row-based display
    if not called_numbers:
        return "--- ቁጥሮች ገና አልተጠሩም (No numbers called yet) ---"
    
    # Group numbers by column letter
    grouped = {col: [] for col in COLUMNS}
    for num in called_numbers:
        col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)
        grouped[col_letter].append(str(num).zfill(2)) # zfill(2) ensures '9' is '09'
        
    output = []
    for col in COLUMNS:
        if grouped[col]:
            output.append(f"**{col}**: {', '.join(grouped[col])}")
    
    return "\n".join(output)


async def refresh_all_player_cards(context: ContextTypes.DEFAULT_TYPE, game_id, players):
    game_data = ACTIVE_GAMES[game_id]
    
    for pid in players:
        card = game_data['cards'][pid]
        msg_id = game_data['card_messages'][pid]
        
        new_keyboard = build_card_keyboard(card, -1, game_id, msg_id, is_selection=False)
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=pid,
                message_id=msg_id,
                reply_markup=new_keyboard
            )
        except Exception as e:
            logger.debug(f"Error refreshing card for {pid}: {e}")

# Note: The card is slightly compacted for better mobile view
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
                label = f"{EMOJI_FREE}"
                callback_data = f"ignore_free"
            elif is_marked:
                label = f"{EMOJI_MARKED}{value}" 
                callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" 
            elif is_called:
                label = f"{EMOJI_CALLED}{value}" 
                callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" 
            else:
                label = f"{EMOJI_UNMARKED}{value}" 
                callback_data = f"ignore_not_called" 
            
            btn_label = str(value) if is_selection else label 

            if is_selection:
                row.append(InlineKeyboardButton(btn_label, callback_data=f"ignore_select_card_num"))
            else:
                row.append(InlineKeyboardButton(btn_label, callback_data=callback_data))
                
        keyboard.append(row)
    
    if is_selection:
        keyboard.append([InlineKeyboardButton(f"✅ Card {card_index+1}: ይሄንን ይምረጡ (Select This)", callback_data=f"SELECT|{card_index}")])
    else:
        keyboard.append([InlineKeyboardButton("🚨 CALL BINGO! 🚨", callback_data=f"BINGO|{game_id}|{msg_id}")])
    
    return InlineKeyboardMarkup(keyboard)

def check_win(card):
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
    called = []
    available_numbers = list(range(1, 76))
    random.shuffle(available_numbers)
    
    ACTIVE_GAMES[game_id]['status'] = 'running'
    game_data = ACTIVE_GAMES[game_id]
    
    # 1. Send the initial Called Numbers Board (for editing)
    board_message_ids = {}
    board_msg_text = "**🎰 የተጠሩ ቁጥሮች (Called Numbers Board) 🎰**\n\n_አረንጓዴው ቁጥር ሲመጣ በካርድዎ ላይ ይጫኑ!_"
    for pid in players:
        msg = await context.bot.send_message(pid, board_msg_text, parse_mode='Markdown')
        board_message_ids[pid] = msg.message_id
    game_data['board_messages'] = board_message_ids

    await asyncio.sleep(2)

    for num in available_numbers:
        if game_id not in ACTIVE_GAMES or ACTIVE_GAMES[game_id]['status'] != 'running':
            break

        called.append(num)
        game_data['called'] = called
        
        # 2. Update all cards with the new 'called' number for the green highlight
        for pid in players:
            card = game_data['cards'][pid]
            c, r = get_card_position(card, num)
            if c is not None and r is not None:
                card['called'][(c, r)] = True

        # Refresh all player cards to show the green highlight
        await refresh_all_player_cards(context, game_id, players)

        # 3. Update the single Calling Board message (V9.1 COMPACT LOGIC)
        col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)
        
        # Format the history board compactly
        history_board = format_called_numbers_compact(called[:-1]) 
        
        current_call = f"\n\n**📣 አሁን የተጠራ (CURRENT CALL): {col_letter}-{num}**"
        
        new_board_text = (
            f"**🎰 የተጠሩ ቁጥሮች (Called Numbers Board) 🎰**\n"
            f"{history_board}"
            f"{current_call}"
        )
        
        for pid in players:
            try:
                await context.bot.edit_message_text(
                    chat_id=pid,
                    message_id=game_data['board_messages'][pid],
                    text=new_board_text, 
                    parse_mode='Markdown'
                )
            except Exception as e:
                 logger.debug(f"Error editing board message for {pid}: {e}")
        
        await asyncio.sleep(8) 
    
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
        "3. **የተጠሩ ቁጥሮች ቦርድ (Called Numbers Board):**\n"
        "   - ይህ ቦርድ ከካርድዎ በላይ ይታያል እና ያለማቋረጥ ይዘመናል።\n"
        "   - **🟢 አረንጓዴ ቁጥር (Green Button):** ይህ ቁጥር ተጠርቷል ማለት ነው።\n"
        "   - **✅ ተጭነው ምልክት ያድርጉ (Tap to Mark):** ቁጥሩን በካርድዎ ላይ ምልክት ለማድረግ አረንጓዴውን ቁጥር ይጫኑ። ወደ **✅** ይቀየራል።\n"
        "4. **ቢንጎ (BINGO):** 5 ምልክት የተደረገባቸው ቁጥሮች (✅) በአንድ ቀጥተኛ መስመር (አግድም፣ ቁመታዊ፣ ወይም ዲያጎናል) ሲገጥሙ:\n"
        "   - **🚨 CALL BINGO! 🚨** የሚለውን ቁልፍ ይጫኑ።\n"
        
        "**እድለኛ ይሁኑ! (Good Luck!)**"
    )
    if update.message:
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        return message

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    create_or_update_user(user_id, update.effective_user.username, update.effective_user.first_name)
    
    await update.message.reply_text(
        f"**👋 እንኳን ወደ አዲስ ቢንጎ በደህና መጡ!**\n\n"
        f"ለመጫወት /play ይጫኑ (Cost: {GAME_COST} Birr).\n\n"
        f"**👉 እባክዎ ከመጀመርዎ በፊት ህጎችን ያንብቡ:**"
    , parse_mode='Markdown')
    
    instructions = await instructions_command(update, context) 
    if instructions:
        await update.message.reply_text(instructions, parse_mode='Markdown')


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

    await update.message.reply_text(f"✅ {GAME_COST} Br ተቀንሷል። (Deducted {GAME_COST} Br).\n\n**እባክዎ ከታች ካሉት 3 ካርዶች አንዱን ይምረጡ።**")

    for i, card in enumerate(card_options):
        keyboard = build_card_keyboard(card, i, is_selection=True)
        
        # V9.1: Compact display of card numbers for selection
        card_layout_text = f"**{COLUMNS[0]}** **{COLUMNS[1]}** **{COLUMNS[2]}** **{COLUMNS[3]}** **{COLUMNS[4]}**\n"
        for r in range(5):
            row_numbers = [str(get_card_value(card, c, r)).center(3) for c in range(5)]
            card_layout_text += " ".join(row_numbers) + "\n"
        
        message_text = (
            f"🃏 **Card Option {i+1}** 🃏\n"
            f"```\n{card_layout_text}```\n"
            f"_ይህን ካርድ ከመምረጥዎ በፊት ቁጥሮቹን በጥንቃቄ ይመልከቱ።_"
        )
        
        msg = await context.bot.send_message(user_id, message_text, reply_markup=keyboard, parse_mode='Markdown')
        card_message_ids.append(msg.message_id)

    LOBBY[user_id] = {
        'cards': card_options,
        'message_ids': card_message_ids,
        'status': 'selecting_card'
    }

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
        
        for msg_id in all_message_ids:
            try:
                if msg_id != query.message.message_id:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                else:
                    await context.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=msg_id,
                        text=f"✅ Card Selected! ጨዋታው ሊጀምር ነው! (Game starting...)\n\n_Tap the numbers on the card below to mark them._",
                        reply_markup=None 
                    )
            except Exception as e:
                logger.debug(f"Error cleaning up card messages: {e}")

        game_id = f"G{random.randint(1000,9999)}"
        
        final_keyboard = build_card_keyboard(selected_card, card_index, game_id, query.message.message_id, is_selection=False)

        final_msg = await context.bot.send_message(
            user_id, 
            "**🃏 የእርስዎ ቢንጎ ካርድ (Your Bingo Card) 🃏**\n\n_አረንጓዴ ቁጥር ሲመጣ ይጫኑ!_", 
            reply_markup=final_keyboard, 
            parse_mode='Markdown'
        )
        
        ACTIVE_GAMES[game_id] = {
            'players': [user_id], 
            'cards': {user_id: selected_card}, 
            'called': [], 
            'status': 'starting', 
            'card_messages': {user_id: final_msg.message_id},
            'board_messages': {} 
        }
        
        asyncio.create_task(run_game_loop(context, game_id, [user_id]))
        await query.answer("Card selected! Get ready to play!")
        return

    # --- MARK and BINGO (Active Game Logic) ---
    
    game_id = data[1] if len(data) > 1 else None
    msg_id = int(data[2]) if len(data) > 2 else None

    if game_id not in ACTIVE_GAMES or user_id not in ACTIVE_GAMES[game_id]['players']:
        await query.answer("This game has ended or you are not a participant.")
        return

    game_data = ACTIVE_GAMES[game_id]
    card = game_data['cards'][user_id]
    
    if action == 'MARK':
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

        card['marked'][pos] = not is_already_marked
        
        new_keyboard = build_card_keyboard(card, -1, game_id, msg_id, is_selection=False)
        
        try:
            await query.edit_message_reply_markup(reply_markup=new_keyboard)
            await query.answer(f"Number {value} {'Marked ✅' if card['marked'][pos] else 'Unmarked 🟢'}")
        except Exception as e:
            logger.error(f"Error editing message reply markup: {e}")
            await query.answer("Error updating card. Is the message too old?")

    elif action == 'BINGO':
        
        if check_win(card):
            game_data['status'] = 'finished'
            update_balance(user_id, PRIZE_AMOUNT)
            
            winner_name = query.from_user.first_name
            win_msg = f"🎉 BINGO!!! 🎉\n\nአሸናፊ (Winner): **{winner_name}**\n**Prize: {PRIZE_AMOUNT} Br Added!**"
            
            for pid in game_data['players']:
                try:
                    await context.bot.edit_message_text(
                        chat_id=pid,
                        message_id=game_data['board_messages'][pid],
                        text=f"**🎉 WINNER: {winner_name} 🎉**\n\n**The Game has ended!**",
                        reply_markup=None,
                        parse_mode='Markdown'
                    )
                except: pass

                await context.bot.send_message(pid, win_msg, parse_mode='Markdown')
            
            try:
                 await query.edit_message_text(
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
