from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import os

CHAT_ID = os.environ['CHANNEL_ID']
WHITELIST = [int(user_id) for user_id in os.environ['WHITELIST'].split(',')]

seen_media_groups: set[str] = set()


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("Got message with photo")
    if not update:
        return

    if not update.message:
        return

    if not update.effective_user or update.effective_user.id not in WHITELIST:
        await update.message.reply_text("У вас нет доступа к использованию данного бота")
        return

    message = update.message
    if not message.photo:
        await update.message.reply_text("В сообщении не обнаружено фото")
        return

    if message.media_group_id:
        if message.media_group_id not in seen_media_groups:
            seen_media_groups.add(message.media_group_id)
            await update.message.reply_text("Мемы нужно отправлять по одному")
        return

    try:
        await context.bot.send_photo(
            chat_id=CHAT_ID,
            photo=message.photo[-1].file_id,
            caption=f"{message.caption+"\n\n" if message.caption else ""}👤`{update.effective_user.first_name}`",
            parse_mode=ParseMode.MARKDOWN,
            has_spoiler=True,
            disable_notification=True
        )
        await update.message.reply_text("Успешно отправлено в канал.\nСпасибо за контент!")
        return
    except Exception as e:
        await update.message.reply_text("Не удалось отправить фото")
        print("Error: Couldn't send photo: ", e)
