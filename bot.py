from telegram import ParseMode
from telegram.ext import Updater
from telegram.utils.helpers import mention_html
from telegram.ext import CommandHandler, MessageHandler
from telegram.ext import Filters
from urlextract import URLExtract
import os, sys
import html
import logging
import traceback
from threading import Thread
from functools import wraps
from secrets import TOKEN, LIST_OF_ADMINS

logging.basicConfig(format='%(asctime)s - %(levelname)s\n%(message)s', level=logging.INFO)

updater = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher

def error(update, context):
    '''send tracebacks to the dev'''
    devs = LIST_OF_ADMINS
    if not update:
        return
    trace = "".join(traceback.format_tb(sys.exc_info()[2]))
    payload = ""
    if update.effective_user:
        payload += f' with the user {mention_html(update.effective_user.id, update.effective_user.first_name)}'
    if update.effective_chat:
        payload += f' within the chat <i>{html.escape(str(update.effective_chat.title))}</i>'
        if update.effective_chat.username:
            payload += f' (@{update.effective_chat.username})'
    if update.poll:
        payload += f' with the poll id {update.poll.id}.'
    text = f"Hey.\n The error <code>{html.escape(str(context.error))}</code> happened{payload}. The full traceback:\n\n<code>{html.escape(trace)}</code>"
    for dev_id in devs:
        context.bot.send_message(dev_id, text, parse_mode=ParseMode.HTML)
    raise

def log(func):
    '''decorator that logs who said what to the bot'''
    @wraps(func)
    def wrapped(update, context, *args, **kwargs):
        id = update.effective_user.id
        name = update.effective_user.username
        logging.info(f'{name} ({id}) said:\n{update.effective_message.text}')
        return func(update, context, *args, **kwargs)
    return wrapped

@log
def restart(update, context):
    def stop_and_restart():
        '''Gracefully stop the Updater and replace the current process with a new one'''
        updater.stop()
        os.execl(sys.executable, sys.executable, *sys.argv)

    update.message.reply_text('Bot is restarting...')
    logging.info('Bot is restarting...')
    Thread(target=stop_and_restart).start()
    update.message.reply_text("...and we're back")
    logging.info("...and we're back")


@log
def incoming(update, context):
    '''check incoming stream for urls and slap an outline.com/ on the front of them'''
    extractor = URLExtract()
    extractor.update_when_older(7) # gets the latest list of TLDs from iana.org every 7 days
    urls = extractor.find_urls(update.effective_message.text)
    #TODO: only act on certain sites
    for url in urls:
        response = f'outline.com/{url}'
        logging.info(f'bot said:\n{response}')
        context.bot.send_message(chat_id=update.effective_message.chat_id, text=response, parse_mode=ParseMode.HTML)

dispatcher.add_handler(MessageHandler(Filters.text, incoming))
dispatcher.add_handler(CommandHandler('r', restart, filters=Filters.user(user_id=LIST_OF_ADMINS)))
dispatcher.add_error_handler(error)

logging.info('outline bot started')
updater.start_polling()
updater.idle()
