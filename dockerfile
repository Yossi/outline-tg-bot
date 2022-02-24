FROM python:3.10-alpine
RUN apk --update-cache upgrade

RUN adduser -D botuser
USER botuser
WORKDIR /home/botuser

COPY requirements.txt .
RUN pip install --no-cache-dir --no-warn-script-location --requirement requirements.txt

COPY bot.py .
CMD ["python", "bot.py"]
