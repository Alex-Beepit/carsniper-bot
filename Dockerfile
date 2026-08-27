FROM python:3.11-slim

# Να δείχνει τα σφάλματα αμέσως & να μην κάνει ερωτήσεις η εγκατάσταση
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Εγκατάσταση εικονικής οθόνης
RUN apt-get update && apt-get install -y xvfb tzdata

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ΕΔΩ ΗΤΑΝ ΤΟ ΛΑΘΟΣ: Πρέπει να έχει python -m μπροστά για να το βρει το Linux!
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium

COPY . .

# Εκκίνηση χωρίς αγκύλες
CMD xvfb-run -a python bot.py
