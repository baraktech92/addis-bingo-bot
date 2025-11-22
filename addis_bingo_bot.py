# Addis (አዲስ) Bingo Bot - V23.1: English Commands, Amharic Instructions, and TTS Fix
# This version ensures all user-facing commands are in English, while instructions remain in Amharic.

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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
)
import firebase_admin
from firebase_admin import credentials, firestore

# --- Configuration ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')
V2_SECRETS = os.environ.get('V2_SECRETS')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') 
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '') 

TELEBIRR_ACCOUNT = "0927922721"
MIN_WITHDRAW = 100.00
REFERRAL_BONUS = 10.00

# Conversation States for Withdrawal
GET_WITHDRAW_AMOUNT, GET_TELEBIRR_ACCOUNT = range(2)

# Admin ID Extraction
ADMIN_USER_ID = None
try:
    if V2_SECRETS and '|' in V2_SECRETS:
        admin_id_str, _ = V2_SECRETS.split('|', 1)
        ADMIN_USER_ID = int(admin_id_str)
except Exception:
    pass

# --- Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Constants ---
CARD_COST = 20       
MIN_REAL_PLAYERS_FOR_NO_BOTS = 5 
MAX_PRESET_CARDS = 200
CALL_DELAY = 2.25  
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
EMOJI_HISTORY = '🔢'

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

def create_or_update_user(user_id, username, first_name, referred_by_id=None):
    if not db: return
    
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    
    data = {
        'username': username or 'N/A',
        'first_name': first_name,
    }
    
    # Only set referred_by_id if it's the first time and a referrer exists
    if referred_by_id and not doc_ref.get().to_dict().get('referred_by_id'):
        data['referred_by_id'] = str(referred_by_id)
        data['referrer_paid'] = False # Flag for referral bonus payout
    
    doc_ref.set(data, merge=True)
    # Ensure balance field exists for new users without overwriting existing balance
    doc_ref.set({'balance': 0}, merge=True)

def get_user_data(user_id: int) -> dict:
    if not db: return {'balance': 0, 'first_name': 'Player'}
    doc = db.collection(USERS_COLLECTION).document(str(user_id)).get()
    if doc.exists: return doc.to_dict()
    return {'balance': 0, 'first_name': 'Player'}

def update_balance(user_id: int, amount: float):
    if not db: return
    db.collection(USERS_COLLECTION).document(str(user_id)).set(
        {'balance': firestore.Increment(amount)}, merge=True
    )

def pay_referrer_bonus(user_id: int):
    """Checks if a user was referred and pays the bonus if they haven't been paid yet."""
    if not db: return
    
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    user_data = doc_ref.get().to_dict()
    
    if user_data and user_data.get('referred_by_id') and not user_data.get('referrer_paid'):
        referrer_id = user_data['referred_by_id']
        
        # 1. Pay the referrer
        update_balance(referrer_id, REFERRAL_BONUS)
        
        # 2. Mark the user as having triggered the payment
        doc_ref.update({'referrer_paid': True})
        
        # Log or notify the referrer (optional, but good practice)
        logger.info(f"Paid {REFERRAL_BONUS} Br referral bonus to user {referrer_id} for user {user_id}")
        return True
    return False

# --- Game State & Bots ---
ACTIVE_GAMES = {} 
PENDING_PLAYERS = {} 
LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}

BOT_ID_COUNTER = -1 
def create_bot_player() -> tuple[int, str]:
    """Creates a bot with a unique negative ID and a realistic 7-digit string name."""
    global BOT_ID_COUNTER
    BOT_ID_COUNTER -= 1
    name = str(random.randint(1000000, 9999999))
    return BOT_ID_COUNTER, name

def get_total_players_target(real_count: int) -> int:
    """Calculates TOTAL desired players (Real + Bot) to create the illusion (Stealth Mode)."""
    if real_count >= MIN_REAL_PLAYERS_FOR_NO_BOTS: 
        return real_count
    if real_count == 0: 
        return 0
    
    if real_count == 1: 
        return random.randint(10, 12)
    if real_count == 2: 
        return random.randint(13, 15)
    if real_count == 3: 
        return random.randint(15, 17)
    if real_count == 4: 
        return random.randint(18, 20)
    
    return real_count

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
    return arr[row_idx] if col != 'N' or row_idx < 2 else arr[row_idx-1] if row_idx > 2 else "FREE"

