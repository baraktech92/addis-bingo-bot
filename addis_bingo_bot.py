# Addis (አዲስ) Bingo Bot - V19.2: Payout & Bot Fixes
# Features: TTS Voice announcement, Inflated Payout Pool (Real + Bot contributions), 
#           and Guaranteed Bot Win when real players < 5.

import os
import logging
import json
import base64
import asyncio
import random
import time
import uuid 
import io      # For in-memory file handling (WAV creation)
import struct   # For binary data manipulation (WAV header)
# NOTE: The 'requests' library is required for the actual API call
# and is assumed to be available in this environment.
try:
    import requests
except ImportError:
    # This block handles deployment scenarios where requests might not be directly available
    requests = None 

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler
)
import firebase_admin
from firebase_admin import credentials, firestore

# --- Configuration & Environment ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')
V2_SECRETS = os.environ.get('V2_SECRETS')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') 
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '') # Required for TTS API call

# Attempt to extract Admin ID for privileged commands
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
WINNER_PAYOUT_AMOUNT = 40 
JACKPOT_PERCENTAGE = 0.10 
MIN_REAL_PLAYERS = 5 
CALL_DELAY = 2.40    
COLUMNS = ['B', 'I', 'N', 'G', 'O']
MAX_PRESET_CARDS = 200

# TTS Constants
TTS_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"
SAMPLE_RATE = 24000 # Standard sample rate for Gemini TTS

# --- Emojis & UI Colors ---
EMOJI_UNMARKED_UNCALLED = '⚫' 
EMOJI_CALLED_UNMARKED = '🟢'   
EMOJI_MARKED = '✅'           
EMOJI_FREE = '⭐️'             
EMOJI_CARD = '🃏'
EMOJI_BALANCE = '💵'

# --- Amharic TTS Concept Dictionary (1-75) ---
AMHARIC_NUMBERS = {
    1: "አንድ", 2: "ሁለት", 3: "ሶስት", 4: "አራት", 5: "አምስት", 6: "ስድስት", 7: "ሰባት", 8: "ስምንት", 9: "ዘጠኝ", 10: "አስር",
    11: "አስራ አንድ", 12: "አስራ ሁለት", 13: "አስራ ሶስት", 14: "አስራ አራት", 15: "አስራ አምስት", 16: "አስራ ስድስት", 17: "አስራ ሰባት", 18: "አስራ ስምንት", 19: "አስራ ዘጠኝ", 20: "ሃያ",
    21: "ሃያ አንድ", 22: "ሃያ ሁለት", 23: "ሃያ ሶስት", 24: "ሃያ አራት", 25: "ሃያ አምስት", 26: "ሃያ ስድስት", 27: "ሃያ ሰባት", 28: "ሃያ ስምንት", 29: "ሃያ ዘጠኝ", 30: "ሰላሳ",
    31: "ሰላሳ አንድ", 32: "ሰላሳ ሁለት", 33: "ሰላሳ ሶስት", 34: "ሰላሳ አራት", 35: "ሰላሳ አምስት", 36: "ሰላሳ ስድስት", 37: "ሰላሳ ሰባት", 38: "ሰላሳ ስምንት", 39: "ሰላሳ ዘጠኝ", 40: "አርባ",
    41: "አርባ አንድ", 42: "አርባ ሁለት", 43: "አርባ ሶስት", 44: "አርባ አምስት", 45: "አርባ ስድስት", 46: "አርባ ሰባት", 47: "አርባ ስምንት", 48: "አርባ ዘጠኝ", 49: "ሃምሳ", 50: "ሃምሳ",
    51: "ሃምሳ አንድ", 52: "ሃምሳ ሁለት", 53: "ሃምሳ ሶስት", 54: "ሃምሳ አራት", 55: "ሃምሳ አምስት", 56: "ሃምሳ ስድስት", 57: "ሃምሳ ሰባት", 58: "ሃምሳ ስምንት", 59: "ሃምሳ ዘጠኝ", 60: "ስልሳ",
    61: "ስልሳ አንድ", 62: "ስልሳ ሁለት", 63: "ስልሳ ሶስት", 64: "ስልሳ አራት", 65: "ስልሳ አምስት", 66: "ስልሳ ስድስት", 67: "ስልሳ ሰባት", 68: "ስልሳ ስምንት", 69: "ስልሳ ዘጠኝ", 70: "ሰባ",
    71: "ሰባ አንድ", 72: "ሰባ ሁለት", 73: "ሰባ ሶስት", 74: "ሰባ አራት", 75: "ሰባ አምስት"
}
def get_amharic_number_text(num: int) -> str:
    """Returns the Amharic phonetic text for a number 1-75."""
    # Ensure correct text for single-digit numbers for the TTS prompt
    if num < 10:
        return f"Say the number {num} in Amharic: {AMHARIC_NUMBERS.get(num, str(num))}"
    return f"Say the number {num} in Amharic: {AMHARIC_NUMBERS.get(num, str(num))}"


# --- TTS Helper Functions (Unchanged) ---

