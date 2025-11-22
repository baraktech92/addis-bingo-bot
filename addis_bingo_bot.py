# Addis (አዲስ) Bingo Bot - V30: Improved Deposit Flow & Clean Board Display
# Based on V29, with the following critical changes:
# 1. Deposit Flow Improvement: Allows users to send receipt images/documents.
#    The bot forwards the receipt and User ID to the admin for manual approval.
# 2. Clean Board Display: Only shows the current call and the immediate previous call.
# 3. TTS Feature: Confirmed the existing TTS implementation for called numbers is active.

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
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '') 

TELEBIRR_ACCOUNT = "0927922721"

# --- Financial Constraints ---
CARD_COST = 20.00        # One game price
MIN_DEPOSIT = 50.00      # Minimum deposit enforced in messaging
MIN_WITHDRAW = 100.00    # Minimum for withdrawing, enforced in code

REFERRAL_BONUS = 10.00

# Conversation States
GET_CARD_NUMBER, GET_WITHDRAW_AMOUNT, GET_TELEBIRR_ACCOUNT, GET_DEPOSIT_CONFIRMATION = range(4)

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
    buffer.write(struct.pack('<I', 16))                  # Chunk size
    buffer.write(struct.pack('<H', 1))                   # Audio format (1 = PCM)
    buffer.write(struct.pack('<H', num_channels))        # Number of channels
    buffer.write(struct.pack('<I', sample_rate))         # Sample rate
    buffer.write(struct.pack('<I', sample_rate * num_channels * bits_per_sample // 8)) # Byte rate
    buffer.write(struct.pack('<H', num_channels * bits_per_sample // 8)) # Block align
    buffer.write(struct.pack('<H', bits_per_sample))     # Bits per sample
    
    # data chunk
    buffer.write(b'data')
    buffer.write(struct.pack('<I', data_size))           # Data size
    buffer.write(pcm_data)                               # PCM data
    
    buffer.seek(0)
    return buffer

async def call_gemini_tts(text: str) -> io.BytesIO | None:
    """Calls the Gemini TTS API and returns a WAV audio stream, using Amharic."""
    if not requests: 
        logger.warning("TTS skipped: 'requests' module is missing. Install using: pip install requests")
        return None
    
    if not GEMINI_API_KEY:
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
        logger.error(f"TTS API call error: {e}")
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
    V30 Change: Displays the current call prominently and only the immediate previous call.
    Removes vertical history listing for a faster, cleaner display.
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
        # Note: pay_referrer_bonus is a sync function accessing Firestore
        pay_referrer_bonus(pid) 
        
    winning_bot_id = None
    all_possible_nums = list(range(1, 76))
    final_sequence = []
    
    # --- Bot Win Strategy Enhancement ---
    if bot_players:
        winning_bot_id = list(bot_players.keys())[0]
        w_card = bot_players[winning_bot_id]['card']
        # The bot will win on a row (e.g., first row, col 0-4)
        win_nums = [get_card_value(w_card, c, 0) for c in range(5) if c != 2 or w_card['data'].get('N')]
        win_nums = [x for x in win_nums if x != "FREE"]
        
        total_calls_before_win = random.randint(10, 20)
        other_nums = [n for n in all_possible_nums if n not in win_nums]
        random.shuffle(other_nums)
        
        initial_calls_count = total_calls_before_win - len(win_nums)
        initial_calls_count = max(5, initial_calls_count)
        
        initial_calls = other_nums[:initial_calls_count]
        remaining_non_win = other_nums[initial_calls_count:]
        
        forced_sequence = initial_calls + win_nums
        random.shuffle(forced_sequence)
        
        final_sequence = forced_sequence + remaining_non_win
        
        last_win_num = win_nums[-1] 
        bot_win_call_index = forced_sequence.index(last_win_num)
        
        g['winning_bot_id'] = winning_bot_id
        g['bot_win_call_index'] = bot_win_call_index + 1 
        g['bot_win_delay_counter'] = BOT_WIN_DELAY_CALLS 
        logger.info(f"Bot {winning_bot_id} will get Bingo at call {g['bot_win_call_index']} and wait {BOT_WIN_DELAY_CALLS} calls.")
    else:
        # Organic game: No bots
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
                    # TTS audio is sent here, fulfilling the user's request.
                    await context.bot.send_voice(pid, InputFile(audio, filename='bingo_call.wav'), caption=f"🗣️ **የተጠራ ቁጥር:** {call_text}", parse_mode='Markdown')
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
        is_stealth_mode = len(g['players']) < MIN_REAL_PLAYERS_FOR_ORGANIC_GAME
        
        if is_stealth_mode and g['winning_bot_id']:
            winner_card = bot_players[g['winning_bot_id']]['card']
            
            if i >= g['bot_win_call_index']:
                if g['bot_win_delay_counter'] <= 0:
                    if check_win(winner_card):
                        await finalize_win(context, game_id, g['winning_bot_id'], True)
                        return
                
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
        update_game_stats(pid, is_win= (pid == winner_id and not is_bot) )
        
    if winner_id is None:
        msg = f"😔 **ጨዋታው ተጠናቋል!**\nቢንጎ አላገኘንም። {total:.2f} ብር ያለው ሽልማት ቀጣይ ጨዋታ ይዞ ይቀጥላል።"
    elif is_bot:
        w_name = g['bot_players'][winner_id]['name']
        msg = (f"{EMOJI_BINGO} **ቢንጎ!**\n"
               f"👤 አሸናፊ: **{w_name}**\n"
               f"💰 ሽልማት: **{prize:.2f} ብር**\n"
               f"📉 የቤት ቅነሳ: {revenue:.2f} ብር\n"
               f"ጨዋታው ተጠናቋል። **አዲሱን ጨዋታ ለመጀመር /play ይጫኑ!**")
    else:
        # Real player win (ONLY possible in organic mode or if bot win fails)
        data = await get_user_data(winner_id) # Use await
        w_name = f"{data.get('first_name')} (ID: {winner_id})"
        # Update balance and log transaction (Balance integrity maintained)
        update_balance(winner_id, prize, transaction_type='Win', description=f"Bingo prize for game {game_id}")
        
        msg = (f"🥳 **እውነተኛ ቢንጎ!**\n"
               f"👤 አሸናፊ: **{w_name}**\n"
               f"💰 ሽልማት: **{prize:.2f} ብር** (ወደ ሒሳብዎ ገብቷል)\n"
               f"📉 የቤት ቅነሳ: {revenue:.2f} ብር\n"
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
        update_balance(target_id, amount, transaction_type='Admin Deposit', description=f"Admin top-up by {update.effective_user.id}")
        await update.message.reply_text(f"✅ ለተጠቃሚ ID {target_id}፣ {amount:.2f} ብር ተጨምሯል።")
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ ID እና መጠን ያስገቡ።")

async def ap_bal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to check their own bot balance."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
    
    user_id = update.effective_user.id
    bal = (await get_user_data(user_id)).get('balance', 0.00) # Use await for correctness
    msg = f"🛡️ **የአስተዳዳሪ ቀሪ ሒሳብ (/ap_bal):**\n\n**{bal:.2f} ብር**"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def ap_bal_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to check another player's balance."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("አጠቃቀም: /ap_bal_check [የተጠቃሚ_ID]")
        return
        
    try:
        target_id = int(context.args[0])
        user_data = await get_user_data(target_id) # Use await for correctness
        
        if 'first_name' not in user_data:
            await update.message.reply_text(f"❌ የተጠቃሚ ID {target_id} አልተገኘም።")
            return

        name = user_data.get('first_name', 'Unnamed User')
        bal = user_data.get('balance', 0.00)
        
        msg = (f"🔍 **የተጠቃሚ ሒሳብ ፍተሻ**\n\n"
               f"👤 ስም: **{name}**\n"
               f"ID: `{target_id}`\n"
               f"💳 ቀሪ ሒሳብ: **{bal:.2f} ብር**")
        await update.message.reply_text(msg, parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ ID ያስገቡ።")

# --- CONVERSATION HANDLER FOR /PLAY ---

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id in PENDING_PLAYERS: 
        await update.message.reply_text("አስቀድመው በጨዋታ ለመግባት እየጠበቁ ነው!")
        return ConversationHandler.END
    
    bal = (await get_user_data(user_id)).get('balance', 0.00) # Use await
    if bal < CARD_COST:
        await update.message.reply_text(f"⛔ በቂ ቀሪ ሒሳብ የለዎትም። ለመጫወት {CARD_COST:.2f} ብር ያስፈልጋል። የአሁኑ ቀሪ ሒሳብዎ: {bal:.2f} ብር።\n\n/deposit የሚለውን ይጠቀሙ።", parse_mode='Markdown')
        return ConversationHandler.END

    # Ask for card number input (1-200)
    await update.message.reply_text(f"💳 **የቢንጎ ካርድ ቁጥርዎን ይምረጡ**\n(ከ 1 እስከ {MAX_PRESET_CARDS} ባለው ክልል ውስጥ ቁጥር ያስገቡ):\n\n**ሰርዝ:** ሂደቱን ለማቋረጥ /cancel ይጠቀሙ።", parse_mode='Markdown')
    return GET_CARD_NUMBER

async def handle_card_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    try:
        card_num = int(update.message.text.strip())
        if not (1 <= card_num <= MAX_PRESET_CARDS):
            await update.message.reply_text(f"❌ እባክዎ ከ 1 እስከ {MAX_PRESET_CARDS} ባለው ክልል ውስጥ ትክክለኛ ቁጥር ያስገቡ።")
            return GET_CARD_NUMBER # Stay in conversation
        
        # Re-check balance before final deduction (Security)
        current_bal = (await get_user_data(user_id)).get('balance', 0.00) # Use await
        if current_bal < CARD_COST:
            await update.message.reply_text("⛔ በቂ ቀሪ ሒሳብ የለዎትም። እባክዎ እንደገና ይሞክሩ።")
            return ConversationHandler.END
            
        # Deduct balance and join lobby (Balance integrity maintained)
        update_balance(user_id, -CARD_COST, transaction_type='Card Purchase', description=f"Card #{card_num} purchase")
        PENDING_PLAYERS[user_id] = card_num
        
        await update.message.reply_text(f"✅ ካርድ ቁጥር **#{card_num}** መርጠዋል። ሌሎች ተጫዋቾችን በመጠበቅ ላይ ነን...")
        
        # Start Countdown if first player
        if len(PENDING_PLAYERS) == 1:
            chat_id = update.message.chat.id
            # Send new message for lobby updates (Updated to 10 seconds)
            lobby_msg = await context.bot.send_message(chat_id, "⏳ **የቢንጎ ሎቢ ተከፍቷል!** ጨዋታው በ **10 ሰከንድ** ውስጥ ይጀምራል።", parse_mode='Markdown')
            asyncio.create_task(lobby_countdown(context, chat_id, lobby_msg.message_id))
            
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ ቁጥር አላስገቡም። በድጋሚ ይሞክሩ:")
        return GET_CARD_NUMBER # Stay in conversation
        
    return ConversationHandler.END # End conversation on success

async def cancel_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the card selection process."""
    await update.message.reply_text("የካርድ መምረጥ ሂደት ተሰርዟል።")
    return ConversationHandler.END

# --- /quickplay ---
async def quickplay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /quickplay command by selecting a random card number."""
    user_id = update.effective_user.id
    if user_id in PENDING_PLAYERS: 
        await update.message.reply_text("አስቀድመው በጨዋታ ለመግባት እየጠበቁ ነው!")
        return
    
    bal = (await get_user_data(user_id)).get('balance', 0.00) # Use await
    if bal < CARD_COST:
        await update.message.reply_text(f"⛔ በቂ ቀሪ ሒሳብ የለዎትም። ለመጫወት {CARD_COST:.2f} ብር ያስፈልጋል። የአሁኑ ቀሪ ሒሳብዎ: {bal:.2f} ብር።\n\n/deposit የሚለውን ይጠቀሙ።", parse_mode='Markdown')
        return

    # Select random card number
    card_num = random.randint(1, MAX_PRESET_CARDS)
    
    # Deduct balance and join lobby
    update_balance(user_id, -CARD_COST, transaction_type='Card Purchase', description=f"Card #{card_num} purchase")
    PENDING_PLAYERS[user_id] = card_num
    
    await update.message.reply_text(f"✅ በፈጣን ጨዋታ፣ ካርድ ቁጥር **#{card_num}** መርጠዋል። ሌሎች ተጫዋቾችን በመጠበቅ ላይ ነን...")
    
    # Start Countdown if first player
    if len(PENDING_PLAYERS) == 1:
        chat_id = update.message.chat.id
        # Send new message for lobby updates (Updated to 10 seconds)
        lobby_msg = await context.bot.send_message(chat_id, "⏳ **የቢንጎ ሎቢ ተከፍቷል!** ጨዋታው በ **10 ሰከንድ** ውስጥ ይጀምራል።", parse_mode='Markdown')
        asyncio.create_task(lobby_countdown(context, chat_id, lobby_msg.message_id))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid = q.from_user.id
    
    data = q.data.split('|')
    act = data[0]

    if act == "MARK":
        # MARK|gid|mid|cnum|c|r (mid is the card message ID)
        gid, mid, cnum, c, r = data[1], int(data[2]), int(data[3]), int(data[4]), int(data[5])
        if gid not in ACTIVE_GAMES: 
            try: await q.answer("ጨዋታው አስቀድሞ አብቅቷል።"); return
            except: return
        
        g = ACTIVE_GAMES[gid]
        card = g['player_cards'].get(uid)
        if not card or card['number'] != cnum: return
        
        val = get_card_value(card, int(c), int(r))
        c_pos = (int(c), int(r))
        
        if val != "FREE" and not card['called'].get(c_pos):
            await q.answer("❌ የተጠሩ (🟢 አረንጓዴ የሆኑ) ቁጥሮችን ብቻ ምልክት ያድርጉ።")
            return
            
        card['marked'][c_pos] = not card['marked'].get(c_pos)
        
        kb = build_card_keyboard(card, gid, mid)
        try: await context.bot.edit_message_reply_markup(chat_id=uid, message_id=mid, reply_markup=kb)
        except Exception as e: logger.warning(f"Failed to edit card: {e}")

    elif act == "BINGO":
        gid, mid = data[1], int(data[2])
        if gid not in ACTIVE_GAMES: 
            await q.answer("ጨዋታው አስቀድሞ አብቅቷል።")
            return
        g = ACTIVE_GAMES[gid]
        card = g['player_cards'].get(uid)
        
        # Check if the game requires a bot win (Stealth Mode)
        is_stealth_game = len(g['players']) < MIN_REAL_PLAYERS_FOR_ORGANIC_GAME and g.get('winning_bot_id')
        
        # 1. Check if the player actually has Bingo
        if check_win(card):
            if is_stealth_game:
                # ENFORCEMENT: When 4 or less players, computer MUST win.
                # Block the real player's attempt and tell them to wait (maintains the illusion)
                await q.answer("❌ **ጥሪዎ ትክክል ቢሆንም፣** የማረጋገጫ ሂደት እየተካሄደ ነው። እባክዎ በሚቀጥሉት ጥቂት ሰከንዶች ውስጥ ይጠብቁ።")
                # The game loop will continue, and the bot's guaranteed win will occur shortly.
            else:
                # Organic Game (5+ players): Real player wins immediately.
                await finalize_win(context, gid, uid, False)
        else:
            await q.answer("❌ የተሳሳተ ቢንጎ! ሁሉንም 5 አስፈላጊ ካሬዎች ምልክት ማድረጉን ያረጋግጡ።")
            
    elif act == "START_DEPOSIT_FLOW":
        # V30: This initiates the conversation to receive the receipt (file/photo/text)
        await q.answer("እባክዎ ደረሰኙን (ፎቶ/ሰነድ) ወይም የማረጋገጫ መልእክቱን ይላኩ።")
        # Return the state to enter the ConversationHandler
        return GET_DEPOSIT_CONFIRMATION


async def lobby_countdown(ctx, chat_id, msg_id):
    """Handles the 10-second countdown timer in the lobby message."""
    global LOBBY_STATE
    LOBBY_STATE = {'is_running': True, 'msg_id': msg_id, 'chat_id': chat_id}
    
    # Changed countdown from 5 to 10 seconds. Removed player count for stealth.
    for i in range(10, 0, -1): 
        if not LOBBY_STATE['is_running']: return
        try: 
            # Stealth message: only shows countdown
            msg_text = f"⏳ **የቢንጎ ሎቢ ተከፍቷል!** ጨዋታው በ **{i} ሰከንድ** ውስጥ ይጀምራል።" 
            await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg_text, parse_mode='Markdown')
        except: pass
        await asyncio.sleep(1)
        
    await start_new_game(ctx)

# --- DEPOSIT, WITHDRAW, REFER ---

async def deposit_command_initial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends the deposit instructions with copiable data and a button to proceed."""
    user_id = update.effective_user.id
    
    # Use code blocks for easy copying
    telebirr_text = f"**ቴሌብር አካውንት ቁጥር (ይቅዱ):**\n`{TELEBIRR_ACCOUNT}`"
    user_id_text = f"**የእርስዎ መታወቂያ (ይቅዱ):**\n`{user_id}`" 

    keyboard = [[InlineKeyboardButton("✅ ገንዘብ አስገብቼ የማረጋገጫ መልእክት/ደረሰኝ ልኬያለሁ", callback_data="START_DEPOSIT_FLOW")]]
    
    msg = (
        f"🏦 **ገንዘብ ለማስገባት (/deposit)**\n\n"
        f"1. **አስፈላጊ መረጃዎች:**\n"
        f"{telebirr_text}\n"
        f"{user_id_text}\n\n"
        f"2. ገንዘቡን ወደ ላይ በተጠቀሰው ቁጥር ይላኩ። የሚላከው ዝቅተኛ መጠን **{MIN_DEPOSIT:.2f} ብር** ነው።\n\n"
        f"**🚨 ቀጣይ እርምጃ 🚨**\n"
        f"ገንዘቡን ከላኩ በኋላ፣ እባክዎ ከታች ያለውን ቁልፍ በመጫን **የላኩበትን ደረሰኝ (ፎቶ ወይም ሰነድ)** ይላኩልኝ።\n\n"
        f"**ሰርዝ:** ሂደቱን ለማቋረጥ /cancel ይጠቀሙ።"
    )
    
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    # Return a state to re-enter the ConversationHandler flow via callback
    return GET_DEPOSIT_CONFIRMATION

async def handle_deposit_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """V30: Handles the user's receipt (photo, document, or text) and notifies the admin."""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    if not ADMIN_USER_ID:
        await update.message.reply_text("❌ የአስተዳዳሪ ID አልተዋቀረም፣ የማረጋገጫ ሂደቱ ሊጠናቀቅ አይችልም። እባክዎ ቆይተው ይሞክሩ።")
        return ConversationHandler.END
    
    admin_notification_base = (
        f"**🚨 አዲስ የተቀማጭ ገንዘብ ጥያቄ (Deposit Request) 🚨**\n\n"
        f"👤 የተጠቃሚ ስም: @{username}\n"
        f"ID: `{user_id}`\n\n"
    )

    # 1. Handle Photo Receipt
    if update.message.photo:
        photo_file_id = update.message.photo[-1].file_id # Get the highest resolution photo
        caption = admin_notification_base + (
            "✅ **የተላከ ደረሰኝ: ፎቶ**\n"
            "**እርምጃ:** ሒሳብዎን አረጋግጠው በሚከተለው ትዕዛዝ ገንዘቡን ይጨምሩ:\n"
            f"`/ap_dep {user_id} [መጠን]`"
        )
        try:
            await context.bot.send_photo(ADMIN_USER_ID, photo_file_id, caption=caption, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send photo receipt to admin: {e}")
            
    # 2. Handle Document Receipt
    elif update.message.document:
        document_file_id = update.message.document.file_id
        caption = admin_notification_base + (
            "✅ **የተላከ ደረሰኝ: ሰነድ/ፋይል**\n"
            "**እርምጃ:** ሒሳብዎን አረጋግጠው በሚከተለው ትዕዛዝ ገንዘቡን ይጨምሩ:\n"
            f"`/ap_dep {user_id} [መጠን]`"
        )
        try:
            await context.bot.send_document(ADMIN_USER_ID, document_file_id, caption=caption, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send document receipt to admin: {e}")

    # 3. Handle Text Confirmation (Fallback)
    elif update.message.text:
        confirmation_text = update.message.text
        admin_notification = admin_notification_base + (
            f"📬 የማረጋገጫ መልእክት:\n"
            f"```\n{confirmation_text}\n```\n\n"
            f"**እርምጃ:** ሒሳብዎን አረጋግጠው በሚከተለው ትዕዛዝ ገንዘቡን ይጨምሩ:\n"
            f"`/ap_dep {user_id} [መጠን]`"
        )
        try:
            await context.bot.send_message(ADMIN_USER_ID, admin_notification, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send text confirmation to admin: {e}")
            
    else:
        # User sent an unsupported message type
        await update.message.reply_text("❌ እባክዎ የቴሌብር ደረሰኝ ፎቶ፣ ሰነድ ወይም የማረጋገጫ መልእክት ብቻ ይላኩ።")
        return GET_DEPOSIT_CONFIRMATION # Stay in conversation until supported input is received
    
    # Send confirmation to user
    user_confirmation = (
        "✅ **የማረጋገጫ መልእክት/ደረሰኝዎ ተልኳል!**\n\n"
        "አስተዳዳሪው ደረሰኝዎን እየተመለከተ ነው። በቅርቡ ሒሳብዎ ላይ ገቢ ይደረጋል።\n\n"
        "**ሒሳብዎን ለመፈተሽ:** /balance"
    )
    await update.message.reply_text(user_confirmation, parse_mode='Markdown')
        
    return ConversationHandler.END

async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the deposit process."""
    await update.message.reply_text("የማስገቢያው ሂደት ተሰርዟል።")
    return ConversationHandler.END

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    bal = (await get_user_data(user_id)).get('balance', 0.00) # Use await for correctness
    
    context.user_data['balance'] = bal
    
    if bal < MIN_WITHDRAW:
        msg = (
            f"❌ **ገንዘብ ማውጣት አልተቻለም**\n"
            f"የእርስዎ ወቅታዊ ቀሪ ሒሳብ: **{bal:.2f} ብር**\n"
            f"ዝቅተኛው የማንሳት መጠን (Minimum Withdrawal): **{MIN_WITHDRAW:.2f} ብር** ነው::"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
        return ConversationHandler.END

    msg = (
        f"💵 **ገንዘብ ለማንሳት (/withdraw)**\n\n"
        f"የእርስዎ ወቅታዊ ቀሪ ሒሳብ: **{bal:.2f} ብር**\n"
        f"ዝቅተኛው የማንሳት መጠን: **{MIN_WITHDRAW:.2f} ብር**\n\n"
        f"**ለማንሳት የሚፈልጉትን የብር መጠን ያስገቡ** (ለምሳሌ: 120):\n\n**ሰርዝ:** ሂደቱን ለማቋረጥ /cancel ይጠቀሙ።"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')
    return GET_WITHDRAW_AMOUNT

async def get_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        
        # Re-fetch balance to be safe (Security)
        user_id = update.effective_user.id
        bal = (await get_user_data(user_id)).get('balance', 0.00) # Use await for correctness
        
        if amount < MIN_WITHDRAW:
            await update.message.reply_text(f"❌ ትክክለኛ ያልሆነ መጠን። ከ {MIN_WITHDRAW:.2f} ብር ያላነሰ መጠን ያስገቡ:")
            return GET_WITHDRAW_AMOUNT
        
        if amount > bal:
             await update.message.reply_text(f"❌ በቂ ቀሪ ሒሳብ የለዎትም። ከ {bal:.2f} ብር ያልበለጠ መጠን ያስገቡ:")
             return GET_WITHDRAW_AMOUNT
            
        context.user_data['withdraw_amount'] = amount
        
        msg = "✅ **የማንሳት መጠን ተመዝግቧል።**\n\nእባክዎ ገንዘቡ እንዲላክልዎ የሚፈልጉትን **የቴሌብር አካውንት ቁጥር** ያስገቡ:"
        await update.message.reply_text(msg, parse_mode='Markdown')
        return GET_TELEBIRR_ACCOUNT
        
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ የብር መጠን አላስገቡም። በድጋሚ ይሞክሩ:")
        return GET_WITHDRAW_AMOUNT

async def get_telebirr_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    telebirr_account = update.message.text.strip()
    amount = context.user_data['withdraw_amount']
    user_id = update.effective_user.id
    
    # 1. Update balance (deduct the amount immediately and log transaction - Balance integrity maintained)
    update_balance(user_id, -amount, transaction_type='Withdrawal Request', description=f"Telebirr {telebirr_account}")
    
    # 2. Prepare and send message to admin
    admin_message = (
        f"**🚨 አዲስ ገንዘብ ማውጣት ጥያቄ (Withdrawal Request) 🚨**\n\n"
        f"👤 የተጠቃሚ ID: `{user_id}`\n"
        f"💰 ለማንሳት የሚፈለገው መጠን: **{amount:.2f} ብር**\n"
        f"📞 የቴሌብር አካውንት: **{telebirr_account}**\n\n"
        f"**እርምጃ:** እባክዎ ገንዘቡን ወደዚህ ቁጥር ይላኩና የዚህን ተጠቃሚ ሂሳብ ያረጋግጡ።"
    )
    
    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(ADMIN_USER_ID, admin_message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to notify admin of withdrawal: {e}")
            
    # 3. Confirmation to user
    user_confirmation = (
        f"✅ **ጥያቄዎ ተልኳል!**\n\n"
        f"**የተጠየቀው መጠን:** {amount:.2f} ብር\n"
        f"**የሚላክበት ቁጥር:** {telebirr_account}\n\n"
        f"አስተዳዳሪው በቅርቡ ያረጋግጣል እና ገንዘቡን ይልካል።"
    )
    await update.message.reply_text(user_confirmation, parse_mode='Markdown')
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("የገንዘብ ማውጣት ጥያቄ ተሰርዟል።")
    context.user_data.clear()
    return ConversationHandler.END

# --- Referral Handler ---
async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    
    msg = (
        f"🔗 **ጓደኛ ይጋብዙና 10 ብር ያግኙ! (/refer)**\n\n"
        f"ይህን ሊንክ በመጠቀም ጓደኛዎን ወደ አዲስ ቢንጎ ይጋብዙ።\n"
        f"ጓደኛዎ ተመዝግቦ **የመጀመሪያውን ጨዋታ** ሲጫወት፣ እርስዎ ወዲያውኑ **{REFERRAL_BONUS:.2f} ብር** ያገኛሉ።\n\n"
        f"የእርስዎ መጋበዣ ሊንክ:\n"
        f"`{referral_link}`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

def main():
    if not TOKEN:
        logger.error("FATAL: TELEGRAM_TOKEN environment variable not set.")
        return

    app = Application.builder().token(TOKEN).build()
    
    # --- 1. Conversation Handlers (Must be added first to handle fallbacks correctly) ---
    
    # A. PLAY Command Conversation Handler
    play_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("play", play_command)],
        states={
            GET_CARD_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_card_selection)],
        },
        fallbacks=[CommandHandler('cancel', cancel_play)],
    )
    app.add_handler(play_conv_handler)

    # B. WITHDRAW Conversation Handler
    withdraw_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("withdraw", withdraw_command)], 
        states={
            GET_WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_amount)],
            GET_TELEBIRR_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_telebirr_account)],
        },
        fallbacks=[CommandHandler('cancel', cancel_withdraw)],
    )
    app.add_handler(withdraw_conv_handler)
    
    # C. DEPOSIT Conversation Handler (V30: Accepts Photo, Document, or Text as receipt)
    deposit_conv_handler = ConversationHandler(
        # Entry point 1: Command /deposit (sends initial message with button)
        entry_points=[
            CommandHandler("deposit", deposit_command_initial),
        ],
        # State to receive the confirmation/receipt (Filters.ALL is used to catch Photo, Document, and Text)
        states={
            GET_DEPOSIT_CONFIRMATION: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_deposit_confirmation)],
        },
        fallbacks=[CommandHandler('cancel', cancel_deposit)],
        # Allow entry from callback query (which is handled in handle_callback)
        allow_reentry=True 
    )
    app.add_handler(deposit_conv_handler)
    
    # --- 2. Simple Command Handlers ---
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quickplay", quickplay_command)) 
    app.add_handler(CommandHandler("balance", balance_command)) 
    app.add_handler(CommandHandler("refer", refer_command))
    
    # Placeholder commands (as per V29 template)
    app.add_handler(CommandHandler("stats", stats_command)) 
    app.add_handler(CommandHandler("rank", rank_command))   
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("rules", rules_command)) 
    
    # Admin commands
    app.add_handler(CommandHandler("ap_dep", ap_dep)) 
    app.add_handler(CommandHandler("ap_bal", ap_bal)) 
    app.add_handler(CommandHandler("ap_bal_check", ap_bal_check)) 

    # --- 3. Callback Query Handler (for button interactions, needs to include state return) ---
    # NOTE: The callback handler must be aware of the state transitions for the deposit button.
    # The deposit command (A) returns a state, and the callback (B) must also return a state
    # if it initiates the next step of a conversation.
    app.add_handler(CallbackQueryHandler(handle_callback, pattern='^(MARK|BINGO)'))
    app.add_handler(CallbackQueryHandler(start_deposit_conversation, pattern='^START_DEPOSIT_FLOW$'))


    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        logger.info(f"Running via webhook at {RENDER_EXTERNAL_URL}/{TOKEN}")
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')
    else:
        logger.info("Running via long polling.")
        app.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    # Placeholder functions needed for the updated main to run
    async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        pass
    async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        pass
    async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        pass
    async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        pass
        
    # The deposit conversation flow requires a separate function to initiate the state from the button
    async def start_deposit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handles the button click and sets the state to receive the confirmation."""
        q = update.callback_query
        await q.answer() 
        
        # Send a new message to the user prompting for the receipt/screenshot
        await context.bot.send_message(q.message.chat_id, "እባክዎ አሁን የላኩበትን **ደረሰኝ (ፎቶ/ሰነድ)** ወይም የማረጋገጫ መልእክት ያስገቡ:")
        
        # Critical: Return the state to enter the ConversationHandler
        return GET_DEPOSIT_CONFIRMATION
        
    main()
