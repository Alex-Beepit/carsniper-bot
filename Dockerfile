FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Εγκατάσταση του Xvfb (Virtual Framebuffer) για την εικονική οθόνη
RUN apt-get update && apt-get install -y xvfb

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Εκκίνηση του bot ΜΕΣΑ στην εικονική οθόνη (xvfb-run -a)
CMD ["xvfb-run", "-a", "python", "bot.py"]
