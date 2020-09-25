import csv
import html
import logging
import os
import sys
import traceback
from urllib.parse import urlsplit, urlunsplit
from functools import wraps
from io import BytesIO, StringIO
from secrets import LIST_OF_ADMINS, TOKEN
from threading import Thread

import requests
from pyshorteners import Shortener
from telegram import ParseMode
from telegram.ext import (CommandHandler, Filters, MessageHandler,
                          PicklePersistence, Updater)
from telegram.utils.helpers import mention_html
from tldextract import extract
from urlextract import URLExtract

logging.basicConfig(format='%(asctime)s - %(levelname)s\n%(message)s', level=logging.INFO)
logger = logging.getLogger("filelock")
logger.setLevel(logging.ERROR) # filelock can stfu

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

def link(url, text):
    return f'<a href="{url}">{text}</a>'

def short(url):
    return Shortener().tinyurl.short(url)

def add_bypass(url, special=False):
    '''Puts together links with various bypass strategies

    special arg is depricated and will be removed eventually'''

    if not url.startswith('http'):
        url = f'http://{url}'

    text = []
    try:
        text.append(link(f'https://outline.com/{short(url)}', 'Outline'))
    except requests.exceptions.Timeout:
        pass

    amp_url = amp(url)
    if amp_url:
        text.append(amp_url)

    text.append(archive(url))

    try:
        text.append(dot_trick(url))
    except requests.exceptions.Timeout:
        pass

    return '\n\n'.join(text)

def dot_trick(url):
    '''Returns the url with a dot after the tld. Seems to maybe trick cookies or something. IDK'''
    domain = get_domain(url)
    dotted_url = f'{domain}.'.join(url.partition(domain)[::2])
    shortened_url = short(dotted_url)
    return link(shortened_url, 'Dot Trick')

def archive(url):
    '''Returns the url of the latest snapshot if avalable on varius archive sites'''
    urls = []
    try:
        r = requests.get(f'http://archive.org/wayback/available?url={url}')
        archive_org_url = r.json().get('archived_snapshots', {}).get('closest', {}).get('url')
        if archive_org_url:
            urls.append(link(archive_org_url, 'Wayback Machine'))
    except requests.exceptions.Timeout:
        pass
    urls.append(link(f'http://archive.is/newest/{url}', 'archive.is'))
    return '\n\n'.join(urls)

def amp(url):
    '''Returns the url wrapped up in AMP stuff'''
    amp_candidates = [(0, '')]
    urls = []

    url_parts = urlsplit(url)
    url_parts = url_parts._replace(scheme='')

    urls.append(urlunsplit(url_parts)[2:]) # domain as is

    domain = get_domain(url)

    url_parts = url_parts._replace(netloc=domain)
    urls.append(urlunsplit(url_parts)[2:]) # naked domain

    url_parts = url_parts._replace(netloc='amp.' + domain)
    urls.append(urlunsplit(url_parts)[2:]) # amp subdomain

    # There exist other ways for sites to serve up amp content. It's just a pain to figure them all out.

    for url in urls:
        amp_url_templates = [
            # f'https://cdn.ampproject.org/v/s/{url}?amp_js_v=a3&amp_gsa=1&_amp=true',
            f'https://cdn.ampproject.org/v/s/{url}?amp_js_v=a3&amp_gsa=1&_amp=true&outputType=amp',
            # f'https://{url}&outputType=amp'
        ]
        for template in amp_url_templates:
            try:
                r = requests.get(template)
                size = len(r.content)
                if r.status_code == 200:
                    amp_candidates.append((size, link(short(amp_url), 'AMP')))
            except (requests.exceptions.Timeout):
                pass
    return sorted(amp_candidates)[-1][1]

@log
def incoming(update, context):
    '''Check incoming stream for urls and put attempted bypasses on them if they are in the list of domains that need it'''
    extractor = URLExtract()
    extractor.update_when_older(7) # gets the latest list of TLDs from iana.org every 7 days
    urls = extractor.find_urls(update.effective_message.text, check_dns=True)
    active_dict = context.chat_data.get('active domains', {})
    for url in urls:
        if get_domain(url) not in active_dict:
            continue
        text = add_bypass(url, active_dict[get_domain(url)])
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
            text = add_bypass(context.chat_data.get('last url'))
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
    '''Make settings avaliable as a CSV file'''
    chat_id = update.effective_message.chat_id
    sio = StringIO() # csv insists on strs...
    w = csv.writer(sio)
    w.writerows(context.chat_data['active domains'].items())
    sio.seek(0)
    bio = BytesIO(sio.read().encode('utf8')) # ...but TG demands bytes
    bio.name = f'{chat_id}.csv'
    context.bot.send_document(chat_id=chat_id, document=bio)

@log
def import_urls(update, context):
    '''Import settings previously exported with /export'''
    #TODO: this


if __name__ == '__main__':
    persistence = PicklePersistence(filename='bot.persist', on_flush=False)
    updater = Updater(token=TOKEN, persistence=persistence, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('include', include))
    dispatcher.add_handler(CommandHandler('remove', remove))
    dispatcher.add_handler(CommandHandler('list', list_active_domains))
    dispatcher.add_handler(CommandHandler('export', export_urls))
    dispatcher.add_handler(CommandHandler('r', restart, filters=Filters.user(user_id=LIST_OF_ADMINS)))
    dispatcher.add_handler(CommandHandler('data', chat_data, filters=Filters.user(user_id=LIST_OF_ADMINS)))
    dispatcher.add_handler(MessageHandler(Filters.text, incoming))
    dispatcher.add_error_handler(error)

    logging.info('outline bot started')
    updater.start_polling()
    updater.idle()