def get_card_position(card, value):
    for c_idx, col in enumerate(COLUMNS):
        arr = card['data'][col]
        if col == 'N':
            if value in arr:
                idx = arr.index(value)
                return c_idx, idx if idx < 2 else idx + 1
        elif value in arr:
            return c_idx, arr.index(value)
    return None, None

def check_win(card):
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
    """Calls the Gemini TTS API and returns a WAV audio stream."""
    if not requests or not GEMINI_API_KEY: 
        logger.warning("TTS skipped: 'requests' module or API key is missing.")
        return None
    
    # Extract the number from the format 'L-N' (e.g., B-12 -> 12)
    try:
        num = int(text.split('-')[1])
        amharic_word = get_amharic_number_text(num)
        tts_prompt = f"Say clearly: {text}. In Amharic: {amharic_word}"
    except (IndexError, ValueError):
        tts_prompt = f"Say clearly: {text}."

    payload = {
        "contents": [{"parts": [{"text": tts_prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"], 
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}
        },
        "model": "gemini-2.5-flash-preview-tts"
    }

    try:
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
                logger.error(f"TTS API returned 200 but missing audio data in 'inlineData.data': {data}")
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
def get_amharic_number_text(num: int) -> str:
    return AMHARIC_NUMBERS.get(num, str(num))

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

def format_history(called):
    """Formats called numbers horizontally."""
    if not called: return ""
    
    # Format each number as L-N (e.g., B-12)
    formatted_nums = [f"{COLUMNS[(n-1)//15]}-{n}" for n in called]
    
    # Arrange them horizontally, separating by a comma and space
    return ", ".join(formatted_nums)

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
        'total_players_announced': total_target
    }
    
    for pid in real_pids: del PENDING_PLAYERS[pid]
    ACTIVE_GAMES[game_id] = game_data
    LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}

    # Announce start with the TOTAL player count (Stealth)
    player_announcement = f"👥 ጠቅላላ ተጫዋቾች: **{game_data['total_players_announced']}**"
    for pid in real_pids:
        await context.bot.send_message(pid, f"✅ **ጨዋታው ተጀምሯል!**\n{player_announcement}", parse_mode='Markdown')

    asyncio.create_task(run_game_loop(context, game_id, real_pids, bot_players))

