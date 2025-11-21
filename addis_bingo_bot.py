# Addis (አዲስ) Bingo - V11.0: 200 Unique Cards, Choose 3
# Changes: Increased card pool to 200 fixed, unique arrangements. Players choose 1 from 3 randomly selected cards.

import os
import logging
import json
import base64
import asyncio
import random
import time
import hashlib # For creating a consistent seed
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler
)
import firebase_admin
from firebase_admin import credentials, firestore

# --- Configuration & Secrets ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')
V2_SECRETS = os.environ.get('V2_SECRETS')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') 
API_KEY = "" # API Key for Gemini is handled by the runtime environment

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Constants ---
GAME_COST = 20       
PRIZE_AMOUNT = 40 
MIN_REAL_PLAYERS = 5 
CALL_DELAY = 2.40    
COLUMNS = ['B', 'I', 'N', 'G', 'O']
TOTAL_CARD_POOL = 200 # New total number of unique, fixed cards
CARDS_TO_CHOOSE = 3   # Number of cards offered to the player per /play

# --- Referral Constant ---
REFERRAL_REWARD = 10.0 

# --- Emojis and Aesthetics ---
EMOJI_UNMARKED = '⚫' 
EMOJI_CALLED = '🟢'   
EMOJI_MARKED = '✅'   
EMOJI_FREE = '🌟'     

# --- Global Game State (In-Memory) ---
LOBBY = {} 
ACTIVE_GAMES = {}
BOT_WINNER_ID = -999999999 

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

# --- Database Helpers (Unchanged) ---
USERS_COLLECTION = 'addis_bingo_users'

def get_user_data(user_id: int) -> dict:
    if not db: return {'balance': 0}
    doc = db.collection(USERS_COLLECTION).document(str(user_id)).get()
    if doc.exists:
        return doc.to_dict()
    return {'balance': 0, 'new_user': True}

def create_or_update_user(user_id: int, username: str, first_name: str, referred_by: int = None):
    if not db: return
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    
    doc = doc_ref.get()
    if doc.exists:
        doc_ref.update({
            'username': username,
            'first_name': first_name,
        })
    else:
        initial_data = {
            'username': username,
            'first_name': first_name,
            'balance': 0.0,
            'created_at': firestore.SERVER_TIMESTAMP,
            'referred_by': referred_by,
            'referral_paid_status': 'PENDING' if referred_by else 'N/A'
        }
        doc_ref.set(initial_data)

def update_balance(user_id: int, amount: float):
    if not db: return
    if user_id < 0: return 
    db.collection(USERS_COLLECTION).document(str(user_id)).update({
        'balance': firestore.Increment(amount)
    })

async def pay_referral_reward(context: ContextTypes.DEFAULT_TYPE, referred_id: int, referrer_id: int):
    if not db: return
    referred_doc_ref = db.collection(USERS_COLLECTION).document(str(referred_id))
    try:
        @firestore.transactional
        def transaction_update(transaction):
            snapshot = referred_doc_ref.get(transaction=transaction)
            current_status = snapshot.get('referral_paid_status')
            
            if current_status == 'PENDING':
                referrer_doc_ref = db.collection(USERS_COLLECTION).document(str(referrer_id))
                transaction.update(referrer_doc_ref, {'balance': firestore.Increment(REFERRAL_REWARD)})
                transaction.update(referred_doc_ref, {'referral_paid_status': 'PAID'})
                return True
            return False

        if transaction_update(db.transaction()):
            await context.bot.send_message(
                referrer_id, 
                f"🎉 **Referral Bonus!** 🎉\n\n**+{REFERRAL_REWARD} Br** has been added to your balance because your friend played their first game!",
                parse_mode='Markdown'
            )
            await context.bot.send_message(
                referred_id, 
                f"🤝 Welcome Bonus Confirmation: Your referrer has received a bonus for your first game. Thanks for playing!",
                parse_mode='Markdown'
            )

    except Exception as e:
        logger.error(f"Error processing referral payment: {e}")

# --- Fixed Bingo Cards Generation (200 unique cards) ---
# We use a deterministic approach (seed) to ensure the 200 cards are always the same.

CARD_GENERATION_SEED = hashlib.sha256("AddisBingo_200UniqueCards".encode('utf-8')).hexdigest()

