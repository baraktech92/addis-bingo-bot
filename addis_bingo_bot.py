# Addis (አዲስ) Bingo Bot - V22.0: Pro Speed & Stealth Logic
# Features: 2.25s Delay, 🔴/🟢 Visuals, 7-Digit Bot IDs, Inflated Player Counts.

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
    # Set requests to None if not available; TTS functions will handle this gracefully.
    requests = None 

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
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '') 

# Admin ID Extraction (for balance top-up)
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
CALL_DELAY = 2.25  # Increased Speed: 2.25 seconds delay between calls
COLUMNS = ['B', 'I', 'N', 'G', 'O']

# Payout Logic: Total Pot is Total Players * CARD_COST
GLOBAL_CUT_PERCENT = 0.20       # 20% cut goes to the house (revenue)
WINNER_SHARE_PERCENT = 0.80     # 80% remaining prize goes to the winner

# --- UI Aesthetics (Updated V22) ---
# Visuals updated as requested: No star (*). Use distinct colors/emojis.
EMOJI_UNMARKED_UNCALLED = '🔴' # Red (Uncalled)
EMOJI_CALLED_UNMARKED = '🟢'   # Green (Called, ready to be marked)
EMOJI_MARKED = '✅'           # Green Checkmark (Player marked)
EMOJI_FREE = '🌟'     
EMOJI_CARD = '🃏'

# --- Amharic Numbers (for TTS) ---
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

# --- Audio Helpers for TTS ---
TTS_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"

def create_wav_bytes(pcm_data: bytes) -> io.BytesIO:
    """Converts raw PCM audio data into a playable WAV format stream."""
    buffer = io.BytesIO()
    data_size = len(pcm_data)
    # WAV Header
    buffer.write(b'RIFF')
    buffer.write(struct.pack('<I', 36 + data_size))
    buffer.write(b'WAVE')
    buffer.write(b'fmt ')
    buffer.write(struct.pack('<I', 16))
    buffer.write(struct.pack('<H', 1))
    buffer.write(struct.pack('<H', 1)) # Mono channel
    buffer.write(struct.pack('<I', 24000)) # Sample rate
    buffer.write(struct.pack('<I', 24000 * 2)) # Byte rate (SampleRate * Channels * BitsPerSample/8)
    buffer.write(struct.pack('<H', 2)) # Block Align (Channels * BitsPerSample/8)
    buffer.write(struct.pack('<H', 16)) # Bits Per Sample
    buffer.write(b'data')
    buffer.write(struct.pack('<I', data_size))
    # PCM Data
    buffer.write(pcm_data)
    buffer.seek(0)
    return buffer