async def run_game_loop(context, game_id, real_pids, bot_players):
    game_data = ACTIVE_GAMES[game_id]
    
    # Referral Bonus Check (Happens when the first game is played after card purchase)
    for pid in real_pids:
        pay_referrer_bonus(pid) 
        
    # Bot Winning Sequence setup (if bots are playing, they win)
    winning_bot_id = None
    forced_sequence = list(range(1, 76))
    random.shuffle(forced_sequence)
    
    if bot_players:
        winning_bot_id = list(bot_players.keys())[0]
        w_card = bot_players[winning_bot_id]['card']
        win_nums = [get_card_value(w_card, c, 0) for c in range(5)]
        win_nums = [x for x in win_nums if x != "FREE"]
        other_nums = [n for n in range(1, 76) if n not in win_nums]
        random.shuffle(other_nums)
        # Ensure winning numbers are called between 10th and 20th call 
        insert_point = random.randint(10, 20)
        
        # Simple sequence generation: A few random, winning numbers, then the rest of random
        temp_seq = other_nums[:]
        
        # Put winning numbers into the sequence
        for num in win_nums:
            if num in temp_seq:
                temp_seq.remove(num)
            temp_seq.insert(insert_point, num)
            insert_point += 1 
            
        random.shuffle(temp_seq)
        forced_sequence = temp_seq

    # Init Messages - Board (Top) and Card (Bottom)
    for pid in real_pids:
        # 1. Send Board Message (Will display calling number and history)
        bm = await context.bot.send_message(pid, "⏳ **የቢንጎ ሰሌዳ እየተጫነ ነው...**", parse_mode='Markdown')
        game_data['board_messages'][pid] = bm.message_id
        
        # 2. Send Card Message (The interactive card)
        card = game_data['player_cards'][pid]
        kb = build_card_keyboard(card, game_id, bm.message_id) # Use board_msg_id temporarily
        cm = await context.bot.send_message(pid, f"{EMOJI_CARD} **Card #{card['number']}**", reply_markup=kb, parse_mode='Markdown')
        game_data['card_messages'][pid] = cm.message_id
        
        # Update callback data on card keyboard to use the correct card message ID
        kb = build_card_keyboard(card, game_id, cm.message_id)
        await context.bot.edit_message_reply_markup(chat_id=pid, message_id=cm.message_id, reply_markup=kb)


    await asyncio.sleep(2) # Initial pause

    for num in forced_sequence:
        if game_data['status'] != 'running': break
        
        game_data['called'].append(num)
        col = COLUMNS[(num-1)//15]
        call_text = f"{col}-{num}"
        
        # 1. Update internal card states (real players and bots)
        for pid in real_pids:
            c_pos = get_card_position(game_data['player_cards'][pid], num)
            if c_pos[0] is not None: game_data['player_cards'][pid]['called'][c_pos] = True
            
        if bot_players:
            for bid, bdata in bot_players.items():
                c_pos = get_card_position(bdata['card'], num)
                if c_pos[0] is not None: 
                    bdata['card']['called'][c_pos] = True
                    # Bots mark called numbers immediately
                    bdata['card']['marked'][c_pos] = True 

        # 2. TTS Audio Call
        audio = await call_gemini_tts(call_text)
        
        # 3. Update Board Message (Calling number and history)
        hist_txt = format_history(game_data['called'][:-1]) # Previous numbers only
        current_call_txt = f"🗣 **አሁን የሚጠራ ቁጥር:** {call_text}"
        history_display = f"{EMOJI_HISTORY} **የተጠሩ ቁጥሮች:**\n{hist_txt}"
        
        board_text = f"{current_call_txt}\n\n{history_display}"

        for pid in real_pids:
            # Board (Calling and History)
            try: await context.bot.edit_message_text(chat_id=pid, message_id=game_data['board_messages'][pid], text=board_text, parse_mode='Markdown')
            except: pass

            # Send Voice/Text
            caption_text = f"📢 **አዲስ ጥሪ:** {call_text}"
            if audio:
                try: 
                    audio.seek(0)
                    await context.bot.send_voice(pid, audio, caption=caption_text, parse_mode='Markdown')
                except Exception as e: 
                    logger.error(f"Failed to send voice: {e}")
                    await context.bot.send_message(pid, caption_text, parse_mode='Markdown')
            else:
                try: await context.bot.send_message(pid, caption_text, parse_mode='Markdown')
                except: pass

            # Card (Refresh for green highlighting)
            card = game_data['player_cards'][pid]
            kb = build_card_keyboard(card, game_id, game_data['card_messages'][pid])
            try: await context.bot.edit_message_reply_markup(chat_id=pid, message_id=game_data['card_messages'][pid], reply_markup=kb)
            except: pass

        # 4. Check Bot Win
        if winning_bot_id and check_win(bot_players[winning_bot_id]['card']):
            await finalize_win(context, game_id, winning_bot_id, True)
            return

        await asyncio.sleep(CALL_DELAY)

    if game_data['status'] == 'running':
        await finalize_win(context, game_id, None, False)


async def finalize_win(context, game_id, winner_id, is_bot=False):
    g = ACTIVE_GAMES.get(game_id)
    if not g or g['status'] != 'running': return
    g['status'] = 'finished'
    
    total = g['total_pot']
    revenue = total * GLOBAL_CUT_PERCENT
    prize = total * WINNER_SHARE_PERCENT
    
    if winner_id is None:
        msg = f"😔 **ጨዋታው ተጠናቋል!**\nቢንጎ አላገኘንም። {total:.2f} ብር ያለው ሽልማት ቀጣይ ጨዋታ ይዞ ይቀጥላል።"
    elif is_bot:
        w_name = g['bot_players'][winner_id]['name']
        msg = (f"{EMOJI_BINGO} **ቢንጎ!**\n"
               f"👤 አሸናፊ: **{w_name}**\n"
               f"💰 ሽልማት: **{prize:.2f} ብር**\n"
               f"📉 የቤት ቅነሳ: {revenue:.2f} ብር\n"
               f"ጨዋታው ተጠናቋል።")
    else:
        # Real player win
        data = get_user_data(winner_id)
        w_name = f"{data.get('first_name')} (ID: {winner_id})"
        update_balance(winner_id, prize)
        msg = (f"🥳 **እውነተኛ ቢንጎ!**\n"
               f"👤 አሸናፊ: **{w_name}**\n"
               f"💰 ሽልማት: **{prize:.2f} ብር** (ወደ ሒሳብዎ ገብቷል)\n"
               f"📉 የቤት ቅነሳ: {revenue:.2f} ብር\n"
               f"ጨዋታው ተጠናቋል።")
           
    for pid in g['players']:
        await context.bot.send_message(pid, msg, parse_mode='Markdown')

    del ACTIVE_GAMES[game_id]


# --- Handlers ---
async def start(u, c): 
    # Check for referral parameter
    referrer_id = None
    if c.args and c.args[0].isdigit():
        referrer_id = c.args[0]
    
    create_or_update_user(u.effective_user.id, u.effective_user.username, u.effective_user.first_name, referrer_id)
    
    await u.message.reply_text("👋 ወደ አዲስ ቢንጎ እንኳን ደህና መጡ!\n\n/deposit - ገንዘብ ለማስገባት\n/withdraw - ገንዘብ ለማውጣት\n/balance - ሂሳብ ለማየት\n/play - ቢንጎ ካርድ ለመግዛት (20 ብር)")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    bal = get_user_data(user_id).get('balance', 0)
    msg = f"💳 **የእርስዎ ቀሪ ሒሳብ:**\n\n**{bal:.2f} ብር**"
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
        update_balance(target_id, amount)
        await update.message.reply_text(f"✅ ለተጠቃሚ ID {target_id}፣ {amount:.2f} ብር ተጨምሯል።")
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ ID እና መጠን ያስገቡ።")

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in PENDING_PLAYERS: 
        await update.message.reply_text("አስቀድመው በጨዋታ ለመግባት እየጠበቁ ነው!")
        return
    
    bal = get_user_data(user_id).get('balance', 0)
    if bal < CARD_COST:
        await update.message.reply_text(f"⛔ በቂ ቀሪ ሒሳብ የለዎትም። ለመጫወት {CARD_COST:.2f} ብር ያስፈልጋል። የአሁኑ ቀሪ ሒሳብዎ: {bal:.2f} ብር።\n\n/deposit የሚለውን ይጠቀሙ።", parse_mode='Markdown')
        return

    # Ask for card number input (1-200)
    await update.message.reply_text(f"💳 **የቢንጎ ካርድ ቁጥርዎን ይምረጡ**\n(ከ 1 እስከ {MAX_PRESET_CARDS} ባለው ክልል ውስጥ ቁጥር ያስገቡ):", parse_mode='Markdown')
    # Use context.user_data to track the state for card selection
    context.user_data['waiting_for_card_number'] = True

async def handle_card_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not context.user_data.get('waiting_for_card_number'): return # Not expecting card number
    
    try:
        card_num = int(update.message.text.strip())
        if not (1 <= card_num <= MAX_PRESET_CARDS):
            await update.message.reply_text(f"❌ እባክዎ ከ 1 እስከ {MAX_PRESET_CARDS} ባለው ክልል ውስጥ ትክክለኛ ቁጥር ያስገቡ።")
            return
        
        # Deduct balance and join lobby
        update_balance(user_id, -CARD_COST)
        PENDING_PLAYERS[user_id] = card_num
        context.user_data['waiting_for_card_number'] = False
        
        await update.message.reply_text(f"✅ ካርድ ቁጥር **#{card_num}** መርጠዋል። ሌሎች ተጫዋቾችን በመጠበቅ ላይ ነን...")
        
        # Start Countdown if first player
        if len(PENDING_PLAYERS) == 1:
            chat_id = update.message.chat.id
            # Send new message for lobby updates
            lobby_msg = await context.bot.send_message(chat_id, "⏳ **የቢንጎ ሎቢ ተከፍቷል!** ጨዋታው በ **5 ሰከንድ** ውስጥ ይጀምራል።", parse_mode='Markdown')
            asyncio.create_task(lobby_countdown(context, chat_id, lobby_msg.message_id))
            
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ ቁጥር አላስገቡም።")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid = q.from_user.id
    try: await q.answer() 
    except: pass
    
    data = q.data.split('|')
    act = data[0]

    if act == "MARK":
        # MARK|gid|mid|cnum|c|r (mid is the card message ID)
        gid, mid, cnum, c, r = data[1], int(data[2]), int(data[3]), int(data[4]), int(data[5])
        if gid not in ACTIVE_GAMES: return
        
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
        
        if check_win(card):
            await finalize_win(context, gid, uid, False)
        else:
            await q.answer("❌ የተሳሳተ ቢንጎ! ሁሉንም 5 አስፈላጊ ካሬዎች ምልክት ማድረጉን ያረጋግጡ።")

async def lobby_countdown(ctx, chat_id, msg_id):
    """Handles the 5-second countdown timer in the lobby message."""
    global LOBBY_STATE
    LOBBY_STATE = {'is_running': True, 'msg_id': msg_id, 'chat_id': chat_id}
    
    for i in range(5, 0, -1):
        if not LOBBY_STATE['is_running']: return
        try: 
            p_count = len(PENDING_PLAYERS)
            msg_text = f"⏳ ጨዋታው በ **{i} ሰከንድ** ውስጥ ይጀምራል።\n(አሁን: {p_count} ተጫዋቾች ካርድ ገዝተዋል)"
            await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg_text, parse_mode='Markdown')
        except: pass
        await asyncio.sleep(1)
        
    await start_new_game(ctx)

