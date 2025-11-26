# Addis Bingo Bot - Version 32 (Simple In-Memory State)
# Author: Gemini
# Description: Telegram Bingo game bot using simple dictionary state.
# Goal: Provide a clean, non-persistent file to test the Render deployment issue.

import os
import time
import random
import logging
import asyncio

# --- Python-Telegram-Bot Imports ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- Configuration ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# !!! CHANGE THIS TO YOUR ACTUAL TELEGRAM USER ID !!!
ADMIN_USER_ID = 5887428731

# Configuration values
GAME_PRICE = 50.0  
INITIAL_BALANCE = 1000.0 
MIN_PLAYERS = 2  
GAME_INTERVAL_SECONDS = 300 
CALL_INTERVAL_SECONDS = 15 

# Environment variables (used for Webhook deployment on Render)
TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
PORT = int(os.environ.get("PORT", 8080))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "YOUR_RENDER_URL_HERE")
WEBHOOK_PATH = "/webhook"

# --- In-Memory Global State ---
# IMPORTANT: This state is NOT persistent and will reset on every redeploy or crash.
user_data_db = {}
global_state = {
    'current_game_id': 0,
    'current_numbers': [],
    'is_game_active': False,
    'last_game_time': time.time() - GAME_INTERVAL_SECONDS,
    'last_call_time': time.time() - CALL_INTERVAL_SECONDS,
    'active_players': {}, 
    'total_prize_pool': 0.0
}

# ==============================================================================
# ----------------------------- STATE MANAGEMENT (IN-MEMORY) ---------------------
# ==============================================================================

def get_user_data(user_id: int) -> dict:
    """Retrieves or creates user data from in-memory dictionary."""
    user_id_str = str(user_id)
    if user_id_str not in user_data_db:
        user_data_db[user_id_str] = {
            'user_id': user_id,
            'balance': INITIAL_BALANCE,
            'cards': {}, 
            'registration_time': time.time()
        }
    return user_data_db[user_id_str]

def save_user_data(user_data: dict):
    """Saves user data back to the in-memory dictionary (no file I/O)."""
    user_data_db[str(user_data['user_id'])] = user_data


# ==============================================================================
# ----------------------------- BINGO GAME LOGIC -------------------------------
# ==============================================================================

def generate_bingo_card():
    """Generates a standard 5x5 Bingo card (B-I-N-G-O)."""
    card = {}
    
    card['B'] = random.sample(range(1, 16), 5)
    card['I'] = random.sample(range(16, 31), 5)
    N_samples = random.sample(range(31, 46), 4)
    card['N'] = [N_samples[0], N_samples[1], 0, N_samples[2], N_samples[3]] # 0 is Free Space
    card['G'] = random.sample(range(46, 61), 5)
    card['O'] = random.sample(range(61, 76), 5)
    
    card_matrix = []
    columns = ['B', 'I', 'N', 'G', 'O']
    for row in range(5):
        row_list = [card[col][row] for col in columns]
        card_matrix.append(row_list)
        
    return card_matrix

def check_for_bingo(card_matrix, called_numbers):
    """Checks the card matrix against called numbers for a Bingo win."""
    def is_covered(number):
        return number == 0 or number in called_numbers

    # Check Rows, Columns, and Diagonals
    for i in range(5):
        # Rows
        if all(is_covered(card_matrix[i][j]) for j in range(5)): return True
        # Columns
        if all(is_covered(card_matrix[j][i]) for j in range(5)): return True

    # Diagonals
    if all(is_covered(card_matrix[i][i]) for i in range(5)): return True
    if all(is_covered(card_matrix[i][4 - i]) for i in range(5)): return True

    return False

def format_card(card_matrix, called_numbers):
    """Formats the card into a readable string with marked numbers."""
    output = "   B  I  N  G  O\n"
    output += " -------------------\n"
    
    for row in range(5):
        row_str = ""
        for col in range(5):
            num = card_matrix[row][col]
            
            if num == 0:
                cell = " F"
            elif num in called_numbers:
                cell = f" X" 
            else:
                cell = f"{num:02d}"
                
            row_str += f"|{cell}"
        output += row_str + "|\n"
        output += " -------------------\n"
        
    return f"```{output}```"


