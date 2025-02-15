# Builder stage
FROM python:3.9-slim-bullseye AS builder

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# FFmpeg stage - using minimal build from mwader/static-ffmpeg
FROM mwader/static-ffmpeg:6.0 AS ffmpeg

# Final stage
FROM python:3.9-slim-bullseye

# Copy FFmpeg from ffmpeg stage
COPY --from=ffmpeg /ffmpeg /usr/local/bin/
COPY --from=ffmpeg /ffprobe /usr/local/bin/

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /usr/local/lib/python3.9/site-packages/ /usr/local/lib/python3.9/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copy only necessary bot files
COPY bot.py music_cog.py docker-entrypoint.sh ./

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Make entrypoint executable
RUN chmod +x docker-entrypoint.sh

# Run the bot
ENTRYPOINT ["./docker-entrypoint.sh"]