import csv
import html
import logging
import os
import sys
import traceback
import threading
import concurrent.futures
from urllib.parse import urlsplit, urlunsplit
from functools import wraps
from io import BytesIO, StringIO
from secrets import LIST_OF_ADMINS, TOKEN
from threading import Thread
from collections import defaultdict
from datetime import datetime, timedelta

import requests
from pyshorteners import Shortener
from telegram import ParseMode, ChatAction
from telegram.ext import (CommandHandler, Filters, MessageHandler,
                          PicklePersistence, Updater)
from telegram.utils.helpers import mention_html
from tldextract import extract
from urlextract import URLExtract
from babel.dates import format_timedelta

logging.basicConfig(format='%(asctime)s - %(levelname)s\n%(message)s', level=logging.INFO)
logger = logging.getLogger("filelock")
logger.setLevel(logging.ERROR) # filelock can stfu

# logging
def error(update, context):
    '''Send tracebacks to the dev(s)'''
    devs = LIST_OF_ADMINS
    if not update:
        return
    trace = "".join(traceback.format_tb(sys.exc_info()[2]))
    payload = ""
    if update.effective_user:
        payload += f' with the user {mention_html(update.effective_user.id, update.effective_user.first_name)}' # if it blows up here it's possibly because you used python < 3.6
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

# admin
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
    '''See and optionally clear chat_data'''
    text = str(context.chat_data)
    if context.args and context.args[0] == 'clear' and len(context.args) > 1:
        context.chat_data.pop(' '.join(context.args[1:]), None)
    say(html.escape(text), update, context)

# internal bot helper stuff
def send_typing_action(func):
    '''decorator that sends typing action while processing func command'''
    @wraps(func)
    def wrapped(update, context, *args, **kwargs):
        context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        return func(update, context, *args, **kwargs)
    return wrapped

