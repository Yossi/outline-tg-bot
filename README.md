# outlinebot


## What this does
This is a telegram bot that attempts to create URL hacks that help people read news sites with user hostile designs. 
The bot will spit out log messages at the info level when messages come in or out. It does not attempt to permanently save these logs anywhere.

Add the bot to a group chat and make it an admin so it can see all messages. Or talk to it in pm.

Regular users can use the bot as follows:  
Post message as usual. The bot will silently detect and remember the last link it sees.  
When you know that the the most recent link seen needs to get the bot treatment you say `/include`.  
Immediately and from now on the bot will attempt to post a list of bypass links for all urls from that domain.  
You can also add domains manually with `/include domain.tld`.  
`/list` will show all the domains the bot is set to act on.  
`/remove domain.tld` to remove one.  

Additionally, users can request a google translate version of the most recent link by sending `/translate`.  
`/translate` defaults to english but will also accept a list of ISO-639-1 language codes. For example `/translate en de es`.

As a bot admin you have some commands that only you can run:  
`/r` - restart the bot.  
`/data` - Show all the stored data for the chat where you sent the command from.  
`/data clear <key>` - delete all the data in `<key>`.  
If the bot throws an exception it will send it to you in a pm.

## Setup
Requires python3.10 or better.  
Get python3.10.  
`sudo add-apt-repository ppa:deadsnakes/ppa -y`  
`sudo apt update`  
`sudo apt install python3.10 python3.10-venv -y`  

Clone this repo and `cd` into it.  
`git clone https://github.com/Yossi/outline-tg-bot.git`  
`cd outline-tg-bot`

Talk to [@botfather](https://t.me/botfather) and get an api key.
Open `secrets.py.example` and add the api key in the right place. Also add your tg user id to list of admins.
Save this edited file as `secrets.py` (no `.example` on the end).

Create a virtualenv.  
`python3.10 -m venv venv/`  
Activate it.  
`source venv/bin/activate`  
Update pip to avoid warnings.  
`pip install --upgrade pip`  
Install requirements.  
`pip install --upgrade -r requirements.txt`

Run the bot.  
`python bot.py`

You can instead run the bot with `git ls-files | entr -r python bot.py` and the bot will autoreload when any of the git tracked files change.

