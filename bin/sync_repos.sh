#!/usr/bin/env bash
# Чекаути всіх репозиторіїв OCA для потрібних серій + плоский пул адонів симлінками.
# depth 1, single-branch: ~2-3 GB на серію. Повторний запуск робить git fetch, не переклонює.
set -euo pipefail
ROOT="${ROOT:-/srv/modidx}"
SERIES="${SERIES:-18.0 19.0}"
LIST="$ROOT/data/oca_repos.txt"
NOT_MODULE_DIRS="setup docs tests template"

mkdir -p "$ROOT/var/repos" "$ROOT/var/pool"

# Канонічний перелік репозиторіїв OCA — з maintainer-tools, без GitHub API
if [ ! -s "$LIST" ]; then
  echo "== беру перелік репозиторіїв OCA"
  tmp=$(mktemp -d)
  git clone -q --depth 1 https://github.com/OCA/maintainer-tools "$tmp/mt"
  grep -oP 'github\.com/OCA/\K\S+' "$tmp/mt/tools/repos_with_ids.txt" \
    | grep -vxF -f <(printf '%s\n' .github ansible-odoo maintainer-tools maintainer-quality-tools \
        OCB OpenUpgrade openupgradelib pylint-odoo odoo-module-migrator oca-port oca-ci \
        oca-github-bot oca-custom oca-decorators odoo-pre-commit-hooks odoorpc \
        odoo-sphinx-autodoc odoo-sentinel odoo-test-helper oca-addons-repo-template \
        contribute-md-template mirrors-flake8 oca.recipe.odoo oca-weblate-deployment \
        odoo-community.org connector-magento-php-extension) \
    | sort -u > "$LIST"
  rm -rf "$tmp"
fi
echo "== репозиторіїв у списку: $(wc -l < "$LIST")"

for S in $SERIES; do
  echo "== серія $S"
  mkdir -p "$ROOT/var/repos/$S" "$ROOT/var/pool/$S"
  n=0
  while read -r repo; do
    d="$ROOT/var/repos/$S/$repo"
    if [ -d "$d/.git" ]; then
      # --filter=blob:none замість --depth 1: повна історія комітів і дерев без
      # вмісту файлів. Потрібна для дати останнього коміту МОДУЛЯ (git log по
      # теці), а вона — найсильніший сигнал покинутості. Коштує ~8% розміру:
      # 12 MB → 13 MB на репозиторій, перевірено на reporting-engine.
      git -C "$d" fetch -q --filter=blob:none origin "$S" 2>/dev/null && \
      git -C "$d" reset -q --hard FETCH_HEAD || true
    else
      git clone -q --filter=blob:none --single-branch --branch "$S" \
        "https://github.com/OCA/$repo" "$d" 2>/dev/null || continue
    fi
    n=$((n+1))
  done < "$LIST"
  echo "   репозиторіїв з гілкою $S: $n"

  # плоский пул: один --addons-path, залежності між репозиторіями резолвяться самі
  find "$ROOT/var/pool/$S" -maxdepth 1 -type l -delete
  for d in "$ROOT/var/repos/$S"/*/; do
    repo=$(basename "$d")
    for m in "$d"*/; do
      mod=$(basename "$m")
      case " $NOT_MODULE_DIRS " in *" $mod "*) continue;; esac
      [ "${mod:0:1}" = "." ] && continue
      [ -f "$m/__manifest__.py" ] || continue
      ln -sfn "$m" "$ROOT/var/pool/$S/$mod"
    done
  done
  echo "   модулів у пулі $S: $(find "$ROOT/var/pool/$S" -maxdepth 1 -type l | wc -l)"
done

du -sh "$ROOT/var/repos" 2>/dev/null || true