def create_wav_bytes(pcm_data: bytes, sample_rate: int = SAMPLE_RATE) -> io.BytesIO:
    """Converts raw 16-bit signed PCM audio data into a WAV byte stream."""
    buffer = io.BytesIO()
    data_size = len(pcm_data)
    
    # 1. RIFF header
    buffer.write(b'RIFF')
    buffer.write(struct.pack('<I', 36 + data_size)) # File size (36 + data)
    buffer.write(b'WAVE')

    # 2. FMT sub-chunk
    buffer.write(b'fmt ')
    buffer.write(struct.pack('<I', 16)) # Sub-chunk size (16 for PCM)
    buffer.write(struct.pack('<H', 1))  # Audio format (1 for PCM)
    buffer.write(struct.pack('<H', 1))  # Number of channels (1)
    buffer.write(struct.pack('<I', sample_rate)) # Sample rate
    buffer.write(struct.pack('<I', sample_rate * 2)) # Byte rate (SampleRate * NumChannels * BitsPerSample/8)
    buffer.write(struct.pack('<H', 2)) # Block align (NumChannels * BitsPerSample/8)
    buffer.write(struct.pack('<H', 16)) # Bits per sample (16)

    # 3. DATA sub-chunk
    buffer.write(b'data')
    buffer.write(struct.pack('<I', data_size)) # Data size
    buffer.write(pcm_data)

    buffer.seek(0)
    return buffer

async def call_gemini_tts(text: str) -> io.BytesIO | None:
    """
    Calls the Gemini TTS API and returns the audio as a WAV BytesIO object.
    """
    if not requests:
        logger.error("The 'requests' library is not available. Cannot perform TTS.")
        return None
    
    if not GEMINI_API_KEY:
        logger.error("TTS API Key is missing. Cannot generate audio.")
        return None

    full_text_prompt = f"In a firm, clear voice, say: Bingo, Number {text}" 

    payload = {
        "contents": [{
            "parts": [{"text": full_text_prompt}]
        }],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                # Using 'Charon' (Informative) for a clear caller voice
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Charon"}} 
            }
        },
        "model": "gemini-2.5-flash-preview-tts"
    }

    try:
        response = requests.post(
            TTS_URL, 
            headers={'Content-Type': 'application/json'},
            data=json.dumps(payload),
            timeout=10 # Set a reasonable timeout
        )
        response.raise_for_status() 
        
        result = response.json()
        
        part = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0]
        audio_data_b64 = part.get('inlineData', {}).get('data')
        
        if audio_data_b64:
            pcm_data = base64.b64decode(audio_data_b64)
            return create_wav_bytes(pcm_data, SAMPLE_RATE)
        
        logger.error("TTS API response missing audio data.")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"TTS API request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"TTS API processing error: {e}")
        return None


# --- Global Game State (Unchanged) ---
ACTIVE_GAMES = {} 
PENDING_PLAYERS = {} 

# --- Bot Player Management ---
BOT_ID_COUNTER = -1 
def create_bot_player() -> tuple[int, str]:
    """
    Creates a unique bot ID and a name that looks like a numerical user ID
    to conceal its bot identity.
    """
    global BOT_ID_COUNTER
    BOT_ID_COUNTER -= 1
    # Generate a random 8-digit hex string for anonymity
    fake_user_id = str(uuid.uuid4()).split('-')[0].upper()
    name = f"User{fake_user_id}"
    return BOT_ID_COUNTER, name

def get_bot_count(real_players_count: int) -> int:
    """
    Determines the number of bots needed based on real players. 
    Dramatically increases bot count when real players < 5 for guaranteed bot win.
    """
    if real_players_count >= MIN_REAL_PLAYERS:
        return 0 
    
    # GUARANTEED BOT WIN LOGIC (High Bot Count)
    if real_players_count == 0: return 0 
    if real_players_count == 1: return random.randint(12, 15) # 13-16 total players
    if real_players_count in (2, 3): return random.randint(15, 20) # 17-23 total players
    if real_players_count == 4: return random.randint(20, 25) # 24-29 total players
    return 0

# --- Database Setup (Unchanged) ---
DB_STATUS = "Unknown"
db = None

try:
    if V2_SECRETS and '|' in V2_SECRETS:
        _, firebase_b64 = V2_SECRETS.split('|', 1)
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
GLOBAL_STATE_DOC = 'addis_bingo_global/state'

def get_user_data(user_id: int) -> dict:
    if not db: return {'balance': 0}
    doc = db.collection(USERS_COLLECTION).document(str(user_id)).get()
    if doc.exists:
        return doc.to_dict()
    return {'balance': 0, 'new_user': True}

def update_balance(user_id: int, amount: float):
    if not db: return
    user_ref = db.collection(USERS_COLLECTION).document(str(user_id))
    try:
        user_ref.set({'balance': firestore.Increment(amount), 'last_update': firestore.SERVER_TIMESTAMP}, merge=True)
    except Exception as e:
        logger.error(f"Error updating balance for user {user_id}: {e}")

def update_jackpot(amount: float):
    if not db: return
    jackpot_ref = db.document(GLOBAL_STATE_DOC)
    try:
        jackpot_ref.set({'jackpot': firestore.Increment(amount)}, merge=True)
    except Exception as e:
        logger.error(f"Error updating jackpot: {e}")