# ==============================================================================
# ----------------------------- HANDLERS ---------------------------------------
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and registers the user."""
    user = update.effective_user
    user_data = get_user_data(user.id) 
    
    await update.message.reply_html(
        rf"ሰላም, {user.mention_html()}! እንኳን ወደ Addis Bingo Bot በደህና መጡ።"
        f"\n\nየአሁኑ ባላንስዎ: **{user_data['balance']:.2f} ብር**"
        f"\nየእርስዎ መለያ (Account ID) **{user.id}** ነው።"
        f"\n\nለመጀመር `/buycard` ብለው የቢንጎ ካርድ ይግዙ።\n"
        f"የጨዋታ ሁኔታን ለማየት `/status` ይጠቀሙ።"
    )

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the user's current balance."""
    user = update.effective_user
    user_data = get_user_data(user.id) 
    
    await update.message.reply_text(
        f"የርስዎ ባላንስ: **{user_data['balance']:.2f} ብር**",
        parse_mode="Markdown"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the current game status, numbers called, and time until next call/game."""
    
    current_time = time.time()
    
    response = f"**🎲 Addis Bingo Game Status 🎲**\n\n"
    
    if global_state.get('is_game_active'):
        called_count = len(global_state['current_numbers'])
        last_number = global_state['current_numbers'][-1] if called_count > 0 else "None"
        
        last_call_time = global_state.get('last_call_time', current_time)
        time_until_next_call = max(0, int(CALL_INTERVAL_SECONDS - (current_time - last_call_time)))
        
        response += (
            f"**Game ID:** #{global_state['current_game_id']}\n"
            f"**Status:** 🔴 Active\n"
            f"**Prize Pool:** {global_state['total_prize_pool']:.2f} ብር\n"
            f"**Called Numbers:** {called_count} / 75\n"
            f"**Last Number:** {last_number}\n\n"
            f"➡️ Next number call in: **{time_until_next_call} seconds**"
        )
    else:
        active_players_count = len(global_state.get('active_players', {}))
        last_game_time = global_state.get('last_game_time', current_time - GAME_INTERVAL_SECONDS)
        
        time_since_last_game = current_time - last_game_time
        time_remaining = max(0, int(GAME_INTERVAL_SECONDS - time_since_last_game))
        
        if time_remaining == 0 and active_players_count >= MIN_PLAYERS:
            next_game_info = "Ready to start next game now!"
        elif time_remaining == 0:
             next_game_info = f"Waiting for {MIN_PLAYERS - active_players_count} more players to join."
        else:
            next_game_info = f"Next game starting in: **{time_remaining} seconds** (if {MIN_PLAYERS} players are ready)"


        response += (
            f"**Status:** 🟢 Inactive (Waiting for players)\n"
            f"**Current Pool:** {global_state['total_prize_pool']:.2f} ብር\n"
            f"**Active Players (Cards Bought):** {active_players_count}\n\n"
            f"**Next Game Check:**\n{next_game_info}"
        )
        
    await update.message.reply_text(response, parse_mode="Markdown")

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles deposit requests by forwarding the request to the admin."""
    user = update.effective_user
    
    if len(context.args) < 1:
        await update.message.reply_text(
            "ለመሙላት የሚፈልጉትን መጠን (Amount) ይላኩ።\n"
            "ምሳሌ: `/deposit 500`"
        )
        return

    try:
        amount = float(context.args[0])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("ትክክለኛ የብር መጠን ያስገቡ።")
        return

    admin_message = (
        f"**New Deposit Request**\n"
        f"User: {user.full_name} (@{user.username or 'N/A'})\n"
        f"User ID: `{user.id}`\n"
        f"Requested Amount: **{amount:.2f} ብር**"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("Approve Deposit", callback_data=f"approve_{user.id}_{amount}"),
            InlineKeyboardButton("Reject Deposit", callback_data=f"reject_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await context.bot.send_message(
            chat_id=ADMIN_USER_ID, 
            text=admin_message, 
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
        await update.message.reply_text(
            f"የ {amount:.2f} ብር መሙያ ጥያቄዎን አስገብተዋል።"
        )
    except Exception as e:
        logger.error(f"Failed to send deposit request to admin: {e}")
        await update.message.reply_text("በአሁኑ ሰዓት ጥያቄዎን ማስተናገድ አልቻልንም።")

async def buycard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allows a user to buy a new bingo card."""
    user = update.effective_user
    user_data = get_user_data(user.id) 
    
    if user_data['balance'] < GAME_PRICE:
        await update.message.reply_text(
            f"ካርድ ለመግዛት በቂ ባላንስ የለዎትም። ({GAME_PRICE:.2f} ብር ያስፈልግዎታል።)"
        )
        return

    current_game_id = global_state.get('current_game_id', 0)
    
    game_id_str = str(current_game_id)
    user_cards_for_game = user_data['cards'].setdefault(game_id_str, {})
        
    if len(user_cards_for_game) >= 1:
         await update.message.reply_text(
            f"ለዚህ ዙር ጨዋታ (Game #{current_game_id}) ካርድ ገዝተዋል።"
        )
         return

    user_data['balance'] -= GAME_PRICE
    new_card_matrix = generate_bingo_card()
    
    card_id = f"{int(time.time())}_{random.randint(100, 999)}"
    
    user_cards_for_game[card_id] = new_card_matrix
    
    global_state['active_players'][str(user.id)] = current_game_id
    global_state['total_prize_pool'] += GAME_PRICE

    save_user_data(user_data) # Saves to memory
    # No need to save global state if we are non-persistent.

    await update.message.reply_text(
        f"በስኬት አዲስ የቢንጎ ካርድ ገዝተዋል! (Game #{current_game_id})\n"
        f"ዋጋ: {GAME_PRICE:.2f} ብር ተቀንሷል።\n"
        f"አዲስ ባላንስዎ: **{user_data['balance']:.2f} ብር**\n\n"
        f"የእርስዎ ካርድ:\n{format_card(new_card_matrix, global_state['current_numbers'])}",
        parse_mode="Markdown"
    )
    
    await check_and_start_game(context)


async def showcards_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows all active bingo cards for the current game."""
    user = update.effective_user
    user_data = get_user_data(user.id) 
    
    current_game_id = global_state.get('current_game_id', 0)
    game_id_str = str(current_game_id)
    
    cards = user_data['cards'].get(game_id_str, {})

    if not cards:
        await update.message.reply_text(
            f"ለአሁኑ ዙር ጨዋታ (Game #{current_game_id}) የገዙት ካርድ የለም።"
        )
        return

    called_numbers = global_state.get('current_numbers', [])
    
    response = f"**የእርስዎ ካርዶች ለ Game #{current_game_id}:**\n"
    response += f"የተጠሩ ቁጥሮች ብዛት: **{len(called_numbers)}**\n\n"
    
    for card_id, card_matrix in cards.items():
        is_bingo = check_for_bingo(card_matrix, called_numbers)
        status = " (BINGO!!!)" if is_bingo else ""
        response += f"__Card ID: {card_id}{status}__\n"
        response += format_card(card_matrix, called_numbers)
        response += "\n"

    await update.message.reply_text(response, parse_mode="Markdown")

async def bingo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the user calling 'BINGO'."""
    user = update.effective_user

    if not global_state.get('is_game_active'):
        await update.message.reply_text("አሁን ላይ ንቁ ጨዋታ የለም።")
        return

    if str(user.id) not in global_state['active_players']:
        await update.message.reply_text("ለዚህ ዙር ጨዋታ ንቁ ካርድ የለዎትም።")
        return
        
    user_data = get_user_data(user.id) 
    current_game_id = global_state.get('current_game_id', 0)
    game_id_str = str(current_game_id)
    
    user_cards = user_data['cards'].get(game_id_str, {})

    if not user_cards:
        await update.message.reply_text("የቢንጎ ካርድ የለዎትም።")
        return

    winner_card = None
    for card_id, card_matrix in user_cards.items():
        if check_for_bingo(card_matrix, global_state['current_numbers']):
            winner_card = card_matrix
            break
            
    if winner_card:
        prize_pool = global_state['total_prize_pool']
        win_amount = prize_pool
        
        user_data['balance'] += win_amount
        save_user_data(user_data) 

        # Reset global state for the next game
        global_state['is_game_active'] = False
        global_state['last_game_time'] = time.time()
        global_state['current_numbers'] = []
        global_state['active_players'] = {}
        global_state['total_prize_pool'] = 0.0
        
        # Remove the job queue task for number calling
        job_name = f"call_numbers_{current_game_id}"
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
             job.schedule_removal()
        
        winner_message = (
            f"🎉🎉🎉 **BINGO! BINGO! BINGO!** 🎉🎉🎉\n"
            f"አሸናፊ: **{user.full_name}**\n"
            f"የተሸለመው የብር መጠን: **{win_amount:.2f} ብር**\n"
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=winner_message, parse_mode="Markdown")
        
    else:
        await update.message.reply_text(
            f"ይቅርታ {user.full_name}፣ እስካሁን በካርድዎ ላይ ቢንጎ የለም።"
        )

# ==============================================================================
# ----------------------------- ADMIN COMMANDS ---------------------------------
# ==============================================================================

def is_admin(user_id: int) -> bool:
    """Checks if the user is the bot administrator."""
    return user_id == ADMIN_USER_ID

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline button clicks for deposit approvals."""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.edit_message_text("Only the bot admin can perform this action.")
        return

    data = query.data.split('_')
    action = data[0]
    target_user_id = int(data[1])
    
    try:
        user_data = get_user_data(target_user_id) 
        
        if action == 'approve':
            amount = float(data[2])
            
            user_data['balance'] += amount
            save_user_data(user_data) 
            
            await query.edit_message_text(
                query.message.text + f"\n\n✅ **APPROVED** by Admin.\nNew Balance: {user_data['balance']:.2f} ብር"
            )

            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"🎉 **የ {amount:.2f} ብር መሙያ ጥያቄዎ ፀድቋል!** 🎉\nአዲስ ባላንስዎ: **{user_data['balance']:.2f} ብር**",
                parse_mode="Markdown"
            )
            
        elif action == 'reject':
            await query.edit_message_text(
                query.message.text + "\n\n❌ **REJECTED** by Admin."
            )
            
            await context.bot.send_message(
                chat_id=target_user_id,
                text="❌ **የመሙያ ጥያቄዎ ውድቅ ተደርጓል።**",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Error in admin callback handler: {e}")
        await query.edit_message_text(f"An error occurred: {e}")


# ==============================================================================
# ----------------------------- GAME LOOP AND SCHEDULING -----------------------
# ==============================================================================

async def check_and_start_game(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Checks if minimum players are met and the interval has passed to start a new game."""
    
    if global_state.get('is_game_active'):
        return

    current_time = time.time()
    last_game_time = global_state.get('last_game_time', 0)
    
    if current_time - last_game_time < GAME_INTERVAL_SECONDS:
        return

    active_players_count = len(global_state.get('active_players', {}))

    if active_players_count >= MIN_PLAYERS:
        # Start a new game
        global_state['current_game_id'] += 1
        global_state['is_game_active'] = True
        global_state['current_numbers'] = []
        global_state['last_call_time'] = time.time() 
        
        start_message = (
            f"🔔🔔🔔 **NEW BINGO GAME STARTED!** (Game #{global_state['current_game_id']})\n"
            f"**Prize Pool:** {global_state['total_prize_pool']:.2f} ብር\n"
        )
        
        player_ids = [int(uid) for uid in global_state['active_players'].keys()]
        for user_id in player_ids:
            try:
                await context.bot.send_message(chat_id=user_id, text=start_message, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Failed to send start message to user {user_id}: {e}")
        
        # Schedule the number calling task
        context.job_queue.run_repeating(
            call_number_task, 
            interval=CALL_INTERVAL_SECONDS,
            first=CALL_INTERVAL_SECONDS, 
            name=f"call_numbers_{global_state['current_game_id']}",
            data=global_state['current_game_id']
        )
        
        logger.info(f"Game {global_state['current_game_id']} started.")

async def call_number_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Task to call a new number during an active game."""
    
    if not global_state.get('is_game_active'):
        context.job.schedule_removal()
        return

    if len(global_state['current_numbers']) >= 75:
        # End game if all numbers are called and no one won
        global_state['is_game_active'] = False
        global_state['last_game_time'] = time.time()
        global_state['current_numbers'] = []
        global_state['active_players'] = {}
        global_state['total_prize_pool'] = 0.0
        
        player_ids = [int(uid) for uid in global_state['active_players'].keys()]
        for user_id in player_ids:
            try:
                await context.bot.send_message(chat_id=user_id, text="** ጨዋታው ተጠናቀቀ! አሸናፊ የለም!** በሚቀጥለው ዙር ይሞክሩ።", parse_mode="Markdown")
            except Exception:
                pass
                
        context.job.schedule_removal()
        return

    all_numbers = set(range(1, 76))
    called_set = set(global_state['current_numbers'])
    available_numbers = list(all_numbers - called_set)
    
    if not available_numbers: return

    new_number = random.choice(available_numbers)
    global_state['current_numbers'].append(new_number)
    global_state['last_call_time'] = time.time() 

    column = next(c for n, c in [(1, "B"), (16, "I"), (31, "N"), (46, "G"), (61, "O")] if new_number >= n and new_number <= n + 14)

    call_message = (
        f"**New Call!**\n"
        f"Column: **{column}**\n"
        f"Number: **{new_number}**\n"
        f"Total Called: **{len(global_state['current_numbers'])}**\n\n"
        f"**If you have BINGO, type /BINGO now!**"
    )
    
    player_ids = [int(uid) for uid in global_state['active_players'].keys()]
    for user_id in player_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=call_message, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Failed to send call message to user {user_id}: {e}")

# ==============================================================================
# ----------------------------- MAIN EXECUTION ---------------------------------
# ==============================================================================

async def post_init(application: Application):
    """Setup initial tasks and webhook after application object is created."""
    
    application.job_queue.run_repeating(
        lambda context: check_and_start_game(context), 
        interval=60, 
        first=5, 
        name="game_scheduler"
    )
    logger.info("Game scheduler task started.")
    
    if RENDER_URL == "YOUR_RENDER_URL_HERE":
        logger.critical("FATAL: RENDER_EXTERNAL_URL is not set correctly. Bot cannot set webhook.")
        # We raise the error here to ensure the bot fails fast if ENV is bad
        raise ValueError("RENDER_EXTERNAL_URL is not set.")
        
    await application.bot.set_webhook(url=f"{RENDER_URL}{WEBHOOK_PATH}")
    logger.info(f"Webhook set to {RENDER_URL}{WEBHOOK_PATH}")


def main() -> None:
    """Starts the bot in Webhook mode for Render deployment."""
    if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.critical("FATAL: TELEGRAM_TOKEN environment variable is not set. Exiting.")
        return

    logger.info("Starting Webhook Mode on Render...")
    application = Application.builder().token(TOKEN).build()
    application.post_init = post_init
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("status", status_command)) 
    application.add_handler(CommandHandler("deposit", deposit_command))
    application.add_handler(CommandHandler("buycard", buycard_command))
    application.add_handler(CommandHandler("showcards", showcards_command))
    application.add_handler(CommandHandler("bingo", bingo_command))
    
    # Admin handlers
    application.add_handler(CallbackQueryHandler(admin_callback_handler))

    # CRITICAL FIX: application.run_webhook is a blocking call that handles 
    # the event loop itself, which avoids the RuntimeError conflict.
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=f"{RENDER_URL}{WEBHOOK_PATH}"
    )


if __name__ == "__main__":
    # We call main() directly. The application.run_webhook inside handles the loop.
    main()
