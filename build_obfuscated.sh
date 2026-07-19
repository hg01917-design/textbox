#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

export PATH="$HOME/Library/Python/3.14/bin:$HOME/Library/Python/3.13/bin:$HOME/Library/Python/3.12/bin:$HOME/.local/bin:$PATH"

if ! command -v pyarmor >/dev/null 2>&1; then
  echo "pyarmor가 설치되어 있지 않습니다. 먼저 실행하세요:"
  echo "python3 -m pip install pyarmor pyinstaller"
  exit 1
fi

rm -rf obf dist_obf
pyarmor gen -O obf \
  app.py main.py config.py sync.py \
  content media publisher storage keywords

if command -v pyinstaller >/dev/null 2>&1; then
  pyinstaller --noconfirm --windowed --name textbox --distpath dist_obf obf/app.py
  echo "난독화 앱 빌드 완료: dist_obf/textbox.app"
else
  echo "난독화 소스 생성 완료: obf/"
  echo "앱 번들까지 만들려면 실행하세요: python3 -m pip install pyinstaller"
fi
