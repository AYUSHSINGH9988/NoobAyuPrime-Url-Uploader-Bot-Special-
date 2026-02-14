# Buster hata kar Bookworm (New Debian) use kar rahe hain
FROM python:3.9-slim-bookworm

# Working Directory
WORKDIR /app

# System Dependencies (ffmpeg, aria2, 7zip)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    p7zip-full \
    && rm -rf /var/lib/apt/lists/*

# Requirements install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY . .

# Start command
CMD ["python3", "main.py"]