async def call_gemini_tts(text: str) -> io.BytesIO | None:
    """Calls the Gemini TTS API and returns a WAV audio stream."""
    # Only proceed if 'requests' library is available and API key is set
    if not requests or not GEMINI_API_KEY: return None
    
    # Prompt combines the letter/number and the Amharic word
    amharic_word = AMHARIC_NUMBERS.get(int(text.split('-')[1]), "")
    tts_prompt = f"Say clearly: {text}. In Amharic: {amharic_word}"

    payload = {
        "contents": [{"parts": [{"text": tts_prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"], 
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}
        },
        "model": "gemini-2.5-flash-preview-tts"
    }

    try:
        # Use asyncio.to_thread for synchronous network calls (requests)
        response = await asyncio.to_thread(lambda: requests.post(
            TTS_URL, 
            headers={'Content-Type': 'application/json'}, 
            data=json.dumps(payload), 
            timeout=5 # Increased timeout slightly for reliable TTS
        ))
        
        if response.status_code == 200:
            data = response.json()
            candidate = data.get('candidates', [{}])[0]
            part = candidate.get('content', {}).get('parts', [{}])[0]
            
            if 'inlineData' in part:
                pcm = base64.b64decode(part['inlineData']['data'])
                return create_wav_bytes(pcm)
    except Exception as e:
        logger.error(f"TTS API Error: {e}")
    return None

# --- Game State & Bots ---
ACTIVE_GAMES = {} 
PENDING_PLAYERS = {} 
LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}

BOT_ID_COUNTER = -1 
def create_bot_player() -> tuple[int, str]:
    """Creates a bot with a unique negative ID and a realistic 7-digit string name."""
    global BOT_ID_COUNTER
    BOT_ID_COUNTER -= 1
    # Generate a realistic Telegram-like ID (6 or 7 digits)
    name = str(random.randint(1000000, 9999999))
    return BOT_ID_COUNTER, name

def get_total_players_target(real_count: int) -> int:
    """
    Calculates the TOTAL desired players (Real + Bot) to create the illusion.
    If 5 or more real players, bots are excluded.
    """
    if real_count >= MIN_REAL_PLAYERS_FOR_NO_BOTS: 
        return real_count
    if real_count == 0: 
        return 0
    
    # Inject bots to reach 10-20 players based on real count
    if real_count == 1: 
        return random.randint(10, 12)  # Target 10-12
    if real_count == 2: 
        return random.randint(13, 15)  # Target 13-15
    if real_count == 3: 
        return random.randint(15, 17)  # Target 15-17
    if real_count == 4: 
        return random.randint(18, 20)  # Target 18-20
    
    return real_count

# --- Database Setup (Firestore) ---
# ... [Firebase Initialization code remains the same]
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

def create_or_update_user(user_id, username, first_name):
    if not db: return
    # Initial setup if user doesn't exist
    doc_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    doc_ref.set({
        'username': username or 'N/A',
        'first_name': first_name,
        'balance': firestore.DELETE_FIELD # Will be ignored if set() is used without merge
    }, merge=True)
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

# --- Bingo Logic ---
# ... [get_preset_card, get_card_value, get_card_position, check_win remain the same]
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
            # Handle N column (center is FREE)
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

# --- UI & Text ---
def build_card_keyboard(card, game_id, msg_id):
    keyboard = []
    # Compact Header (Removed ~ sign, reduced horizontal space)
    keyboard.append([InlineKeyboardButton(c, callback_data="ignore") for c in COLUMNS])
    
    for r in range(5):
        row = []
        for c in range(5):
            val = get_card_value(card, c, r)
            pos = (c, r)
            is_marked = card['marked'].get(pos, False)
            is_called = card['called'].get(pos, False)
            
            # Use requested emojis
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
    if not called: return "History: None"
    # Compact history: Last 5 numbers only
    last_5 = called[-5:]
    hist_str = " | ".join([f"{COLUMNS[(n-1)//15]}-{n}" for n in last_5])
    return f"📜 .. {hist_str}"

# --- Core Game Loop ---
async def start_new_game(context: ContextTypes.DEFAULT_TYPE):
    global LOBBY_STATE
    players_data = list(PENDING_PLAYERS.items())
    real_pids = [pid for pid, _ in players_data]
    
    if not real_pids:
        LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}
        return

    game_id = f"G{int(time.time())}"
    
    # Bot Injection Logic
    total_target = get_total_players_target(len(real_pids))
    bots_needed = total_target - len(real_pids)
    bot_players = {}
    
    # Used cards to ensure uniqueness
    used_cards = [num for _, num in players_data]
    pool = [c for c in range(1, MAX_PRESET_CARDS+1) if c not in used_cards]

    for _ in range(bots_needed):
        bid, bname = create_bot_player()
        if not pool: 
            logger.warning("Ran out of unique card numbers for bots!")
            break
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
        'total_pot': total_target * CARD_COST, # Money calculation based on total players
        'total_players_announced': total_target
    }
    
    # Clear pending players for the current game
    for pid in real_pids: del PENDING_PLAYERS[pid]
    ACTIVE_GAMES[game_id] = game_data
    LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}

    # Announce start with the inflated player count
    player_announcement = f"👥 Players: **{game_data['total_players_announced']}**"
    for pid in real_pids:
        await context.bot.send_message(pid, f"✅ **Game Starting!**\n{player_announcement}", parse_mode='Markdown')

    asyncio.create_task(run_game_loop(context, game_id, real_pids, bot_players))

