  Для користувачів

  - Показувати текст вирішення з GLPI у сповіщенні про закриття заявки
  (запит ITILSolution по ticket_id, якщо поле не порожнє)

  ---
  Для техніків

  - Взяти заявку в роботу — кнопка прямо в сповіщенні групи
  - Закрити заявку — без заходу в GLPI
  - Призначити на конкретного техніка — через inline-кнопки в групі

  ---
  Адміністративне

  - Авторизація через членство в групі — перевірка getChatMember(ALLOWED_GROUP_ID)
  замість статичного списку ALLOWED_USER_IDS
  - Увімкнути авторизацію — вже є в коді, треба лише розкоментувати і заповнити
  ALLOWED_USER_IDS
  - Команда /admin — статистика: скільки відкритих заявок, хто не відповів тощо
  - Автоматичне нагадування — якщо заявка без відповіді > N годин, нагадування в групу

  ---
  Технічне

  - .gitignore для .env — захист токенів від потрапляння в репозиторій

  ---
  Security (результат code review)

  HIGH
  - [ ] [H-1] handlers/tickets.py:140 — IDOR: cancel_ticket_callback показує діалог без перевірки власника ticket_id (є тільки в cancel_yes:)
  - [ ] [H-2] middleware.py:68 — rate limiting вимкнений під час FSM-станів; додати м'який ліміт ~30/60с

  MEDIUM
  - [ ] [M-1] polling.py:82 — HTML-ін'єкція: ticket_name в hbold() без html.escape()
  - [ ] [M-2] polling.py:109 — при переповненні _notified_followups (10k) .clear() → повторна доставка старих follow-up; замінити на LRU-eviction
  - [ ] [M-3] handlers/followup.py:54 — немає ліміту довжини follow-up відповіді (ticket form має 2000 символів)
  - [ ] [M-5] handlers/common.py:53 — str(exc) адміну може містити GLPI session token; логувати повний exc на сервері, адміну — тільки тип

  LOW
  - [ ] [L-2] docker-compose.yml:23 — GLPI порт відкритий на 0.0.0.0:8080; змінити на 127.0.0.1:8080:80
  - [ ] [L-3] Redis без пароля; додати requirepass в обидва compose + REDIS_URL в .env.example
  - [ ] [L-4] requirements.txt — aiohttp==3.9.5 має CVE; оновити до >=3.10.11