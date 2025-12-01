from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from handlers import handle_link_message, handle_video_message
import os

BOT_TOKEN = os.environ['BOT_TOKEN']

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & (filters.Entity("url") | filters.Entity("text_link")), handle_link_message))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_message))
    print("Starting...")
    app.run_polling()

if __name__ == "__main__":
    main()