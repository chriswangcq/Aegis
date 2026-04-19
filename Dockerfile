FROM python:3.11-slim

WORKDIR /app

# Install git (needed for CI runner to clone repos)
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for SQLite
RUN mkdir -p /app/data

EXPOSE 9800

CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "9800"]
