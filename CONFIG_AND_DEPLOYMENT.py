"""
==============================================================================
CONFIG & DEPLOYMENT - COMPLETE SETUP
==============================================================================
Everything needed for deployment:
- Docker & Docker Compose
- Environment variables
- Requirements.txt
- Alembic migrations
- Nginx configuration
- Deployment guides
==============================================================================
"""

# ==============================================================================
# 1. REQUIREMENTS.TXT
# ==============================================================================
fastapi==0.109.2
uvicorn[standard]==0.27.1
python-dotenv==1.0.1
pydantic==2.6.1
pydantic-settings==2.1.0
sqlalchemy[asyncio]==2.0.27
asyncpg==0.29.0
alembic==1.13.1
psycopg2-binary==2.9.9
redis==5.0.1
hiredis==2.3.2
openai==1.12.0
anthropic==0.18.1
Pillow==10.2.0
pillow-heif==0.15.0
pydub==0.25.1
ffmpeg-python==0.2.0
opencv-python==4.9.0.80
moviepy==1.0.3
python-docx==1.1.0
openpyxl==3.1.2
pypdf==4.0.1
reportlab==4.1.0
pandas==2.2.0
numpy==1.26.3
cryptography==42.0.2
bcrypt==4.1.2
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
pyjwt==2.8.0
stripe==8.1.0
twilio==8.13.0
sendgrid==6.11.0
python-telegram-bot==20.8
httpx==0.26.0
aiohttp==3.9.3
requests==2.31.0
beautifulsoup4==4.12.3
lxml==5.1.0
selenium==4.17.2
facebook-sdk==3.1.0
tweepy==4.14.0
clamd==1.0.2
python-slugify==8.0.4
python-dateutil==2.8.2
pytz==2024.1
python-multipart==0.0.9
email-validator==2.1.0.post1
phonenumbers==8.13.29
sentry-sdk[fastapi]==1.40.0
loguru==0.7.2
pytest==8.0.0
pytest-asyncio==0.23.4
pytest-cov==4.1.0

# ==============================================================================
# 2. .ENV.EXAMPLE
# ==============================================================================
DATABASE_URL=postgresql+asyncpg://autospare:password@localhost:5432/autospare
DB_PASSWORD=your-secure-password
REDIS_URL=redis://localhost:6379
REDIS_PASSWORD=your-redis-password
JWT_SECRET_KEY=your-jwt-secret-change-in-production
JWT_REFRESH_SECRET_KEY=your-refresh-secret-change-in-production
ENCRYPTION_KEY=your-32-byte-base64-encryption-key
GITHUB_TOKEN=ghp_your_github_token
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_PHONE_NUMBER=+1234567890
STRIPE_SECRET_KEY=sk_test_xxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxx
SENDGRID_API_KEY=SG.xxxxx
SENDGRID_FROM_EMAIL=support@autospare.com
GOV_API_KEY=your-gov-api-key
CLAMAV_HOST=localhost
CLAMAV_PORT=3310
SENTRY_DSN=https://xxxxx@sentry.io/xxxxx
ENVIRONMENT=development
DEBUG=true
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
MAX_UPLOAD_SIZE_MB=25
FILE_EXPIRY_DAYS=30
VAT_PERCENTAGE=17
PROFIT_MARGIN_PERCENTAGE=45
DEFAULT_SHIPPING_COST_ILS=91
CURRENCY_EXCHANGE_RATE_USD_TO_ILS=3.65

# ==============================================================================
# 3. DOCKER-COMPOSE.YML
# ==============================================================================
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: autospare_postgres
    environment:
      POSTGRES_DB: autospare
      POSTGRES_USER: autospare
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U autospare"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: autospare_redis
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    restart: unless-stopped

  backend:
    build: ./backend
    container_name: autospare_backend
    environment:
      DATABASE_URL: postgresql+asyncpg://autospare:${DB_PASSWORD}@postgres:5432/autospare
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      JWT_REFRESH_SECRET_KEY: ${JWT_REFRESH_SECRET_KEY}
      ENCRYPTION_KEY: ${ENCRYPTION_KEY}
      GITHUB_TOKEN: ${GITHUB_TOKEN}
      TWILIO_ACCOUNT_SID: ${TWILIO_ACCOUNT_SID}
      TWILIO_AUTH_TOKEN: ${TWILIO_AUTH_TOKEN}
      TWILIO_PHONE_NUMBER: ${TWILIO_PHONE_NUMBER}
      STRIPE_SECRET_KEY: ${STRIPE_SECRET_KEY}
      SENDGRID_API_KEY: ${SENDGRID_API_KEY}
      ENVIRONMENT: production
      DEBUG: "false"
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - upload_files:/app/uploads
    restart: unless-stopped
    command: sh -c "alembic upgrade head && uvicorn BACKEND_API_ROUTES:app --host 0.0.0.0 --port 8000 --workers 4"

  frontend:
    build: ./frontend
    container_name: autospare_frontend
    ports:
      - "80:80"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  upload_files:

