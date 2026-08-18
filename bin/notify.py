#!/usr/bin/env python3
"""Пошта без власного MTA і без платних сервісів.

Власний MTA на VPS не піднімати: немає репутації IP і PTR — листи підуть у спам,
а домен постраждає. Тут звичайний авторизований SMTP-relay через наявну скриньку.

Налаштування у /srv/modidx/.env (це приватний проєкт — беремо особисту скриньку,
не корпоративну):

  # Gmail: потрібен App Password, не звичайний пароль
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=you@gmail.com
  SMTP_PASS=<app password>
  SMTP_FROM=you@gmail.com
  SMTP_TO=you@gmail.com

Коли дійде до розсилки партнерам — відправляти з адреси на allservices.one і
СПОЧАТКУ налаштувати SPF, DKIM і DMARC на домені. Без них половина листів не дійде.

Використання:
  python3 bin/notify.py "тема" "текст"
  echo "текст" | python3 bin/notify.py "тема"
"""
import os, pathlib, smtplib, ssl, sys
from email.message import EmailMessage

ROOT = pathlib.Path(os.environ.get("ROOT", "/srv/modidx"))


def env():
    cfg = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg.setdefault(k.strip(), v.strip())
    return cfg


def send(subject, body, to=None):
    c = env()
    host = c.get("SMTP_HOST")
    if not host:
        raise SystemExit("SMTP не налаштовано: додайте SMTP_* у .env")
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = c.get("SMTP_FROM", c.get("SMTP_USER"))
    m["To"] = to or c.get("SMTP_TO", c.get("SMTP_USER"))
    m.set_content(body)
    with smtplib.SMTP(host, int(c.get("SMTP_PORT", "587")), timeout=30) as s:
        s.ehlo()
        s.starttls(context=ssl.create_default_context())
        s.login(c["SMTP_USER"], c["SMTP_PASS"])
        s.send_message(m)
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("використання: notify.py <тема> [текст]")
    subject = sys.argv[1]
    body = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
    send(subject, body)
    print("надіслано")
