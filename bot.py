from telegram import ParseMode
from telegram.ext import Updater, PicklePersistence
from telegram.utils.helpers import mention_html
from telegram.ext import CommandHandler, MessageHandler
from telegram.ext import Filters
from urlextract import URLExtract
from tldextract import extract
from pyshorteners import Shortener, Shorteners
import os, sys
import csv
import html
import logging
import traceback
from io import StringIO, BytesIO
from threading import Thread
from functools import wraps
from secrets import TOKEN, LIST_OF_ADMINS

logging.basicConfig(format='%(asctime)s - %(levelname)s\n%(message)s', level=logging.INFO)

persistence = PicklePersistence(filename='bot.persist', on_flush=False)
updater = Updater(token=TOKEN, persistence=persistence, use_context=True)
dispatcher = updater.dispatcher

def error(update, context):
    '''Send tracebacks to the dev(s)'''
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
    '''Decorator that logs who said what to the bot'''
    @wraps(func)
    def wrapped(update, context, *args, **kwargs):
        id = update.effective_user.id
        name = update.effective_user.username
        logging.info(f'{name} ({id}) said:\n{update.effective_message.text}')
        return func(update, context, *args, **kwargs)
    return wrapped

@log
def restart(update, context):
    '''This sometimes loses the recent chat_data changes
       I think it's a bug in the library
       Clean shutdown doesn't have this problem'''
    def stop_and_restart():
        '''Gracefully stop the updater and replace the current process with a new one'''
        persistence.flush()
        updater.stop()
        os.execl(sys.executable, sys.executable, *sys.argv)

    update.message.reply_text('Bot is restarting...')
    logging.info('Bot is restarting...')
    Thread(target=stop_and_restart).start()
    update.message.reply_text("...and we're back")
    logging.info("...and we're back")

@log
def chat_data(update, context):
    '''See and clear chat_data'''
    text = str(context.chat_data)
    if context.args and context.args[0] == 'clear' and len(context.args) > 1:
        context.chat_data.pop(' '.join(context.args[1:]), None)
    say(text, update, context)

def say(text, update, context):
    logging.info(f'bot said:\n{text}')
    context.bot.send_message(chat_id=update.effective_message.chat_id, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

def get_domain(url):
    '''Get the domain.tld of url. Ignore any subdomains'''
    extract_result = extract(url)
    if extract_result.domain and extract_result.suffix:
        return extract_result.domain + '.' + extract_result.suffix
    return 'no domain'

def add_outline(url, short=False):
    if not url.startswith('http'):
        url = f'http://{url}'
    if short:
        url = Shortener(Shorteners.TINYURL).short(url)
    return f'https://outline.com/{url}'

@log
def incoming(update, context):
    '''Check incoming stream for urls and slap an outline.com/ on the front of some of them'''
    extractor = URLExtract()
    extractor.update_when_older(7) # gets the latest list of TLDs from iana.org every 7 days
    urls = extractor.find_urls(update.effective_message.text)
    active_dict = context.chat_data.get('active domains', {})
    for url in urls:
        if get_domain(url) not in active_dict:
            continue
        text = add_outline(url, active_dict[get_domain(url)])
        say(text, update, context)
    if len(urls) == 1:
        context.chat_data['last url'] = urls[0]

@log
def include(update, context):
    '''Add domains to the set that gets acted on'''
    active_dict = context.chat_data.get('active domains', {})
    try:
        if not context.args:
            domain = get_domain(context.chat_data.get('last url'))
            text = add_outline(context.chat_data.get('last url'))
        else:
            domain = get_domain(context.args[0])
            text = f'{context.args[0]} added'
    except TypeError:
        domain = text = 'no domain'
    if domain != 'no domain':
        active_dict[domain] = False
        if len(context.args) == 2:
            active_dict[domain] = bool(context.args[1])
        context.chat_data['active domains'] = active_dict
    say(text, update, context)

@log
def list_active_domains(update, context):
    '''List only. /list used to be an alias for /remove, but that's just asking for trouble'''
    active_dict = context.chat_data.get('active domains', {})
    text = '</code>\n<code>'.join((f'{url} {short}' for url, short in active_dict.items()))
    text = f"<code>{text}</code>"
    say(text, update, context)

@log
def remove(update, context):
    '''See and remove domains in/from active_dict if needed'''
    active_dict = context.chat_data.get('active domains', {})
    if not context.args and active_dict:
        text = '</code>\n<code>'.join((f'{url} {short}' for url, short in active_dict.items()))
        text = f"<code>{text}</code>"
    else:
        try:
            del active_dict[' '.join(context.args)]
            text = f"Removed {' '.join(context.args)}"
        except KeyError:
            text = f"Failed to remove {' '.join(context.args)}\n Check your spelling?"
    say(text, update, context)

@log
def export_urls(update, context):
    chat_id = update.effective_message.chat_id
    sio = StringIO() # csv insists on strs...
    w = csv.writer(sio)
    w.writerows(context.chat_data['active domains'].items())
    sio.seek(0)
    bio = BytesIO(sio.read().encode('utf8')) # ...but TG demands bytes
    bio.name = f'{chat_id}.csv'
    context.bot.send_document(chat_id=chat_id, document=bio)


dispatcher.add_handler(MessageHandler(Filters.text, incoming))
dispatcher.add_handler(CommandHandler('include', include))
dispatcher.add_handler(CommandHandler('remove', remove))
dispatcher.add_handler(CommandHandler('list', list_active_domains))
dispatcher.add_handler(CommandHandler('export', export_urls))
dispatcher.add_handler(CommandHandler('r', restart, filters=Filters.user(user_id=LIST_OF_ADMINS)))
dispatcher.add_handler(CommandHandler('data', chat_data, filters=Filters.user(user_id=LIST_OF_ADMINS)))
dispatcher.add_error_handler(error)

logging.info('outline bot started')
updater.start_polling()
updater.idle()
