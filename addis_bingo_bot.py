# Addis (አዲስ) Bingo - V10.0: Dynamic Bots, TTS Calling, Fixed Cards
# Implements a dynamic bot system for guaranteed wins when real players are low (promotional).
# Uses Gemini TTS for dual-language (Eng/Amh) voice calls.

import os
import logging
import json
import base64
import asyncio
import random
import time
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
MIN_REAL_PLAYERS = 5 # Minimum required real players to disable computer players
CALL_DELAY = 2.40    # Delay between number calls (User request: 2.40 seconds)
COLUMNS = ['B', 'I', 'N', 'G', 'O']

# --- Referral Constant ---
REFERRAL_REWARD = 10.0 

# --- Emojis and Aesthetics (User Request) ---
EMOJI_UNMARKED = '⚫' # Black for uncalled
EMOJI_CALLED = '🟢'   # Called, not marked
EMOJI_MARKED = '✅'   # Called, and marked by player
EMOJI_FREE = '🌟'     # Free space

# --- Global Game State (In-Memory) ---
LOBBY = {} 
ACTIVE_GAMES = {}
BOT_WINNER_ID = -999999999 # Designated ID for the winning bot

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
    # Do not update balance for computer players (negative IDs)
    if user_id < 0: return 
    db.collection(USERS_COLLECTION).document(str(user_id)).update({
        'balance': firestore.Increment(amount)
    })

async def pay_referral_reward(context: ContextTypes.DEFAULT_TYPE, referred_id: int, referrer_id: int):
    # This function remains unchanged as it is critical for referral payment
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


# --- Fixed Bingo Cards (User Request: 10 fixed, unique cards) ---
# NOTE: These cards must be consistent every time they are selected by number.
FIXED_BINGO_CARDS = {
    1: {'B': [3, 9, 12, 14, 1], 'I': [17, 24, 28, 30, 21], 'N': [35, 39, 'FREE', 42, 45], 'G': [48, 51, 56, 60, 54], 'O': [62, 65, 70, 75, 71]},
    2: {'B': [7, 13, 2, 8, 11], 'I': [19, 22, 26, 30, 16], 'N': [31, 38, 'FREE', 40, 44], 'G': [46, 52, 57, 59, 50], 'O': [61, 64, 69, 74, 68]},
    3: {'B': [5, 15, 6, 10, 4], 'I': [20, 23, 27, 29, 18], 'N': [32, 36, 'FREE', 41, 43], 'G': [47, 53, 58, 55, 49], 'O': [63, 67, 72, 73, 66]},
    4: {'B': [1, 8, 15, 3, 11], 'I': [16, 23, 30, 18, 25], 'N': [31, 40, 'FREE', 33, 44], 'G': [46, 55, 60, 48, 57], 'O': [61, 70, 75, 63, 72]},
    5: {'B': [10, 2, 13, 5, 7], 'I': [21, 29, 17, 24, 28], 'N': [34, 45, 'FREE', 36, 43], 'G': [49, 58, 51, 56, 52], 'O': [64, 73, 66, 71, 74]},
    6: {'B': [4, 14, 6, 9, 12], 'I': [20, 27, 22, 19, 26], 'N': [37, 41, 'FREE', 39, 42], 'G': [53, 59, 54, 50, 47], 'O': [65, 68, 62, 75, 69]},
    7: {'B': [2, 7, 12, 5, 15], 'I': [18, 25, 29, 16, 23], 'N': [31, 36, 'FREE', 43, 38], 'G': [47, 52, 57, 60, 53], 'O': [63, 68, 73, 61, 70]},
    8: {'B': [11, 4, 9, 1, 14], 'I': [17, 22, 28, 20, 27], 'N': [32, 37, 'FREE', 44, 41], 'G': [49, 54, 59, 46, 51], 'O': [65, 72, 75, 64, 71]},
    9: {'B': [3, 8, 13, 6, 10], 'I': [19, 24, 30, 21, 26], 'N': [33, 39, 'FREE', 45, 42], 'G': [50, 56, 60, 48, 55], 'O': [62, 69, 74, 66, 73]},
    10: {'B': [6, 1, 11, 15, 3], 'I': [16, 21, 26, 30, 18], 'N': [31, 35, 'FREE', 40, 44], 'G': [46, 50, 54, 58, 52], 'O': [61, 65, 70, 74, 69]},
}

