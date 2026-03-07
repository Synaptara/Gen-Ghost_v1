FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

# Normalize requirements encoding to UTF-8 in case the source file is UTF-16.
RUN python -c "from pathlib import Path; p=Path('/app/requirements.txt'); raw=p.read_bytes(); text=raw.decode('utf-16') if raw.startswith((b'\\xff\\xfe', b'\\xfe\\xff')) else raw.decode('utf-8'); p.write_text(text, encoding='utf-8')"

RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY . /app

RUN mkdir -p /app/data

CMD ["python", "main.py"]
