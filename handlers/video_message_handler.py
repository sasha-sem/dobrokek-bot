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
    if not update:
        return
    
    if not update.message:
        return
    
    if not update.effective_user or update.effective_user.id not in WHITELIST:
        await update.message.reply_text("У вас нет доступа к использованию данного бота")
        return

    message = update.message
    if not message or not message.video:
        await update.message.reply_text("В сообщении не обнаружено видео")
        return
    try:
        await context.bot.send_video(
            chat_id=CHAT_ID,
            video=message.video,
            caption=f"{message.caption+"\n\n" if message.caption else ""}👤`{update.effective_user.first_name}`",
            parse_mode=ParseMode.MARKDOWN,
            has_spoiler=True,
            disable_notification=True
        )
        await update.message.reply_text("Успешно отправлено в канал.\nСпасибо за контент!")
        return
    except Exception as e:
        await update.message.reply_text("Не удалось отправить видео")
        print("Error: Couldn't send video: ", e)
