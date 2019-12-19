## outlinebot

Only tested with python 3.6.9. Will in all likelihood work with any python 3.6 or better.

Create a virtualenv or whatever you are using to keep this tidy.
`virtualenv -p python3.6 venv`

Install requirements with pip
`pip3 install -r requirements.txt` (or just pip if you are in a venv)

Talk to @botfather and get an api key.
Open secrets.py.example and add the api key in the right place. Also add your tg user id to list of admins.
Save this edited file as secrets.py (no .example on the end).

Run the bot.
`python bot.py`

The bot will put out log messages at the info level as it sees things and when it talks. It does not attempt to save these logs anywhere.

Add the bot to a group chat and make it an admin so it can see all messages. Or talk to it in pm.

Regular users can use the bot as follows:
Post message as usual. The bot will silently detect and remember the last link it sees.
If you know what the last link seen needs to get the outline.com treatment you say `/include`.
From now on the bot will post an outline.com link for all urls from that domain.
You can also add domains manually with `/include domain.tld`.
Note: some domains are blacklisted in outline.com (newyorktimes.com, wsj.com). Work around is to use a url shortner.
This will set the domain to get url shortened first `/include domain.tld True`.
`/list` will show all the domains the bot is set to act on.
`/remove domain.tld` does just that.

As a bot admin you have some commands that only you can run:
`/r` - restart the bot. Handy for development. Note, this seems to sometimes lose the most recent settings. Work around is shut down with ctrl+C and rerun.
`/data` - Show all the stored data for the chat where you sent the command from. 
`/data clear <key>` - delete all the data in <key>.
If the bot throws an exception it will send it to you in a pm.
