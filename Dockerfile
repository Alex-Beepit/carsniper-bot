FROM python:3.11

# Κλείνουμε όλες τις ερωτήσεις του Linux κατά την εγκατάσταση
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Εγκατάσταση εικονικής οθόνης (xvfb) αθόρυβα
RUN apt-get update && apt-get install -y xvfb tzdata

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Εγκατάσταση Chromium και των εξαρτήσεών του
RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

# Εκκίνηση του bot στην εικονική οθόνη
CMD ["xvfb-run", "-a", "python", "bot.py"]
