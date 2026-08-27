FROM python:3.11-slim

WORKDIR /app

# 1. Εγκατάσταση των Python βιβλιοθηκών
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Εγκατάσταση ΜΟΝΟ του ελαφρού Chromium και των συστημάτων εικονικής οθόνης
RUN playwright install chromium
RUN playwright install-deps chromium
RUN apt-get update && apt-get install -y xvfb

COPY . .

# 3. Εκκίνηση του bot μέσα στην εικονική οθόνη
CMD ["xvfb-run", "-a", "python", "bot.py"]
