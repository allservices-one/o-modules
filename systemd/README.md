# Встановлення юнітів

```bash
cp /srv/modidx/systemd/*.service /srv/modidx/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now modidx-runner@1 modidx-runner@2
systemctl enable --now modidx-harvest.timer modidx-export.timer modidx-maint.timer
```

**Рівно два воркери.** Третій на 8 GB RAM викличе OOM: кожен контейнер Odoo обмежений 2 GB,
плюс 1.5 GB Postgres і решта системи.

Перевірка:
```bash
systemctl status 'modidx-*'
journalctl -u modidx-runner@1 -f
```