def generate_unique_bingo_cards(count=TOTAL_CARD_POOL):
    random.seed(CARD_GENERATION_SEED)
    unique_cards = {}
    card_set = set() 

    def create_card_data():
        # B: 1-15, I: 16-30, N: 31-45 (4 numbers), G: 46-60, O: 61-75
        data = {
            'B': tuple(sorted(random.sample(range(1, 16), 5))),
            'I': tuple(sorted(random.sample(range(16, 31), 5))),
            'N': tuple(sorted(random.sample(range(31, 46), 4))), # 4 numbers, one free space
            'G': tuple(sorted(random.sample(range(46, 61), 5))),
            'O': tuple(sorted(random.sample(range(61, 76), 5))),
        }
        # Create a unique, sortable tuple representation of the card data (excluding FREE)
        card_tuple = (data['B'], data['I'], data['N'], data['G'], data['O'])
        return data, card_tuple

    # Generate the required number of unique cards
    for i in range(1, count + 1):
        # Safety limit to prevent infinite loops if the card space was small (it is huge here)
        attempts = 0 
        while attempts < 100: 
            card_data_dict, card_data_tuple = create_card_data()
            if card_data_tuple not in card_set:
                card_set.add(card_data_tuple)
                # Store the fixed arrangement for the card ID
                unique_cards[i] = {
                    'B': list(card_data_dict['B']), 
                    'I': list(card_data_dict['I']), 
                    'N': list(card_data_dict['N']) + ['FREE'], # Add FREE back for consistency
                    'G': list(card_data_dict['G']), 
                    'O': list(card_data_dict['O'])
                }
                break
            attempts += 1
        else:
            logger.error(f"Could not generate unique card {i} after 100 attempts.")
        
    random.seed() # Reset seed for general use
    return unique_cards

# Initialize the 200 fixed, unique bingo cards
FIXED_BINGO_CARDS = generate_unique_bingo_cards(TOTAL_CARD_POOL)
logger.info(f"Generated {len(FIXED_BINGO_CARDS)} fixed, unique Bingo cards.")


def generate_card(card_id: int):
    # Retrieve the fixed card data based on the ID
    fixed_data = FIXED_BINGO_CARDS.get(card_id)
    if not fixed_data:
        # Fallback to a generic card if ID is out of the 1-200 range
        return generate_random_card_internal() 

    # Convert fixed data into the game state format
    card_data = {
        'data': {
            'B': fixed_data['B'], 'I': fixed_data['I'], 
            'N': [n for n in fixed_data['N'] if n != 'FREE'], 
            'G': fixed_data['G'], 'O': fixed_data['O']
        },
        'marked': {(2, 2): True}, # Free space is always marked
        'called': {(2, 2): True}, # Free space is always considered called
        'card_id': card_id
    }
    return card_data

# Internal random generator (only used for fallback/bot generation)
def generate_random_card_internal():
    card_data = {
        'data': {
            'B': random.sample(range(1, 16), 5),
            'I': random.sample(range(16, 31), 5),
            'N': random.sample(range(31, 46), 5),
            'G': random.sample(range(46, 61), 5),
            'O': random.sample(range(61, 76), 5),
        },
        'marked': {(2, 2): True}, 
        'called': {(2, 2): True},
        'card_id': -1
    }
    return card_data

# --- Utility Functions (Mostly Unchanged) ---

def get_card_value(card, col_idx, row_idx):
    if col_idx == 2 and row_idx == 2:
        return "FREE"
    return card['data'][COLUMNS[col_idx]][row_idx]

def get_card_position(card, value):
    for c_idx, col_letter in enumerate(COLUMNS):
        # Handle the FREE space index offset for 'N' column
        if col_letter == 'N':
            for r_idx, v in enumerate(card['data'][col_letter]):
                if v == value:
                    # If it's the N column, the index 2 is FREE, so adjust index
                    return c_idx, r_idx if r_idx < 2 else r_idx + 1
            # Check for the FREE space itself
            if value == 'FREE':
                return 2, 2
        else:
            try:
                r_idx = card['data'][col_letter].index(value)
                return c_idx, r_idx
            except ValueError:
                continue
    return None, None

def format_called_numbers_compact(called_numbers):
    if not called_numbers:
        return "--- ቁጥሮች ገና አልተጠሩም (No numbers called yet) ---"
    
    grouped = {col: [] for col in COLUMNS}
    for num in called_numbers:
        col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)
        grouped[col_letter].append(str(num).zfill(2))
        
    output = []
    for col in COLUMNS:
        if grouped[col]:
            output.append(f"**{col}**: {', '.join(grouped[col])}")
    
    return "\n".join(output)

def get_current_call_text(num):
    if num is None:
        return "**📣 በመጠባበቅ ላይ... (Awaiting first call)**"
    col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)
    
    return (
        f"**\n\n📢 አሁን የተጠራ (CURRENT CALL):**\n"
        f"======================\n"
        f"**#️⃣ 👑 {col_letter} - {num} 👑**\n"
        f"======================\n\n"
    )

