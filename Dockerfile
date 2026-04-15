FROM python:3.10-slim

WORKDIR /app

# 1. Install Dependencies (wget add kiya hai)
RUN apt-get update && apt-get install -y \
    git \
    ffmpeg \
    aria2 \
    p7zip-full \
    curl \
    wget \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 2. Install MegaCMD (Ye naya part hai)
RUN wget https://mega.nz/linux/repo/Debian_11/amd64/megacmd-Debian_11_amd64.deb && \
    apt-get update && apt-get install -y ./megacmd-Debian_11_amd64.deb && \
    rm megacmd-Debian_11_amd64.deb

# 3. Install Rclone (Updated Method)
RUN curl https://rclone.org/install.sh | bash

# 4. Download and install Deno locally for yt-dlp JavaScript challenges
RUN curl -fsSL https://deno.land/install.sh | sh

# Add Deno to the system PATH
ENV PATH="/root/.deno/bin:$PATH"

# 5. Python Reqs
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# 6. Copy Code
COPY . .

# 7. Permissions & Start Command (Is line ko dhyan se copy karna)
RUN chmod +x start.sh
CMD ["bash", "start.sh"]