async def get_jackpot_amount(context: ContextTypes.DEFAULT_TYPE) -> float:
    if not db: return 0.0
    try:
        doc = db.document(GLOBAL_STATE_DOC).get()
        return doc.to_dict().get('jackpot', 0.0) if doc.exists else 0.0
    except Exception as e:
        logger.error(f"Error reading jackpot: {e}")
        return 0.0

# --- Game Utilities (Unchanged) ---

def get_preset_card(card_number: int):
    """Generates a deterministic 5x5 Bingo card based on the card_number (seed)."""
    random.seed(card_number)
    
    card_data = {
        'data': {
            'B': sorted(random.sample(range(1, 16), 5)),
            'I': sorted(random.sample(range(16, 31), 5)),
            'N': sorted(random.sample(range(31, 46), 5)), 
            'G': sorted(random.sample(range(46, 61), 5)),
            'O': sorted(random.sample(range(61, 76), 5)),
        },
        'marked': {(2, 2): True}, 
        'called': {(2, 2): True}, 
        'status': 'active',
        'number': card_number
    }
    # Reset random seed after use to avoid affecting other global operations
    random.seed(time.time())
    return card_data

def get_card_value(card, col_idx, row_idx):
    """Gets the number/FREE from the card based on indices."""
    if col_idx == 2 and row_idx == 2: return "FREE"
    col_letter = COLUMNS[col_idx]
    
    col_list = card['data'][col_letter]
    if col_letter == 'N':
        # N column has the free space at (2, 2), so we skip one index
        return col_list[row_idx] if row_idx < 2 else col_list[row_idx - 1] if row_idx > 2 else 'FREE'
    
    return card['data'][col_letter][row_idx]

def get_card_position(card, value):
    """Finds the coordinates (c, r) of a number on the card."""
    for c_idx, col_letter in enumerate(COLUMNS):
        if col_letter == 'N':
            for r_idx, v in enumerate(card['data'][col_letter]):
                if v == value:
                    # Adjust row index for the free space in N column
                    return c_idx, r_idx if r_idx < 2 else r_idx + 1
            if value == 'FREE':
                return 2, 2
        else:
            try:
                r_idx = card['data'][col_letter].index(value)
                return c_idx, r_idx
            except ValueError:
                continue
    return None, None

def check_win(card):
    """Checks if the given card has a winning line (5 marked)."""
    def is_marked(c, r): return card['marked'].get((c, r), False)
    
    for r in range(5):
        if all(is_marked(c, r) for c in range(5)): return True
    for c in range(5):
        if all(is_marked(c, r) for r in range(5)): return True
    if all(is_marked(i, i) for i in range(5)): return True
    if all(is_marked(i, 4 - i) for i in range(5)): return True
    
    return False

# --- Keyboard and Formatting (Unchanged) ---

def build_card_keyboard(card, game_id, msg_id):
    """Generates the inline keyboard for a specific player's card."""
    keyboard = []
    # Header row
    header = [InlineKeyboardButton(f"~{col}~", callback_data=f"ignore_header") for col in COLUMNS]
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
                # Marked: Display checkmark with white text (using Markdown bold for contrast)
                label = f"{EMOJI_MARKED} **{value}**" 
                callback_data = f"MARK|{game_id}|{msg_id}|{card['number']}|{c}|{r}" 
            elif is_called:
                # Called but unmarked: Green circle with white text
                label = f"{EMOJI_CALLED_UNMARKED} **{value}**" 
                callback_data = f"MARK|{game_id}|{msg_id}|{card['number']}|{c}|{r}" 
            else:
                # Uncalled and unmarked: Black circle with dark gray text
                label = f"{EMOJI_UNMARKED_UNCALLED} {value}" 
                callback_data = f"ignore_not_called" 
            
            # Use shorter labels for a more compact appearance
            if len(str(value)) > 2 and value != "FREE":
                 label = label.replace(f"**{value}**", str(value)) 
            
            row.append(InlineKeyboardButton(label, callback_data=callback_data))
        keyboard.append(row)
    
    # Action button
    action_label = "✅ BINGO CLAIMED ✅" if card['status'] == 'bingo_claimed' else "🚨 CALL BINGO! 🚨"
    action_data = f"BINGO|{game_id}|{msg_id}|{card['number']}"
    keyboard.append([InlineKeyboardButton(action_label, callback_data=action_data)])
    
    return InlineKeyboardMarkup(keyboard)