async def run_game_loop(context, game_id, real_pids, bot_players):
    game_data = ACTIVE_GAMES[game_id]
    
    # Bot Winning Sequence (Only forces a win if bots are playing)
    winning_bot_id = None
    forced_sequence = list(range(1, 76))
    random.shuffle(forced_sequence)
    
    if bot_players:
        # Computer always wins if bots are present
        winning_bot_id = list(bot_players.keys())[0]
        w_card = bot_players[winning_bot_id]['card']
        
        # Get numbers needed for the first winning condition (e.g., Row 1)
        win_nums = [get_card_value(w_card, c, 0) for c in range(5)]
        win_nums = [x for x in win_nums if x != "FREE"]
        
        # Shuffle all other numbers
        other_nums = [n for n in range(1, 76) if n not in win_nums]
        random.shuffle(other_nums)
        
        # Insert winning numbers around turn 10-15 to make it believable
        insert_point = random.randint(10, 15)
        # Create the new sequence: (pre-win numbers) + (win numbers) + (remaining numbers)
        forced_sequence = other_nums[:insert_point] + win_nums + other_nums[insert_point:]
        # Ensure the sequence is unique and covers all 75 numbers (list concatenation handles this)
        
    # Init Messages
    for pid in real_pids:
        bm = await context.bot.send_message(pid, "⏳ **Loading Bingo Board...**", parse_mode='Markdown')
        game_data['board_messages'][pid] = bm.message_id
        
        card = game_data['player_cards'][pid]
        cm = await context.bot.send_message(pid, "⏳ **Loading Card...**", parse_mode='Markdown')
        game_data['card_messages'][pid] = cm.message_id
        
        kb = build_card_keyboard(card, game_id, cm.message_id)
        await context.bot.edit_message_text(chat_id=pid, message_id=cm.message_id, 
                                            text=f"{EMOJI_CARD} **Card #{card['number']}**", 
                                            reply_markup=kb, parse_mode='Markdown')

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
                    bdata['card']['marked'][c_pos] = True # Bot always auto-marks

        # 2. TTS Audio Call
        audio = await call_gemini_tts(call_text)
        
        # 3. Send Message/Voice to Real Players
        for pid in real_pids:
            caption_text = f"🗣 **{call_text}**"
            if audio:
                # Send voice audio if successful
                try: 
                    # Use a temporary file-like object for the voice
                    audio.seek(0)
                    await context.bot.send_voice(pid, audio, caption=caption_text, parse_mode='Markdown')
                except Exception as e: 
                    logger.error(f"Failed to send voice to {pid}: {e}")
                    # Fallback to text if voice fails
                    await context.bot.send_message(pid, caption_text, parse_mode='Markdown')
            else:
                # Text-only fallback
                try: await context.bot.send_message(pid, caption_text, parse_mode='Markdown')
                except: pass

        # 4. Update Visuals
        hist_txt = format_history(game_data['called'])
        for pid in real_pids:
            # Board (Called Numbers List)
            try: await context.bot.edit_message_text(chat_id=pid, message_id=game_data['board_messages'][pid], 
                                                     text=f"🎰 **BINGO BOARD**\n{hist_txt}\n\n📢 **LAST: {call_text}**", 
                                                     parse_mode='Markdown')
            except: pass
            
            # Card (Refresh for green highlighting)
            card = game_data['player_cards'][pid]
            kb = build_card_keyboard(card, game_id, game_data['card_messages'][pid])
            try: await context.bot.edit_message_reply_markup(chat_id=pid, message_id=game_data['card_messages'][pid], reply_markup=kb)
            except: pass

        # 5. Check Bot Win (Only if bots are playing)
        if winning_bot_id and check_win(bot_players[winning_bot_id]['card']):
            await finalize_win(context, game_id, winning_bot_id, True)
            return

        await asyncio.sleep(CALL_DELAY)

    # If the sequence finishes without a win, finalize as "no winner" (should not happen in bot-controlled games)
    if game_data['status'] == 'running':
        await finalize_win(context, game_id, None, False)


