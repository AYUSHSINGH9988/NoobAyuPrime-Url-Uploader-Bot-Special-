# Python ka lightweight version use kar rahe hain
FROM python:3.9-slim-buster

# Working Directory set kar rahe hain
WORKDIR /app

# System Dependencies install kar rahe hain (FFmpeg, Aria2, 7zip)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    aria2 \
    p7zip-full \
    && rm -rf /var/lib/apt/lists/*

# Requirements file copy karke install kar rahe hain
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baaki sara code copy kar rahe hain
COPY . .

# Bot start karne ka command
CMD ["python3", "main.py"]