def format_called_numbers(called_numbers):
    """Formats the list of called numbers for the board message."""
    if not called_numbers:
        return "--- ቁጥሮች ገና አልተጠሩም ---"
    
    output = []
    for num in called_numbers:
        col_letter = next(col for col, (start, end) in [
            ('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), 
            ('G', (46, 60)), ('O', (61, 75))
        ] if start <= num <= end)
        output.append(f"**{col_letter}**-{num}")
    
    # Show last 10-12 calls only for compactness
    history = output[-10:]
    history_text = ", ".join(history)
    
    return f"**Recent Calls:** {history_text}"

def get_current_call_text(num):
    """Returns the formatted text for the current number being called (Reduced height/V19.0)."""
    if num is None:
        return "**📢 በመጠባበቅ ላይ... (Waiting)**\n"
        
    col_letter = next(col for col, (start, end) in [
        ('B', (1, 15)), ('I', (16, 30)), ('N', (31, 45)), 
        ('G', (46, 60)), ('O', (61, 75))
    ] if start <= num <= end)
    
    amharic_text = AMHARIC_NUMBERS.get(num, str(num))

    # Use reduced vertical spacing
    call_text = (
        f"**\n📢 CURRENT CALL ({CALL_DELAY:.2f}s Delay):\n"
        f"👑 {col_letter} - {num} 👑\n" 
        f"({col_letter} - {amharic_text})\n**"
    )
    return call_text

# --- Game Loop ---

async def run_game_loop(context: ContextTypes.DEFAULT_TYPE, game_id, players_starting_data, bot_players):
    """The main asynchronous game loop."""
    
    real_player_ids = [pid for pid, _ in players_starting_data]
    
    if game_id not in ACTIVE_GAMES:
        logger.error(f"Game {game_id} started but does not exist in ACTIVE_GAMES.")
        return

    game_data = ACTIVE_GAMES[game_id]
    game_data['status'] = 'running'
    game_data['bot_players'] = bot_players 
    
    available_numbers = list(range(1, 76))
    random.shuffle(available_numbers)
    
    # Send initial messages to real players
    all_players_count = len(real_player_ids) + len(bot_players)
    for pid in real_player_ids:
        await context.bot.send_message(pid, f"✅ **Game {game_id} is starting!** {all_players_count} total players.")
    
        # Board message (will be edited)
        board_msg = await context.bot.send_message(pid, "**🎰 የተጠሩ ቁጥሮች ታሪክ (History) 🎰**", parse_mode='Markdown')
        game_data['board_messages'][pid] = board_msg.message_id
        
        # Card message (only one card per real player in this setup)
        card = game_data['player_cards'][pid]
        card_text = get_current_call_text(None) + f"{EMOJI_CARD} **ቢንጎ ካርድ #{card['number']}**\n_🟢 አረንጓዴ ቁጥር ሲመጣ ይጫኑ!_"
        
        card_msg = await context.bot.send_message(pid, card_text, parse_mode='Markdown')
        msg_id = card_msg.message_id 
        game_data['card_messages'][pid] = msg_id
        
        kb = build_card_keyboard(card, game_id, msg_id)
        await context.bot.edit_message_reply_markup(chat_id=pid, message_id=msg_id, reply_markup=kb)

    await asyncio.sleep(2) # Short delay before first call

    # 2. Start calling numbers
    for num in available_numbers:
        if game_id not in ACTIVE_GAMES or ACTIVE_GAMES[game_id]['status'] != 'running':
            break

        game_data['called'].append(num)

        # --- TTS CALL ---
        amharic_text = get_amharic_number_text(num)
        tts_text_for_api = f"{num}. {amharic_text}" # Send the number and Amharic text to the model
        
        # Use simple exponential backoff for the API call
        wav_audio_buffer = None
        for attempt in range(3):
            wav_audio_buffer = await call_gemini_tts(tts_text_for_api)
            if wav_audio_buffer:
                break
            await asyncio.sleep(2 ** attempt) # Backoff: 1s, 2s, 4s

        # 3. Update all player and bot cards with the called number
        # Real Players: Update 'called' status
        for pid in real_player_ids:
            card = game_data['player_cards'][pid]
            c, r = get_card_position(card, num)
            if c is not None:
                card['called'][(c, r)] = True 

        # Bots: Check for win and auto-mark
        winning_bot_id = None
        if len(real_player_ids) < MIN_REAL_PLAYERS:
            # Bot Win Guarantee is enforced by the high number of bots
            for bot_id, bot_data in bot_players.items():
                card = bot_data['card']
                c, r = get_card_position(card, num)
                
                if c is not None:
                    card['called'][(c, r)] = True
                    card['marked'][(c, r)] = True 
                    
                    if check_win(card):
                        winning_bot_id = bot_id
                        break
        
        # 4. Announce and update visuals for real players
        current_call_text = get_current_call_text(num)
        board_history_text = format_called_numbers(game_data['called'])
        full_board_text = f"**🎰 የተጠሩ ቁጥሮች ታሪክ (History) 🎰**\n{board_history_text}"
        full_card_text = current_call_text + f"{EMOJI_CARD} **ቢንጎ ካርድ**\n_🟢 Tap Green to Mark!_"

        for pid in real_player_ids:
            # Send Voice Message First (WAV file from in-memory buffer)
            if wav_audio_buffer:
                try:
                    wav_audio_buffer.seek(0) # Reset buffer pointer for each send
                    await context.bot.send_voice(chat_id=pid, voice=wav_audio_buffer, caption=f"👑 {COLUMNS[0:5][(num-1)//15]} - {num} 👑")
                except Exception as e:
                    logger.error(f"Failed to send voice message to {pid}: {e}")

            # Update Board
            try:
                await context.bot.edit_message_text(
                    chat_id=pid, message_id=game_data['board_messages'][pid], 
                    text=full_board_text, parse_mode='Markdown'
                )
            except Exception: pass

            # Update Card
            card = game_data['player_cards'][pid]
            msg_id = game_data['card_messages'][pid]
            kb = build_card_keyboard(card, game_id, msg_id)
            try:
                await context.bot.edit_message_text(
                    chat_id=pid, message_id=msg_id, 
                    text=full_card_text, reply_markup=kb, parse_mode='Markdown'
                )
            except Exception: pass
            
        # 5. Check Bot Win (Immediate end if bot wins)
        if winning_bot_id:
            await finalize_win(context, game_id, winning_bot_id, is_bot_win=True)
            return

        await asyncio.sleep(CALL_DELAY) 

    # 6. Game finished without BINGO
    if game_id in ACTIVE_GAMES:
        for pid in real_player_ids:
            await context.bot.send_message(pid, "💔 ጨዋታው ተጠናቀቀ (Game Over). ሁሉም ቁጥሮች ተጠርተዋል።")
        del ACTIVE_GAMES[game_id]


async def finalize_win(context: ContextTypes.DEFAULT_TYPE, game_id: str, winner_id: int, is_bot_win: bool = False):
    """
    Handles the conclusion of the game and prize distribution.
    The payout amount is based on all (Real + Bot) contributions.
    Bot wins: Real player jackpot contributions are returned to the global pot.
    """
    if game_id not in ACTIVE_GAMES: return
    
    game_data = ACTIVE_GAMES[game_id]
    game_data['status'] = 'finished'
    
    # Jackpot share is the inflated amount based on all players
    jackpot_share = game_data['jackpot_share']
    real_player_count = len(game_data['players'])
    
    if is_bot_win:
        # Bot wins. Payout is skipped. The illusionary prize is calculated for display.
        winner_data = game_data['bot_players'][winner_id]
        winner_name = winner_data['name'] # The anonymous ID name
        
        # 1. Reverse the deduction of real players' jackpot contribution
        real_contribution_to_jackpot = real_player_count * CARD_COST * JACKPOT_PERCENTAGE
        update_jackpot(real_contribution_to_jackpot) 
        
        total_prize = WINNER_PAYOUT_AMOUNT + jackpot_share # The inflated prize for display
        
        # Win message is designed to look like a real player won
        win_msg = (
            f"🎉 BINGO!!! 🎉\n\n"
            f"አሸናፊ (Winner): **{winner_name}**\n"
            f"**Base Prize: {WINNER_PAYOUT_AMOUNT:.2f} Br**\n"
            f"**Jackpot Share: {jackpot_share:.2f} Br**\n"
            f"**Total Won: {total_prize:.2f} Br**\n"
            f"_The prize money has been added to the winner's balance._" 
        )
        
    else:
        # Real player wins. The player gets the full inflated prize.
        total_prize = WINNER_PAYOUT_AMOUNT + jackpot_share
        update_balance(winner_id, total_prize) 
        user_data = get_user_data(winner_id)
        winner_name = user_data.get('first_name', f"Player {winner_id}")
        
        win_msg = (
            f"🎉 BINGO!!! 🎉\n\n"
            f"አሸናፊ (Winner): **{winner_name}**\n"
            f"**Base Prize: {WINNER_PAYOUT_AMOUNT:.2f} Br**\n"
            f"**Jackpot Share: {jackpot_share:.2f} Br**\n"
            f"**Total Won: {total_prize:.2f} Br Added to your balance!**"
        )
        
    # Notify all real players
    for pid in game_data['players']:
        await context.bot.send_message(pid, win_msg, parse_mode='Markdown')
        try:
            # Remove the card keyboard
            msg_id = game_data['card_messages'][pid]
            await context.bot.edit_message_reply_markup(
                chat_id=pid, message_id=msg_id, reply_markup=None
            )
        except Exception:
            pass
            
    # Remove game from active list
    del ACTIVE_GAMES[game_id]

# --- Telegram Handlers ---

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompts player to choose one card number (1-200)."""
    user_id = update.effective_user.id
    
    if user_id in PENDING_PLAYERS or any(user_id in g['players'] for g in ACTIVE_GAMES.values()):
        await update.message.reply_text("⏳ እባክዎ አሁን ያለው ጨዋታ እስኪጠናቀቅ ይጠብቁ።")
        return

    data = get_user_data(user_id)
    if data.get('balance', 0) < CARD_COST:
        await update.message.reply_text(f"⛔ በቂ ቀሪ ሂሳብ የለዎትም። ለመጫወት {CARD_COST} Br ያስፈልጋል።")
        return

    # Create card selection keyboard (10 buttons for preview)
    keyboard = []
    # Ensure selected cards are not currently in use by other pending players
    used_cards = set(PENDING_PLAYERS.values())
    available_cards = [c for c in range(1, MAX_PRESET_CARDS + 1) if c not in used_cards]
    
    card_options = random.sample(available_cards, min(10, len(available_cards)))
    
    row = []
    for card_num in card_options:
        row.append(InlineKeyboardButton(f"Card #{card_num}", callback_data=f"SELECT_CARD|{card_num}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    
    # Add refresh button if there are many cards left, or a manual input option
    keyboard.append([InlineKeyboardButton("Refresh Card Options", callback_data="SELECT_CARD_REFRESH")])
    keyboard.append([InlineKeyboardButton("Choose Specific Card (1-200)", callback_data="SELECT_CARD_MANUAL")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "**💳 እባክዎ የሚፈልጉትን የቢንጎ ካርድ ቁጥር (1-200) ይምረጡ።**\n"
        f"ዋጋ: {CARD_COST} Br. (አንድ ካርድ በአንድ ጨዋታ ብቻ)",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_card_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the card number selection callback and adds player to lobby."""
    query = update.callback_query
    user_id = query.from_user.id
    try: await query.answer()
    except Exception: pass
    
    data = query.data.split('|')
    action = data[0]

    if action == "SELECT_CARD_REFRESH":
        await play_command(update, context) 
        return

    if action == "SELECT_CARD_MANUAL":
        await query.edit_message_text("እባክዎ የሚፈልጉትን የካርድ ቁጥር (1 እስከ 200) ያስገቡ። ለምሳሌ: `145`")
        return

    if action == "SELECT_CARD":
        card_number = int(data[1])
        if not (1 <= card_number <= MAX_PRESET_CARDS):
            await query.edit_message_text(f"❌ ልክ ያልሆነ የካርድ ቁጥር። እባክዎ ከ1 እስከ {MAX_PRESET_CARDS} ይምረጡ።")
            return
        
        if card_number in PENDING_PLAYERS.values():
            await query.edit_message_text(f"❌ ካርድ #{card_number} በሌላ ተጫዋች ተመርጧል። እባክዎ ሌላ ይምረጡ።")
            return
            
        total_cost = CARD_COST
        
        # 1. Deduct cost and update jackpot
        user_data = get_user_data(user_id)
        if user_data.get('balance', 0) < total_cost:
            await query.edit_message_text(f"⛔ በቂ ቀሪ ሂሳብ የለዎትም። {total_cost} Br ያስፈልጋል።")
            return

        update_balance(user_id, -total_cost)
        jackpot_contribution = total_cost * JACKPOT_PERCENTAGE
        update_jackpot(jackpot_contribution)
        
        # 2. Add player to lobby
        PENDING_PLAYERS[user_id] = card_number
        
        # 3. Inform player
        current_players = len(PENDING_PLAYERS)
        
        # Determine current game status (with bots in mind)
        bot_count = get_bot_count(current_players)
        
        if current_players < MIN_REAL_PLAYERS:
            # Bot Win Guarantee is active
            status_message = (
                f"⏳ **{current_players} የሰው ተጫዋቾች ተቀላቅለዋል።**\n"
                f"በቂ ተጫዋች ስለሌለ፣ {bot_count} የኮምፒውተር ተጫዋቾች ይካተታሉ።\n"
                f"_❗ የኮምፒውተር ተጫዋች ሊያሸንፍ ይችላል።_"
            )
        else:
            status_message = (
                f"✅ **{current_players} የሰው ተጫዋቾች ተቀላቅለዋል።**\n"
                f"በቂ ተጫዋች አለ! ጨዋታው በቅርቡ ይጀምራል።"
            )
            
        await query.edit_message_text(
            f"✅ **{total_cost} Br ተቀንሷል** (Card #{card_number}).\n"
            f"**የጃክፖት አስተዋፅኦ:** {jackpot_contribution:.2f} Br\n\n"
            f"{status_message}"
        )
        
        # 4. Check if game can start (immediately if MIN_REAL_PLAYERS are reached or if minimum time has passed)
        if current_players >= MIN_REAL_PLAYERS or (current_players > 0 and current_players < MIN_REAL_PLAYERS and current_players == len(PENDING_PLAYERS.values())):
            await start_new_game(context)


async def start_new_game(context: ContextTypes.DEFAULT_TYPE):
    """Initializes and starts a new game."""
    
    # Take all current pending players (simple lobby)
    players_starting_data = list(PENDING_PLAYERS.items())
    real_player_ids = [pid for pid, _ in players_starting_data]
    real_player_count = len(real_player_ids)
    
    if real_player_count == 0: return # Safety check
    
    game_id = f"G{int(time.time()*1000)}"
    
    # 1. Add Bot Players if needed
    bot_players = {}
    bot_count = get_bot_count(real_player_count)
    
    if bot_count > 0:
        bot_used_card_numbers = [card_num for _, card_num in players_starting_data]
        available_card_pool = [c for c in range(1, MAX_PRESET_CARDS + 1) if c not in bot_used_card_numbers]
        
        for _ in range(bot_count):
            bot_id, bot_name = create_bot_player()
            if available_card_pool:
                card_num = random.choice(available_card_pool)
                available_card_pool.remove(card_num)
            else:
                card_num = random.randint(1, MAX_PRESET_CARDS) 
            
            bot_players[bot_id] = {
                'name': bot_name,
                'card': get_preset_card(card_num)
            }
            
    # 2. Prepare initial game state
    game_data = {
        'players': real_player_ids, # Only real player IDs here
        'player_cards': {pid: get_preset_card(card_num) for pid, card_num in players_starting_data},
        'card_messages': {pid: None for pid in real_player_ids},
        'board_messages': {},
        'called': [],
        'status': 'pending',
        'bot_players': bot_players
    }
    
    # Clear the lobby
    for pid in real_player_ids:
        del PENDING_PLAYERS[pid]
        
    ACTIVE_GAMES[game_id] = game_data
    
    # 3. Calculate and Dedicate Jackpot Share (Updated Logic - Inflated Prize Pool)
    # The prize pool should be based on ALL players (real + bot) as requested.
    all_players_count = real_player_count + bot_count
    
    # This is the total jackpot prize to be displayed and awarded (inflated)
    jackpot_prize_for_game = all_players_count * CARD_COST * JACKPOT_PERCENTAGE
    game_data['jackpot_share'] = jackpot_prize_for_game
    
    # Deduct the REAL player contribution only from the global jackpot
    # (Since bot contributions are illusory for display purposes)
    real_contribution_to_jackpot = real_player_count * CARD_COST * JACKPOT_PERCENTAGE
    update_jackpot(-real_contribution_to_jackpot) 

    # 4. Start the game loop asynchronously
    asyncio.create_task(run_game_loop(context, game_id, players_starting_data, bot_players))

# --- Remaining Handlers (Unchanged) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user = update.effective_user
    data = get_user_data(user.id)
    if data.get('new_user'):
        update_balance(user.id, 0.0)
        
    jackpot = await get_jackpot_amount(context)
    
    await update.message.reply_text(
        f"ሰላም {user.first_name}!\n"
        f"እንኳን ወደ አዲስ ቢንጎ በደህና መጡ።\n"
        f"የእርስዎ ቀሪ ሂሳብ: **{data.get('balance', 0.0):.2f} Br**\n"
        f"**💰 Progressive Jackpot:** **{jackpot:.2f} Br**\n\n"
        f"ለመጀመር: /play\n"
        f"ቀሪ ሂሳብ: /balance\n"
        f"መመሪያዎች: /instructions",
        parse_mode='Markdown'
    )

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /balance command to display current user balance."""
    user = update.effective_user
    data = get_user_data(user.id)
    jackpot = await get_jackpot_amount(context)
    
    await update.message.reply_text(
        f"{EMOJI_BALANCE} **የእርስዎ ቀሪ ሂሳብ (Your Balance):** **{data.get('balance', 0.0):.2f} Br**\n"
        f"**💰 Progressive Jackpot:** **{jackpot:.2f} Br**\n"
        f"ለመጫወት: /play",
        parse_mode='Markdown'
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles button presses (SELECT_CARD, MARK and BINGO)."""
    query = update.callback_query
    user_id = query.from_user.id
    try: await query.answer()
    except Exception: pass
    
    data = query.data.split('|')
    action = data[0]

    if action in ['SELECT_CARD', 'SELECT_CARD_MANUAL', 'SELECT_CARD_REFRESH']:
        await handle_card_selection(update, context)
        return

    if action in ['ignore_header', 'ignore_free', 'ignore_not_called']:
        return 

    if len(data) < 4:
        await query.answer("Invalid action data.")
        return

    game_id = data[1]
    msg_id = int(data[2])
    card_number = int(data[3]) 

    if game_id not in ACTIVE_GAMES or ACTIVE_GAMES[game_id]['status'] != 'running':
        await query.answer("ይህ ጨዋታ ተጠናቋል።")
        return

    game_data = ACTIVE_GAMES[game_id]
    
    if user_id not in game_data['player_cards'] or game_data['player_cards'][user_id]['number'] != card_number:
        await query.answer("You are not a player in this game or this card is invalid.")
        return
        
    card = game_data['player_cards'][user_id]
    
    if card['status'] != 'active':
        await query.answer("ይህ ካርድ ቢንጎ ተጠይቆበታል።")
        return

    if action == 'MARK':
        c, r = int(data[4]), int(data[5])
        pos = (c, r)
        
        value = get_card_value(card, c, r)
        is_called_in_game = value == 'FREE' or value in game_data['called']

        if not is_called_in_game:
            await query.answer("ቁጥሩ ገና አልተጠራም! (Wait for the number to be called/green)")
            return
            
        # Toggle mark state
        if not card['marked'].get(pos, False):
            card['marked'][pos] = True
        else:
            card['marked'][pos] = False

        # Re-render the card message
        current_num = game_data['called'][-1] if game_data['called'] else None
        new_text = get_current_call_text(current_num) + f"{EMOJI_CARD} **ቢንጎ ካርድ #{card['number']}**\n_🟢 Tap Green to Mark!_"
        kb = build_card_keyboard(card, game_id, msg_id)
        
        try:
            await context.bot.edit_message_text(
                chat_id=user_id, message_id=msg_id, 
                text=new_text, reply_markup=kb, parse_mode='Markdown'
            )
        except Exception:
            pass
        await query.answer("Marked!" if card['marked'][pos] else "Unmarked")

    elif action == 'BINGO':
        if check_win(card):
            card['status'] = 'bingo_claimed'
            
            # Update card message immediately to reflect claim
            current_num = game_data['called'][-1] if game_data['called'] else None
            new_text = get_current_call_text(current_num) + f"{EMOJI_CARD} **ቢንጎ ካርድ #{card['number']}**\n🎉 **BINGO CLAIMED!** Waiting for verification..."
            kb = build_card_keyboard(card, game_id, msg_id)
            try:
                 await context.bot.edit_message_text(
                    chat_id=user_id, message_id=msg_id, 
                    text=new_text, reply_markup=kb, parse_mode='Markdown'
                )
            except Exception:
                pass
                
            await finalize_win(context, game_id, user_id, is_bot_win=False)
        else:
            await query.answer("❌ Bingo Check Failed. ❌ እባክዎ በትክክል መሙላትዎን ያረጋግጡ።")


async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /deposit command."""
    user_id = update.effective_user.id
    jackpot = await get_jackpot_amount(context)
    
    await update.message.reply_text(
        f"**💰 Progressive Jackpot:** **{jackpot:.2f} Br**\n\n"
        f"ገንዘብ ለማስገባት (Deposit) ወደ **09xxxxxxxx** (Admin's number) ይላኩ።\n"
        f"ከዚያም ደረሰኙን ለ{ADMIN_USERNAME} ይላኩ።\n"
        f"የእርስዎ መለያ መታወቂያ (User ID): `{user_id}`",
        parse_mode='Markdown'
    )

async def instructions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /instructions command."""
    await update.message.reply_text(
        "**የጨዋታ መመሪያዎች (Game Instructions)**\n"
        f"1. **/play** ብለው በመጫን የሚፈልጉትን **የካርድ ቁጥር (1-{MAX_PRESET_CARDS})** ይምረጡ። {CARD_COST} Br ከሂሳብዎ ይቀነሳል።\n"
        f"2. {MIN_REAL_PLAYERS} የሰው ተጫዋቾች ካልተሟሉ የኮምፒውተር ተጫዋቾች (Bots) ይጨመራሉ።\n"
        f"3. ቁጥር ሲጠራ **ድምፅ (Voice)** ይላካል ካርድዎ ላይ ካለ፣ **አረንጓዴ (🟢)** ይሆናል።\n"
        f"4. አረንጓዴውን ቁጥር **ይጫኑ (Tap)**። ሲጫኑ **ምልክት (✅)** ያደርጋል። (Marked numbers are displayed in **white text**).\n"
        "5. በአግድም፣ በአቀባዊ ወይም በሰያፍ (Row, Column, Diagonal) **5 ቁጥሮችን** ሙሉ ምልክት (✅) ያድርጉ።\n"
        "6. 5 ቁጥሮችን ሲያሟሉ **'🚨 CALL BINGO! 🚨'** የሚለውን ቁልፍ ይጫኑ።\n"
        f"7. ትክክል ከሆኑ **{WINNER_PAYOUT_AMOUNT} Br** መሰረታዊ ሽልማት እና የጃክፖት ድርሻ ያሸንፋሉ!\n"
        f"_ማሳሰቢያ: ሁሉንም ቁጥሮች በትክክል ምልክት ማድረግዎን ያረጋግጡ!_" ,
        parse_mode='Markdown'
    )

# --- Admin Commands (Unchanged) ---

async def admin_set_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to set a user's balance: /admin_set_balance <user_id> <amount>"""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        if db:
            user_ref = db.collection(USERS_COLLECTION).document(str(target_id))
            user_ref.set({'balance': amount, 'last_update': firestore.SERVER_TIMESTAMP}, merge=True)
            await update.message.reply_text(f"✅ User `{target_id}` balance set to **{amount:.2f} Br**.")
        else:
            await update.message.reply_text("❌ Database not connected.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: Invalid format or operation failed. Use: `/admin_set_balance <user_id> <amount>`. Error: {e}")

async def admin_get_jackpot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to view the current jackpot amount."""
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    jackpot = await get_jackpot_amount(context)
    await update.message.reply_text(f"**💰 Current Global Jackpot:** **{jackpot:.2f} Br**", parse_mode='Markdown')


# --- Application Setup ---
def main():
    """Starts the bot using Webhook for deployment or Polling for local."""
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN is not set.")
        return

    logger.info(f"Firebase DB Status: {DB_STATUS}")
    logger.info(f"Admin User ID: {ADMIN_USER_ID}")
    logger.info(f"TTS API Key set: {bool(GEMINI_API_KEY)}")

    app = Application.builder().token(TOKEN).build()

    # Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("balance", balance_command)) 
    app.add_handler(CommandHandler("play", play_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("instructions", instructions_command))
    
    # Admin Commands
    app.add_handler(CommandHandler("admin_set_balance", admin_set_balance_command))
    app.add_handler(CommandHandler("admin_get_jackpot", admin_get_jackpot_command))


    # Callback Handler (for inline buttons)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # --- DEPLOYMENT FIX & RUN ---
    PORT = int(os.environ.get('PORT', '8080'))
    
    if RENDER_EXTERNAL_URL:
        WEBHOOK_PATH = TOKEN 
        WEBHOOK_URL = f'{RENDER_EXTERNAL_URL}/{WEBHOOK_PATH}'

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=WEBHOOK_URL
        )
        logger.info(f"Webhook set to: {WEBHOOK_URL}. Bot is running on port {PORT}.")

    else:
        logger.info("RENDER_EXTERNAL_URL not set. Running in polling mode.")
        app.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