async def refresh_all_player_cards(context: ContextTypes.DEFAULT_TYPE, game_id, players, current_call_num=None):
    game_data = ACTIVE_GAMES[game_id]
    
    current_call_text = get_current_call_text(current_call_num)
    
    for pid in players:
        if pid < 0: continue
        
        card = game_data['cards'][pid]
        msg_id = game_data['card_messages'][pid]
        
        new_keyboard = build_card_keyboard(card, -1, game_id, msg_id, is_selection=False)
        
        new_card_text = (
            f"{current_call_text}" 
            f"**🃏 የእርስዎ ቢንጎ ካርድ (Your Bingo Card) 🃏**\n"
            f"_🟢 አረንጓዴ ቁጥር ሲመጣ ይጫኑ! (Numbers are White)_"
        )
        
        try:
            await context.bot.edit_message_text(
                chat_id=pid,
                message_id=msg_id,
                text=new_card_text,
                reply_markup=new_keyboard,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.debug(f"Error refreshing card for {pid}: {e}")

def build_card_keyboard(card, card_index, game_id=None, msg_id=None, is_selection=True):
    keyboard = []
    
    header = [InlineKeyboardButton(f"⚪ {col} ⚪", callback_data=f"ignore_header") for col in COLUMNS]
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
                label = f"{EMOJI_MARKED} {value}" 
                callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" 
            elif is_called:
                label = f"{EMOJI_CALLED} {value}" 
                callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" 
            else:
                label = f"{EMOJI_UNMARKED} {value}" 
                callback_data = f"ignore_not_called" 
            
            if is_selection:
                row.append(InlineKeyboardButton(str(value).center(3), callback_data=f"ignore_select_card_num"))
            else:
                row.append(InlineKeyboardButton(label, callback_data=callback_data))
                
        keyboard.append(row)
    
    if is_selection:
        # For selection phase, show the card number and select button
        keyboard.append([InlineKeyboardButton(f"✅ Card {card_index}: ይሄንን ይምረጡ (Select This)", callback_data=f"SELECT|{card_index}")])
    else:
        keyboard.append([InlineKeyboardButton("🚨 CALL BINGO! 🚨", callback_data=f"BINGO|{game_id}|{msg_id}")])
    
    return InlineKeyboardMarkup(keyboard)

def check_win(card):
    def is_marked(c, r):
        return card['marked'].get((c, r), False)

    for r in range(5):
        if all(is_marked(c, r) for c in range(5)): return True

    for c in range(5):
        if all(is_marked(c, r) for r in range(5)): return True

    if all(is_marked(i, i) for i in range(5)): return True
    if all(is_marked(i, 4 - i) for i in range(5)): return True
    
    return False

# --- TTS Logic (Gemini API) ---
async def text_to_speech_call(col_letter: str, number: int):
    """Generates audio for the call: English letter + Amharic number, and returns the audio URL."""
    # (TTS implementation remains the same, requires 'requests' module)
    import requests 
    prompt = (
        f"Say the letter {col_letter} in a clear English voice, and immediately follow it by saying the number {number} in Amharic (Ethiopian language)."
    )
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": "Kore"} # Clear Voice
                }
            }
        },
        "model": "gemini-2.5-flash-preview-tts"
    }

    apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={API_KEY}"
    
    for attempt in range(4):
        try:
            response = await asyncio.to_thread(
                lambda: requests.post(apiUrl, headers={'Content-Type': 'application/json'}, data=json.dumps(payload), timeout=10)
            )
            response.raise_for_status()
            result = response.json()
            
            part = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0]
            audio_data = part.get('inlineData', {}).get('data')
            mime_type_full = part.get('inlineData', {}).get('mimeType')

            if audio_data and mime_type_full:
                return audio_data, mime_type_full

            logger.error("TTS API did not return audio data.")
            break 

        except requests.exceptions.RequestException as e:
            if attempt < 3:
                await asyncio.sleep(2 ** attempt) 
            else:
                logger.error(f"TTS API failed after multiple retries: {e}")
                break
        except Exception as e:
            logger.error(f"TTS API general error: {e}")
            break
            
    return None, None

# --- Computer Player Logic (Unchanged) ---

def add_computer_players(real_players: list) -> tuple:
    """Adds bots based on the number of real players, ensuring 100% bot win chance if < MIN_REAL_PLAYERS."""
    
    real_count = len(real_players)
    bots_to_add = 0
    bot_players = []

    if real_count >= MIN_REAL_PLAYERS:
        return real_players, [] 
    
    if real_count == 1:
        bots_to_add = random.randint(7, 8) 
    elif real_count in (2, 3):
        bots_to_add = random.randint(10, 12)
    elif real_count == 4:
        bots_to_add = random.randint(10, 20)
    
    for i in range(bots_to_add):
        bot_players.append(BOT_WINNER_ID - i - 1) 
    
    if BOT_WINNER_ID not in bot_players:
         bot_players.append(BOT_WINNER_ID)
         
    return real_players + bot_players, bot_players

