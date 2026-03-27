# GLPI 11 + Telegram Bot — Тестове середовище

## Структура проекту

```
glpi-bot/
├── docker-compose.yml
├── .env
├── glpi/
│   ├── config/
│   └── files/
└── bot/
    ├── Dockerfile
    ├── bot.py
    ├── requirements.txt
    └── .env
```

---

## 1. Створення структури

```bash
mkdir -p glpi-bot/{glpi/{config,files},bot}
cd glpi-bot
```

---

## 2. `.env` (корінь проекту)

```env
MARIADB_ROOT_PASSWORD=rootpassword
MARIADB_DATABASE=glpi
MARIADB_USER=glpi
MARIADB_PASSWORD=glpipassword

BOT_TOKEN=your_telegram_bot_token
TECHNICIANS_CHAT_ID=your_group_chat_id
GLPI_URL=http://glpi:80
GLPI_APP_TOKEN=          # заповнити після налаштування GLPI
GLPI_USER_TOKEN=         # заповнити після налаштування GLPI
```

---

## 3. `docker-compose.yml`

```yaml
version: '3.8'

services:
  mariadb:
    image: mariadb:10.11
    container_name: glpi_db
    restart: unless-stopped
    environment:
      MARIADB_ROOT_PASSWORD: ${MARIADB_ROOT_PASSWORD}
      MARIADB_DATABASE: ${MARIADB_DATABASE}
      MARIADB_USER: ${MARIADB_USER}
      MARIADB_PASSWORD: ${MARIADB_PASSWORD}
    volumes:
      - db_data:/var/lib/mysql
    networks:
      - glpi_net

  glpi:
    image: glpi/glpi:11.0.0
    container_name: glpi_app
    restart: unless-stopped
    ports:
      - "8080:80"
    environment:
      MARIADB_HOST: mariadb
      MARIADB_PORT: 3306
      MARIADB_DATABASE: ${MARIADB_DATABASE}
      MARIADB_USER: ${MARIADB_USER}
      MARIADB_PASSWORD: ${MARIADB_PASSWORD}
    volumes:
      - ./glpi/config:/var/www/html/config
      - ./glpi/files:/var/www/html/files
    depends_on:
      - mariadb
    networks:
      - glpi_net

  bot:
    build: ./bot
    container_name: glpi_bot
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      - glpi
    networks:
      - glpi_net

volumes:
  db_data:

networks:
  glpi_net:
    driver: bridge
```

---

## 4. `bot/requirements.txt`

```
aiogram==3.7.0
aiohttp==3.9.5
python-dotenv==1.0.1
```

---

## 5. `bot/Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
```

---

## 6. Запуск

```bash
# Запустити середовище
docker compose up -d

# Перевірити логи
docker compose logs -f

# Зупинити
docker compose down
```

---

## 7. Перше налаштування GLPI

1. Відкрити `http://localhost:8080`
2. Пройти інсталятор (вказати дані з `.env`)
3. Увімкнути REST API:
   - **Setup → General → API**
   - Увімкнути `Enable Rest API`
   - Створити **Application token** → вставити в `.env` як `GLPI_APP_TOKEN`
4. Отримати **User token**:
   - **My account → API token** → вставити в `.env` як `GLPI_USER_TOKEN`
5. Перезапустити бота:
   ```bash
   docker compose restart bot
   ```

---

## 8. Категорії заявок (для бота)

| ID | Назва |
|----|-------|
| 1  | 💻 Комп'ютер / ноутбук |
| 2  | 🖨 Принтер |
| 3  | 🌐 Мережа / інтернет |
| 4  | 📦 Програмне забезпечення |
| 5  | 🔧 Інше |

> ID потрібно буде звірити з реальними ID категорій у GLPI після налаштування.

---

## Наступні кроки

- [ ] Створити бота через @BotFather, отримати токен
- [ ] Отримати chat_id групи техніків
- [ ] Запустити `docker compose up -d`
- [ ] Налаштувати GLPI через веб-інтерфейс
- [ ] Заповнити токени в `.env`
- [ ] Написати `bot.py`