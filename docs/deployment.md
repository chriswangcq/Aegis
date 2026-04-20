# Aegis Deployment Guide

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | Required |
| Git | Any | Needed by CI runner |
| SSH Client | Any | Needed for remote CI/deploy |
| Docker | 20+ | Optional (recommended) |

---

## Option 1: Docker (Recommended)

### Single Container

```bash
# Build
docker build -t aegis .

# Run
docker run -d \
  --name aegis \
  -p 9800:9800 \
  -e AEGIS_ADMIN_KEY=your-secure-random-key-here \
  -v aegis-data:/app/data \
  -v $HOME/.ssh:/root/.ssh:ro \
  --restart unless-stopped \
  aegis
```

> **Note:** Mount `.ssh` directory if you need SSH-based CI execution.

### Docker Compose

```yaml
version: "3.8"
services:
  aegis:
    build: .
    ports:
      - "9800:9800"
    environment:
      AEGIS_ADMIN_KEY: "${AEGIS_ADMIN_KEY}"
    volumes:
      - aegis-data:/app/data
      - ${HOME}/.ssh:/root/.ssh:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9800/status"]
      interval: 30s
      timeout: 5s
      retries: 3

volumes:
  aegis-data:
```

```bash
AEGIS_ADMIN_KEY=my-secret docker compose up -d
```

---

## Option 2: Bare Metal

```bash
# Clone
git clone https://github.com/chriswangcq/Aegis.git
cd Aegis

# Install
pip install -r requirements.txt

# Run
export AEGIS_ADMIN_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
echo "Admin key: $AEGIS_ADMIN_KEY"

python -m uvicorn server.main:app \
  --host 0.0.0.0 \
  --port 9800 \
  --workers 1
```

> **Important:** SQLite doesn't support multiple workers. Keep `--workers 1`.

### Systemd Service

```ini
# /etc/systemd/system/aegis.service
[Unit]
Description=Aegis Engineering Governance Platform
After=network.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/opt/aegis
Environment=AEGIS_ADMIN_KEY=your-secret-key
ExecStart=/opt/aegis/venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 9800
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable aegis
sudo systemctl start aegis
```

---

## Option 3: Behind Nginx (Production)

```nginx
# /etc/nginx/sites-available/aegis
server {
    listen 443 ssl http2;
    server_name aegis.your-domain.com;

    ssl_certificate /etc/letsencrypt/live/aegis.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aegis.your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:9800;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (future)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## Post-Deployment Checklist

### 1. Verify Health

```bash
curl http://localhost:9800/status
# {"projects": 0, "tickets": 0, "agents": 0, "roles": 5}
```

### 2. Create Admin User

```bash
curl -X POST http://localhost:9800/api/register \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"admin", "password":"your-password", "display_name":"Admin"}'
```

### 3. Create First Project

```bash
curl -X POST http://localhost:9800/projects \
  -H "Authorization: Bearer $AEGIS_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "my-project",
    "name": "My Project",
    "repo_url": "https://github.com/org/repo.git",
    "master_id": "admin"
  }'
```

### 4. Distribute CLI

```bash
# Copy CLI to agent machines
scp cli/aegis.py agent-host:~/aegis

# On agent machine
chmod +x ~/aegis
~/aegis init --server https://aegis.your-domain.com --api-key <key>
```

### 5. Setup SSH Keys for CI

```bash
# Generate SSH key pair (if not existing)
ssh-keygen -t ed25519 -f ~/.ssh/aegis_ci -N ""

# Copy to CI/deploy machines
ssh-copy-id -i ~/.ssh/aegis_ci deploy@ci-host
ssh-copy-id -i ~/.ssh/aegis_ci deploy@pre-host
ssh-copy-id -i ~/.ssh/aegis_ci deploy@prod-host
```

---

## Backup & Recovery

### Backup

```bash
# Data is a single SQLite file
cp /app/data/command-center.db /backup/aegis-$(date +%Y%m%d).db
```

### Automated Backup (cron)

```bash
# Daily backup at 2 AM
0 2 * * * cp /opt/aegis/data/command-center.db /backup/aegis-$(date +\%Y\%m\%d).db
```

### Restore

```bash
# Stop Aegis
systemctl stop aegis

# Restore backup
cp /backup/aegis-20260420.db /opt/aegis/data/command-center.db

# Start Aegis
systemctl start aegis
```

---

## Monitoring

### Health Endpoint

```
GET /status
```

Response:
```json
{
  "projects": 3,
  "tickets": 47,
  "agents": 8,
  "roles": 5
}
```

### Key Metrics to Monitor

| Metric | How to Check | Alert When |
|--------|-------------|------------|
| Server up | `GET /status` returns 200 | Non-200 for 60s |
| DB size | `ls -la data/command-center.db` | > 1GB |
| Stale tickets | Tickets in `implementation` for > 24h | Agent may be stuck |
| Canary failures | Events with `canary_failed` | Auto-rollback triggered |

---

## Upgrading

```bash
# Pull latest
git pull origin main

# Re-install dependencies
pip install -r requirements.txt

# Restart (schema migrations run automatically on startup)
systemctl restart aegis
```

> **Note:** Schema migrations in `db.py` use `CREATE TABLE IF NOT EXISTS`, so upgrades are safe.