def generate_winning_sequence(game_data):
    """
    Creates a card and prioritizes the winning numbers for the BOT_WINNER_ID.
    Returns: a modified list of available numbers, and the winning bot's card.
    """
    
    # 1. Generate a standard random card for the winning bot
    bot_card = generate_random_card_internal()
    
    # 2. Select a winning line (e.g., the first row)
    winning_numbers = [get_card_value(bot_card, c, 0) for c in range(5)]

    # 3. Create a list of all numbers 1-75, removing the winning numbers
    all_numbers = list(range(1, 76))
    for num in winning_numbers:
        if isinstance(num, int) and num in all_numbers:
            all_numbers.remove(num)
            
    # 4. Shuffle the remaining numbers
    random.shuffle(all_numbers)
    
    # 5. Insert the winning numbers strategically for a quick win
    final_win_num = winning_numbers.pop(random.randrange(len(winning_numbers)))
    
    available_numbers = winning_numbers + all_numbers[:10] + [final_win_num] + all_numbers[10:]
    
    for num in winning_numbers:
        c, r = get_card_position(bot_card, num)
        if c is not None:
            bot_card['marked'][(c, r)] = True

    game_data['winning_num'] = final_win_num
    game_data['winning_card'] = bot_card
    game_data['winner_id'] = BOT_WINNER_ID

    return available_numbers

# --- Game Loop (Unchanged) ---
async def run_game_loop(context: ContextTypes.DEFAULT_TYPE, game_id, real_players):
    import requests # Ensure requests is available in this async function

    all_players, bot_players = add_computer_players(real_players)
    is_bot_game = len(bot_players) > 0
    
    game_data = ACTIVE_GAMES[game_id]
    
    if is_bot_game:
        available_numbers = generate_winning_sequence(game_data)
        game_data['cards'][BOT_WINNER_ID] = game_data['winning_card']
        
        for bot_id in [b for b in bot_players if b != BOT_WINNER_ID]:
            game_data['cards'][bot_id] = generate_random_card_internal()

        game_data['players'] = all_players
        
        await context.bot.send_message(
            real_players[0], 
            f"🤖 **የኮምፒውተር ተጫዋቾች (Ghost Players)** 👻\n\nበቂ ተጫዋች እስኪመጣ ድረስ **{len(bot_players)}** የኮምፒውተር ተጫዋቾች (Players) ጨዋታውን ተቀላቅለዋል።",
            parse_mode='Markdown'
        )
    else:
        game_data['players'] = real_players
        available_numbers = list(range(1, 76))
        random.shuffle(available_numbers)
        game_data['winning_num'] = None
        game_data['winner_id'] = None
        
        await context.bot.send_message(
            real_players[0], 
            f"✅ **ሙሉ ተጫዋቾች (Full House)**\n\n{MIN_REAL_PLAYERS} ተጫዋቾች ተሟልተዋል። ምንም የኮምፒውተር ተጫዋቾች አይሳተፉም።",
            parse_mode='Markdown'
        )

    ACTIVE_GAMES[game_id]['status'] = 'running'
    
    board_message_ids = {}
    board_msg_text = "**🎰 የተጠሩ ቁጥሮች ታሪክ (Called Numbers History) 🎰**\n\n_ይህ የጥሪ ታሪክ ነው (This is the call history log)._"
    for pid in real_players: 
        msg = await context.bot.send_message(pid, board_msg_text, parse_mode='Markdown')
        board_message_ids[pid] = msg.message_id
    game_data['board_messages'] = board_message_ids

    await refresh_all_player_cards(context, game_id, real_players, current_call_num=None)

    await asyncio.sleep(2)

    for num in available_numbers:
        if game_id not in ACTIVE_GAMES or ACTIVE_GAMES[game_id]['status'] != 'running':
            break

        game_data['called'].append(num)
        col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)

        if is_bot_game and num == game_data['winning_num']:
            await asyncio.sleep(1.0) 
            await finalize_win(context, game_id, game_data['winner_id'])
            return 

        for pid in game_data['players']:
            card = game_data['cards'][pid]
            c, r = get_card_position(card, num)
            if c is not None and r is not None:
                card['called'][(c, r)] = True

        audio_data_b64, mime_type = await text_to_speech_call(col_letter, num)
        if audio_data_b64:
            audio_bytes = base64.b64decode(audio_data_b64)
            for pid in real_players:
                try:
                    await context.bot.send_voice(chat_id=pid, voice=audio_bytes, caption=f"**{col_letter} - {num}**", parse_mode='Markdown')
                except Exception as e:
                    logger.warning(f"Failed to send TTS audio to {pid}: {e}")
        else:
             for pid in real_players:
                await context.bot.send_message(pid, f"**📣 👑 {col_letter} - {num} 👑**", parse_mode='Markdown')

        await refresh_all_player_cards(context, game_id, real_players, current_call_num=num)

        history_board = format_called_numbers_compact(game_data['called']) 
        new_board_text = f"**🎰 የተጠሩ ቁጥሮች ታሪክ (Called Numbers History) 🎰**\n{history_board}"
        
        for pid in real_players:
            try:
                await context.bot.edit_message_text(
                    chat_id=pid,
                    message_id=game_data['board_messages'][pid],
                    text=new_board_text, 
                    parse_mode='Markdown'
                )
            except Exception as e:
                 logger.debug(f"Error editing board message for {pid}: {e}")
        
        await asyncio.sleep(CALL_DELAY) 
    
    if game_id in ACTIVE_GAMES:
        for pid in real_players:
            await context.bot.send_message(pid, "💔 ጨዋታው ተጠናቀቀ (Game Over). ሁሉም ቁጥሮች ተጠርተዋል።")
        del ACTIVE_GAMES[game_id]

