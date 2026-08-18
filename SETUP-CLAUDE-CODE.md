# Як поставити Claude Code на сервер і почати

## 1. Доставити комплект на сервер

З вашого Mac (архів `modidx-kit-v3.zip` з чату):

```bash
scp modidx-kit-v3.zip root@65.21.189.197:/tmp/     # підставте свій IP і користувача
ssh root@65.21.189.197

apt-get update && apt-get install -y unzip
unzip -q /tmp/modidx-kit-v3.zip -d /tmp/kitsrc     # усе лежить у теці kit/
mkdir -p /srv/modidx
cp -a /tmp/kitsrc/kit/. /srv/modidx/
rm -rf /tmp/kitsrc /tmp/modidx-kit-v3.zip
cd /srv/modidx && chmod +x bin/*.sh && ls -1
```

Має бути видно: `CLAUDE.md`, `PLAN.md`, `README.md`, `bin/`, `indexer/`, `systemd/`, `data/`.

## 2. Поставити Claude Code

Потрібен Node 18+.

```bash
# Debian/Ubuntu
apt-get update && apt-get install -y curl
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs
node -v && npm -v

npm install -g @anthropic-ai/claude-code
claude --version
```

## 3. Запустити в теці проєкту

```bash
cd /srv/modidx
claude
```

При першому запуску попросить авторизуватися — увійдіть тим самим акаунтом, що й тут.
Claude Code сам прочитає `CLAUDE.md` і буде знати весь контекст: цифри, архітектуру,
обмеження машини, порядок робіт і чого не робити.

**Порада:** запускайте у `tmux` або `screen`, щоб сесія не вмирала разом із SSH:
```bash
apt-get install -y tmux
tmux new -s modidx
cd /srv/modidx && claude
# відʼєднатися: Ctrl+B, потім D · повернутися: tmux attach -t modidx
```

## 4. Перше повідомлення в сесії на сервері

Скопіюйте це як перший промпт:

> Прочитай CLAUDE.md. Ми на сервері проєкту. Задача сесії 1:
> 1) запусти `bash bin/preflight.sh`, покажи і поясни результат;
> 2) визнач, що саме зараз слухає 80 і 443 і як це коректно перевести на 127.0.0.1:8080,
>    зберігши робочу копію конфігу;
> 3) перевір DNS апексу allservices.one, окремо запис AAAA — він може вести на інший хост;
> 4) якщо конфліктів немає, виконай bootstrap.sh і `python3 indexer/harvest.py`,
>    і порівняй цифри з тими, що в CLAUDE.md.
> Нічого не видаляй і не перезаписуй без попереднього бекапу. Показуй команди перед виконанням.

## 5. Що варто зробити в перші дві хвилини

```bash
cp -a /etc/nginx /root/backup-nginx-$(date +%F)     # або apache2 — що там є
crontab -l > /root/backup-crontab-$(date +%F).txt 2>/dev/null
docker ps -a > /root/backup-docker-$(date +%F).txt 2>/dev/null
```
Бекап конфігу наявного ресурсу **до** будь-яких змін портів. Це п'ять секунд,
які врятують вечір.

## Чому саме так, а не через SSH зі сторони чату

Сесія в чаті працює в пісочниці, де вихід у мережу дозволений лише на 80/443 —
порт 22 закритий навіть до github.com. Тому доступ по SSH мені передавати немає сенсу:
технічно не запрацює. Claude Code на самому сервері має повний локальний доступ і
робить те саме без посередників.

При цьому цикл перевірки лишається двобічним: коли Caddy підніметься, я з чату можу
відкрити `https://allservices.one` по 443 і сказати, що видно зовні — сертификат,
заголовки, вміст сторінок, чи правильно працюють 301 зі старих шляхів.
