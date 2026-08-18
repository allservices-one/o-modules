#!/usr/bin/env bash
# Preflight: збирає все, що потрібно знати про сервер ПЕРЕД розгортанням.
# Тільки читає. Нічого не змінює, нічого не встановлює.
# Паролі й ключі не виводить.
#
#   bash preflight.sh              # на екран
#   bash preflight.sh > pf.txt     # у файл, щоб надіслати
set -uo pipefail
line(){ printf '\n── %s ──\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }

echo "PREFLIGHT $(date -Is)"

line "Система"
. /etc/os-release 2>/dev/null && echo "OS: $PRETTY_NAME"
echo "Ядро: $(uname -r)  Арх: $(uname -m)"
echo "Uptime: $(uptime -p 2>/dev/null || uptime)"
echo "cgroup: $(stat -fc %T /sys/fs/cgroup 2>/dev/null)   (потрібен cgroup2fs для лімітів памʼяті)"
echo "Віртуалізація: $(systemd-detect-virt 2>/dev/null || echo '?')"

line "CPU / RAM / Swap"
echo "vCPU: $(nproc)"
free -h 2>/dev/null | sed 's/^/  /'
echo "swap-файли: $(swapon --show=NAME --noheadings 2>/dev/null | tr '\n' ' ' || echo 'немає')"
echo "vm.swappiness: $(cat /proc/sys/vm/swappiness 2>/dev/null)"

line "Диск"
df -hT / /var /srv 2>/dev/null | sed 's/^/  /'
echo "inode /: $(df -i / | awk 'NR==2{print $5" використано"}')"

line "Docker"
if have docker; then
  docker --version
  docker compose version 2>/dev/null || echo "docker compose plugin: НЕМАЄ"
  echo "контейнерів запущено: $(docker ps -q 2>/dev/null | wc -l)"
  docker ps --format '  {{.Names}}  {{.Image}}  {{.Status}}  {{.Ports}}' 2>/dev/null
  echo "образів: $(docker images -q 2>/dev/null | wc -l), місце:"
  docker system df 2>/dev/null | sed 's/^/  /'
else
  echo "docker НЕ встановлено"
fi

line "Порти 80 / 443 / 5432 — головна перевірка на конфлікт"
if have ss; then
  ss -tlnp 2>/dev/null | awk 'NR==1 || /:80 |:443 |:5432 /' | sed 's/^/  /'
else
  netstat -tlnp 2>/dev/null | grep -E ':80 |:443 |:5432 ' | sed 's/^/  /'
fi
for svc in nginx apache2 httpd caddy traefik haproxy postgresql mysql docker; do
  systemctl is-active "$svc" >/dev/null 2>&1 && echo "  АКТИВНИЙ сервіс: $svc"
done
echo "  (якщо 80/443 зайняті — Caddy не підніметься, треба вирішити хто головний)"

line "Що вже є з ПО"
for c in git python3 pip3 psql curl wget jq goaccess certbot ufw firewall-cmd nft iptables node npm; do
  printf '  %-12s %s\n' "$c" "$(have "$c" && ("$c" --version 2>/dev/null | head -1) || echo '—')"
done
python3 -c "import psycopg2; print('  psycopg2:', psycopg2.__version__)" 2>/dev/null || echo "  psycopg2: немає (треба apt install python3-psycopg2)"

line "Мережа"
echo "Зовнішній IPv4: $(curl -4 -s --max-time 8 https://api.ipify.org 2>/dev/null || echo '?')"
echo "Зовнішній IPv6: $(curl -6 -s --max-time 8 https://api64.ipify.org 2>/dev/null || echo 'немає')"
ip -brief addr 2>/dev/null | sed 's/^/  /'

line "DNS цільового субдомену"
for h in allservices.one www.allservices.one app.allservices.one; do
  echo "  $h -> $(getent ahosts "$h" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || echo 'не резолвиться')"
done
echo "  (апекс мусить вказувати на IP ЦЬОГО сервера — і A, і AAAA. Якщо AAAA веде на інший хост, ACME впаде)"

line "Фаєрвол"
have ufw && ufw status 2>/dev/null | head -12 | sed 's/^/  /'
have nft && nft list ruleset 2>/dev/null | head -20 | sed 's/^/  /'
have iptables && iptables -S 2>/dev/null | head -15 | sed 's/^/  /'

line "Обмеження і ліміти"
echo "max_map_count: $(cat /proc/sys/vm/max_map_count 2>/dev/null)"
echo "file-max: $(cat /proc/sys/fs/file-max 2>/dev/null)"
echo "ulimit -n: $(ulimit -n)"
echo "SELinux/AppArmor: $(getenforce 2>/dev/null || aa-status --enabled 2>/dev/null && echo apparmor-on || echo 'немає/вимкнено')"

line "Тест: чи зможе сервер тягнути образи Odoo"
if have docker; then
  timeout 25 docker pull -q hello-world >/dev/null 2>&1 && echo "  docker pull працює" || echo "  docker pull НЕ працює (мережа/реєстр)"
fi
timeout 15 git ls-remote --heads https://github.com/OCA/web >/dev/null 2>&1 \
  && echo "  git до github працює" || echo "  git до github НЕ працює"

line "Оцінка придатності"
CPU=$(nproc); MEM=$(free -m | awk '/^Mem:/{print $2}'); FREE=$(df -m / | awk 'NR==2{print $4}')
echo "  vCPU=$CPU  RAM=${MEM}MB  вільно на /=${FREE}MB"
[ "$CPU" -ge 4 ]     && echo "  ✓ CPU: 2 воркери" || echo "  ! CPU<4: тільки 1 воркер"
[ "$MEM" -ge 7500 ]  && echo "  ✓ RAM: 2 контейнери по 2G + Postgres" || echo "  ! RAM<8G: 1 воркер і swap обовʼязково"
[ "$FREE" -ge 45000 ] && echo "  ✓ Диск: вистачить на 2 серії" || echo "  ! мало місця: почати з однієї серії (19.0)"
echo
echo "PREFLIGHT ГОТОВО. Надішліть цей вивід повністю."
