# Addis (አዲስ) Bingo - V1: Auto-Webhook
# This bot is designed to run on a cloud server (like Render) using a webhook.
# This version automatically detects its own URL from Render.

import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Configuration ---
# Your Telegram API Token MUST be set as an Environment Variable named TELEGRAM_TOKEN
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    logging.error("TELEGRAM_TOKEN environment variable not set.")
    
# Render provides the public URL in this environment variable
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL')

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Handlers: Functions for Commands and Messages ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    # Using the official Amharic Welcome Message (from our blueprint)
    welcome_message = "እንኳን ወደ አዲስ ቢንጎ በደህና መጡ!"
    await update.message.reply_text(welcome_message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /help is issued."""
    help_text = (
        "አዲስ ቢንጎ የጨዋታ መመሪያ:\n"
        "/deposit - የገንዘብ ማስገቢያ መመሪያዎችን ያገኛሉ\n"
        "/withdraw - ገንዘብ ማውጣት ይጠይቁ\n"
        "/play - አዲስ ጨዋታ ለመጀመር ወይም ለመቀላቀል"
    )
    await update.message.reply_text(help_text)

async def echo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echoes the user message. This will be replaced by game logic in V2."""
    if update.message and update.message.text:
        # For V1, we just echo the text back to confirm the connection works.
        await update.message.reply_text(f"የእርስዎ መልዕክት: {update.message.text}")

# --- Main Bot Initialization ---

def main() -> None:
    """Start the bot."""
    # Check for the token before proceeding
    if not TOKEN:
        print("Bot failed to start: TELEGRAM_TOKEN missing.")
        return

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TOKEN).build()

    # Register all our handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_message))

    # --- Deployment Configuration for Render ---
    # Render requires us to run the bot as a web service using webhooks.
    PORT = int(os.environ.get('PORT', '8080'))
    
    if RENDER_EXTERNAL_URL:
        # This is the production setup for Render
        logger.info(f"Starting bot in production on port {PORT}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN, # This is the "path" part of the URL
            webhook_url=f'{RENDER_EXTERNAL_URL}/{TOKEN}' # This is the *full* URL
        )
    else:
        # This is a fallback for local testing (which we aren't doing, but it's good practice)
        logger.warning("RENDER_EXTERNAL_URL not set. Running in local polling mode.")
        # application.run_polling() # We comment this out, we only want to run on Render
        logger.error("Bot cannot start without RENDER_EXTERNAL_URL in production.")

if __name__ == '__main__':
    main()
