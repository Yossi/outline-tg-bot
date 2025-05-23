'''Telegram bot that (primarily) attempts to perform url hacks to get around paywalls'''


__version__ = '2.6.1'


import asyncio
import functools
import html
import logging
import pprint
import sys
import time
import traceback
from io import BytesIO
from urllib.parse import urlsplit
from datetime import datetime, timezone, timedelta

import httpx
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackContext, MessageHandler, PicklePersistence, filters
from telegram.helpers import mention_html, create_deep_linked_url
from tldextract import extract
from urlextract import URLExtract

from data.secrets import LIST_OF_ADMINS, TOKEN  # If it crashed here it's because you didn't create secrets.py correctly (or at all). Or you didn't pass docker run -v /full/path/to/data/:/home/botuser/data/
import subprocess


logging.basicConfig(format='%(asctime)s - %(levelname)s %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


# logging
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Log the error and send a telegram message to notify the developer.'''
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    if not update:
        return

    trace = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    user_info = ""
    if update.effective_user:
        user_info += f' when {mention_html(update.effective_user.id, update.effective_user.first_name)}'  # If it blows up here it's possibly because you've used python < 3.6
    if update.effective_chat:
        if update.effective_chat.title:
            user_info += f' in the chat <i>{html.escape(str(update.effective_chat.title))}</i>'
        if update.effective_chat.username:
            user_info += f' (@{update.effective_chat.username})'

    text = update.effective_message.text if update.effective_message else None

    message = f"Hey.\n The error <code>{sys.exc_info()[0].__name__}: {html.escape(str(context.error))}</code> happened{user_info} said <code>{text}</code>.\n\n<pre><code class='language-python'>{html.escape(trace)}</code></pre>"

    for admin_id in LIST_OF_ADMINS:
        await context.bot.send_message(chat_id=admin_id, text=message, parse_mode=ParseMode.HTML)


# decorators
def log(func):
    '''Decorator to log who said what to the bot'''
    @functools.wraps(func)
    def wrapped(update, context, *args, **kwargs):
        id = update.effective_user.id
        name = update.effective_user.username
        logging.info(f'{name} ({id}) said:\n{update.effective_message.text}')
        logging.info(f'Function {func.__name__}() called')

        # # Detailed debug info
        # logging.info(f'Full update data: {update}')
        # logging.info(f'update.message: {bool(update.message)}')
        # logging.info(f'update.edited_message: {bool(update.edited_message)}')

        return func(update, context, *args, **kwargs)
    return wrapped


def drop_edits(func):
    '''Decorator to ignore edited messages'''
    @functools.wraps(func)
    def wrapped(update, context, *args, **kwargs):
        if update.edited_message:
            return say('', update, context)  # Basically a noop to keep async happy
        return func(update, context, *args, **kwargs)
    return wrapped


def timer(func):
    '''Decorator to measure how long a function ran. Need to set logging level to debug to see results'''
    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        t1 = time.monotonic() # perf_counter() ?
        result = await func(*args, **kwargs)
        t2 = time.monotonic()
        logging.debug(f'Function {func.__name__!r} executed in {(t2-t1):.4f}s')
        return result
    return wrapped


def snitch(func):
    '''Decorator to catch and handle errors that come up in the individual bypasses'''
    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except:
            trace = "".join(traceback.format_tb(sys.exc_info()[2]))
            logging.warning(trace)
    return wrapped


def send_typing_action(func):
    '''Decorator to send typing action while processing func command'''
    @functools.wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        await func(update, context, *args, **kwargs)
    return wrapped


# admin only commands
@log
@drop_edits
@send_typing_action
async def chat_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''See and optionally clear chat_data'''
    if context.args:
        if context.args[0] == 'clear' and len(context.args) > 1:
            context.chat_data.pop(' '.join(context.args[1:]), None)
            text = pprint.pformat(context.chat_data)
        else:
            text = '/data clear <key>'
    else:
        text = pprint.pformat(context.chat_data)

    await say(html.escape(text), update, context)


@log
@drop_edits
@send_typing_action
async def library_versions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Show installed library versions and the latest available versions online'''

    installed_result = subprocess.run(['pip', 'list', '--format=columns'], capture_output=True, text=True)
    outdated_result = subprocess.run(['pip', 'list', '--outdated', '--format=columns'], capture_output=True, text=True)

    installed_libraries = installed_result.stdout.splitlines()
    outdated_libraries = outdated_result.stdout.splitlines()

    outdated_dict = {line.split()[0]: line.split()[2] for line in outdated_libraries[2:]}

    response = [installed_libraries[0]]  # Headers
    for line in installed_libraries[2:]:
        lib_name = line.split()[0]
        if lib_name in outdated_dict:
            response.append(f"{line} (latest: {outdated_dict[lib_name]})")
        else:
            response.append(line)

    further_instructions = ''
    if outdated_dict:
        further_instructions = "To update all outdated libraries, run:\n<code>pip list --outdated | awk 'NR>2 {print $1}' | xargs -n1 pip install -U</code>"

    text = html.escape("\n".join(response))

    if text:
        await say(f'<pre>{text}</pre>{further_instructions}', update, context)


# internal bot helper stuff
async def say(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    '''Send text to channel'''
    if text:
        logging.info(f'bot said:\n{text}')
        sent_message = await context.bot.send_message(chat_id=update.effective_message.chat_id, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, disable_notification=True)
        return sent_message.message_id


async def edit(text: str, message_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    '''Edit message `message_id` to say `text`. Delete entirely if `text` is blank'''
    logging.info(f'bot edited {message_id} to:\n{text}')
    if text:
        try:
            edited_message = await context.bot.edit_message_text(chat_id=update.effective_message.chat_id, message_id=message_id, text=text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            return edited_message.message_id
        except BadRequest:
            logging.info('no change')
    else:
        await delete(message_id, update, context)


async def delete(message_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Remove message `message_id`'''
    await context.bot.delete_message(chat_id=update.effective_message.chat_id, message_id=message_id)
    logging.info(f'bot deleted message {message_id}')
    response_record_remove(message_id, context)


def response_record_add(incoming_id: int, response_id: int, incoming_text: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Track `message_id` of message that triggered the bot and `message_id` of the bot's response'''
    if response_id:
        response_record = context.chat_data.get('response record', {})
        response_record[incoming_id] = response_id
        if len(response_record) > 10:
            response_record.pop(next(iter(response_record)))  # Pop and throw away old one

        response_text_record = context.chat_data.get('response text record', {})
        response_text_record[incoming_id] = incoming_text
        if len(response_text_record) > 10:
            response_text_record.pop(next(iter(response_text_record)))

        context.chat_data['response record'] = response_record
        context.chat_data['response text record'] = response_text_record


def response_record_remove(message_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Remove deleted `message_id` from record'''
    response_record = context.chat_data.get('response record', {})
    response_text_record = context.chat_data.get('response text record', {})
    incoming_id = next((incoming_id for incoming_id, response_id in response_record.items() if response_id == message_id), None)
    response_record.pop(incoming_id, None)  # Remove from the record
    response_text_record.pop(incoming_id, None)
    context.chat_data['response record'] = response_record
    context.chat_data['response text record'] = response_text_record


def get_url(text: str) -> str:
    extractor = URLExtract()
    extractor.update_when_older(7)  # Gets up-to-date list of TLDs from iana.org every 7 days
    urls = extractor.find_urls(text, check_dns=True)
    if urls:
        return urls[0]
    else:
        return ''


def get_domain(url: str) -> str:
    '''Get the domain.tld of url. Ignore any subdomains. Is smart about things like .co.uk'''
    extract_result = extract(url)
    if extract_result.domain and extract_result.suffix:
        return f'{extract_result.domain}.{extract_result.suffix}'.lower()
    return 'no domain'


def link(url: str, text: str) -> str:
    return f'<a href="{url}">{text}</a>'


@timer
async def add_bypasses(url: str) -> str:
    '''Puts together links with various bypass strategies'''
    if not url:
        return ''
    if not url.startswith('http'):
        url = f'http://{url}'

    text = []

    bypass_names = (
        (wayback, 'Wayback Machine'),
        (google_cache, 'Google Cache'),
        (twelve_ft, '12ft.io'),
        (archive_is, 'archive.is'),
        (ghostarchive, 'Ghost Archive'),
        (txtify_it, 'txtify.it'),
        (twitter, 'Twitter Embed'),
        (nitter, 'Twiiit'),
        (lite_mode, 'Lite Mode')
    )

    async with httpx.AsyncClient(http2=True) as client:
        bypasses, bp_texts = zip(*bypass_names)
        tasks = [bypass(url, client) for bypass in bypasses]
        bp_urls = await asyncio.gather(*tasks)

        for bp_url, bp_text in zip(bp_urls, bp_texts):
            if bp_url:
                text.append(link(bp_url, bp_text))

    return '\n\n'.join(text)


# bypasses
@timer
@snitch
async def wayback(url: str, client: httpx.AsyncClient) -> str | None:
    '''Returns the url of the latest snapshot if avalable on wayback machine'''
    try:
        r = await client.get(f'http://archive.org/wayback/available?url={url}', timeout=2)
        archive_org_url = r.json().get('archived_snapshots', {}).get('closest', {}).get('url')
        if archive_org_url:
            return archive_org_url
        else:
            url = urlsplit(url)._replace(query='').geturl()  # Strip query to maybe canonicalize url
            r = await client.get(f'http://archive.org/wayback/available?url={url}', timeout=2)
            archive_org_url = r.json().get('archived_snapshots', {}).get('closest', {}).get('url')
            if archive_org_url:
                return archive_org_url

    except httpx.TimeoutException:
        pass


@timer
@snitch
async def google_cache(url: str, client: httpx.AsyncClient) -> str | None:
    gcache_url = f'http://webcache.googleusercontent.com/search?q=cache:{url}'
    try:
        r = await client.get(gcache_url, timeout=2)
        if f'<base href="{url}' in r.text:
            return gcache_url
    except httpx.TimeoutException:
        pass


@timer
@snitch
async def twelve_ft(url: str, client: httpx.AsyncClient) -> str | None:
    twelve_ft_url = f'https://12ft.io/{url}'
    try:
        r = await client.get(f'https://12ft.io/api/proxy?ref=&q={url}', timeout=2)
        r.raise_for_status()
        if '12ft has been disabled for this site' not in r.text and \
           'detected unusual activity from your computer network' not in r.text:
            return twelve_ft_url
    except (httpx.TimeoutException, httpx.HTTPStatusError):
        pass


@timer
@snitch
async def archive_is(url: str, client: httpx.AsyncClient) -> str | None:
    '''Returns the url for this page at archive.is if it exists'''
    # List of TLDs they have: .is .ph .md .li .vn .fo .today
    try:
        headers = {'user-agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/114.0'}
        r = await client.get(f'https://archive.today/timemap/{url}', timeout=2, headers=headers)
        if r.status_code == 200:
            return f'https://archive.is/newest/{url}'
    except httpx.TimeoutException:
        pass


@timer
@snitch
async def ghostarchive(url: str, client: httpx.AsyncClient) -> str | None:
    '''Returns the url for this page at ghostarchive.org if it exists'''
    ghostarchive_url = f'https://ghostarchive.org/search?term={url}'
    try:
        r = await client.get(ghostarchive_url, timeout=2)
        r.raise_for_status()
        if 'No archives for that site.' in r.text:
            return
        start = r.text.find('<a href="/archive/')
        if start == -1: return
        end = r.text.find('">', start)
        path = r.text[start+len('<a href="'):end]
        if path:
            return f'https://ghostarchive.org{path}'
    except (httpx.TimeoutException, httpx.HTTPStatusError):
        pass


@timer
@snitch
async def txtify_it(url: str, client: httpx.AsyncClient) -> str | None:
    txtify_it_url = f'https://txtify.it/{url}'
    try:
        r = await client.get(txtify_it_url, timeout=2)
        if r.content.strip():
            return txtify_it_url
    except httpx.TimeoutException:
        pass


@timer
@snitch
async def twitter(url: str, client: httpx.AsyncClient) -> str | None:
    '''Converts twitter links to twitter embed links that load faster and allow logged out viewing'''
    if get_domain(url) in ('twitter.com', 'fxtwitter.com', 'x.com'):
        url_parts = urlsplit(url)
        if '/status/' in url_parts.path:
            tweet_id = url_parts.path.split('/')[-1]
            return url_parts._replace(netloc='platform.twitter.com', path='/embed/Tweet.html', query=f'id={tweet_id}').geturl()


@timer
@snitch
async def nitter(url: str, client: httpx.AsyncClient) -> str | None:
    '''Converts twitter links to a randomly chosen instance of nitter'''
    if get_domain(url) in ('twitter.com', 'fxtwitter.com', 'x.com'):
        return urlsplit(url)._replace(netloc='twiiit.com').geturl()


@timer
@snitch
async def lite_mode(url: str, client: httpx.AsyncClient) -> str | None:
    '''Converts certain news sites to their lite versions'''
    domain = get_domain(url)
    url_parts = urlsplit(url)

    if domain == 'csmonitor.com':
        lite_url = url_parts._replace(path='/layout/set/text/' + url_parts.path).geturl()

    elif domain == 'npr.org':
        try:
            lite_url = url_parts._replace(netloc='text.npr.org', path=url_parts.path.split('/')[4]).geturl()  # This [4] can conceivably wind up out of range
        except:
            lite_url = ''

    # elif domain == 'cnn.com':
    #     lite_url = 'http://lite.cnn.com/en/article/h_{unidentified_hash}'

    elif domain == 'cbc.ca':
        lite_url = url_parts._replace(path='/lite/story/' + url_parts.path.split('-')[-1]).geturl()

    else:
        lite_url = ''

    if lite_url:
        try:
            r = await client.get(lite_url, timeout=2)
            if r.status_code == 200:
                return lite_url
        except httpx.TimeoutException:
            pass


# main thing
@log
async def incoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Check incoming message stream for urls and put attempted bypasses on them if they are in the list of domains that need it'''
    response_record = context.chat_data.get('response record', {})
    response_text_record = context.chat_data.get('response text record', {})
    incoming_id = update.effective_message.message_id
    incoming_text = update.effective_message.text

    old_text = response_text_record.get(incoming_id, '')
    if incoming_text == old_text:
        logging.info('GOT YOU! GTFO here with your broken reactions')
        return  # It's actually just a reaction on a message over one hour old. Bail out.

    if update.edited_message and incoming_id not in response_record:
        logging.info("Ignoring edited message because it's too old")
        return

    url = get_url(incoming_text)
    if url:
        context.chat_data['last url'] = incoming_id, url

    active_set = context.chat_data.get('active domains', set())
    text = await add_bypasses(url) if get_domain(url) in active_set else ''

    if incoming_id in response_record:  # Ie, edited message has already been responded to previously
        response_id = await edit(text, response_record[incoming_id], update, context)  # Will delete the response if the new text is empty
    else:
        response_id = await say(text, update, context)

    if response_id:
        response_record_add(incoming_id, response_id, incoming_text, context)


# user accessible commands
@log
@drop_edits
@send_typing_action
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = create_deep_linked_url(context.bot.username, 'yes', group=True)
    text = f'Use <a href="{url}">this link</a> to share this bot to another group.\n' \
           f'FYI, your telegram user id is <code>{update.effective_user.id}</code>, ' \
           f'this chat id is <code>{update.effective_chat.id}</code> ' \
           f'and this bot\'s user id is <code>{application.bot.id}</code>'
    await say(text, update, context)


@log
@drop_edits
@send_typing_action
async def version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = 'https://raw.githubusercontent.com/Yossi/outline-tg-bot/master/VERSION'
    r = httpx.get(url)
    if r.text.strip() != __version__:
        await say(f'Running: {__version__}\nLatest: <a href="https://github.com/Yossi/outline-tg-bot">{r.text}</a>', update, context)
    else:
        await say(__version__, update, context)


@log
@drop_edits
@send_typing_action
async def translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Run the page at url through google translate'''
    if update.effective_message.reply_to_message:
        url = get_url(update.effective_message.reply_to_message.text)
    else:
        url = context.chat_data.get('last url')[1]

    if not url:
        return

    languages = ['en']
    if context.args:
        languages = context.args

    text = []
    for lang in languages:
        text.append(link(f'https://translate.google.com/translate?tl={lang}&u={url}', f'Translation to {lang}'))

    await say('\n\n'.join(text), update, context)


@log
@drop_edits
@send_typing_action
async def include(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Add domains to the set that gets acted on'''
    incoming_text = ''

    def include_domain(domain: str) -> str:
        if domain == 'no domain':
            return 'No domain found to include'

        active_set = context.chat_data.get('active domains', set())
        active_set.add(domain)
        context.chat_data['active domains'] = active_set
        return f"Added {domain}"


    if update.effective_message.reply_to_message:  # Add domain by replying to a message
        incoming_text = update.effective_message.reply_to_message.text
        incoming_id = update.effective_message.reply_to_message.message_id
        url = get_url(incoming_text)
        domain = get_domain(url)  # Returns string 'no domain' if none found
        text = include_domain(domain)
        if url:
            text = await add_bypasses(url)

    elif context.args:  # Directly add domain
        responses = []
        for arg in context.args:
            domain = get_domain(arg)
            responses.append(include_domain(domain))

        text = '\n'.join(responses)

    else:  # Add domain from last url
        incoming_id, url = context.chat_data.get('last url')
        domain = get_domain(url)
        text = include_domain(domain)
        if url:
            text = await add_bypasses(url)

    response_id = await say(text, update, context)
    if response_id and incoming_text:
        response_record_add(incoming_id, response_id, incoming_text, context)


@log
@drop_edits
@send_typing_action
async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Remove domains from the active set'''

    def remove_domain(domain: str) -> str:
        if domain == 'no domain':
            return 'No domain found to remove'
        try:
            active_set = context.chat_data.get('active domains', set())
            active_set.remove(domain)
            return f"Removed {domain}"
        except KeyError:
            return f"Failed to remove {domain}\nAlready gone? Check your spelling?"


    if update.effective_message.reply_to_message:
        incoming_text = update.effective_message.reply_to_message.text
        url = get_url(incoming_text)
        domain = get_domain(url)  # Returns string 'no domain' if none found
        text = remove_domain(domain)

    elif context.args:
        responses = []
        for domain in context.args:
            responses.append(remove_domain(domain))

        text = '\n'.join(responses)

    else:
        text = 'Usage syntax: /remove <domain.tld> or reply to a message with /remove'

    await say(html.escape(text), update, context)


@log
@drop_edits
@send_typing_action
async def list_active_domains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''List only. /list used to be an alias for /remove, but that's just asking for trouble'''
    active_set = context.chat_data.get('active domains', set())
    text = '</code>\n<code>'.join((f'{url}' for url in sorted(active_set)))
    if not text:
        text = 'no domains yet'
    text = f"<code>{text}</code>"
    await say(text, update, context)


@log
@drop_edits
async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''If someone replies to a bot message with /delete, get rid of the message'''
    if not update.effective_message.reply_to_message:
        return

    me = await application.bot.get_me()
    bot_user_id = me['id']

    reply_to_user_id = update.effective_message.reply_to_message.from_user.id
    if bot_user_id == reply_to_user_id:
        target_id = update.effective_message.reply_to_message.message_id
        reply_id = update.effective_message.message_id
        try:
            await delete(target_id, update, context)
            await delete(reply_id, update, context)  # Clean up the /delete command. Only works if the bot has permission to delete others' messages
        except BadRequest:
            logging.info('Message probably too old to delete')


@log
@drop_edits
async def export_urls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Make settings avaliable as a CSV file'''
    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    chat_id = update.effective_message.chat_id
    bio = BytesIO('\n'.join(context.chat_data['active domains']).encode('utf8'))
    bio.name = f'{chat_id}_urls_backup.txt'
    await context.bot.send_document(chat_id=chat_id, document=bio)


@log
@drop_edits
async def import_urls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Import settings previously exported with /export'''
    chat_id = update.effective_message.chat_id
    file_name = update.message.effective_attachment.file_name
    mime_type = update.message.effective_attachment.mime_type
    if (file_name, mime_type) != (f'{chat_id}_urls_backup.txt', 'text/plain'):
        return

    file = await update.message.effective_attachment.get_file()
    bio = BytesIO()
    await file.download_to_memory(bio)
    bio.seek(0)

    active_set = context.chat_data.get('active domains', set())

    line_ending = '\n' if ',' not in bio.read().decode() else ',\n'
    bio.seek(0)

    added_domains = []
    for domain in bio.read().decode().split(line_ending):
        if domain == get_domain(domain):
            active_set.add(domain)
            added_domains.append(domain)

    context.chat_data['active domains'] = active_set

    text = '\n'.join(added_domains)
    if text:
        await say(f'Added:\n{text}', update, context)


# bot setup
async def post_init(application: Application) -> None:
    '''Stuff that runs once on startup'''
    logging.info(f'outline bot started as @{application.bot.username}')

    await application.bot.set_my_commands([
        ('include', 'Add recent url to active list. Other domain may be passed instead.'),
        ('list', 'Display active list.'),
        ('remove', 'Remove passed domain. Same as /list if domain not passed.'),
        ('translate', 'Translate recent url to en. Other language code(s) may be passed as well.'),
        ('delete', 'Reply to a bot message with this to delete that message.'),
        ('version', 'Show running bot version.'),
    ])

    await application.bot.set_my_description(
        'This bot will try find alternate (free) places to read the paywalled links you and your friends share. '
        'It can work in a one to one chat like this one, but is intended to be used in a group chat. '
        'The bot must be given admin rights in the group chat to be able to see all messages (Telegram rule). '
        'Once added, you post a link, then you say /include and from then on the bot will act to try to get around all links from that domain when they are posted. '
    )

    await application.bot.set_my_short_description(f'Paywall bypass finder bot {__version__}')

    await migrate(application)


async def migrate(application: Application) -> None:
    '''Migrate chat_data to latest format'''
    chat_data = await application.persistence.get_chat_data()
    for chat, data in chat_data.items():
        if not isinstance(data.get('active domains', set()), set):
            logging.info(f'Migrating chat {chat} to new active domains format')
            data['active domains'] = set(data['active domains'].keys())  # Strong assumption that the old format was a dict

        if not isinstance(data.get('last url', (0, '')), tuple):
            logging.info(f'Migrating chat {chat} to new last url format')
            data['last url'] = (0, data['last url'])

        context = CallbackContext(application, chat)
        context.chat_data.update(data)


if __name__ == '__main__':
    persistence = PicklePersistence(filepath='data/bot.persist', on_flush=False)
    application = Application.builder().token(TOKEN).persistence(persistence).post_init(post_init).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('version', version))
    application.add_handler(CommandHandler('translate', translate))
    application.add_handler(CommandHandler('include', include))
    application.add_handler(CommandHandler('remove', remove))
    application.add_handler(CommandHandler('list', list_active_domains))
    application.add_handler(CommandHandler('delete', delete_message))
    application.add_handler(CommandHandler('export', export_urls))
    application.add_handler(CommandHandler('data', chat_data, filters=filters.User(user_id=LIST_OF_ADMINS)))
    application.add_handler(CommandHandler('library_versions', library_versions, filters=filters.User(user_id=LIST_OF_ADMINS)))

    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), incoming))
    application.add_handler(MessageHandler(filters.Document.TEXT, import_urls)) # filters.Caption(['/import']) &

    application.add_error_handler(error_handler)

    application.run_polling()