# ==============================================================================
# 4. DOCKERFILE (Backend)
# ==============================================================================
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    postgresql-client \
    ffmpeg \
    clamav \
    libjpeg-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "BACKEND_API_ROUTES:app", "--host", "0.0.0.0", "--port", "8000"]

# ==============================================================================
# 5. NGINX.CONF
# ==============================================================================
user nginx;
worker_processes auto;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    client_max_body_size 25M;
    keepalive_timeout 65;

    gzip on;
    gzip_vary on;
    gzip_types text/plain text/css application/json application/javascript;

    upstream backend {
        server backend:8000;
    }

    server {
        listen 80;
        server_name autospare.com www.autospare.com;

        location / {
            root /usr/share/nginx/html;
            try_files $uri $uri/ /index.html;
        }

        location /api/ {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location /api/v1/chat/ws {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }
    }
}

# ==============================================================================
# 6. ALEMBIC.INI
# ==============================================================================
[alembic]
script_location = alembic
sqlalchemy.url = postgresql+asyncpg://autospare:password@localhost:5432/autospare

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s

# ==============================================================================
# 7. DEPLOYMENT GUIDES
# ==============================================================================

"""
===================
RAILWAY DEPLOYMENT
===================
1. Install Railway CLI: npm install -g @railway/cli
2. Login: railway login
3. Initialize: railway init
4. Add PostgreSQL: railway add postgresql
5. Add Redis: railway add redis
6. Set env vars: railway variables set KEY=value
7. Deploy: railway up
8. Get URL: railway domain
Cost: ~$5-10/month
"""

"""
===================
VPS DEPLOYMENT (Ubuntu 22.04)
===================
1. Update: sudo apt update && sudo apt upgrade -y
2. Install: sudo apt install -y python3.11 python3-pip postgresql redis nginx nodejs git
3. Clone repo: git clone <repo-url>
4. Setup venv: python3.11 -m venv venv && source venv/bin/activate
5. Install deps: pip install -r requirements.txt
6. Setup DB: sudo -u postgres psql
   CREATE DATABASE autospare;
   CREATE USER autospare WITH PASSWORD 'password';
   GRANT ALL PRIVILEGES ON DATABASE autospare TO autospare;
7. Run migrations: alembic upgrade head
8. Build frontend: cd frontend && npm install && npm run build
9. Configure Nginx: copy nginx.conf to /etc/nginx/sites-available/
10. SSL: sudo certbot --nginx -d autospare.com
11. Start: uvicorn BACKEND_API_ROUTES:app --host 0.0.0.0 --port 8000
"""

"""
===================
REPLIT DEPLOYMENT
===================
1. Fork to Replit
2. Create .replit file:
   run = "uvicorn BACKEND_API_ROUTES:app --host 0.0.0.0 --port 8000"
3. Add Secrets (env vars)
4. Run
Cost: Free tier available, ~$7/month for always-on
"""

# ==============================================================================
# 8. HELPER SCRIPTS
# ==============================================================================

# --- generate_keys.py ---
"""
import secrets, base64
print(f"JWT_SECRET_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}")
print(f"JWT_REFRESH_SECRET_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}")
print(f"ENCRYPTION_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}")
"""

# --- deploy.sh ---
"""
#!/bin/bash
git pull origin main
cd backend && source venv/bin/activate && pip install -r requirements.txt
alembic upgrade head
cd ../frontend && npm install && npm run build
sudo systemctl restart autospare-backend nginx
echo "Deployed!"
"""

# ==============================================================================
# END OF CONFIG & DEPLOYMENT
# ==============================================================================

print('✅ Complete Configuration & Deployment Ready!')
print('📦 Docker Compose configured')
print('🔧 All deployment guides included')
print('🚀 Ready to deploy to Railway, VPS, or Replit')