async def finalize_win(context: ContextTypes.DEFAULT_TYPE, game_id: str, winner_id: int):
    if game_id not in ACTIVE_GAMES: return
    game_data = ACTIVE_GAMES[game_id]
    
    if winner_id < 0:
        bot_names = ["Lij Yonas", "Kalkidan", "Firaol", "Aisha", "Dawit"]
        winner_name = f"{random.choice(bot_names)} (ID: {abs(winner_id) % 1000})"
    else:
        data = get_user_data(winner_id)
        winner_name = data.get('first_name', f"Player {winner_id}")
        update_balance(winner_id, PRIZE_AMOUNT) 

    game_data['status'] = 'finished'
    win_msg = f"🎉 BINGO!!! 🎉\n\nአሸናፊ (Winner): **{winner_name}**\n"
    
    if winner_id > 0:
         win_msg += f"**Prize: {PRIZE_AMOUNT} Br Added!**"
    else:
         win_msg += f"_The game was won by another player._"
    
    real_players = [pid for pid in game_data['players'] if pid > 0]
    
    for pid in real_players:
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
            msg_id = game_data['card_messages'][pid]
            await context.bot.edit_message_reply_markup(
                chat_id=pid,
                message_id=msg_id,
                reply_markup=None
            )
        except: pass
    
    del ACTIVE_GAMES[game_id]