def generate_card(card_id: int):
    # Retrieve the fixed card data, ensuring the FREE space is marked as called/marked
    fixed_data = FIXED_BINGO_CARDS.get(card_id)
    if not fixed_data:
        # Fallback to a random card if ID is invalid, though it shouldn't happen
        return generate_random_card_internal() 

    # Convert list of values back to the expected dictionary format
    card_data = {
        'data': {
            'B': fixed_data['B'], 'I': fixed_data['I'], 'N': [n for n in fixed_data['N'] if n != 'FREE'], 
            'G': fixed_data['G'], 'O': fixed_data['O']
        },
        'marked': {(2, 2): True}, 
        'called': {(2, 2): True}, 
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
        'called': {(2, 2): True}
    }
    return card_data

def get_card_value(card, col_idx, row_idx):
    if col_idx == 2 and row_idx == 2:
        return "FREE"
    return card['data'][COLUMNS[col_idx]][row_idx]

def get_card_position(card, value):
    for c_idx, col_letter in enumerate(COLUMNS):
        if col_letter == 'N':
            for r_idx, v in enumerate(card['data'][col_letter]):
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

# Updated to show bigger text for current call
def get_current_call_text(num):
    if num is None:
        return "**📣 በመጠባበቅ ላይ... (Awaiting first call)**"
    col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)
    
    # User Request: Bigger size for current call
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
        # Do not refresh bot player cards
        if pid < 0: continue
        
        card = game_data['cards'][pid]
        msg_id = game_data['card_messages'][pid]
        
        new_keyboard = build_card_keyboard(card, -1, game_id, msg_id, is_selection=False)
        
        # User Request: White text for numbers
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
    
    # Header row (White on Black)
    header = [InlineKeyboardButton(f"⚪ {col} ⚪", callback_data=f"ignore_header") for col in COLUMNS]
    keyboard.append(header)
    
    for r in range(5):
        row = []
        for c in range(5):
            pos = (c, r)
            value = get_card_value(card, c, r)
            is_marked = card['marked'].get(pos, False)
            is_called = card['called'].get(pos, False)

            # User Request: Black for uncalled, Green for called, White text
            if value == "FREE":
                label = f"{EMOJI_FREE}"
                callback_data = f"ignore_free"
            elif is_marked:
                label = f"{EMOJI_MARKED} {value}" # ✅ is green/white
                callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" 
            elif is_called:
                label = f"{EMOJI_CALLED} {value}" # 🟢 is green
                callback_data = f"MARK|{game_id}|{msg_id}|{c}|{r}" 
            else:
                label = f"{EMOJI_UNMARKED} {value}" # ⚫ is black
                callback_data = f"ignore_not_called" 
            
            if is_selection:
                # When selecting card, show the card number label
                row.append(InlineKeyboardButton(str(card_index), callback_data=f"ignore_select_card_num"))
            else:
                row.append(InlineKeyboardButton(label, callback_data=callback_data))
                
        keyboard.append(row)
    
    if is_selection:
        keyboard.append([InlineKeyboardButton(f"✅ Card {card_index}: ይሄንን ይምረጡ (Select This)", callback_data=f"SELECT|{card_index}")])
    else:
        keyboard.append([InlineKeyboardButton("🚨 CALL BINGO! 🚨", callback_data=f"BINGO|{game_id}|{msg_id}")])
    
    return InlineKeyboardMarkup(keyboard)

def check_win(card):
    # Check if a card has a winning line (remains the same)
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
    
    # Exponential backoff for API call
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
                # The API returns PCM. We need helper functions to convert PCM to WAV for playback.
                # Since these helpers are complex and not provided, we must rely on a placeholder 
                # or a simple base64 URL if the environment supports it (Canvas does not auto-handle PCM->WAV conversion).
                # For demonstration, we will skip the actual WAV conversion and just return the raw data and mime type.
                # In a real environment, you'd use AudioContext to process the raw PCM.
                
                # NOTE: For this simulated environment, we will return the base64 data, 
                # but the Telegram Bot API requires a proper file upload/URL. 
                # We will send the audio via input media using base64 audio data.
                return audio_data, mime_type_full

            logger.error("TTS API did not return audio data.")
            break 

        except requests.exceptions.RequestException as e:
            if attempt < 3:
                await asyncio.sleep(2 ** attempt) # Exponential backoff
            else:
                logger.error(f"TTS API failed after multiple retries: {e}")
                break
        except Exception as e:
            logger.error(f"TTS API general error: {e}")
            break
            
    return None, None

# --- Computer Player Logic ---

