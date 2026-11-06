#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/capture_facile"
if ! gh auth status >/dev/null 2>&1; then
  echo 'Connectez-vous a GitHub avec : gh auth login'
  exit 1
fi
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git init -b main
fi
git add .
if ! git diff --cached --quiet; then
  git commit -m 'Version 1 - application Capture Facile Windows'
fi
if ! gh repo view janintibo-art/capture-facile >/dev/null 2>&1; then
  gh repo create janintibo-art/capture-facile --public --source=. --remote=origin --push
else
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin https://github.com/janintibo-art/capture-facile.git
  fi
  git push -u origin main
fi
if ! git rev-parse v1 >/dev/null 2>&1; then
  git tag v1
fi
git push origin v1
echo 'Depot cree et build EXE lance : https://github.com/janintibo-art/capture-facile/actions'
