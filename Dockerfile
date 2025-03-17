FROM python:3.10-slim

# Set working directory
WORKDIR /llm-service

# Install dependencies
RUN apt-get update && apt-get install -y \
    redis-server \
    curl \
    ca-certificates \
    apt-transport-https \
    lsb-release \
    gnupg && \
    rm -rf /var/lib/apt/lists/*

# Copy all application files
COPY . /llm-service/

# Install Python dependencies (if any)
COPY requirements.txt /llm-service/
RUN pip install --no-cache-dir -r requirements.txt || true

# Copy environment variables
COPY .env /llm-service/

# Copy and set the entrypoint script
# COPY entrypoint.sh /entrypoint.sh
# RUN chmod +x /entrypoint.sh

# Use the entrypoint script to manage startup
# ENTRYPOINT ["/entrypoint.sh"]
RUN python redis_pub_sub.py
