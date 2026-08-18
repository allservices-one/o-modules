# Git-потік: локально ↔ GitHub ↔ сервер

Репозиторій: **https://github.com/deasonsv/modules**

## Чому саме так
Правки під час першого живого прогону робитимуться **на сервері** (`runner.py` ще не
запускався в реальному Docker). Через git вони повертаються до вас у локальну теку одним
`git pull`, а не копіюванням файлів туди-сюди.

## Крок 1. Прибрати сміття після перенесення (один раз)
```bash
cd /Users/serhii/Dev/modules
bash cleanup-after-transfer.sh
```
Обовʼязково **до** будь-яких команд git: залишились порожні `.git/*.lock`, які їх блокують.

## Крок 2. Запушити з Mac
```bash
cd /Users/serhii/Dev/modules
git add -A
git commit -m "docs: git-потік"        # якщо є незакомічене
git remote add origin https://github.com/deasonsv/modules.git
git branch -M main
git push -u origin main
```
Якщо просить логін — або `gh auth login`, або HTTPS з Personal Access Token замість пароля.

## Крок 3. Публічний чи приватний?

**Рекомендація: публічний.** Причини:
- Датасет за планом і так має бути відкритим — це канал цитування в LLM і offsite-бекап.
- Клон на сервер без жодних креденшелів.
- Секретів у коді немає: `.env` (пароль Postgres) у `.gitignore`, і туда він не потрапить.
- Моат проєкту — щоденні прогони й накопичені дані, а не 300 рядків Python.

Якщо все ж приватний — на сервері потрібен deploy key, команди нижче.

## Крок 4. Клон на сервер

### Публічний репозиторій
```bash
ssh root@65.21.189.197
apt-get update && apt-get install -y git
git clone https://github.com/deasonsv/modules.git /srv/modidx
cd /srv/modidx && chmod +x bin/*.sh && ls -1
```

### Приватний репозиторій — deploy key
На сервері:
```bash
ssh-keygen -t ed25519 -C "modidx-server" -f /root/.ssh/modidx -N ""
cat /root/.ssh/modidx.pub
```
Публічний ключ вставити в GitHub: репозиторій → Settings → Deploy keys → Add deploy key,
**увімкнути «Allow write access»** (сервер має пушити свої правки).
Далі на сервері:
```bash
cat >> /root/.ssh/config <<'CFG'
Host github-modidx
  HostName github.com
  User git
  IdentityFile /root/.ssh/modidx
  IdentitiesOnly yes
CFG
chmod 600 /root/.ssh/config
git clone git@github-modidx:deasonsv/modules.git /srv/modidx
```

## Крок 5. Робоче правило

**Сервер комітить, ви пулите.** На сервері:
```bash
cd /srv/modidx
git config user.name "modidx-server"
git config user.email "server@allservices.one"
git pull --rebase
# … правки під час прогонів …
git add -A && git commit -m "fix(runner): реальний шлях до addons" && git push
```
У вас на Mac:
```bash
cd /Users/serhii/Dev/modules && git pull
```

## Що НІКОЛИ не комітити
- `.env` — там пароль Postgres. Уже в `.gitignore`, не прибирати.
- `var/` — чекаути OCA (гігабайти), логи, згенерований сайт, бекапи.
- `pf.txt` — вивід preflight: там зовнішні IP і структура сервера.

Перевірка перед першим пушем:
```bash
git ls-files | grep -E '^\.env|^var/|pf\.txt' && echo "СТОП: секрети у git" || echo "чисто"
```

## Окремий репозиторій під датасет
Код і датасет краще тримати роздільно: датасет оновлюється щодня, і його історія
засмітить репозиторій коду. Коли дійде до пункту 26 у `STEPS.md` — створити другий
репозиторій (наприклад `deasonsv/odoo-module-index-data`) і підключити його як
`var/dataset`. Він у `.gitignore` цього репозиторію, тому конфлікту не буде.
