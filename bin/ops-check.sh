#!/usr/bin/env bash
# Перевірка перед комітом: чи не тікають секрети в ПУБЛІЧНИЙ репозиторій.
#   bash bin/ops-check.sh
#
# Три принципи, усі здобуті на помилках цього проєкту:
#  1. Ловимо ЛІТЕРАЛИ, не посилання на змінні. PGPASSWORD="$PGPASSWORD" — здоровий
#     код. Чек, який лається на нього, починають ігнорувати.
#  2. Скануємо і НЕВІДСЛІДКОВУВАНІ файли: git grep бачить лише індекс, тобто
#     пропускав би саме новий лог в ops/outbox/, для якого й існує.
#  3. Якщо чек не може виконатися — він КРИЧИТЬ і повертає 2, а не «чисто».
#     Версія з mapfile тихо падала на macOS (bash 3.2) і друкувала «чисто».
#     Тому тут лише POSIX: ні mapfile, ні масивів, ні подвійних дужок.
set -u

ROOT="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo /srv/modidx)}"
cd "$ROOT" || { echo "ops-check: НЕ ВДАЛОСЯ увійти в $ROOT"; exit 2; }

BAD=0
say(){ printf '%s\n' "$*"; }
flag(){ say "  ✗ $*"; BAD=1; }
die(){ say "ops-check: ЗЛАМАНИЙ — $*"; say "ops-check: НЕ ВВАЖАТИ ЧИСТИМ"; exit 2; }

command -v git   >/dev/null 2>&1 || die "немає git"
command -v grep  >/dev/null 2>&1 || die "немає grep"

LIST=$(mktemp) || die "mktemp не працює"
trap 'rm -f "$LIST"' EXIT INT TERM

# Усе, що піде в коміт: індекс + нове, крім ignored. Себе і протокол виключаємо,
# бо вони описують патерни й самі б на них спрацювали.
git ls-files --cached --others --exclude-standard \
  | grep -v -x -e 'bin/ops-check.sh' -e 'ops/README.md' > "$LIST" \
  || true
[ -s "$LIST" ] || die "список файлів порожній — git не відповів як слід"
say "ops-check: файлів під перевіркою: $(wc -l < "$LIST" | tr -d ' ')"

# Пошук по кожному файлу окремо: працює з пробілами в іменах і не залежить
# від xargs-специфіки. Кілька сотень файлів — це швидко.
scan(){ # $1 = ERE
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    grep -n -I -E -- "$1" "$f" 2>/dev/null | sed "s|^|$f:|"
  done < "$LIST"
}
scanF(){ # $1 = фіксований рядок
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    grep -n -I -F -- "$1" "$f" 2>/dev/null | sed "s|^|$f:|"
  done < "$LIST"
}

# 1. Файли, яких у репозиторії бути не може
for f in .env pf.txt; do
  grep -q -x "$f" "$LIST" && flag "у коміт потрапив $f"
done
grep -q '^var/' "$LIST" && flag "у коміт потрапило var/"
grep -q -E '(^|/)(id_rsa|id_ed25519|.*\.ppk|.*\.pem)$' "$LIST" && flag "у коміт потрапив приватний ключ"

# 2. Справжній пароль Postgres із .env — найточніша перевірка
if [ -f .env ]; then
  PW=$(grep -m1 '^PGPASSWORD=' .env 2>/dev/null | cut -d= -f2-)
  if [ -n "${PW:-}" ] && [ "${#PW}" -ge 8 ]; then
    HITS=$(scanF "$PW")
    [ -n "$HITS" ] && { flag "ПАРОЛЬ POSTGRES у файлах:"; printf '%s\n' "$HITS" | head -10 | sed 's/^/      /'; }
  fi
fi

# 3. Літеральні паролі: значення починається з букви або цифри — тобто це не
#    $VAR, не "$VAR", не {VAR} і не %s
HITS=$(scan '(PGPASSWORD|--db_password|--db_pass|password)=[A-Za-z0-9+/][A-Za-z0-9+/=_-]{7,}')
[ -n "$HITS" ] && { flag "літеральний пароль:"; printf '%s\n' "$HITS" | head -20 | sed 's/^/      /'; }

# 4. Однозначні секрети
HITS=$(scan 'BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20}|github_pat_[A-Za-z0-9_]{20}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10}')
[ -n "$HITS" ] && { flag "ключ або токен:"; printf '%s\n' "$HITS" | head -10 | sed 's/^/      /'; }

# 5. Сторонні публічні IP у ops/ — попередження, не блокування.
#    Межі (^|[^0-9.]) … ([^0-9.]|$) обовʼязкові: без них версія 19.0.1.0.0
#    читається як IP, і попередження стає шумом, який перестають читати.
IP4='(^|[^0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}([^0-9.]|$)'
PRIV='(^|[^0-9.])(127\.0\.0\.1|0\.0\.0\.0|10\.|172\.1[6-9]\.|172\.2[0-9]\.|172\.3[01]\.|192\.168\.|169\.254\.|65\.21\.189\.197)'
# Версії Odoo (19.0.1.3, 19.0.1.0.0) синтаксично не відрізняються від IP. Свідомий
# компроміс: відсіюємо рядки з NN.0.N — це коштує сліпої зони в 19.0.0.0/8, зате
# попередження лишається сигналом, а не шумом. Це warning, не блокування.
VERS='[0-9]{2}\.0\.[0-9]'
if [ -d ops ]; then
  HITS=$(grep '^ops/' "$LIST" | grep -v -x 'ops/README.md' | while IFS= read -r f; do
    [ -f "$f" ] || continue
    grep -n -I -E -- "$IP4" "$f" 2>/dev/null | sed "s|^|$f:|"
  done | grep -v -E "$PRIV" | grep -v -E "$VERS" || true)
  [ -n "$HITS" ] && { say "  ! сторонні IP у ops/ — перевірте вручну:"; printf '%s\n' "$HITS" | head -10 | sed 's/^/      /'; }
fi

if [ "$BAD" = 0 ]; then say "ops-check: чисто"; else say "ops-check: СТОП, не комітити"; fi
exit "$BAD"
