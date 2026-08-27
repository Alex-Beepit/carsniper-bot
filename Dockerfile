FROM python:3.11-slim

# Η ΠΙΟ ΣΗΜΑΝΤΙΚΗ ΕΝΤΟΛΗ: Λέει στην Python να δείχνει τα σφάλματα αμέσως!
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update && apt-get install -y xvfb tzdata

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

# Εκκίνηση χωρίς αγκύλες (shell mode) για να μην μπερδεύεται το Xvfb
CMD xvfb-run -a python bot.py
