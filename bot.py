'''Telegram bot that primarily attempts to perform url hacks to get around paywalls'''


__version__ = '2.1.3'


import asyncio
import functools
import html
import logging
import pprint
import sys
import time
import traceback
from io import BytesIO
from urllib.parse import urlsplit, urlunsplit

import httpx
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, PicklePersistence, filters
from telegram.helpers import mention_html, create_deep_linked_url
from tldextract import extract
from urlextract import URLExtract

from data.secrets import LIST_OF_ADMINS, TOKEN  # If it crashed here it's because you didn't create secrets.py correctly (or at all). Or you didn't pass docker run -v /full/path/to/data/:/home/botuser/data/


logging.basicConfig(format='%(asctime)s - %(levelname)s %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


# logging
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Log the error and send a telegram message to notify the developer.'''
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    if not update:
        return

    trace = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    payload = ""
    if update.effective_user:
        payload += f' with the user {mention_html(update.effective_user.id, update.effective_user.first_name)}'  # if it blows up here it's possibly because you've used python < 3.6
    if update.effective_chat:
        if update.effective_chat.title:
            payload += f' within the chat <i>{html.escape(str(update.effective_chat.title))}</i>'
        if update.effective_chat.username:
            payload += f' (@{update.effective_chat.username})'

    message = f"Hey.\n The error <code>{sys.exc_info()[0].__name__}: {html.escape(str(context.error))}</code> happened{payload}. The full traceback:\n\n<code>{html.escape(trace)}</code>"

    for admin_id in LIST_OF_ADMINS:
        await context.bot.send_message(chat_id=admin_id, text=message, parse_mode=ParseMode.HTML)


# decorators
def log(func):
    '''Decorator that logs who said what to the bot'''
    @functools.wraps(func)
    def wrapped(update, context, *args, **kwargs):
        id = update.effective_user.id
        name = update.effective_user.username
        logging.info(f'{name} ({id}) said:\n{update.effective_message.text}')
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
    '''Decorator to catch and handle errors that come up in the invidual bypasses'''
    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except:
            trace = "".join(traceback.format_tb(sys.exc_info()[2]))
            logging.warning(trace)
    return wrapped


def send_typing_action(func):
    '''Decorator that sends typing action while processing func command'''
    @functools.wraps(func)
    async def wrapped(update, context, *args, **kwargs):
        await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        await func(update, context, *args, **kwargs)
    return wrapped


# admin
@log
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


# internal bot helper stuff
async def say(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    '''Send text to channel'''
    logging.info(f'bot said:\n{text}')
    if text:
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
    '''Remove message message_id'''
    await context.bot.delete_message(chat_id=update.effective_message.chat_id, message_id=message_id)
    logging.info(f'bot deleted message {message_id}')
    response_record_remove(message_id, context)


def response_record_add(incoming_id: int, response_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Track message_ids of what message triggered the bot and what message the bot responded'''
    if response_id:
        response_record = context.chat_data.get('response record', {})
        response_record[incoming_id] = response_id
        if len(response_record) > 10:
            response_record.pop(next(iter(response_record)))  # pop and throw away old one
        context.chat_data['response record'] = response_record


def response_record_remove(message_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Remove deleted message_id from record'''
    response_record = context.chat_data.get('response record', {})
    incoming_id = next((incoming_id for incoming_id, response_id in response_record.items() if response_id == message_id), None)
    response_record.pop(incoming_id, None)  # Remove from the record
    context.chat_data['response record'] = response_record


def get_url(text: str) -> str:
    extractor = URLExtract()
    extractor.update_when_older(7)  # Gets the latest list of TLDs from iana.org every 7 days
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
async def add_bypasses(url: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    '''Puts together links with various bypass strategies'''
    if not url:
        return ''
    if not url.startswith('http'):
        url = f'http://{url}'

    text = []

    bypass_names = (
        (wayback, 'Wayback Machine'),
        (google_cache, 'Google Cache'),
        (remove_js, 'RemoveJS'),
        (twelve_ft, '12ft.io'),
        (archive_is, 'archive.is'),
        (ghostarchive, 'Ghost Archive'),
        (txtify_it, 'txtify.it'),
        (nitter, 'Twiiit'),
        (unnitter, 'Twitter'),
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
async def remove_js(url: str, client: httpx.AsyncClient) -> str | None:
    remove_js_url = f'https://remove-js.com/{url}'
    try:
        r = await client.get(remove_js_url, timeout=2)
        if 'Make sure you enter a valid URL (e.g., http://example.com)' not in r.text and \
           'detected unusual activity from your computer network' not in r.text:
            return remove_js_url
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
    try:
        r = await client.get(f'http://archive.is/timemap/{url}', timeout=2)
        if r.status_code == 200:
            return f'http://archive.is/newest/{url}'
    except httpx.TimeoutException:
        pass


@timer
@snitch
async def ghostarchive(url: str, client: httpx.AsyncClient) -> str | None:
    ghostarchive_url = f'https://ghostarchive.org/search?term={url}'
    try:
        r = await client.get(ghostarchive_url, timeout=2)
        if 'No archives for that site.' in r.text:
            return
        start = r.text.find('/archive/')
        end = r.text.find('">', start)
        path = r.text[start:end]
        if path:
            return f'https://ghostarchive.org{path}'
    except httpx.TimeoutException:
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
async def nitter(url: str, client: httpx.AsyncClient) -> str | None:
    '''Converts twitter links to a randomly chosen instance of nitter'''
    if get_domain(url) == 'twitter.com':
        url_parts = urlsplit(url)
        url_parts = url_parts._replace(netloc='twiiit.com')
        return urlunsplit(url_parts)


@timer
@snitch
async def unnitter(url: str, client: httpx.AsyncClient) -> str | None:
    '''Convert nitter link back to twitter'''
    try:
        r = await client.get(url, timeout=2)
        r.raise_for_status()
        if '<a class="icon-bird" title="Open in Twitter" href="https://twitter.com/' in r.text:
            url_parts = urlsplit(url)
            url_parts = url_parts._replace(netloc='twitter.com')
            return urlunsplit(url_parts)
    except (httpx.TimeoutException, httpx.HTTPStatusError):
        pass


@timer
@snitch
async def lite_mode(url: str, client: httpx.AsyncClient) -> str | None:
    '''Converts certain news sites to their lite versions'''
    domain = get_domain(url)
    url_parts = urlsplit(url)

    if domain == 'csmonitor.com':
        lite_url = urlunsplit(url_parts._replace(path='/layout/set/text/' + url_parts.path))

    elif domain == 'npr.org':
        try:
            lite_url = urlunsplit(url_parts._replace(netloc='text.npr.org', path=url_parts.path.split('/')[4]))  # this [4] can conceivably wind up out of range
        except:
            lite_url = ''

    # elif domain == 'cnn.com':
    #     lite_url = 'http://lite.cnn.com/en/article/h_{unidentified_hash}'

    elif domain == 'cbc.ca':
        lite_url = urlunsplit(url_parts._replace(path='/lite/story/' + url_parts.path.split('-')[-1]))

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
    '''Check incoming stream for urls and put attempted bypasses on them if they are in the list of domains that need it'''
    url = get_url(update.effective_message.text)
    if url:
        context.chat_data['last url'] = url

    active_dict = context.chat_data.get('active domains', {})  # This sh/could have been a set instead. stuck as dict for legacy reasons
    text = await add_bypasses(url, context=context) if get_domain(url) in active_dict else ''

    incoming_id = update.effective_message.message_id
    response_id = None
    response_record = context.chat_data.get('response record', {})

    if incoming_id in response_record:  # Ie, edited message has already been responded to previously
        response_id = await edit(text, response_record[incoming_id], update, context)  # Will delete the response if the new text is empty
    elif text:  # This gets checked inside say() as well, but that creates phanton "bot said: nothing" type log messages
        response_id = await say(text, update, context)

    if response_id:
        response_record_add(incoming_id, response_id, context)


# user accessible commands
@log
@send_typing_action
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = create_deep_linked_url(context.bot.username, 'yes', group=True)
    text = f'Use <a href="{url}">this link</a> to share this bot to another group.\n' \
           f'FYI, your telegram user id is <code>{update.effective_user.id}</code>, ' \
           f'this chat id is <code>{update.effective_chat.id}</code> ' \
           f'and this bot\'s user id is <code>{application.bot.id}</code>'
    await say(text, update, context)


@log
@send_typing_action
async def version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = 'https://raw.githubusercontent.com/Yossi/outline-tg-bot/master/VERSION'
    r = httpx.get(url)
    if r.text != __version__:
        await say(f'Local version: {__version__}\nOnline version: <a href="https://github.com/Yossi/outline-tg-bot">{r.text}</a>', update, context)
    else:
        await say(__version__, update, context)


@log
@send_typing_action
async def translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Run the page at url through google translate'''
    if update.effective_message.reply_to_message:
        url = get_url(update.effective_message.reply_to_message.text)
    else:
        url = context.chat_data.get('last url')

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
@send_typing_action
async def include(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Add domains to the set that gets acted on'''
    active_dict = context.chat_data.get('active domains', {})
    try:
        if context.args:
            domain = get_domain(context.args[0])
            text = f'{context.args[0]} added'
        elif update.effective_message.reply_to_message:
            url = get_url(update.effective_message.reply_to_message.text)
            domain = text = get_domain(url)
            if url:
                text = await add_bypasses(url, context=context)
        else:
            domain = get_domain(context.chat_data.get('last url'))
            text = await add_bypasses(context.chat_data.get('last url'), context=context)

    except TypeError:
        domain = text = 'no domain'

    if domain != 'no domain':
        active_dict[domain] = None  # Really wish this was a set instead of dict
        context.chat_data['active domains'] = active_dict

    await say(text, update, context)


@log
@send_typing_action
async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''See and remove domains in/from active_dict if needed'''
    active_dict = context.chat_data.get('active domains', {})
    if not context.args and active_dict:
        await list_active_domains(update, context)
    else:
        try:
            del active_dict[' '.join(context.args)]
            text = f"Removed {' '.join(context.args)}"
        except KeyError:
            text = f"Failed to remove {' '.join(context.args)}\nAlready gone? Check your spelling?"
        await say(text, update, context)


@log
@send_typing_action
async def list_active_domains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''List only. /list used to be an alias for /remove, but that's just asking for trouble'''
    active_dict = context.chat_data.get('active domains', {})
    text = '</code>\n<code>'.join((f'{url}' for url in active_dict.keys()))
    if not text:
        text = 'no domains yet'
    text = f"<code>{text}</code>"
    await say(text, update, context)


@log
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
            logging.info('message probably too old to delete')


@log
async def export_urls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    '''Make settings avaliable as a CSV file'''
    await context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    chat_id = update.effective_message.chat_id
    bio = BytesIO('\n'.join(context.chat_data['active domains'].keys()).encode('utf8'))
    bio.name = f'{chat_id}_urls_backup.txt'
    await context.bot.send_document(chat_id=chat_id, document=bio)


@log
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

    active_dict = context.chat_data.get('active domains', {})

    line_ending = '\n' if ',' not in bio.read().decode() else ',\n'
    bio.seek(0)

    added_domains = []
    for domain in bio.read().decode().split(line_ending):
        if domain == get_domain(domain):
            active_dict[domain] = None  # Really wish this was a set instead of dict
            added_domains.append(domain)

    context.chat_data['active domains'] = active_dict

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

    await application.bot.set_my_short_description('Paywall bypass finder bot')


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
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), incoming))
    application.add_handler(MessageHandler(filters.Document.TEXT, import_urls)) # filters.Caption(['/import']) &

    application.add_error_handler(error_handler)

    application.run_polling()