# --- Handlers ---

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    data = get_user_data(user_id)
    if data.get('balance', 0) < GAME_COST:
        await update.message.reply_text(f"⛔ በቂ ሂሳብ የለዎትም (Not enough balance).\nያስፈልጋል: {GAME_COST} Br\nአለዎት: {data.get('balance', 0)} Br")
        return

    if user_id in LOBBY or any(user_id in g['players'] for g in ACTIVE_GAMES.values()):
        await update.message.reply_text("⏳ ተራ ይጠብቁ (Already waiting or in a game).")
        return

    referred_by = data.get('referred_by')
    referral_status = data.get('referral_paid_status', 'N/A')
    
    if referred_by and referral_status == 'PENDING':
        await pay_referral_reward(context, user_id, referred_by)
        
    update_balance(user_id, -GAME_COST)
    
    # Select 3 random, unique card IDs from the pool of 200
    available_card_ids = random.sample(list(FIXED_BINGO_CARDS.keys()), CARDS_TO_CHOOSE) 
    available_card_ids.sort() # Sort for presentation

    card_options = {id: generate_card(id) for id in available_card_ids}
    card_message_ids = []

    await update.message.reply_text(f"✅ **{GAME_COST} Br ተቀንሷል። (Deducted {GAME_COST} Br).**\n\n**እባክዎ ከ{TOTAL_CARD_POOL} ካርዶች ውስጥ የተመረጡትን {CARDS_TO_CHOOSE} ካርዶች ይመልከቱ እና የሚፈልጉትን ቁጥር ይምረጡ።**")

    for card_id in available_card_ids:
        card = card_options[card_id]
        
        # Build the preview text using the fixed ID and numbers
        card_layout_text = f"**B** **I** **N** **G** **O**\n"
        
        # Get the underlying fixed data directly for display consistency
        fixed_data = FIXED_BINGO_CARDS.get(card_id)
        if fixed_data:
            col_data = {
                 'B': fixed_data['B'],
                 'I': fixed_data['I'],
                 'N': fixed_data['N'], # Includes 'FREE'
                 'G': fixed_data['G'],
                 'O': fixed_data['O'],
            }
            
            for r in range(5):
                row_numbers = []
                for col in COLUMNS:
                    val = col_data[col][r]
                    if val == 'FREE':
                        row_numbers.append(str('FREE').center(3))
                    else:
                        row_numbers.append(str(val).center(3))
                card_layout_text += " ".join(row_numbers) + "\n"
        
        message_text = (
            f"🃏 **Card Number {card_id}** 🃏\n"
            f"```\n{card_layout_text}```\n"
            f"_ይህን ካርድ ከመምረጥዎ በፊት ቁጥሮቹን በጥንቃቄ ይመልከቱ።_"
        )
        
        keyboard = build_card_keyboard(card, card_id, is_selection=True)

        msg = await context.bot.send_message(user_id, message_text, reply_markup=keyboard, parse_mode='Markdown')
        card_message_ids.append(msg.message_id)

    LOBBY[user_id] = {
        'cards': card_options,
        'message_ids': card_message_ids,
        'selected_ids': available_card_ids, 
        'status': 'selecting_card'
    }


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data.split('|')
    action = data[0]

    try:
        await query.answer()
    except Exception as e:
        logger.error(f"Failed to ACK query answer: {e}")

    game_id = None
    msg_id = None
    
    try:
        if len(data) > 1: game_id = data[1]
        if len(data) > 2 and data[2].isdigit(): msg_id = int(data[2])
    except Exception as e:
        logger.error(f"Error extracting game/msg ID: {e}")

    if action == 'SELECT':
        if user_id not in LOBBY or LOBBY[user_id]['status'] != 'selecting_card':
            await query.answer("Invalid card selection or session expired.")
            return

        card_id = int(data[1])
        lobby_data = LOBBY.pop(user_id) 
        selected_card = lobby_data['cards'][card_id]
        all_message_ids = lobby_data['message_ids']
        
        # Delete all 3 card messages
        for mid in all_message_ids:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=mid)
            except Exception as e:
                logger.debug(f"Error cleaning up card messages: {e}")

        game_id = f"G{int(time.time() * 1000)}"
        
        initial_card_text = get_current_call_text(None) + "\n\n**🃏 የእርስዎ ቢንጎ ካርድ (Your Bingo Card) 🃏**\n_🟢 አረንጓዴ ቁጥር ሲመጣ ይጫኑ! (Numbers are White)_"
        
        final_keyboard = build_card_keyboard(selected_card, card_id, game_id, 0, is_selection=False) # 0 is placeholder msg_id

        final_msg = await context.bot.send_message(
            user_id, 
            initial_card_text, 
            reply_markup=final_keyboard, 
            parse_mode='Markdown'
        )
        
        # Update the callback data with the correct message ID
        final_keyboard_updated = build_card_keyboard(selected_card, card_id, game_id, final_msg.message_id, is_selection=False)
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=user_id,
                message_id=final_msg.message_id,
                reply_markup=final_keyboard_updated
            )
        except Exception as e:
            logger.error(f"Error updating message reply markup after selection: {e}")


        pending_players = [pid for pid in ACTIVE_GAMES.get('PENDING', {}).get('players', [])] + [user_id]
        
        ACTIVE_GAMES['PENDING'] = {
             'players': pending_players,
             'cards': {
                **ACTIVE_GAMES.get('PENDING', {}).get('cards', {}),
                user_id: selected_card
             },
             'card_messages': {
                **ACTIVE_GAMES.get('PENDING', {}).get('card_messages', {}),
                user_id: final_msg.message_id
             }
        }
        
        
        if len(pending_players) >= MIN_REAL_PLAYERS:
            game_data_to_start = ACTIVE_GAMES.pop('PENDING')
            ACTIVE_GAMES[game_id] = game_data_to_start
            ACTIVE_GAMES[game_id]['called'] = []
            asyncio.create_task(run_game_loop(context, game_id, pending_players))
            
        elif len(pending_players) == 1:
            await context.bot.send_message(user_id, "⏳ **ተራ ይጠብቁ (Awaiting players)...**\n\nሌሎች ተጫዋቾችን እየጠበቅን ነው። በቂ ተጫዋች ካልተገኘ **በ10 ሰከንዶች** ውስጥ የኮምፒውተር ተጫዋቾች ተቀላቅለው ጨዋታው ይጀመራል!")
            await asyncio.sleep(10) 
            
            if game_id not in ACTIVE_GAMES and 'PENDING' in ACTIVE_GAMES and len(ACTIVE_GAMES['PENDING']['players']) > 0:
                game_data_to_start = ACTIVE_GAMES.pop('PENDING')
                real_players_now = game_data_to_start['players']
                
                ACTIVE_GAMES[game_id] = game_data_to_start
                ACTIVE_GAMES[game_id]['called'] = []
                
                asyncio.create_task(run_game_loop(context, game_id, real_players_now))
                
        else:
            await context.bot.send_message(user_id, f"✅ **{len(pending_players)}/{MIN_REAL_PLAYERS} ተጫዋቾች ተመዝግበዋል!**\n\nሌሎች ተጫዋቾች ሲመዘገቡ ወዲያውኑ ጨዋታው ይጀምራል።")

        return

    if action in ('MARK', 'BINGO'):
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
            
            is_already_marked = card['marked'].get(pos, False)

            if not card['called'].get(pos, False) and get_card_value(card, c, r) != 'FREE':
                await query.answer("That number has not been called yet (Wait for the Green)! ⛔")
                return

            card['marked'][pos] = not is_already_marked 
            
            current_call_num = game_data['called'][-1] if game_data['called'] else None
            current_call_text = get_current_call_text(current_call_num)
            
            new_card_text = (
                f"{current_call_text}" 
                f"**🃏 የእርስዎ ቢንጎ ካርድ (Your Bingo Card) 🃏**\n"
                f"_🟢 አረንጓዴ ቁጥር ሲመጣ ይጫኑ! (Numbers are White)_"
            )
            
            new_keyboard = build_card_keyboard(card, -1, game_id, msg_id, is_selection=False)
            
            try:
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=msg_id,
                    text=new_card_text,
                    reply_markup=new_keyboard,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.debug(f"Error editing message reply markup: {e}")
                await query.answer("Error updating card. Is the message too old?")

        elif action == 'BINGO':
            try:
                if check_win(card):
                    await finalize_win(context, game_id, user_id)
                else:
                    await query.answer("❌ ውሸት! (False Bingo). Keep playing. ❌")
            
            except Exception as e:
                logger.error(f"FATAL ERROR in BINGO action: {e}")
                await query.answer("🚨 An internal error occurred. Try again. 🚨")


