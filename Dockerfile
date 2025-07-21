FROM python:3.12-slim

# Set working directory
WORKDIR /llm-service

# Copy all application files
COPY . .

# Install Python dependencies (if any)
COPY requirements_production.txt /
RUN pip install --no-cache-dir -r requirements_production.txt || true

# Copy environment variables
COPY .env /llm-service/

CMD ["python", "redis_pub_sub.py"]
