FROM python:3.11-slim

WORKDIR /app

# 1. Πρώτα ενημερώνουμε το σύστημα της Linux και βάζουμε την εικονική οθόνη
RUN apt-get update && apt-get install -y xvfb

# 2. Αντιγράφουμε και εγκαθιστούμε τα πακέτα της Python (pandas, playwright κ.λπ.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Χρησιμοποιούμε την Python για να εγκαταστήσει σωστά τον Chromium
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium

COPY . .

# 4. Εκκίνηση του bot μέσα στην εικονική οθόνη
CMD ["xvfb-run", "-a", "python", "bot.py"]
