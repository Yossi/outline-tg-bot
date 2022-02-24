FROM python:3.10-slim
RUN apt-get update\
 && apt-get --no-install-recommends -y upgrade\
 && apt-get clean\
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home botuser
USER botuser

WORKDIR /home/botuser

ENV VIRTUAL_ENV=./venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip wheel\
 && pip install --no-cache-dir --requirement requirements.txt\
 && pip uninstall -y wheel pip

COPY bot.py .
CMD ["python", "bot.py"]
