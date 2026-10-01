#!/usr/bin/env bash
set -euo pipefail

cd /workspace

if [ ! -f .env ]; then
  cp .env.example .env
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

export DJANGO_SETTINGS_MODULE=config.settings.dev
export PYTHONPATH=/workspace/src

python manage.py collectstatic --noinput
