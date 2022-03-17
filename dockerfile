FROM python:3.10-alpine
#RUN apk --update-cache upgrade

RUN adduser -D botuser
USER botuser
WORKDIR /home/botuser

# see .dockerignore for what files will be copied
COPY . .
RUN pip install --no-cache-dir --no-warn-script-location --requirement requirements.txt

CMD ["python", "bot.py"]