async def finalize_win(context, game_id, winner_id, is_bot=False):
    g = ACTIVE_GAMES.get(game_id)
    if not g or g['status'] != 'running': return
    g['status'] = 'finished'
    
    total = g['total_pot'] # Total money collected (e.g., 18 players * 20 = 360 Br)
    revenue = total * GLOBAL_CUT_PERCENT
    prize = total * WINNER_SHARE_PERCENT
    
    if winner_id is None:
        msg = f"😔 **Game Ended**\nNo BINGO found! Pot of {total:.2f} Br rolled over."
        # No money changes
    elif is_bot:
        w_name = g['bot_players'][winner_id]['name']
        # Prize money goes to the house (Bot/Computer)
        msg = (f"🏆 **BINGO!**\n"
               f"👤 Winner: **{w_name}**\n"
               f"💰 Prize: **{prize:.2f} Br**\n"
               f"📉 Rev Cut: {revenue:.2f} Br\n"
               f"Game Over!")
    else:
        # Real player win
        data = get_user_data(winner_id)
        w_name = f"{data.get('first_name')} (ID: {winner_id})"
        update_balance(winner_id, prize)
        msg = (f"🥳 **REAL BINGO!**\n"
               f"👤 Winner: **{w_name}**\n"
               f"💰 Prize: **{prize:.2f} Br** (Credited)\n"
               f"📉 Rev Cut: {revenue:.2f} Br\n"
               f"Game Over!")
           
    for pid in g['players']:
        await context.bot.send_message(pid, msg, parse_mode='Markdown')

    del ACTIVE_GAMES[game_id]


# --- Handlers ---
async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id in PENDING_PLAYERS: 
        await update.message.reply_text("You are already in the lobby!")
        return
    
    # Balance Check
    bal = get_user_data(user_id).get('balance', 0)
    if bal < CARD_COST:
        await update.message.reply_text(f"⛔ Insufficient balance. Need {CARD_COST} Br to play. Your Balance: {bal:.2f} Br.")
        return

    # Deduct (Deduct before card selection to lock the slot)
    update_balance(user_id, -CARD_COST)
    
    # Card Selection: Show 5 random cards from the pool
    opts = random.sample(range(1, MAX_PRESET_CARDS+1), 5)
    kb = [[InlineKeyboardButton(f"Card {n}", callback_data=f"SEL|{n}")] for n in opts]
    await update.message.reply_text("💳 **Choose Your Bingo Card:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid = q.from_user.id
    try: await q.answer() # Respond quickly to the button press
    except: pass
    
    data = q.data.split('|')
    act = data[0]

    if act == "SEL":
        c_num = int(data[1])
        if uid in PENDING_PLAYERS: return # Already selected
        
        PENDING_PLAYERS[uid] = c_num
        await q.edit_message_text(f"✅ Selected Card **#{c_num}**. Waiting for others to join...", parse_mode='Markdown')
        
        # Start Countdown if first player
        if len(PENDING_PLAYERS) == 1:
            chat_id = q.message.chat.id
            msg_id = q.message.message_id
            # Send new message for lobby updates
            lobby_msg = await context.bot.send_message(chat_id, "⏳ **Lobby created!** Game starts in **5s**...", parse_mode='Markdown')
            asyncio.create_task(lobby_countdown(context, chat_id, lobby_msg.message_id))
            
    elif act == "MARK":
        # MARK|gid|mid|cnum|c|r
        gid, mid, cnum, c, r = data[1], int(data[2]), int(data[3]), int(data[4]), int(data[5])
        if gid not in ACTIVE_GAMES: return
        
        g = ACTIVE_GAMES[gid]
        card = g['player_cards'].get(uid)
        if not card or card['number'] != cnum: return
        
        val = get_card_value(card, int(c), int(r))
        c_pos = (int(c), int(r))
        
        # Mark only if the number has been called (is green 🟢)
        if val != "FREE" and not card['called'].get(c_pos):
            await q.answer("❌ You can only mark numbers that have been called (🟢 Green).")
            return
            
        # Toggle mark state
        card['marked'][c_pos] = not card['marked'].get(c_pos)
        
        # Refresh the keyboard to update the emoji
        kb = build_card_keyboard(card, gid, mid)
        try: await context.bot.edit_message_reply_markup(chat_id=uid, message_id=mid, reply_markup=kb)
        except Exception as e: logger.warning(f"Failed to edit card: {e}")

    elif act == "BINGO":
        gid, mid = data[1], int(data[2])
        if gid not in ACTIVE_GAMES: 
            await q.answer("Game already ended.")
            return
        g = ACTIVE_GAMES[gid]
        card = g['player_cards'].get(uid)
        
        if check_win(card):
            # Finalize win immediately
            await finalize_win(context, gid, uid, False)
        else:
            await q.answer("❌ False BINGO! All 5 required cells must be marked.")

async def lobby_countdown(ctx, chat_id, msg_id):
    """Handles the 5-second countdown timer in the lobby message."""
    global LOBBY_STATE
    LOBBY_STATE = {'is_running': True, 'msg_id': msg_id, 'chat_id': chat_id}
    
    for i in range(5, 0, -1):
        if not LOBBY_STATE['is_running']: return
        try: 
            # Update countdown with current player count
            p_count = len(PENDING_PLAYERS)
            msg_text = f"⏳ Game starts in **{i}s**...\n({p_count} real players joined)"
            await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg_text, parse_mode='Markdown')
        except: pass
        await asyncio.sleep(1)
        
    await start_new_game(ctx)