# --- Utility Handlers (Unchanged) ---
async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    bot_username = context.bot.username
    
    if not bot_username:
        await update.message.reply_text("⛔ Could not determine the bot's username. Please contact the administrator.")
        return

    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    
    message = (
        f"**🔗 የእርስዎ የሪፈራል ሊንክ (Your Referral Link) 🔗**\n\n"
        f"ይህን ሊንክ ለጓደኞችዎ ያጋሩ እና **{REFERRAL_REWARD} Br** ሽልማት ያግኙ! ሽልማቱ ጓደኛዎ የመጀመሪያ ጨዋታውን ሲጫወት ወዲያውኑ ወደ ሂሳብዎ ይገባል።\n\n"
        f"**ለመጋራት ይጫኑ (Tap to Share):**\n`{referral_link}`"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def instructions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Update instructions to reflect 200 cards and 3 card selection
    message = (
        "**📜 የመጫወቻ ህጎች (Game Rules) 📜**\n\n"
        f"1. **ክፍያ (Cost):** እያንዳንዱ ጨዋታ ለመጫወት **{GAME_COST} Br** ያስከፍላል።\n"
        f"2. **አሸናፊ (Winner):** {MIN_REAL_PLAYERS} ተጫዋቾች ሲመዘገቡ ጨዋታው ይጀምራል። በቂ ተጫዋቾች ከሌሉ **የኮምፒውተር ተጫዋቾች** ጨዋታውን ተቀላቅለው ያሸንፋሉ።\n"
        f"3. **ሽልማት (Prize):** ያሸነፉ ተጫዋቾች **{PRIZE_AMOUNT} Br** ወዲያውኑ ወደ ሂሳባቸው ይገባል!\n"
        f"4. **ጋብዝ (Refer):** ጓደኛን ጋብዘው የመጀመሪያ ጨዋታቸውን ሲጫወቱ **{REFERRAL_REWARD} Br** ሽልማት ያግኙ። /refer የሚለውን ይጫኑ።\n\n"
        
        "**🕹️ እንዴት እንጫወታለን? (How to Play) 🕹️**\n"
        "1. **/play** ይጫኑ እና የጨዋታውን ዋጋ ይከፍላሉ።\n"
        f"2. **የቢንጎ ካርድ ምርጫ (Card Selection):** ከ{TOTAL_CARD_POOL} ቋሚ ካርዶች ውስጥ በዘፈቀደ የተመረጡ **{CARDS_TO_CHOOSE}** ካርዶች ይቀርቡልዎታል። የሚፈልጉትን የካርድ ቁጥር ይምረጡ። ተመሳሳይ የካርድ ቁጥር ሁልጊዜም ተመሳሳይ የቁጥሮች ዝግጅት ይኖረዋል።\n"
        "3. **ጨዋታው ሲጀመር:** ቁጥሮች በድምጽ (Voice) ይጠራሉ፤ **የእንግሊዘኛ ፊደል (Letter) + የአማርኛ ቁጥር** ነው ጥሪው።\n"
        "   - **🟢 አረንጓዴ ቁጥር (Green Button):** ይህ ቁጥር አሁን ተጠርቷል ማለት ነው።\n"
        "   - **✅ ተጭነው ምልክት ያድርጉ (Tap to Mark):** ቁጥሩን በካርድዎ ላይ ምልክት ለማድረግ አረንጓዴውን ቁጥር ይጫኑ። ወደ **✅** ይቀየራል።\n"
        "4. **ቢንጎ (BINGO):** 5 ምልክት የተደረገባቸው ቁጥሮች (✅) በአንድ ቀጥተኛ መስመር ሲገጥሙ:\n"
        "   - **🚨 CALL BINGO! 🚨** የሚለውን ቁልፍ ይጫኑ።\n"
        
        "**እድለኛ ይሁኑ! (Good Luck!)**"
    )
    if update.message:
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        return message

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    
    referrer_id = None
    if context.args:
        try:
            potential_referrer_id = int(context.args[0])
            if potential_referrer_id != user_id:
                referrer_id = potential_referrer_id
        except ValueError:
            logger.warning(f"Invalid referrer ID in start payload: {context.args[0]}")
            
    create_or_update_user(user_id, user.username, user.first_name, referred_by=referrer_id)
    
    await update.message.reply_text(
        f"**👋 እንኳን ወደ አዲስ ቢንጎ በደህና መጡ!**\n\n"
        f"ለመጫወት /play ይጫኑ (Cost: {GAME_COST} Br).\n"
        f"**👉 ጓደኛ ይጋብዙና {REFERRAL_REWARD} Br ያግኙ:** /refer\n\n"
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
        f"1. ዝቅተኛ የማስገቢያ መጠን: **50 Br** (Minimum Deposit: 50 Br). 👈\n"
        f"2. Telebirr ቁጥር: **{telebirr_number}** ይጠቀሙ።\n"
        f"3. የእርስዎ መለያ ቁጥር (Telegram ID):\n"
        f"   **{user_id}**\n\n"
        f"4. የላኩበትን ደረሰኝ (Screenshot) እና **ID ቁጥርዎን** ወዲያውኑ ለኛ ይላኩ:\n"
        f"{link_message}\n\n"
        f"_ገንዘብዎ በአንድ ደቂቃ ውስጥ ወደ ሂሳብዎ ይገባል!_"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    contact_info = ADMIN_USERNAME if ADMIN_USERNAME else str(ADMIN_USER_ID)
    
    if ADMIN_USERNAME and ADMIN_USERNAME.startswith('@'):
        link_name = f"Admin ({ADMIN_USERNAME})"
        link_message = f"[Click here to start a chat with {link_name}](https://t.me/{ADMIN_USERNAME.lstrip('@')})"
    else:
        link_message = f"Contact Admin: {contact_info}"

    message = (
        f"**💸 ገንዘብ የማስወጣት መመሪያዎች (Withdrawal Instructions) 💸**\n\n" 
        f"1. በመጀመሪያ ቀሪ ሂሳብዎን በ /balance ያረጋግጡ።\n"
        f"2. ለማውጣት የሚፈልጉትን መጠንና የሚፈልጉትን የመክፈያ ዘዴ (ለምሳሌ: Telebirr) በማስገባት ለአድሚን መልእክት ይላኩ።\n"
        f"   - የእርስዎ ID ቁጥር: **{user_id}**\n"
        f"   - የሚፈልጉት መጠን (Amount):\n"
        f"   - የመክፈያ ዘዴ (Payment Method): \n\n"
        f"3. የአድሚን አድራሻ:\n"
        f"{link_message}\n\n"
        f"_ሂሳብዎ በፍጥነት ተረጋግጦ ይላክልዎታል!_"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')

# Admin Handlers (Unchanged)
async def check_balance_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_USER_ID is None or update.effective_user.id != ADMIN_USER_ID: return
    if not context.args: await update.message.reply_text("⛔ Error. Usage: /check_balance [user_id]"); return
    try:
        target_id = int(context.args[0])
        data = get_user_data(target_id)
        balance = data.get('balance', 0.0)
        await update.message.reply_text(f"**✅ User Balance Check**\nUser ID: `{target_id}`\nBalance: **{balance} Br**\nName: {data.get('first_name', 'N/A')} (@{data.get('username', 'N/A')})", parse_mode='Markdown')
    except:
        await update.message.reply_text("⛔ Error. User ID must be a valid number.")

async def approve_deposit_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_USER_ID is None or update.effective_user.id != ADMIN_USER_ID: return
    try:
        tid = int(context.args[0])
        amt = float(context.args[1])
        update_balance(tid, amt) 
        await update.message.reply_text(f"✅ Approved deposit of {amt} Br to User ID {tid}")
        await context.bot.send_message(tid, f"💰 የገንዘብ ማስገቢያዎ ጸድቋል! +{amt} Br ወደ ሂሳብዎ ገብቷል።")
    except:
        await update.message.reply_text("⛔ Error. Usage: /ap_dep [user_id] [amount] (Both must be numbers)")

async def approve_withdrawal_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if ADMIN_USER_ID is None or update.effective_user.id != ADMIN_USER_ID: return
    try:
        tid = int(context.args[0])
        amt = float(context.args[1])
        if get_user_data(tid).get('balance', 0) < amt:
            await update.message.reply_text(f"⛔ User ID {tid} has insufficient balance. Deduction aborted.")
            return

        update_balance(tid, -amt) 
        await update.message.reply_text(f"✅ Approved withdrawal of {amt} Br from User ID {tid}")
        await context.bot.send_message(tid, f"💸 ገንዘብ የማውጣት ጥያቄዎ ጸድቋል! -{amt} Br ከሂሳብዎ ተቀንሶ ተልኳል።")
    except:
        await update.message.reply_text("⛔ Error. Usage: /ap_wit [user_id] [amount] (Both must be numbers)")


# --- Main ---
def main():
    if not TOKEN: return
    import requests 
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("withdraw", withdraw_command))
    app.add_handler(CommandHandler("play", play_command))
    app.add_handler(CommandHandler("refer", refer_command))
    app.add_handler(CommandHandler("instructions", instructions_command))
    
    app.add_handler(CommandHandler("check_balance", check_balance_admin)) 
    app.add_handler(CommandHandler("ap_dep", approve_deposit_admin))
    app.add_handler(CommandHandler("ap_wit", approve_withdrawal_admin)) 
    
    app.add_handler(CallbackQueryHandler(handle_callback))

    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')

if __name__ == '__main__':
    import requests 
    main()
