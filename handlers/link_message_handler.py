from telegram import Update
from telegram.constants import ParseMode
import re
from telegram.ext import ContextTypes
from sources import Source, ShortsSource, ReelsSource, TikTokSource
import os

CHAT_ID = os.environ['CHANNEL_ID']
WHITELIST = [int(user_id) for user_id in os.environ['WHITELIST'].split(',')]

LINK_PATTERN = r"https?://[^\s]+"
DOWNLOAD_PATH = r"downloads"

SOURCES: list[type[Source]] = [ReelsSource(), ShortsSource(), TikTokSource()]


async def handle_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("Got message with link")
    if not update:
        return
    
    if not update.message:
        return
    
    if not update.effective_user or update.effective_user.id not in WHITELIST:
        await update.message.reply_text("У вас нет доступа к использованию данного бота")
        return

    message = update.message
    if not message or not message.entities:
        await update.message.reply_text("В сообщении не обнаружена ссылка")
        return

    links = re.findall(LINK_PATTERN, message.text)
    if len(links) == 0:
        await update.message.reply_text("В сообщении не обнаружена ссылка")

    url = links[0]
    filepath = ""
    for source in SOURCES:
        if not source.supports(url):
            continue
        try:
            filepath = source.download(url, DOWNLOAD_PATH)
            break
        except Exception as e:
            await update.message.reply_text("Не удалось загрузить видео")
            print("Error: Couldn't download video: ", e)
            return
    else:
        await update.message.reply_text("Данный источник не поддерживается")
        return

    with open(filepath, 'rb') as document:
        try:
            await context.bot.send_video(
                CHAT_ID,
                document,
                caption=f"{message.text}\n\n👤`{update.effective_user.first_name}`",
                parse_mode=ParseMode.MARKDOWN,
                has_spoiler=True,
                disable_notification=True
            )
            await update.message.reply_text("Успешно отправлено в канал.\nСпасибо за контент!")
            return
        except Exception as e:
            await update.message.reply_text("Не удалось скачать видео")
            print("Error: Couldn't send video: ", e)
