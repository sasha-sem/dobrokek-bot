from telegram import Update, helpers
from telegram.constants import ParseMode
import re
from telegram.ext import ContextTypes
from sources import Source, ShortsSource, ReelsSource, TikTokSource
import os

CHAT_ID = os.environ['CHANNEL_ID']
WHITELIST = [int(user_id) for user_id in os.environ['WHITELIST'].split(',')]

LINK_PATTERN = r"https?://[^\s]+"
DOWNLOAD_PATH = r"downloads"


async def handle_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("Got message with video")
    if update.effective_user.id not in WHITELIST:
        await update.message.reply_text("У вас нет доступа к использованию данного бота")
        return

    message = update.message
    if not message or not message.video:
        await update.message.reply_text("В сообщении не обнаружено видео")
        return
    try:
        await context.bot.send_video(CHAT_ID, message.video, caption=f"{helpers.escape_markdown(message.caption if message.caption else message.video.file_name)}\n\n👤`{update.effective_user.first_name}`", parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("Успешно отправлено в канал.\nСпасибо за контент!")
        return
    except Exception as e:
        await update.message.reply_text("Не удалось отправить видео")
        print("Error: Couldn't send video: ", e)