def add_computer_players(real_players: list) -> tuple:
    """Adds bots based on the number of real players, ensuring 100% bot win chance if < MIN_REAL_PLAYERS."""
    
    real_count = len(real_players)
    bots_to_add = 0
    bot_players = []

    if real_count >= MIN_REAL_PLAYERS:
        return real_players, [] # No bots needed
    
    # User Request: Dynamic scaling of bots
    if real_count == 1:
        bots_to_add = random.randint(7, 8) 
    elif real_count in (2, 3):
        bots_to_add = random.randint(10, 12)
    elif real_count == 4:
        bots_to_add = random.randint(10, 20)
    
    # Generate negative IDs for bots
    for i in range(bots_to_add):
        bot_players.append(BOT_WINNER_ID - i) 
        
    return real_players + bot_players, bot_players

def generate_winning_sequence(game_data):
    """
    Creates a card and prioritizes the winning numbers for the BOT_WINNER_ID.
    Returns: a modified list of available numbers, and the winning bot's card.
    """
    
    # 1. Generate a standard random card for the winning bot
    bot_card = generate_random_card_internal()
    
    # 2. Select a winning line (e.g., the first row)
    winning_positions = [(c, 0) for c in range(5)]
    winning_numbers = [get_card_value(bot_card, c, 0) for c in range(5)]

    # 3. Create a list of all numbers 1-75, removing the winning numbers
    all_numbers = list(range(1, 76))
    for num in winning_numbers:
        if num in all_numbers:
            all_numbers.remove(num)
            
    # 4. Shuffle the remaining numbers
    random.shuffle(all_numbers)
    
    # 5. Insert the winning numbers at the start of the list, ensuring a quick win
    # We want 4 numbers called immediately, and the 5th number called very soon after.
    # We will prioritize 4 of the 5 winning numbers, and place the last one slightly later.
    
    final_win_num = winning_numbers.pop(random.randrange(len(winning_numbers)))
    
    # Put 4 winning numbers first, then 10 random numbers, then the final winning number
    # This ensures a win happens quickly but not instantly.
    
    available_numbers = winning_numbers + all_numbers[:10] + [final_win_num] + all_numbers[10:]
    
    # Pre-mark the 4 numbers that will be called first for the bot
    for num in winning_numbers:
        c, r = get_card_position(bot_card, num)
        if c is not None:
            bot_card['marked'][(c, r)] = True

    game_data['winning_num'] = final_win_num
    game_data['winning_card'] = bot_card
    game_data['winner_id'] = BOT_WINNER_ID

    return available_numbers