# --- English Command Handlers with Amharic Instructions ---

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    admin_tag = f"@{ADMIN_USERNAME}" if ADMIN_USERNAME else "አስተዳዳሪ"
    
    amharic_message = (
        f"🏦 **ገንዘብ ለማስገባት (/deposit)**\n\n"
        f"1. ገንዘቡን ወደዚህ የቴሌብር ቁጥር ይላኩ: **{TELEBIRR_ACCOUNT}**\n"
        f"2. የገንዘብ ዝውውር ማረጋገጫ (receipt) ስክሪንሾት ያንሱ።\n"
        f"3. ስክሪንሾቱን እና የእርስዎን የቴሌግራም መታወቂያ (ID: `{user_id}`) ለዚህ አስተዳዳሪ ይላኩ: {admin_tag}\n\n"
        f"ዝቅተኛ የተቀማጭ ገንዘብ መጠን (Minimum Deposit): **{CARD_COST} ብር**"
    )
    await update.message.reply_text(amharic_message, parse_mode='Markdown')

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    bal = get_user_data(user_id).get('balance', 0)
    
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
        f"**ለማንሳት የሚፈልጉትን የብር መጠን ያስገቡ** (ለምሳሌ: 120):"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')
    return GET_WITHDRAW_AMOUNT

async def get_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
        bal = context.user_data['balance']
        
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
    
    # 1. Update balance (deduct the amount immediately)
    update_balance(user_id, -amount)
    
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
        f"ጓደኛዎ ተመዝግቦ **የመጀመሪያውን ተቀማጭ** ሲያደርግ፣ እርስዎ ወዲያውኑ **{REFERRAL_BONUS:.2f} ብር** ያገኛሉ!\n\n"
        f"የእርስዎ መጋበዣ ሊንክ:\n"
        f"`{referral_link}`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