# --- Standard Commands ---
async def start(u, c): 
    create_or_update_user(u.effective_user.id, u.effective_user.username, u.effective_user.first_name)
    await u.message.reply_text("👋 Welcome to Addis Bingo! The house is ready to play. /play to start your card purchase (20 Br).")

async def deposit(u, c):
    await u.message.reply_text(f"🏦 To deposit, send the funds to: **0927922721** (M-Pesa/Bank).\n\n"
                              f"Send the receipt and your Telegram ID (`{u.effective_user.id}`) to the admin: @{ADMIN_USERNAME}", parse_mode='Markdown')

async def balance(u, c):
    b = get_user_data(u.effective_user.id).get('balance', 0)
    await u.message.reply_text(f"💰 Your Balance: **{b:.2f} Br**", parse_mode='Markdown')

async def ap_dep(u, c):
    """Admin command to top up user balance (Example: /ap_dep <user_id> <amount>)."""
    if u.effective_user.id != ADMIN_USER_ID: return
    try: 
        target_id = int(c.args[0])
        amount = float(c.args[1])
        update_balance(target_id, amount) 
        await u.message.reply_text(f"✅ Deposited {amount:.2f} Br to user ID {target_id}")
    except (ValueError, IndexError): 
        await u.message.reply_text("Usage: /ap_dep <user_id> <amount>")

def main():
    if not TOKEN:
        logger.error("FATAL: TELEGRAM_TOKEN environment variable not set.")
        return

    app = Application.builder().token(TOKEN).build()
    
    # User Commands
    app.add_handler(CommandHandler("play", play_command))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("deposit", deposit))
    app.add_handler(CommandHandler("balance", balance))
    
    # Admin Command
    app.add_handler(CommandHandler("ap_dep", ap_dep))
    
    # Callback Query Handler (for button interactions)
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        # Webhook setup for hosting environment like Render
        logger.info(f"Running via webhook at {RENDER_EXTERNAL_URL}/{TOKEN}")
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')
    else:
        # Polling for local development/testing
        logger.info("Running via long polling.")
        app.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    main()
