FROM python:3.11-alpine
#RUN apk --update-cache upgrade

RUN adduser -D outlinebot
USER outlinebot
WORKDIR /home/outlinebot

# see .dockerignore for what files will be copied
COPY . .
RUN pip install --no-cache-dir --no-warn-script-location --requirement requirements.txt

CMD ["python", "bot.py"]
