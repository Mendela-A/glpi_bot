# Розгортання GLPI Telegram Bot на CentOS 9

> Сценарій: GLPI встановлений нативно на хості (без Docker).
> Бот і Redis запускаються у Docker-контейнерах на тій самій машині.

---

## Вимоги

- CentOS 9 з Docker CE (інструкція нижче)
- GLPI запущений на хості (Apache/Nginx + PHP + MariaDB)
- Машина має **вихідний** доступ до інтернету на порту 443 (`api.telegram.org`) — потрібен для polling

---

## 1. Встановити Docker

```bash
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
```

Перевірити:
```bash
docker compose version   # має бути v2.x
```

---

## 2. Отримати код

```bash
git clone <repo_url> glpi_bot
cd glpi_bot
```

---

## 3. Налаштувати `.env`

```bash
cp .env.example .env
vim .env
```

Ключові змінні:

| Змінна | Значення |
|--------|---------|
| `BOT_TOKEN` | токен від @BotFather |
| `TECHNICIANS_CHAT_ID` | ID групи техніків (від'ємне число) |
| `GLPI_URL` | `http://host.docker.internal:80` — порт де GLPI слухає на хості |
| `GLPI_EXTERNAL_URL` | `http://192.168.X.X:80` — IP машини в локалці (для посилань у повідомленнях) |
| `GLPI_APP_TOKEN` | Налаштування GLPI → Загальне → API |
| `GLPI_USER_TOKEN` | Профіль користувача GLPI → API token |
| `REDIS_URL` | `redis://redis:6379/0` (залишити як є) |

### Як отримати ID групи Telegram

Переслати будь-яке повідомлення з групи боту [@userinfobot](https://t.me/userinfobot).

---

## 4. Firewalld — доступ контейнера до GLPI на хості

Docker-контейнери ходять через bridge-мережу (`172.16.0.0/12`).
Firewalld за замовчуванням блокує цей трафік до хоста.

```bash
sudo firewall-cmd --permanent --zone=trusted --add-source=172.16.0.0/12
sudo firewall-cmd --reload
```

Перевірити що GLPI доступний з контейнера:
```bash
docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl \
  curl -s http://host.docker.internal:80/apirest.php
# має повернути JSON з version
```

---

## 5. Запустити

```bash
docker compose -f docker-compose.bot.yml up -d --build
```

Перевірити логи:
```bash
docker logs glpi_bot -f
```

Очікувані рядки при успішному старті:
```
INFO GLPI сесію успішно ініціалізовано
INFO Завантажено категорій: N
INFO Завантажено bot-тікетів: N
INFO Run polling for bot @...
```

---

## 6. Управління

```bash
# Статус
docker compose -f docker-compose.bot.yml ps

# Зупинити
docker compose -f docker-compose.bot.yml down

# Перезапустити після оновлення коду
docker compose -f docker-compose.bot.yml up -d --build

# Логи в реальному часі
docker logs glpi_bot -f
```

---

## 7. Перевірка після розгортання

1. Написати боту `/start` у приваті — має з'явитися меню категорій
2. Створити тестову заявку — перевірити що вона з'явилась у GLPI
3. Додати коментар до заявки в GLPI — через ≤5 хв має прийти сповіщення в Telegram

---

## Troubleshooting

**`ConnectionRefusedError` до GLPI**
- Перевірити що firewalld дозволяє трафік з Docker bridge (крок 4)
- Перевірити порт: `ss -tlnp | grep :80`
- Перевірити `GLPI_URL` в `.env` — чи правильний порт

**Бот не відповідає**
- `docker logs glpi_bot` — шукати `ERROR`
- Перевірити `BOT_TOKEN`
- Перевірити вихідний доступ: `curl -I https://api.telegram.org`

**SELinux блокує**
```bash
sudo ausearch -c 'python3' --raw | audit2allow -M mypol
sudo semodule -i mypol.pp
```
