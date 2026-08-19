#!/usr/bin/env bash
# Перевірка перед комітом: чи не тікають секрети в ПУБЛІЧНИЙ репозиторій.
#   bash bin/ops-check.sh
#
# Два принципи, обидва здобуті на помилках:
#  1. Ловимо ЛІТЕРАЛИ, не посилання на змінні. PGPASSWORD="$PGPASSWORD" — це
#     нормальний код. Чек, який лається на здоровий код, починають ігнорувати.
#  2. Скануємо і НЕВІДСЛІДКОВУВАНІ файли. `git grep` бачить лише те, що вже в
#     індексі, — тобто пропускав би саме новий лог в ops/outbox/, для якого й існує.
set -uo pipefail
ROOT="${ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo /srv/modidx)}"
cd "$ROOT" || exit 1
BAD=0
say(){ printf '%s\n' "$*"; }
flag(){ say "  ✗ $*"; BAD=1; }

# Усе, що піде в коміт: в індексі + нове, крім ignored. Себе і протокол виключаємо.
mapfile -t FILES < <(git ls-files --cached --others --exclude-standard \
  | grep -vE '^(bin/ops-check\.sh|ops/README\.md)$' || true)
scan(){ [ ${#FILES[@]} -eq 0 ] && return 0; printf '%s\0' "${FILES[@]}" | xargs -0 -r grep -nIE "$1" -- 2>/dev/null || true; }
scanF(){ [ ${#FILES[@]} -eq 0 ] && return 0; printf '%s\0' "${FILES[@]}" | xargs -0 -r grep -nIF "$1" -- 2>/dev/null || true; }

# 1. Файли, яких у репозиторії бути не може
for f in .env pf.txt; do
  printf '%s\n' "${FILES[@]}" | grep -qx "$f" && flag "у коміт потрапив $f"
done
printf '%s\n' "${FILES[@]}" | grep -q '^var/' && flag "у коміт потрапило var/"
printf '%s\n' "${FILES[@]}" | grep -qE '(^|/)(id_rsa|id_ed25519|.*\.ppk|.*\.pem)$' && flag "у коміт потрапив приватний ключ"

# 2. Справжній пароль Postgres із .env — найточніша перевірка
if [ -f .env ]; then
  PW=$(grep -m1 '^PGPASSWORD=' .env | cut -d= -f2-)
  if [ -n "${PW:-}" ] && [ ${#PW} -ge 8 ]; then
    HITS=$(scanF "$PW")
    [ -n "$HITS" ] && { flag "ПАРОЛЬ POSTGRES у файлах:"; printf '%s\n' "$HITS" | head -10 | sed 's/^/      /'; }
  fi
fi

# 3. Літеральні паролі: значення починається з букви або цифри, тобто це не $VAR, "{VAR}" чи %s
HITS=$(scan '(PGPASSWORD|--db_password|--db_pass|password)=[A-Za-z0-9+/][A-Za-z0-9+/=_-]{7,}')
[ -n "$HITS" ] && { flag "літеральний пароль:"; printf '%s\n' "$HITS" | head -20 | sed 's/^/      /'; }

# 4. Однозначні секрети
HITS=$(scan 'BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20}|github_pat_[A-Za-z0-9_]{20}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10}')
[ -n "$HITS" ] && { flag "ключ або токен:"; printf '%s\n' "$HITS" | head -10 | sed 's/^/      /'; }

# 5. Сторонні публічні IP у ops/ — попередження, не блокування
if [ -d ops ]; then
  mapfile -t OPSF < <(git ls-files --cached --others --exclude-standard -- ops | grep -v '^ops/README\.md$' || true)
  if [ ${#OPSF[@]} -gt 0 ]; then
    HITS=$(printf '%s\0' "${OPSF[@]}" | xargs -0 -r grep -nIE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' -- 2>/dev/null \
      | grep -vE '127\.0\.0\.1|0\.0\.0\.0|10\.|172\.1[6-9]\.|172\.2[0-9]\.|172\.3[01]\.|192\.168\.|65\.21\.189\.197' || true)
    [ -n "$HITS" ] && { say "  ! сторонні IP у ops/ — перевірте вручну:"; printf '%s\n' "$HITS" | head -10 | sed 's/^/      /'; }
  fi
fi

if [ "$BAD" = 0 ]; then say "ops-check: чисто"; else say "ops-check: СТОП, не комітити"; fi
exit "$BAD"
