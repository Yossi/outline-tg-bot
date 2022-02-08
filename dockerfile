FROM python:3.10

VOLUME /app/secrets.py

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
COPY . .

CMD [ "python3", "bot.py"]