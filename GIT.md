# Git-потік: Mac ↔ GitHub ↔ сервер

Репозиторій: **https://github.com/allservices-one/o-modules** — публічний.
На сервері клонується в `/srv/modidx`.

## Чому через git
Правки під час першого живого прогону робитимуться **на сервері** (`runner.py` ще не
запускався в реальному Docker). Через git вони повертаються до вас одним `git pull`,
а не копіюванням файлів туди-сюди.

---

## Крок 1. Прибрати сміття після перенесення (один раз, на Mac)
```bash
cd /Users/serhii/Dev/modules
bash cleanup-after-transfer.sh
```
Обовʼязково **до** будь-яких команд git: після перенесення через мостик залишились
порожні `.git/*.lock`, які блокують git.

## Крок 2. Перевірити, що секретів немає
```bash
git ls-files | grep -E '^\.env$|^var/|pf\.txt|\.pem$|id_rsa' && echo "СТОП" || echo "чисто"
```

## Крок 3. Запушити з Mac
```bash
cd /Users/serhii/Dev/modules
git add -A
git commit -m "Module Health Index: стартовий комплект, план, перший зріз OCA"
git remote add origin https://github.com/allservices-one/o-modules.git
git branch -M main
git push -u origin main
```
Якщо просить креденшели: `gh auth login`, або HTTPS із Personal Access Token замість пароля.

---

## Крок 4. Доступ для сервера

Репозиторій публічний, тому **читати** можна без креденшелів. Але сервер має ще й **пушити**
свої правки — для цього потрібен ключ. Найпростіше зробити одразу через SSH, тоді і клон,
і push працюють одним механізмом.

На сервері:
```bash
ssh-keygen -t ed25519 -C "modidx-server" -f /root/.ssh/modidx -N ""
cat /root/.ssh/modidx.pub
```
Вивід вставити в GitHub: репозиторій → **Settings → Deploy keys → Add deploy key**,
назва `modidx-server`, і **обовʼязково увімкнути «Allow write access»**.

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
ssh -T git@github-modidx     # має відповісти, що аутентифікація успішна
git clone git@github-modidx:allservices-one/o-modules.git /srv/modidx
cd /srv/modidx && chmod +x bin/*.sh && ls -1
```

Якщо не хочете зараз морочитися з ключем — можна клонувати анонімно і додати push пізніше:
```bash
git clone https://github.com/allservices-one/o-modules.git /srv/modidx
```

---

## Крок 5. Робоче правило

**Сервер комітить, ви пулите.**

На сервері одноразово:
```bash
cd /srv/modidx
git config user.name "modidx-server"
git config user.email "server@allservices.one"
```
У роботі:
```bash
git pull --rebase
# … правки під час прогонів …
git add -A && git commit -m "fix(runner): реальний шлях до addons" && git push
```
У вас на Mac:
```bash
cd /Users/serhii/Dev/modules && git pull
```

---

## Що НІКОЛИ не комітити
| Що | Чому |
|---|---|
| `.env` | пароль Postgres |
| `var/` | чекаути OCA (гігабайти), логи, згенерований сайт, бекапи |
| `pf.txt` | вивід preflight: зовнішні IP і структура сервера |

Усе це вже в `.gitignore`. Не прибирати звідти.

---

## Другий репозиторій — під датасет
Код і датасет тримати роздільно: датасет оновлюється щодня і засмітив би історію коду.
Коли дійде до пункту 26 у `STEPS.md` — створити `allservices-one/o-modules-data`
і підключити його як `var/dataset` (він у `.gitignore` цього репозиторію, конфлікту не буде).
Саме той репозиторій стає публічним джерелом, на яке посилаються і яке цитують LLM.