def main():
    if not TOKEN:
        logger.error("FATAL: TELEGRAM_TOKEN environment variable not set.")
        return

    app = Application.builder().token(TOKEN).build()
    
    # 1. Start command (handles referral)
    app.add_handler(CommandHandler("start", start))
    
    # 2. Card Selection Flow
    app.add_handler(CommandHandler("play", play_command)) # English Command
    # Ignore commands during card selection, allow text input
    app.add_handler(MessageHandler(filters.TEXT & filters.COMMAND, lambda u, c: ConversationHandler.END)) 
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_card_selection))
    
    # 3. Payment and Balance Commands
    app.add_handler(CommandHandler("deposit", deposit_command)) # English Command
    app.add_handler(CommandHandler("balance", balance)) 
    
    # 4. Withdrawal Conversation Handler
    withdraw_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("withdraw", withdraw_command)], # English Command
        states={
            GET_WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_amount)],
            GET_TELEBIRR_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_telebirr_account)],
        },
        fallbacks=[CommandHandler('cancel', cancel_withdraw)],
    )
    app.add_handler(withdraw_conv_handler)
    
    # 5. Referral Command
    app.add_handler(CommandHandler("refer", refer_command)) # English Command
    
    # 6. Callback Query Handler (for button interactions)
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # 7. Admin Top-up 
    app.add_handler(CommandHandler("ap_dep", ap_dep))

    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        logger.info(f"Running via webhook at {RENDER_EXTERNAL_URL}/{TOKEN}")
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')
    else:
        logger.info("Running via long polling.")
        app.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    main()
