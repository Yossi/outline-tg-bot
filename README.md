# outlinebot


## What this does
This is a telegram bot that attempts to create URL hacks that help people read news sites with user hostile designs. 

Add the bot to a group chat and make it an admin so it can see all messages. Or talk to it in pm.

Regular users can use the bot as follows:  
Post message as usual. The bot will silently detect and remember the most recent link it sees.  
When you know that the the most recent link needs to get the bot treatment you say `/include`.  
Immediately and from now on the bot will attempt to post a list of bypass links for all urls from that domain.  
You can also send `/include` as a reply to a massage to traget the domain in that message even if it isnt the most resent one anymore. Or you can add domains manually with `/include domain.tld`.  
`/list` will show all the domains the bot is set to act on.  
`/remove domain.tld` to remove one.  
Reply to a bot message with `/delete` and the bot will delete that message and your `/delete` message too (if it can) to keep things tidy.  
Only works on bot messages less than 48 hours old (telegram restriction) or less than 10 bot messages ago (bot restriction).  

Additionally, users can request a google translate version of the most recent link by sending `/translate`.  
`/translate` defaults to english but will also accept a list of ISO-639-1 language codes. For example `/translate en fr es`.

As a bot admin you have some commands that only you can run:  
`/data` - Show all the stored data for the chat where you sent the command from.  
`/data clear <key>` - delete all the data in `<key>`.  
If the bot throws an exception it will send it to you in a pm.  
The bot will spit out log messages at the info level when messages come in or out. It does not attempt to permanently save these logs anywhere.  
Data stored by the bot (like the list of domains to bypass) lives in `data/bot.persist`.  

## Maintenance
Ideally new versions of the bot will be backward compatible with the existing `data/bot.persist` file, so back that up.  
But just in case, the list of domains to bypass can be exported per group with the `/export` command. This will create a simple text file with the domains listed in it. The filename will be `{chat_id}_urls_backup.txt`.  
To import this list back into the bot you just upload a text file with the exact fielname to match the chat you're in. You can even forward the message from `/export` back into the chat and not have to download the file.  

You can also run `/start` to find the chat_id to use for this filename, and your user_id to use in the list of admins.    

## Setup
Clone this repo and `cd` into it.  
`git clone https://github.com/Yossi/outline-tg-bot.git`  
`cd outline-tg-bot`

Talk to the [@botfather](https://t.me/botfather) and get an api key.
Open `data/secrets.py.example` and add the api key in the right place. Also add your tg user id to the list of admins.
Save this edited file as `data/secrets.py` (without `.example` on the end).

- **If you are going to use docker, skip ahead to [that section](#docker).**

Requires python3.10 or better.  
Get python3.10.  
`sudo add-apt-repository ppa:deadsnakes/ppa -y`  
`sudo apt update`  
`sudo apt install python3.10 python3.10-venv -y`  

Create a virtualenv.  
`python3.10 -m venv venv/`  
Activate it.  
`source venv/bin/activate`  
Update pip to avoid warning.  
`pip install --upgrade pip`  
Install requirements.  
`pip install --upgrade -r requirements.txt`

Run the bot.  
`python bot.py`

You can instead run the bot with `git ls-files | entr -r python bot.py` and the bot will autoreload when any of the git tracked files change.

### Docker
Build the docker image.  
`docker build --pull -t outlinebot .`  
Run it while passing in the full path to the `data/` directory. (`/home/you/outline-tg-bot/data/` perhaps?)  
`docker run -v /full/path/to/data/:/home/outlinebot/data/ --cap-drop=ALL outlinebot`  

## List of bypasses
- ~~[Outline](https://outline.com)~~ Dead as of March 2022  
- [Wayback Machine](https://archive.org)
- [archive.is](https://archive.is)  
- [Ghost Archive](https://ghostarchive.org)  
- Google Search Cache  
- [12ft Ladder](https://12ft.io)  
- [RemoveJS](https://remove-js.com)  
- [txtify it](https://txtify.it)  
- [Twiiit](https://twiiit.com) For twitter. Chooses a random nitter instance for you.  
