# Addis (አዲስ) Bingo Bot - V32: Game Reliability & Win Guarantee

import os
import logging
import json
import base64
import asyncio
import random
import time
import uuid 
import io      
import struct  

# Try importing requests for TTS
try:
    import requests
except ImportError:
    requests = None 

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
)
import firebase_admin
from firebase_admin import credentials, firestore, exceptions

# --- Configuration ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')
V2_SECRETS = os.environ.get('V2_SECRETS')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') 
# NOTE TO USER: If TTS is not working, ensure GEMINI_API_KEY is correctly set in your environment variables.
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '') 

TELEBIRR_ACCOUNT = "0927922721"

# --- Financial Constraints ---
CARD_COST = 20.00      # One game price
MIN_DEPOSIT = 50.00    # Minimum deposit enforced in messaging
MIN_WITHDRAW = 100.00  # Minimum for withdrawing, enforced in code

REFERRAL_BONUS = 10.00

# Conversation States
GET_CARD_NUMBER, GET_WITHDRAW_AMOUNT, GET_TELEBIRR_ACCOUNT, GET_DEPOSIT_CONFIRMATION = range(4)
# Renaming GET_CARD_NUMBER to be used as GET_DEPOSIT_AMOUNT for clarity in the flow,
# but keeping the variable name for consistency with user's original constants.

# Admin ID Extraction
ADMIN_USER_ID = None
try:
    if V2_SECRETS and '|' in V2_SECRETS:
        admin_id_str, _ = V2_SECRETS.split('|', 1)
        ADMIN_USER_ID = int(admin_id_str)
except Exception:
    pass

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Constants ---
MIN_REAL_PLAYERS_FOR_ORGANIC_GAME = 5 
MAX_PRESET_CARDS = 200
CALL_DELAY = 2.25  
BOT_WIN_DELAY_CALLS = random.randint(1, 3) 
COLUMNS = ['B', 'I', 'N', 'G', 'O']

# Payout Logic
GLOBAL_CUT_PERCENT = 0.20      
WINNER_SHARE_PERCENT = 0.80    

# TTS URL
TTS_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"

# --- UI Aesthetics ---
EMOJI_UNMARKED_UNCALLED = '🔴' 
EMOJI_CALLED_UNMARKED = '🟢'   
EMOJI_MARKED = '✅'         
EMOJI_FREE = '🌟'      
EMOJI_CARD = '🃏'
EMOJI_BINGO = '🏆'
EMOJI_CALL = '📢'
EMOJI_HISTORY = '📜'

# --- Database Setup (Firestore) ---
db = None
try:
    if V2_SECRETS and '|' in V2_SECRETS:
        _, firebase_b64 = V2_SECRETS.split('|', 1)
        cred = credentials.Certificate(json.loads(base64.b64decode(firebase_b64).decode('utf-8')))
        firebase_admin.initialize_app(cred)
        db = firestore.client()
except Exception as e: 
    logger.error(f"Firestore initialization failed: {e}")
    
USERS_COLLECTION = 'addis_bingo_users'
STATS_COLLECTION = 'addis_bingo_stats' 
TRANSACTIONS_COLLECTION = 'addis_bingo_transactions'

def create_or_update_user(user_id, username, first_name, referred_by_id=None):
    if not db: return
    
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    user_data = doc_ref.get().to_dict()
    
    data = {
        'username': username or 'N/A',
        'first_name': first_name,
        'last_updated': firestore.SERVER_TIMESTAMP
    }
    
    # CRITICAL FIX: Ensure 'balance' exists and is a float/number for initial user.
    if user_data is None:
        data['balance'] = 0.00
        data['games_played'] = 0
        data['wins'] = 0
    
    if referred_by_id and not (user_data and user_data.get('referred_by_id')):
        data['referred_by_id'] = str(referred_by_id)
        data['referrer_paid'] = False 
    
    try:
        doc_ref.set(data, merge=True)
    except exceptions.FirebaseError as e:
        logger.error(f"Failed to create or update user {user_id}: {e}")

# --- ASYNCHRONOUS GET USER DATA (Performance Fix) ---
async def get_user_data(user_id: int) -> dict:
    """Retrieves user data asynchronously using asyncio.to_thread for Firestore operations."""
    if not db: return {'balance': 0.00, 'first_name': 'Player', 'games_played': 0, 'wins': 0}
    
    try:
        doc = await asyncio.to_thread(lambda: db.collection(USERS_COLLECTION).document(str(user_id)).get())
    except exceptions.FirebaseError as e:
        logger.error(f"Firestore get_user_data failed for {user_id}: {e}")
        return {'balance': 0.00, 'first_name': 'Player', 'games_played': 0, 'wins': 0}
        
    if doc.exists: 
        data = doc.to_dict()
        # Ensure balance is treated as a float
        data['balance'] = float(data.get('balance', 0.00))
        return data
        
    return {'balance': 0.00, 'first_name': 'Player', 'games_played': 0, 'wins': 0}