def say(text, update, context):
    logging.info(f'bot said:\n{text}')
    if text:
        context.bot.send_message(chat_id=update.effective_message.chat_id, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

def link(url, text):
    return f'<a href="{url}">{text}</a>'

def short(url):
    return Shortener().tinyurl.short(url)

def get_domain(url):
    '''Get the domain.tld of url. Ignore any subdomains. Is smart about things like .co.uk'''
    extract_result = extract(url)
    if extract_result.domain and extract_result.suffix:
        return f'{extract_result.domain}.{extract_result.suffix}'.lower()
    return 'no domain'

def url_bookkeeping(context):
    '''Keeps a 3 day record of all urls and their timestamps for repost policing purposes'''
    url_record = context.chat_data.get('url record', defaultdict(list))
    url_record[context.chat_data['last url']].append(datetime.now())
    purge = []
    for url, times in url_record.items():
        recent = list(filter(lambda dt: dt > datetime.now()-timedelta(days=3), times))
        if recent:
            url_record[url] = recent
        else:
            purge.append(url)

    for url in purge:
        del url_record[url]

    context.chat_data['url record'] = url_record

def add_bypass(url, context):
    '''Puts together links with various bypass strategies'''
    if not url.startswith('http'):
        url = f'http://{url}'

    text = []

    bypasses = (
        (outline, 'Outline'),
        (wayback, 'Wayback Machine'),
        (amp, 'AMP'),
        (google_cache, 'Google Cache'),
        (twelve_ft, '12ft.io'),
        (archive_is, 'archive.is'),
        (remove_js, 'RemoveJS'),
        (nitter, 'Twiiit'),
        (lite_mode, 'Lite Mode')
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, os.cpu_count()*5), thread_name_prefix='add_bypass') as executor:
        future_to_bp_text = {executor.submit(bypass, url): bp_text for bypass, bp_text in bypasses}
        for future in concurrent.futures.as_completed(future_to_bp_text):
            try:
                bp_text = future_to_bp_text[future]
                bp_url = future.result()
                if bp_url:
                    text.append(link(bp_url, bp_text))
            except:
                devs = LIST_OF_ADMINS
                for dev_id in devs:
                    context.bot.send_message(dev_id, f'error when trying to apply {bp_text} bypass to {url}', parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                trace = "".join(traceback.format_tb(sys.exc_info()[2]))
                logging.warning(trace)

    return '\n\n'.join(text)

# bypasses
def outline(url):
    '''Returns the url for this page at outline.com if it exists'''
    try:
        r = requests.get(f'https://api.outline.com/v3/parse_article?source_url={short(url)}')
        if r.status_code is 200 \
           and "We've detected unusual activity from your computer network" not in r.text \
           and "We're sorry, but this URL is not supported by Outline" not in r.text:
            return f'https://outline.com/{short(url)}'
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass

def wayback(url):
    '''Returns the url of the latest snapshot if avalable on wayback machine'''
    try:
        r = requests.get(f'http://archive.org/wayback/available?url={url}')
        archive_org_url = r.json().get('archived_snapshots', {}).get('closest', {}).get('url')
        if archive_org_url:
            return archive_org_url
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass

def amp(url):
    '''Returns the url wrapped up in AMP stuff'''
    if get_domain(url) not in ('twitter.com',):
        amp_candidates = [(0, '')]
        urls = []

        url_parts = urlsplit(url)
        url_parts = url_parts._replace(scheme='')

        urls.append(urlunsplit(url_parts)[2:]) # domain as is  (news.site.tld)

        domain = get_domain(url)

        url_parts = url_parts._replace(netloc=domain)
        urls.append(urlunsplit(url_parts)[2:]) # naked domain  (site.tld)

        url_parts = url_parts._replace(netloc='amp.' + domain)
        urls.append(urlunsplit(url_parts)[2:]) # amp subdomain  (amp.site.tld)

        # Other ways exist for sites to serve up amp content. It's just a pain to figure them all out.

        for url in urls:
            amp_url_templates = [
                f'https://cdn.ampproject.org/v/s/{url}?amp_js_v=a3&amp_gsa=1&_amp=true&outputType=amp',
                f'https://{url}/amp',
            ]
            for amp_url in amp_url_templates:
                try:
                    r = requests.get(amp_url)
                    size = len(r.content)
                    if r.status_code == 200:
                        amp_candidates.append((size, amp_url))
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    pass
        return sorted(amp_candidates)[-1][1] # use the largest one. probably not truncated

def google_cache(url):
    gcache_url = f'http://webcache.googleusercontent.com/search?q=cache:{url}'
    try:
        r = requests.get(gcache_url)
        if f'<base href="{url}' in r.text:
            return gcache_url
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass

def archive_is(url):
    '''Returns the url for this page at archive.is if it exists'''
    try:
        r = requests.get(f'http://archive.is/timemap/{url}')
        if r.status_code is 200:
            return f'http://archive.is/newest/{url}'
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass

def remove_js(url):
    remove_js_url = f'https://remove-js.com/{url}'
    try:
        r = requests.get(remove_js_url)
        if 'Make sure you enter a valid URL (e.g., http://example.com)' not in r.text and \
           'detected unusual activity from your computer network' not in r.text:
            return remove_js_url
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass

def twelve_ft(url):
    twelve_ft_url = f'https://12ft.io/{url}'
    try:
        r = requests.get(f'https://12ft.io/api/proxy?ref=&q={url}')
        if '12ft has been disabled for this site' not in r.text and \
           'detected unusual activity from your computer network' not in r.text:
            return twelve_ft_url
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass

def nitter(url):
    '''Converts twitter links to a randomly chosen instance of nitter'''
    if get_domain(url) == 'twitter.com':
        url_parts = urlsplit(url)
        url_parts = url_parts._replace(netloc='twiiit.com')
        return urlunsplit(url_parts)

def lite_mode(url):
    '''Converts certain news sites to their lite versions'''
    domain = get_domain(url)
    url_parts = urlsplit(url)

    if domain == 'csmonitor.com':
        lite_url = urlunsplit(url_parts._replace(path='/layout/set/text/' + url_parts.path))

    elif domain == 'npr.org':
        try:
            lite_url = urlunsplit(url_parts._replace(netloc='text.npr.org', path=url_parts.path.split('/')[4])) # this [4] can conceivably wind up out of range
        except:
            lite_url = ''

    # elif domain == 'cnn.com':
    #     lite_url = 'http://lite.cnn.com/en/article/h_{unidentified_hash}'

    else:
        lite_url = ''

    if lite_url:
        try:
            r = requests.get(lite_url)
            if r.status_code is 200:
                return lite_url
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            pass

# main thing
@log
def incoming(update, context):
    '''Check incoming stream for urls and put attempted bypasses on them if they are in the list of domains that need it'''
    extractor = URLExtract()
    extractor.update_when_older(7) # gets the latest list of TLDs from iana.org every 7 days
    urls = extractor.find_urls(update.effective_message.text, check_dns=True)
    active_dict = context.chat_data.get('active domains', {}) # this s/could have been a set instead. stuck as dict for legacy reasons
    for url in urls:
        if get_domain(url) not in active_dict:
            continue
        context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        text = add_bypass(url, context=context)
        say(text, update, context)
    if len(urls) == 1:
        context.chat_data['last url'] = urls[0]
        url_bookkeeping(context)

# user accessible commands
@log
@send_typing_action
def include(update, context):
    '''Add domains to the set that gets acted on'''
    active_dict = context.chat_data.get('active domains', {})
    try:
        if not context.args:
            domain = get_domain(context.chat_data.get('last url'))
            text = add_bypass(context.chat_data.get('last url'), context=context)
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
@send_typing_action
def remove(update, context):
    '''See and remove domains in/from active_dict if needed'''
    active_dict = context.chat_data.get('active domains', {})
    if not context.args and active_dict:
        list_active_domains(update, context)
    else:
        try:
            del active_dict[' '.join(context.args)]
            text = f"Removed {' '.join(context.args)}"
        except KeyError:
            text = f"Failed to remove {' '.join(context.args)}\nAlready gone? Check your spelling?"
        say(text, update, context)

@log
@send_typing_action
def list_active_domains(update, context):
    '''List only. /list used to be an alias for /remove, but that's just asking for trouble'''
    active_dict = context.chat_data.get('active domains', {})
    text = '</code>\n<code>'.join((f'{url}' for url in active_dict.keys()))
    text = f"<code>{text}</code>"
    say(text, update, context)

@log
@send_typing_action
def translate(update, context):
    '''Run the page at url through google translate'''
    text = []
    url = context.chat_data.get('last url')
    languages = ['en']
    if context.args:
        languages = context.args

    for lang in languages:
        text.append(link(f'https://translate.google.com/translate?tl={lang}&u={url}', f'Translation to {lang}'))

    say('\n\n'.join(text), update, context)

@log
@send_typing_action
def repost_police(update, context):
    '''Check if url has been posted in the last 3 days and call the cops if it was'''
    url = context.chat_data.get('last url')
    url_record = context.chat_data.get('url record', defaultdict(list))
    previous_hits = url_record[url]
    if len(previous_hits) >= 2:
        most_recent = format_timedelta(previous_hits[-2] - datetime.now(), add_direction=True, threshold=1.1)
        say(f'🙅🚨REPOST🚨🔁\n{url}\nwas recently seen {most_recent}', update, context)
    else:
        say(f'Sorry, no memory of {url} being reposted', update, context)

# useless junk feature
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

    dispatcher.add_handler(CommandHandler('translate', translate))
    dispatcher.add_handler(CommandHandler('repost', repost_police))
    dispatcher.add_handler(CommandHandler('include', include))
    dispatcher.add_handler(CommandHandler('remove', remove))
    dispatcher.add_handler(CommandHandler('list', list_active_domains))
    dispatcher.add_handler(CommandHandler('export', export_urls))
    dispatcher.add_handler(CommandHandler('r', restart, filters=Filters.user(user_id=LIST_OF_ADMINS)))
    dispatcher.add_handler(CommandHandler('data', chat_data, filters=Filters.user(user_id=LIST_OF_ADMINS)))
    dispatcher.add_handler(MessageHandler(Filters.text, incoming))
    dispatcher.add_error_handler(error)

    updater.start_polling()
    logging.info('outline bot started as @%s' % updater.bot.getMe()['username'])
    updater.idle()