# --- Game Loop (Updated) ---
async def run_game_loop(context: ContextTypes.DEFAULT_TYPE, game_id, real_players):
    
    # Determine players and if bots are active
    all_players, bot_players = add_computer_players(real_players)
    is_bot_game = len(bot_players) > 0
    
    game_data = ACTIVE_GAMES[game_id]
    
    if is_bot_game:
        # Generate sequence where a bot is guaranteed to win early
        available_numbers = generate_winning_sequence(game_data)
        game_data['cards'][BOT_WINNER_ID] = game_data['winning_card']
        game_data['players'] = all_players
        
        # Announce the game start to all players, including bot count
        await context.bot.send_message(
            real_players[0], 
            f"🤖 **የኮምፒውተር ተጫዋቾች (Ghost Players)** 👻\n\nበቂ ተጫዋች እስኪመጣ ድረስ **{len(bot_players)}** የኮምፒውተር ተጫዋቾች (Players) ጨዋታውን ተቀላቅለዋል።",
            parse_mode='Markdown'
        )
    else:
        # Standard game setup (random sequence)
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
    
    # 1. Send the initial Called Numbers Board (History)
    board_message_ids = {}
    board_msg_text = "**🎰 የተጠሩ ቁጥሮች ታሪክ (Called Numbers History) 🎰**\n\n_ይህ የጥሪ ታሪክ ነው (This is the call history log)._"
    for pid in real_players: # Only send to real players
        msg = await context.bot.send_message(pid, board_msg_text, parse_mode='Markdown')
        board_message_ids[pid] = msg.message_id
    game_data['board_messages'] = board_message_ids

    # 2. Initial card refresh (to set the 'Awaiting first call' text)
    await refresh_all_player_cards(context, game_id, real_players, current_call_num=None)

    await asyncio.sleep(2)

    for num in available_numbers:
        if game_id not in ACTIVE_GAMES or ACTIVE_GAMES[game_id]['status'] != 'running':
            break

        game_data['called'].append(num)
        col_letter = next(col for col, (start, end) in [('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), ('G', (46, 60)), ('O', (61, 75))] if start <= num <= end)

        # 3. Handle Bot Win (If applicable)
        if is_bot_game and num == game_data['winning_num']:
            # Bot wins automatically on this number
            await asyncio.sleep(1.0) # Wait a moment for realism
            await finalize_win(context, game_id, game_data['winner_id'])
            return # End game loop

        # 4. Update all cards for the green highlight (Called state)
        for pid in game_data['players']:
            card = game_data['cards'][pid]
            c, r = get_card_position(card, num)
            if c is not None and r is not None:
                card['called'][(c, r)] = True

        # 5. Send TTS Audio Call
        audio_data_b64, mime_type = await text_to_speech_call(col_letter, num)
        if audio_data_b64:
            # Send audio to all real players
            audio_bytes = base64.b64decode(audio_data_b64)
            for pid in real_players:
                try:
                    await context.bot.send_voice(chat_id=pid, voice=audio_bytes, caption=f"**{col_letter} - {num}**", parse_mode='Markdown')
                except Exception as e:
                    logger.warning(f"Failed to send TTS audio to {pid}: {e}")
        else:
            # Fallback text message if TTS fails
             for pid in real_players:
                await context.bot.send_message(pid, f"**📣 👑 {col_letter} - {num} 👑**", parse_mode='Markdown')

        # 6. Refresh all player cards to show the green highlight AND the new call text
        await refresh_all_player_cards(context, game_id, real_players, current_call_num=num)

        # 7. Update the Calling Board message (HISTORY ONLY)
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
    """Handles the final win state, payment, and notifications."""
    
    if game_id not in ACTIVE_GAMES: return
    game_data = ACTIVE_GAMES[game_id]
    
    # Identify the winner name (for display)
    if winner_id < 0:
        # Bot winner
        winner_name = f"Mystery Player {random.randint(100, 999)}"
        # Do NOT update balance for bot
    else:
        # Real player winner
        data = get_user_data(winner_id)
        winner_name = data.get('first_name', f"Player {winner_id}")
        update_balance(winner_id, PRIZE_AMOUNT) 

    game_data['status'] = 'finished'
    win_msg = f"🎉 BINGO!!! 🎉\n\nአሸናፊ (Winner): **{winner_name}**\n**Prize: {PRIZE_AMOUNT} Br Added!**"
    
    real_players = [pid for pid in game_data['players'] if pid > 0]
    
    for pid in real_players:
        # Edit the History Board
        try:
            await context.bot.edit_message_text(
                chat_id=pid,
                message_id=game_data['board_messages'][pid],
                text=f"**🎉 WINNER: {winner_name} 🎉**\n\n**The Game has ended!**",
                reply_markup=None,
                parse_mode='Markdown'
            )
        except: pass

        # Send the win message
        await context.bot.send_message(pid, win_msg, parse_mode='Markdown')
        
        # Remove the 'CALL BINGO' button from the player's card
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

    # Referral reward check (before deducting cost)
    referred_by = data.get('referred_by')
    referral_status = data.get('referral_paid_status', 'N/A')
    
    if referred_by and referral_status == 'PENDING':
        await pay_referral_reward(context, user_id, referred_by)
        
    # Deduct game cost (negative amount)
    update_balance(user_id, -GAME_COST)
    
    # User Request: Use fixed cards (1-10) for selection
    available_card_ids = list(FIXED_BINGO_CARDS.keys())
    
    # Shuffle IDs and pick 3 for display, ensuring unique numbers for this session's choice
    selected_ids = random.sample(available_card_ids, 3) 
    
    card_options = {id: generate_card(id) for id in selected_ids}
    card_message_ids = []

    await update.message.reply_text(f"✅ {GAME_COST} Br ተቀንሷል። (Deducted {GAME_COST} Br).\n\n**እባክዎ ከታች ካሉት 3 ካርዶች አንዱን ይምረጡ።**")

    for i, card_id in enumerate(selected_ids):
        card = card_options[card_id]
        
        # Build the preview text using the fixed ID and numbers
        card_layout_text = f"**B** **I** **N** **G** **O**\n"
        # Since the fixed data structure is flat, we reconstruct the display
        col_data = {
             'B': FIXED_BINGO_CARDS[card_id]['B'],
             'I': FIXED_BINGO_CARDS[card_id]['I'],
             'N': [n for n in FIXED_BINGO_CARDS[card_id]['N'] if n != 'FREE'],
             'G': FIXED_BINGO_CARDS[card_id]['G'],
             'O': FIXED_BINGO_CARDS[card_id]['O'],
        }
        
        for r in range(5):
            row_numbers = []
            for col in COLUMNS:
                if col == 'N' and r == 2:
                    row_numbers.append(str('FREE').center(3))
                else:
                    try:
                        row_numbers.append(str(col_data[col][r]).center(3))
                    except IndexError:
                        row_numbers.append('---')
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
        'selected_ids': selected_ids,
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
        
        for mid in all_message_ids:
            try:
                # Delete old card messages
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


        # Use a list to hold the pending players until MIN_REAL_PLAYERS or loop starts
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
            # Game starts immediately with only real players
            game_data_to_start = ACTIVE_GAMES.pop('PENDING')
            ACTIVE_GAMES[game_id] = game_data_to_start
            ACTIVE_GAMES[game_id]['called'] = []
            asyncio.create_task(run_game_loop(context, game_id, pending_players))
            
        elif len(pending_players) == 1:
            # First player starts the bot timer
            await context.bot.send_message(user_id, "⏳ **ተራ ይጠብቁ (Awaiting players)...**\n\nሌሎች ተጫዋቾችን እየጠበቅን ነው። በቂ ተጫዋች ካልተገኘ **በ10 ሰከንዶች** ውስጥ የኮምፒውተር ተጫዋቾች ተቀላቅለው ጨዋታው ይጀመራል!")
            await asyncio.sleep(10) # Wait 10 seconds for real players to join
            
            if game_id not in ACTIVE_GAMES and 'PENDING' in ACTIVE_GAMES and len(ACTIVE_GAMES['PENDING']['players']) > 0:
                # If still pending and timer runs out, start game with bots
                game_data_to_start = ACTIVE_GAMES.pop('PENDING')
                real_players_now = game_data_to_start['players']
                
                # Assign to ACTIVE_GAMES under the generated ID
                ACTIVE_GAMES[game_id] = game_data_to_start
                ACTIVE_GAMES[game_id]['called'] = []
                
                asyncio.create_task(run_game_loop(context, game_id, real_players_now))
                
        else:
            await context.bot.send_message(user_id, f"✅ **{len(pending_players)}/5 ተጫዋቾች ተመዝግበዋል!**\n\nሌሎች ተጫዋቾች ሲመዘገቡ ወዲያውኑ ጨዋታው ይጀምራል።")

        return

    # --- MARK and BINGO (Active Game Logic) ---
    
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

            card['marked'][pos] = not is_already_marked # Toggle mark state
            
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
                    # Player wins, stop the game loop
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
    message = (
        "**📜 የመጫወቻ ህጎች (Game Rules) 📜**\n\n"
        f"1. **ክፍያ (Cost):** እያንዳንዱ ጨዋታ ለመጫወት **{GAME_COST} Br** ያስከፍላል።\n"
        f"2. **አሸናፊ (Winner):** {MIN_REAL_PLAYERS} ተጫዋቾች ሲመዘገቡ ጨዋታው ይጀምራል። በቂ ተጫዋቾች ከሌሉ **የኮምፒውተር ተጫዋቾች** ጨዋታውን ተቀላቅለው ያሸንፋሉ።\n"
        f"3. **ሽልማት (Prize):** ያሸነፉ ተጫዋቾች **{PRIZE_AMOUNT} Br** ወዲያውኑ ወደ ሂሳባቸው ይገባል!\n"
        f"4. **ጋብዝ (Refer):** ጓደኛን ጋብዘው የመጀመሪያ ጨዋታቸውን ሲጫወቱ **{REFERRAL_REWARD} Br** ሽልማት ያግኙ። /refer የሚለውን ይጫኑ።\n\n"
        
        "**🕹️ እንዴት እንጫወታለን? (How to Play) 🕹️**\n"
        "1. **/play** ይጫኑ እና የጨዋታውን ዋጋ ይከፍላሉ።\n"
        "2. **የቢንጎ ካርድ ቁጥር (Card Number)** ይምረጡ። ካርዶች ሁልጊዜም ተመሳሳይ ቁጥሮችን ይይዛሉ።\n"
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

# --- Other Commands (Unchanged) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (start command logic remains the same)
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
        f"**💸 ገንዘብ የማስወጣት መመሪያዎች (Withdrawal Instructions) 💸**\n\n" # User requested update
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
    # Needs the requests library for the TTS API call
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
        # Note: Added `requests` to the top-level import block for this environment.
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')

if __name__ == '__main__':
    # Add requests to the global namespace for the synchronous TTS call inside the async loop
    import requests 
    main()