def update_balance(user_id: int, amount: float, transaction_type: str = 'General', description: str = ''):
    """Updates balance and logs the transaction."""
    if not db: 
        logger.warning(f"DB not initialized. Failed to update balance for {user_id}.")
        return
    try:
        # 1. Update Balance (Atomic Increment is used for safety)
        user_ref = db.collection(USERS_COLLECTION).document(str(user_id))
        user_ref.update({'balance': firestore.Increment(amount)})
        
        # 2. Log Transaction
        db.collection(TRANSACTIONS_COLLECTION).add({
            'user_id': str(user_id),
            'amount': amount,
            'type': transaction_type, 
            'description': description,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        logger.info(f"Balance updated for {user_id}: {amount:.2f} ({transaction_type})")
    except exceptions.FirebaseError as e:
        logger.error(f"CRITICAL: Firestore balance update failed for user {user_id} (Amount: {amount}): {e}")

def pay_referrer_bonus(user_id: int):
    """Checks if a user was referred and pays the bonus if they haven't been paid yet."""
    if not db: return
    
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    user_data = doc_ref.get().to_dict()
    
    if user_data and user_data.get('referred_by_id') and not user_data.get('referrer_paid'):
        referrer_id = user_data['referred_by_id']
        
        # 1. Pay the referrer and log transaction
        update_balance(referrer_id, REFERRAL_BONUS, transaction_type='Referral Bonus', description=f"Bonus for referring user {user_id}")
        
        # 2. Mark the user as having triggered the payment
        doc_ref.update({'referrer_paid': True})
        
        logger.info(f"Paid {REFERRAL_BONUS} Br referral bonus to user {referrer_id} for user {user_id}")
        return True
    return False

def update_game_stats(user_id: int, is_win: bool):
    """Updates games_played and wins counter."""
    if not db: return
    
    user_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    update_data = {'games_played': firestore.Increment(1)}
    
    if is_win:
        update_data['wins'] = firestore.Increment(1)
        
    try:
        user_ref.update(update_data)
    except exceptions.FirebaseError as e:
        logger.error(f"Failed to update game stats for user {user_id}: {e}")

# --- Game State & Bots ---
ACTIVE_GAMES = {} 
PENDING_PLAYERS = {} 
LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}

BOT_ID_COUNTER = -1 
def create_bot_player() -> tuple[int, str]:
    global BOT_ID_COUNTER
    BOT_ID_COUNTER -= 1
    # Bot names are generic IDs to simulate real users without being identifiable
    name = f"P-{random.randint(100000, 999999)}"
    return BOT_ID_COUNTER, name

def get_total_players_target(real_count: int) -> int:
    """
    CTO Rule Implementation: Determines the total number of players (real + bots).
    If real_count >= 5, returns real_count (No bots, organic game).
    If real_count <= 4, ensures a high player count (Stealth mode).
    """
    if real_count >= MIN_REAL_PLAYERS_FOR_ORGANIC_GAME: 
        return real_count  # No bots, organic game
    
    # When 4 or fewer players, maximize bot appearance
    if real_count == 1: 
        return random.randint(10, 12)
    if real_count == 2: 
        return random.randint(13, 15)
    if real_count == 3: 
        return random.randint(15, 17)
    if real_count == 4: 
        return random.randint(18, 20)
    
    return 0 

# --- Bingo Logic ---

def get_preset_card(card_number: int):
    random.seed(card_number)
    card_data = {
        'data': {
            'B': sorted(random.sample(range(1, 16), 5)),
            'I': sorted(random.sample(range(16, 31), 5)),
            'N': sorted(random.sample(range(31, 46), 5)), 
            'G': sorted(random.sample(range(46, 61), 5)),
            'O': sorted(random.sample(range(61, 76), 5)),
        },
        'marked': {(2, 2): True}, 'called': {(2, 2): True}, 
        'status': 'active', 'number': card_number
    }
    random.seed(time.time())
    return card_data

def get_card_value(card, col_idx, row_idx):
    if col_idx == 2 and row_idx == 2: return "FREE"
    col = COLUMNS[col_idx]
    arr = card['data'][col]
    # N column has the free space at (2,2). 
    if col == 'N':
        return arr[row_idx] if row_idx < 2 else arr[row_idx - 1] if row_idx > 2 else "FREE"
    return arr[row_idx]

def get_card_position(card, value):
    for c_idx, col in enumerate(COLUMNS):
        arr = card['data'][col]
        if col == 'N':
            # Check for value in N column, excluding the free space position
            if value in arr:
                idx = arr.index(value)
                return c_idx, idx if idx < 2 else idx + 1
        elif value in arr:
            return c_idx, arr.index(value)
    return None, None

def check_win(card):
    """Checks if a card has a complete BINGO line (5 marked)."""
    def is_marked(c, r): return card['marked'].get((c, r), False)
    for i in range(5):
        if all(is_marked(c, i) for c in range(5)): return True # Row
        if all(is_marked(i, r) for r in range(5)): return True # Col
    if all(is_marked(i, i) for i in range(5)): return True # Diag 1
    if all(is_marked(i, 4-i) for i in range(5)): return True # Diag 2
    return False

# --- Audio Helpers (TTS) ---
def create_wav_bytes(pcm_data: bytes, sample_rate: int = 24000) -> io.BytesIO:
    """Converts raw 16-bit PCM audio data into a playable WAV format stream."""
    buffer = io.BytesIO()
    data_size = len(pcm_data)
    num_channels = 1
    bits_per_sample = 16
    
    # WAV Header
    buffer.write(b'RIFF')
    buffer.write(struct.pack('<I', 36 + data_size))
    buffer.write(b'WAVE')
    
    # fmt chunk
    buffer.write(b'fmt ')
    buffer.write(struct.pack('<I', 16))          # Chunk size
    buffer.write(struct.pack('<H', 1))           # Audio format (1 = PCM)
    buffer.write(struct.pack('<H', num_channels))  # Number of channels
    buffer.write(struct.pack('<I', sample_rate))  # Sample rate
    buffer.write(struct.pack('<I', sample_rate * num_channels * bits_per_sample // 8)) # Byte rate
    buffer.write(struct.pack('<H', num_channels * bits_per_sample // 8)) # Block align
    buffer.write(struct.pack('<H', bits_per_sample))  # Bits per sample
    
    # data chunk
    buffer.write(b'data')
    buffer.write(struct.pack('<I', data_size))   # Data size
    buffer.write(pcm_data)                       # PCM data
    
    buffer.seek(0)
    return buffer

async def call_gemini_tts(text: str) -> io.BytesIO | None:
    """Calls the Gemini TTS API and returns a WAV audio stream, using Amharic."""
    if not requests: 
        logger.warning("TTS skipped: 'requests' module is missing. Install using: pip install requests")
        return None
    
    if not GEMINI_API_KEY:
        # User reported this error. This indicates an environment configuration issue.
        logger.error("TTS skipped: GEMINI_API_KEY is not set. Please configure the environment variable.")
        return None

    # Extract the number from the format 'L-N' (e.g., B-12 -> 12)
    try:
        num = int(text.split('-')[1])
        amharic_word = AMHARIC_NUMBERS.get(num, str(num))
        # PROMPT STRUCTURE: Keeps English call (for letter) and Amharic for number.
        tts_prompt = f"Say clearly: {text}. In Amharic: {amharic_word}" 
    except (IndexError, ValueError):
        tts_prompt = f"Say clearly: {text}."

    payload = {
        "contents": [{"parts": [{"text": tts_prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"], 
            # Use 'Kore' voice explicitly
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}
        },
        "model": "gemini-2.5-flash-preview-tts"
    }

    # Added robust retry logic using exponential backoff for the synchronous requests call
    MAX_RETRIES = 3
    DELAY = 1
    for attempt in range(MAX_RETRIES):
        try:
            # Use asyncio.to_thread for the synchronous requests call
            response = await asyncio.to_thread(lambda: requests.post(
                TTS_URL, 
                headers={'Content-Type': 'application/json'}, 
                data=json.dumps(payload), 
                timeout=8
            ))
            
            if response.status_code == 200:
                data = response.json()
                candidate = data.get('candidates', [{}])[0]
                part = candidate.get('content', {}).get('parts', [{}])[0]
                
                if 'inlineData' in part and part['inlineData'].get('data'):
                    pcm = base64.b64decode(part['inlineData']['data'])
                    return create_wav_bytes(pcm)
                else:
                    logger.error(f"TTS API returned 200 but missing audio data: {data}")
            else:
                logger.error(f"TTS API call failed with status {response.status_code}: {response.text}")
            
        except Exception as e:
            logger.error(f"TTS API call error on attempt {attempt + 1}: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(DELAY * (2 ** attempt))
            else:
                logger.error("TTS failed after all retries.")
                return None
        
        # If response was successful but didn't return audio, still retry if status code allows
        if response and response.status_code == 200:
            break
            
    return None

# --- Amharic Numbers ---
AMHARIC_NUMBERS = {
    1: "አንድ", 2: "ሁለት", 3: "ሶስት", 4: "አራት", 5: "አምስት", 6: "ስድስት", 7: "ሰባት", 8: "ስምንት", 9: "ዘጠኝ", 10: "አስር",
    11: "አስራ አንድ", 12: "አስራ ሁለት", 13: "አስራ ሶስት", 14: "አስራ አራት", 15: "አስራ አምስት", 16: "አስራ ስድስት", 17: "አስራ ሰባት", 18: "አስራ ስምንት", 19: "አስራ ዘጠኝ", 20: "ሃያ",
    21: "ሃያ አንድ", 22: "ሃያ ሁለት", 23: "ሃያ ሶስት", 24: "ሃያ አራት", 25: "ሃያ አምስት", 26: "ሃያ ስድስት", 27: "ሃያ ሰባት", 28: "ሃያ ስምንት", 29: "ሃያ ዘጠኝ", 30: "ሰላሳ",
    31: "ሰላሳ አንድ", 32: "ሰላሳ ሁለት", 33: "ሰላሳ ሶስት", 34: "ሰላሳ አራት", 35: "ሰላሳ አምስት", 36: "ሰላሳ ስድስት", 37: "ሰላሳ ሰባት", 38: "ሰላሳ ስምንት", 39: "ሰላሳ ዘጠኝ", 40: "አርባ",
    41: "አርባ አንድ", 42: "አርባ ሁለት", 43: "አርባ ሶስት", 44: "አርባ አራት", 45: "አርባ አምስት", 46: "አርባ ስድስት", 47: "አርባ ሰባት", 48: "አርባ ስምንት", 49: "ሃምሳ", 50: "ሃምሳ",
    51: "ሃምሳ አንድ", 52: "ሃምሳ ሁለት", 53: "ሃምሳ ሶስት", 54: "ሃምሳ አራት", 55: "ሃምሳ አምስት", 56: "ሃምሳ ስድስት", 57: "ሃምሳ ሰባት", 58: "ሃምሳ ስምንት", 59: "ሃምሳ ዘጠኝ", 60: "ስልሳ",
    61: "ስልሳ አንድ", 62: "ስልሳ ሁለት", 63: "ስልሳ ሶስት", 64: "ስልሳ አራት", 65: "ስልሳ አምስት", 66: "ስልሳ ስድስት", 67: "ስልሳ ሰባት", 68: "ስልሳ ስምንት", 69: "ስልሳ ዘጠኝ", 70: "ሰባ",
    71: "ሰባ አንድ", 72: "ሰባ ሁለት", 73: "ሰባ ሶስት", 74: "ሰባ አራት", 75: "ሰባ አምስት"
}

# --- UI & Text ---
def build_card_keyboard(card, game_id, msg_id):
    keyboard = []
    # Compact Header (B I N G O)
    keyboard.append([InlineKeyboardButton(c, callback_data="ignore") for c in COLUMNS])
    
    for r in range(5):
        row = []
        for c in range(5):
            val = get_card_value(card, c, r)
            pos = (c, r)
            is_marked = card['marked'].get(pos, False)
            is_called = card['called'].get(pos, False)
            
            if val == "FREE": label = EMOJI_FREE
            elif is_marked: label = f"{EMOJI_MARKED} {val}" # ✅
            elif is_called: label = f"{EMOJI_CALLED_UNMARKED} {val}" # 🟢
            else: label = f"{EMOJI_UNMARKED_UNCALLED} {val}" # 🔴
            
            cb = f"MARK|{game_id}|{msg_id}|{card['number']}|{c}|{r}" if val != "FREE" else "ignore"
            row.append(InlineKeyboardButton(label, callback_data=cb))
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🚨 CALL BINGO! 🚨", callback_data=f"BINGO|{game_id}|{msg_id}|{card['number']}")])
    return InlineKeyboardMarkup(keyboard)

def get_board_display_text(current_call_text: str, called_history: list) -> str:
    """
    Displays the current call prominently and only the immediate previous call.
    """
    
    # 1. Previous Call (Second to last number in the full history)
    previous_call_text = "የለም"
    if len(called_history) >= 2:
        last_called_num = called_history[-2] # Second to last is the previous call
        col = COLUMNS[(last_called_num - 1) // 15]
        previous_call_text = f"{col}-{last_called_num}"
    
    history_display = f"{EMOJI_HISTORY} **የቀድሞ ጥሪ:** *{previous_call_text}*"
        
    # 2. Current Call (Prominent and Single-line)
    current_call_display = f"{EMOJI_CALL} **አሁን የሚጠራ ቁጥር:** ***{current_call_text}***"

    # Simplified display format
    return (
        f"**{history_display}**\n\n"
        f"**{current_call_display}**"
    )


# --- Core Game ---

async def start_new_game(context: ContextTypes.DEFAULT_TYPE):
    global LOBBY_STATE
    players_data = list(PENDING_PLAYERS.items())
    real_pids = [pid for pid, _ in players_data]
    
    if not real_pids:
        LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}
        return

    game_id = f"G{int(time.time())}"
    
    total_target = get_total_players_target(len(real_pids))
    bots_needed = total_target - len(real_pids)
    bot_players = {}
    
    used_cards = [num for _, num in players_data]
    pool = [c for c in range(1, MAX_PRESET_CARDS+1) if c not in used_cards]

    for _ in range(bots_needed):
        bid, bname = create_bot_player()
        if not pool: break
        cnum = random.choice(pool)
        pool.remove(cnum)
        bot_players[bid] = {'name': bname, 'card': get_preset_card(cnum)}
    
    game_data = {
        'players': real_pids,
        'player_cards': {pid: get_preset_card(num) for pid, num in players_data},
        'card_messages': {pid: None for pid in real_pids},
        'board_messages': {},
        'called': [],
        'status': 'running',
        'bot_players': bot_players,
        'total_pot': total_target * CARD_COST,
        'total_players_announced': total_target,
        'winning_bot_id': None,
        'bot_win_call_index': None,
        'bot_win_delay_counter': 0
    }
    
    # Clear pending players
    for pid in real_pids: del PENDING_PLAYERS[pid]
    ACTIVE_GAMES[game_id] = game_data
    LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}

    # Announce start with the TOTAL player count (Stealth)
    player_announcement = f"👥 ጠቅላላ ተጫዋቾች: **{game_data['total_players_announced']}**"
    for pid in real_pids:
        await context.bot.send_message(pid, f"✅ **ጨዋታው ተጀምሯል!**\n{player_announcement}", parse_mode='Markdown')

    asyncio.create_task(run_game_loop(context, game_id, real_pids, bot_players))

async def run_game_loop(context, game_id, real_pids, bot_players):
    g = ACTIVE_GAMES[game_id]
    
    # Referral Bonus Check
    for pid in real_pids:
        # Note: pay_referrer_bonus is a sync function accessing Firestore, using asyncio.to_thread
        await asyncio.to_thread(pay_referrer_bonus, pid)
        
    all_possible_nums = list(range(1, 76))
    
    # --- V32 FIX: Robust Sequence Generation (Guarantees full 75 calls in stealth mode) ---
    is_stealth_mode = len(g['players']) < MIN_REAL_PLAYERS_FOR_ORGANIC_GAME
    final_sequence = []

    if is_stealth_mode and bot_players:
        winning_bot_id = list(bot_players.keys())[0]
        g['winning_bot_id'] = winning_bot_id
        w_card = bot_players[winning_bot_id]['card']
        
        # The bot will win on a row (e.g., first row, col 0-4)
        win_nums = [get_card_value(w_card, c, 0) for c in range(5) if c != 2 or w_card['data'].get('N')]
        win_nums = [x for x in win_nums if x != "FREE"] # 5 winning numbers
        
        # 1. Decide the win timing (more calls for a longer game)
        total_calls_at_win = random.randint(20, 30) 
        
        # 2. Get other numbers (75 - 5 = 70)
        other_nums = [n for n in all_possible_nums if n not in win_nums]
        random.shuffle(other_nums)
        
        # 3. Create the winning call index list (includes the 5 win_nums)
        # We need (total_calls_at_win - 5) non-win numbers for the initial block
        initial_non_win_count = max(0, total_calls_at_win - 5)
        
        win_sequence_block = other_nums[:initial_non_win_count] + win_nums
        random.shuffle(win_sequence_block)
        
        # 4. Determine the actual index of the last winning number in the shuffled block
        last_win_num = win_nums[-1] 
        g['bot_win_call_index'] = win_sequence_block.index(last_win_num) + 1 # +1 for 1-based index
        g['bot_win_delay_counter'] = BOT_WIN_DELAY_CALLS 
        
        # 5. Final sequence is the win block + the rest of the numbers (guarantees 75 calls)
        remaining_non_win = other_nums[initial_non_win_count:]
        
        # The final sequence contains all 75 numbers, shuffled.
        final_sequence = win_sequence_block + remaining_non_win
        logger.info(f"Stealth game length: {len(final_sequence)}. Bot wins at call {g['bot_win_call_index']} (plus delay).")
        
    else:
        # Organic game (5+ players): No bots, regular shuffle
        random.shuffle(all_possible_nums)
        final_sequence = all_possible_nums
        g['winning_bot_id'] = None
    
    # Init Messages - Board (Top) and Card (Bottom)
    for pid in real_pids:
        # 1. Send Board Message (Initial state)
        initial_board_text = get_board_display_text("ጨዋታው ሊጀመር ነው...", [])
        bm = await context.bot.send_message(pid, initial_board_text, parse_mode='Markdown')
        g['board_messages'][pid] = bm.message_id
        
        # 2. Send Card Message (The interactive card)
        card = g['player_cards'][pid]
        kb = build_card_keyboard(card, game_id, bm.message_id) # Use board_msg_id temporarily
        cm = await context.bot.send_message(pid, f"{EMOJI_CARD} **Card #{card['number']}**", reply_markup=kb, parse_mode='Markdown')
        g['card_messages'][pid] = cm.message_id
        
        # Update callback data on card keyboard to use the correct card message ID
        kb = build_card_keyboard(card, game_id, cm.message_id)
        await context.bot.edit_message_reply_markup(chat_id=pid, message_id=cm.message_id, reply_markup=kb)

    await asyncio.sleep(2) # Initial pause

    for i, num in enumerate(final_sequence, 1):
        if g['status'] != 'running': break
        
        g['called'].append(num)
        col = COLUMNS[(num-1)//15]
        call_text = f"{col}-{num}"
        
        # 1. Update internal card states (real players and bots)
        for pid in real_pids:
            c_pos = get_card_position(g['player_cards'][pid], num)
            if c_pos[0] is not None: g['player_cards'][pid]['called'][c_pos] = True
            
        if bot_players:
            for bid, bdata in bot_players.items():
                c_pos = get_card_position(bdata['card'], num)
                if c_pos[0] is not None: 
                    bdata['card']['called'][c_pos] = True
                    # Bots mark called numbers immediately
                    bdata['card']['marked'][c_pos] = True 

        # 2. TTS Audio Call
        audio = await call_gemini_tts(call_text)
        
        # 3. Update Board Message (Current call and limited history)
        board_text = get_board_display_text(call_text, g['called'])

        for pid in real_pids:
            # Board (Calling and History)
            try: await context.bot.edit_message_text(chat_id=pid, message_id=g['board_messages'][pid], text=board_text, parse_mode='Markdown')
            except: pass
            
            # Send the call text message and audio
            if audio:
                try: 
                    audio.seek(0)
                    # Removed caption to stop repetitive text listing, voice remains.
                    await context.bot.send_voice(pid, InputFile(audio, filename='bingo_call.wav')) 
                except Exception as e: 
                    logger.error(f"Failed to send voice: {e}")
            else:
                # Fallback: send text if TTS fails
                await context.bot.send_message(pid, f"🗣️ **የተጠራ ቁጥር:** {call_text}", parse_mode='Markdown')
            
            # Card (Refresh for green highlighting)
            card = g['player_cards'][pid]
            kb = build_card_keyboard(card, game_id, g['card_messages'][pid])
            try: await context.bot.edit_message_reply_markup(chat_id=pid, message_id=g['card_messages'][pid], reply_markup=kb)
            except: pass

        # 4. Check Bot Win (with Stealth Delay and CRITICAL TIMING ENFORCEMENT)
        if is_stealth_mode and g['winning_bot_id']:
            winner_card = bot_players[g['winning_bot_id']]['card']
            
            if i >= g['bot_win_call_index']:
                if g['bot_win_delay_counter'] <= 0:
                    if check_win(winner_card):
                        await finalize_win(context, game_id, g['winning_bot_id'], True)
                        return # Game ends immediately on bot win

                # Decrement delay counter only if the bot's call index is met
                if g['bot_win_delay_counter'] > 0:
                    g['bot_win_delay_counter'] -= 1
                    logger.info(f"Bot win delay remaining: {g['bot_win_delay_counter']}")

        # 5. Check Real Player Win (ONLY in Organic Game Mode)
        if not is_stealth_mode:
            # ORGANIC GAME: Real player can win and stop the loop early
            for pid in real_pids:
                if check_win(g['player_cards'][pid]):
                    await finalize_win(context, game_id, pid, False)
                    return # Game ends immediately

        await asyncio.sleep(CALL_DELAY)

    if g['status'] == 'running':
        # If the loop finished (all 75 numbers called) and no one won
        await finalize_win(context, game_id, None, False)


async def finalize_win(context, game_id, winner_id, is_bot=False):
    g = ACTIVE_GAMES.get(game_id)
    if not g or g['status'] != 'running': return
    g['status'] = 'finished'
    
    total = g['total_pot']
    revenue = total * GLOBAL_CUT_PERCENT
    prize = total * WINNER_SHARE_PERCENT
    
    # 1. Update stats for all real players
    for pid in g['players']:
        # update_game_stats is sync, wrapping it
        await asyncio.to_thread(update_game_stats, pid, is_win= (pid == winner_id and not is_bot) )
        
    if winner_id is None:
        # V32 IMPROVEMENT: Prominent "No Winner" message
        msg = (f"💔 **ቢንጎ የለም! (NO BINGO)** 💔\n\n"
               f"በዚህ ዙር ማንም ቢንጎ አላገኘም።\n"
               f"💰 የሽልማት ገንዘብ: **{total:.2f} ብር** ቀጣይ ጨዋታ ይዞ ይቀጥላል።\n\n"
               f"**አዲሱን ጨዋታ ለመጀመር /play ይጫኑ!**")
    elif is_bot:
        w_name = g['bot_players'][winner_id]['name']
        # V31: Make announcement more visible
        msg = (f"🎉🎉 **BINGO WINNER!** 🎉🎉\n\n"
               f"👤 አሸናፊ: **{w_name}** (Bot)\n"
               f"💰 ሽልማት: **{prize:.2f} ብር**\n"
               f"📉 የቤት ቅነሳ: {revenue:.2f} ብር\n\n"
               f"ጨዋታው ተጠናቋል። **አዲሱን ጨዋታ ለመጀመር /play ይጫኑ!**")
    else:
        # Real player win (ONLY possible in organic mode or if bot win fails)
        data = await get_user_data(winner_id) # Use await
        w_name = f"{data.get('first_name')} (ID: {winner_id})"
        # Update balance and log transaction (Balance integrity maintained)
        await asyncio.to_thread(update_balance, winner_id, prize, transaction_type='Win', description=f"Bingo prize for game {game_id}")
        
        # V31: Make announcement more visible
        msg = (f"🌟🏆 **BIG BINGO WINNER!!!** 🏆🌟\n\n"
               f"🥳 **እውነተኛ አሸናፊ (REAL WINNER):** **{w_name}**\n"
               f"💰 **ትልቅ ሽልማት (GRAND PRIZE):** **{prize:.2f} ብር** (ወደ ሒሳብዎ ገብቷል)\n\n"
               f"ጨዋታው ተጠናቋል። **አዲሱን ጨዋታ ለመጀመር /play ይጫኑ!**")
            
    # 3. Clean up display and send final message
    for pid in g['players']:
        # Delete Board Message 
        try:
            await context.bot.delete_message(chat_id=pid, message_id=g['board_messages'][pid])
        except Exception as e:
            logger.warning(f"Could not delete board message for {pid}: {e}")

        # Send final win/loss message
        await context.bot.send_message(pid, msg, parse_mode='Markdown')

    del ACTIVE_GAMES[game_id]


# --- Handlers ---
async def start(u, c): 
    # Check for referral parameter
    referrer_id = None
    if c.args and c.args[0].isdigit():
        referrer_id = c.args[0]
    
    create_or_update_user(u.effective_user.id, u.effective_user.username, u.effective_user.first_name, referrer_id)
    
    msg = (
        "👋 **ወደ አዲስ ቢንጎ እንኳን ደህና መጡ!**\n\n"
        "🃏 **አዲስ የጨዋታ ህጎች:**\n"
        f"1. **የመጫወቻ ዋጋ:** እያንዳንዱ የቢንጎ ካርድ **{CARD_COST:.2f} ብር** ነው።\n"
        f"2. **አሸናፊነት (ቢንጎ):** በካርዱ ላይ አግድም፣ ቁመት ወይም ሰያፍ (Diagonal) አምስት ቁጥሮች ሲሞሉ ቢንጎ ይሆናል።\n\n"
        
        "### 📜 **ተጨማሪ መመሪያዎች:**\n"
        
        "#### **A. የቢንጎ ካርድ አጠቃቀም**\n"
        "• **የቁጥር ምልክት:** ቁጥሩ **ከተጠራ** በኋላ በካርድዎ ላይ ያለውን ቁጥር በመጫን **ምልክት (✅)** ማድረግ አለብዎት።\n"
        "• **አረንጓዴ/ቀይ:** አረንጓዴ (🟢) የተጠሩ ቁጥሮች ሲሆኑ ገና ምልክት ያላደረጉባቸው ናቸው። ቀይ (🔴) ደግሞ ገና ያልተጠሩ ቁጥሮች ናቸው።\n"
        "• **ነጻ ቦታ (🌟):** በማዕከሉ ላይ ያለው ኮከብ (🌟) ሁልጊዜም እንደተሞላ ይቆጠራል።\n"
        "• **ቢንጎ መጥራት:** አምስት ምልክት የተደረገባቸው ካሬዎች በተከታታይ (በየትኛውም አቅጣጫ) ሲኖሩዎት **🚨 CALL BINGO! 🚨** የሚለውን ቁልፍ ይጫኑ።\n\n"
        
        "#### **B. የገንዘብ ሕጎች**\n"
        f"• **ዝቅተኛ ማስገቢያ:** ለመጫወት ገንዘብ ሲያስገቡ **ቢያንስ {MIN_DEPOSIT:.2f} ብር** መሆን አለበት። (/deposit)\n"
        f"• **ዝቅተኛ ማውጣት:** ገንዘብ ለማውጣት በሒሳብዎ ላይ **ቢያንስ {MIN_WITHDRAW:.2f} ብር** ሊኖር ይገባል። (/withdraw)\n"
        f"• **የሽልማት ድርሻ:** አሸናፊው ከጠቅላላው ሽልማት **{WINNER_SHARE_PERCENT * 100:.0f}%** ያገኛል።\n"
        f"• **የሪፈራል ቦነስ:** ጓደኛዎን ሲጋብዙ **{REFERRAL_BONUS:.2f} ብር** ያገኛሉ። (/refer)\n\n"
        
        "👇 **ዋና ዋና ትዕዛዞች:**\n"
        "/play - ቢንጎ ካርድ ይግዙና ጨዋታውን ይቀላቀሉ\n"
        "/quickplay - ፈጣን የካርድ ግዢ እና ጨዋታ\n"
        "/deposit - ገንዘብ ለማስገባት\n"
        "/balance - የአሁኑን ቀሪ ሒሳብዎን ለማየት\n"
        "/withdraw - ገንዘብ ለማውጣት\n"
        "/rules - ሁሉንም መመሪያዎች በዝርዝር ለማየት"
    )
    
    await u.message.reply_text(msg, parse_mode='Markdown')

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    bal = (await get_user_data(user_id)).get('balance', 0.00) # Use await for correctness
    msg = f"💳 **የእርስዎ ቀሪ ሒሳብ (/balance):**\n\n**{bal:.2f} ብር**"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def ap_dep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Admin only command for top-up
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
    
    if len(context.args) != 2:
        await update.message.reply_text("አጠቃቀም: /ap_dep [የተጠቃሚ_ID] [መጠን]")
        return
        
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        
        # Use update_balance with transaction logging (Atomic update)
        await asyncio.to_thread(update_balance, target_id, amount, transaction_type='Admin Deposit', description=f"Admin top-up by {update.effective_user.id}")
        
        # Notify the admin
        target_data = await get_user_data(target_id)
        target_name = target_data.get('first_name', f"User {target_id}")
        
        await update.message.reply_text(
            f"✅ **ስኬት!**\n"
            f"👤 ለተጠቃሚ {target_name} ({target_id}): **{amount:.2f} ብር** ተጨምሯል።",
            parse_mode='Markdown'
        )
    except ValueError:
        await update.message.reply_text("⛔ የመጠን ወይም የተጠቃሚ መታወቂያ ትክክለኛ ቅርጸት አይደለም።")
    except Exception as e:
        logger.error(f"Admin deposit error: {e}")
        await update.message.reply_text(f"⛔ ያልተጠበቀ ስህተት ተፈጥሯል: {e}")
        
async def enter_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    balance = user_data.get('balance', 0.00)

    if ACTIVE_GAMES:
        await update.message.reply_text("⚠️ **አሁን የሚካሄድ ጨዋታ አለ።** ጨዋታው እስኪያልቅ ድረስ መሳተፍ አይችሉም።")
        return

    if balance < CARD_COST:
        await update.message.reply_text(f"❌ ለመጫወት በቂ ሒሳብ የለዎትም። የካርድ ዋጋ **{CARD_COST:.2f} ብር** ነው። እባክዎ /deposit ይጠቀሙ።")
        return

    if user_id in PENDING_PLAYERS:
        await update.message.reply_text("⚠️ **ቀድሞውንም ተመዝግበዋል።** ጨዋታው እስኪጀመር ይጠብቁ።")
        return

    # Check for available card number
    used_cards = list(PENDING_PLAYERS.values())
    pool = [c for c in range(1, MAX_PRESET_CARDS + 1) if c not in used_cards]

    if not pool:
        await update.message.reply_text("❌ ይቅርታ፣ ሁሉም ካርዶች ጥቅም ላይ ውለዋል። እባክዎ በኋላ ይሞክሩ።")
        return

    card_number = random.choice(pool)
    PENDING_PLAYERS[user_id] = card_number
    
    # Deduct cost and log transaction
    await asyncio.to_thread(update_balance, user_id, -CARD_COST, transaction_type='Card Purchase', description=f"Purchase card #{card_number}")
    
    remaining_balance = balance - CARD_COST
    
    msg = (f"✅ **Card #{card_number} ተገዝቷል!**\n"
           f"💳 **ዋጋ:** {CARD_COST:.2f} ብር\n"
           f"💰 **የቀረ ሒሳብ:** {remaining_balance:.2f} ብር\n\n"
           f"👥 **ተጫዋቾች:** {len(PENDING_PLAYERS)}\n\n"
           f"ጨዋታው በቅርቡ ይጀምራል። እባክዎ ይጠብቁ።")
           
    # Update lobby message or send a new one
    if not LOBBY_STATE['is_running']:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 ጨዋታውን አሁን ጀምር", callback_data="START_GAME")]])
        
        try:
            if LOBBY_STATE['msg_id'] and LOBBY_STATE['chat_id']:
                await context.bot.edit_message_text(
                    chat_id=LOBBY_STATE['chat_id'],
                    message_id=LOBBY_STATE['msg_id'],
                    text=msg,
                    reply_markup=kb,
                    parse_mode='Markdown'
                )
            else:
                lobby_msg = await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
                LOBBY_STATE['is_running'] = True
                LOBBY_STATE['msg_id'] = lobby_msg.message_id
                LOBBY_STATE['chat_id'] = update.effective_chat.id
        except:
            # Fallback if editing fails
            lobby_msg = await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
            LOBBY_STATE['is_running'] = True
            LOBBY_STATE['msg_id'] = lobby_msg.message_id
            LOBBY_STATE['chat_id'] = update.effective_chat.id
    
    # If a player buys a card and the game is already in lobby, just update the player
    await update.message.reply_text(msg, parse_mode='Markdown')

async def quickplay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Quickplay just calls enter_lobby as the logic is the same
    await enter_lobby(update, context)
    
async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    referral_link = f"https://t.me/{context.bot.username}?start={user_id}"
    
    msg = (f"🔗 **ጓደኞችዎን ይጋብዙና ገንዘብ ያግኙ!**\n\n"
           f"ይህንን ማገናኛ በመጠቀም ጓደኛዎ ሲመዘገብ እና የመጀመሪያውን ጨዋታ ሲጫወት፣ እርስዎ **{REFERRAL_BONUS:.2f} ብር** ጉርሻ ያገኛሉ!\n\n"
           f"**የእርስዎ መጋበዣ ማገናኛ (Referral Link):**\n`{referral_link}`\n\n"
           f"ይላኩትና መሸለም ይጀምሩ!")
           
    await update.message.reply_text(msg, parse_mode='Markdown')


# --- Deposit Conversation Flow ---

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = (f"**💰 ገንዘብ ለማስገባት (Deposit)**\n\n"
           f"እባክዎ ማስገባት የሚፈልጉትን **መጠን በብር** ያስገቡ።\n"
           f"**ማስታወሻ:** ዝቅተኛው ማስገቢያ **{MIN_DEPOSIT:.2f} ብር** ነው።")
    await update.message.reply_text(msg, parse_mode='Markdown')
    return GET_CARD_NUMBER # Using GET_CARD_NUMBER state for amount input

async def get_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        if amount < MIN_DEPOSIT:
            await update.message.reply_text(f"❌ ማስገባት የሚቻለው **ቢያንስ {MIN_DEPOSIT:.2f} ብር** ነው። ትክክለኛውን መጠን ያስገቡ።")
            return GET_CARD_NUMBER
            
        # Store amount in conversation context
        context.user_data['deposit_amount'] = amount
        
        msg = (f"✅ **የማስገቢያ ጥያቄ:**\n"
               f"**መጠን:** {amount:.2f} ብር\n"
               f"**የመክፈያ ዘዴ:** Telebirr\n\n"
               f"እባክዎ **{amount:.2f} ብር** ወደ **Telebirr ቁጥር: `{TELEBIRR_ACCOUNT}`** ይላኩና ክፍያውን የሚያረጋግጥ **የክፍያ ኮድ (Transaction ID)** በዚሁ መልስ ይላኩልኝ።\n"
               f"ከላኩ በኋላ **'ክፍያ ተፈጸመ'** የሚለውን ቁልፍ ይጫኑ።")
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("ክፍያ ተፈጸመ", callback_data="CONFIRM_DEPOSIT")]])
        await update.message.reply_text(msg, reply_markup=kb, parse_mode='Markdown')
        
        return GET_DEPOSIT_CONFIRMATION # Wait for confirmation message or button press
        
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ የብር መጠን አላስገቡም። እባክዎ እንደ '50.00' ያለ ቁጥር ያስገቡ።")
        return GET_CARD_NUMBER

async def get_deposit_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    
    if query and query.data == "CONFIRM_DEPOSIT":
        # Request the transaction ID after button press
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🙏 **ክፍያውን የሚያረጋግጥ የግብይት መታወቂያ (Transaction ID) ወይም የክፍያ ማረጋገጫ መልዕክት ይላኩልኝ።**"
        )
        return GET_DEPOSIT_CONFIRMATION # Stay in this state, waiting for the ID
    
    # Handle the incoming message which should be the transaction ID
    if update.message and update.message.text:
        tx_id = update.message.text.strip()
        amount = context.user_data.get('deposit_amount')
        user_id = update.effective_user.id
        
        # Admin Notification (Simulated Manual Confirmation)
        admin_msg = (f"🚨 **አዲስ የማስገቢያ ጥያቄ**\n"
                     f"**ተጠቃሚ ID:** {user_id}\n"
                     f"**መጠን:** {amount:.2f} ብር\n"
                     f"**የግብይት ID:** {tx_id}\n\n"
                     f"እባክዎ ክፍያውን ያረጋግጡና **{user_id}** ን በመጠቀም ገንዘቡን ይጨምሩ።\n"
                     f"አጠቃቀም: `/ap_dep {user_id} {amount:.2f}`")
        
        if ADMIN_USER_ID:
            await context.bot.send_message(ADMIN_USER_ID, admin_msg, parse_mode='Markdown')
        
        # User confirmation
        await update.message.reply_text(
            "⏳ **አመሰግናለሁ!** የክፍያ መታወቂያው ደርሶናል። አስተዳዳሪው ክፍያዎን አረጋግጦ ሒሳብዎን በቅርቡ ይጨምራል። በትዕግስት ይጠብቁ።",
            parse_mode='Markdown'
        )
        
        # Clean up conversation state
        context.user_data.pop('deposit_amount', None)
        return ConversationHandler.END
        
    # If something unexpected happens, cancel
    await update.message.reply_text("⛔ ያልተጠበቀ ስህተት ተፈጥሯል። እባክዎ እንደገና ይሞክሩ። /deposit")
    return ConversationHandler.END


# --- Withdraw Conversation Flow ---

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    balance = user_data.get('balance', 0.00)
    
    if balance < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ ገንዘብ ለማውጣት **ቢያንስ {MIN_WITHDRAW:.2f} ብር** በሂሳብዎ ላይ ሊኖር ይገባል። የአሁኑ ሒሳብዎ: **{balance:.2f} ብር**")
        return ConversationHandler.END
        
    context.user_data['balance'] = balance
    
    msg = (f"**💸 ገንዘብ ለማውጣት (Withdraw)**\n\n"
           f"**የእርስዎ ቀሪ ሒሳብ:** {balance:.2f} ብር\n"
           f"እባክዎ ማውጣት የሚፈልጉትን **መጠን በብር** ያስገቡ።\n"
           f"**ማስታወሻ:** ዝቅተኛው ማውጣት **{MIN_WITHDRAW:.2f} ብር** ነው።")
           
    await update.message.reply_text(msg, parse_mode='Markdown')
    return GET_WITHDRAW_AMOUNT

async def get_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        balance = context.user_data['balance']
        
        if amount < MIN_WITHDRAW:
            await update.message.reply_text(f"❌ ማውጣት የሚቻለው **ቢያንስ {MIN_WITHDRAW:.2f} ብር** ነው።")
            return GET_WITHDRAW_AMOUNT
            
        if amount > balance:
            await update.message.reply_text(f"❌ በቂ ሒሳብ የለዎትም። ከ**{balance:.2f} ብር** በላይ ማውጣት አይችሉም።")
            return GET_WITHDRAW_AMOUNT
            
        context.user_data['withdraw_amount'] = amount
        await update.message.reply_text("✅ እባክዎ ገንዘቡ እንዲላክልዎት የሚፈልጉትን **Telebirr ስልክ ቁጥር** ያስገቡ።")
        return GET_TELEBIRR_ACCOUNT
        
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ የብር መጠን አላስገቡም። እባክዎ እንደ '100.00' ያለ ቁጥር ያስገቡ።")
        return GET_WITHDRAW_AMOUNT

async def get_telebirr_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    telebirr_account = update.message.text.strip()
    amount = context.user_data['withdraw_amount']
    user_id = update.effective_user.id
    
    if not telebirr_account.isdigit() or len(telebirr_account) not in [9, 10]:
        await update.message.reply_text("❌ ትክክለኛ ያልሆነ የስልክ ቁጥር ቅርጽ። እባክዎ የቴሌብር ቁጥርዎን እንደገና ያስገቡ (ለምሳሌ 09XXXXXXXX)።")
        return GET_TELEBIRR_ACCOUNT
        
    # Deduct amount immediately to secure the funds
    await asyncio.to_thread(update_balance, user_id, -amount, transaction_type='Withdrawal Pending', description=f"Withdrawal request to {telebirr_account}")
    
    # Admin Notification
    admin_msg = (f"🚨 **አዲስ የማውጣት ጥያቄ**\n"
                 f"**ተጠቃሚ ID:** {user_id}\n"
                 f"**መጠን:** {amount:.2f} ብር\n"
                 f"**Telebirr:** `{telebirr_account}`\n\n"
                 f"በመውጣት ሂደት ላይ: ተጠቃሚው ቀድሞውንም ተቀንሷል።")
    
    if ADMIN_USER_ID:
        await context.bot.send_message(ADMIN_USER_ID, admin_msg, parse_mode='Markdown')
        
    await update.message.reply_text(
        f"⏳ **የማውጣት ጥያቄዎ ደርሶናል!**\n"
        f"**መጠን:** {amount:.2f} ብር\n"
        f"**Telebirr ቁጥር:** {telebirr_account}\n\n"
        f"ገንዘብዎ በቅርቡ ወደ ቴሌብር አካውንትዎ ይላካል። ለትዕግስትዎ እናመሰግናለን!",
        parse_mode='Markdown'
    )
    
    # Clean up context
    context.user_data.pop('withdraw_amount', None)
    context.user_data.pop('balance', None)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text('❌ **ሂደቱ ተሰርዟል።**', parse_mode='Markdown')
    # Clear any pending context data
    context.user_data.pop('deposit_amount', None)
    context.user_data.pop('withdraw_amount', None)
    context.user_data.pop('balance', None)
    return ConversationHandler.END


# --- Callback Query Handler (MARK/BINGO) ---

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('|')
    action = data[0]
    user_id = query.from_user.id
    
    if action == "ignore":
        return
    
    if action == "START_GAME":
        if LOBBY_STATE['is_running'] and len(PENDING_PLAYERS) > 0:
            await context.bot.edit_message_text(
                chat_id=LOBBY_STATE['chat_id'],
                message_id=LOBBY_STATE['msg_id'],
                text="🚀 **ጨዋታው በግዳጅ እየተጀመረ ነው...**",
                parse_mode='Markdown'
            )
            await start_new_game(context)
        return
        
    if action == "CONFIRM_DEPOSIT":
        # Pass the query update to the deposit confirmation step
        await get_deposit_confirmation(update, context)
        return

    # Game actions (MARK, BINGO)
    if len(data) < 4: return # Basic check for game actions
    
    game_id, msg_id, card_num = data[1:4]
    
    if game_id not in ACTIVE_GAMES:
        await query.edit_message_caption("❌ ይህ ጨዋታ ተጠናቅቋል። /play በመጠቀም አዲስ ጨዋታ ይጀምሩ።")
        return

    g = ACTIVE_GAMES[game_id]
    if user_id not in g['players']:
        await query.edit_message_caption("❌ የዚህ ጨዋታ ተጫዋች አይደሉም።")
        return

    card = g['player_cards'][user_id]
    
    if action == "MARK":
        if len(data) != 6: return
        c, r = int(data[4]), int(data[5])
        pos = (c, r)
        val = get_card_value(card, c, r)
        
        if pos not in card['called']:
            await query.edit_message_caption("❌ ይህ ቁጥር ገና አልተጠራም።")
            return
            
        if card['marked'].get(pos, False):
            # User un-marks a number
            del card['marked'][pos]
        else:
            # User marks a number
            card['marked'][pos] = True
            
        # Rebuild and edit the keyboard
        kb = build_card_keyboard(card, game_id, msg_id)
        try:
            await query.edit_message_reply_markup(reply_markup=kb)
        except Exception as e:
            logger.warning(f"Failed to edit card reply markup: {e}")
            
    elif action == "BINGO":
        if check_win(card):
            # Real player win (ONLY possible in organic mode or if bot win fails)
            g['status'] = 'finished' # Stop the game loop
            await query.edit_message_caption("🎉 **ቢንጎ!** አስተዳዳሪው እያረጋገጠ ነው።")
            # Finalize the win immediately
            await finalize_win(context, game_id, user_id, False)
        else:
            await context.bot.send_message(user_id, "❌ **ይቅርታ፣ ቢንጎ የለዎትም።** እባክዎ ካርድዎን በትክክል ያረጋግጡና እንደገና ይሞክሩ።")
            
# --- Main Function ---

def main() -> None:
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN is not set. The bot cannot run.")
        return

    application = Application.builder().token(TOKEN).build()

    # Conversation Handlers
    deposit_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_command)],
        states={
            GET_CARD_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deposit_amount)],
            GET_DEPOSIT_CONFIRMATION: [CallbackQueryHandler(get_deposit_confirmation, pattern='^CONFIRM_DEPOSIT$'),
                                       MessageHandler(filters.TEXT & ~filters.COMMAND, get_deposit_confirmation)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True
    )
    
    withdraw_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("withdraw", withdraw_command)],
        states={
            GET_WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_amount)],
            GET_TELEBIRR_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_telebirr_account)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True
    )

    # Command Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("rules", start)) # Rules is the same as start
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("play", enter_lobby))
    application.add_handler(CommandHandler("quickplay", quickplay_command))
    application.add_handler(CommandHandler("refer", refer_command))
    
    # Admin Handlers
    application.add_handler(CommandHandler("ap_dep", ap_dep))

    # Conversation Handlers
    application.add_handler(deposit_conv_handler)
    application.add_handler(withdraw_conv_handler)

    # Callback Handler (Game actions)
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Start the Bot
    application.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    main()
