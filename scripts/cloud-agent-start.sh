#!/usr/bin/env bash
set -euo pipefail

cd /workspace

if ! redis-cli ping >/dev/null 2>&1; then
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl start redis-server 2>/dev/null || true
  fi
  if ! redis-cli ping >/dev/null 2>&1; then
    redis-server --daemonize yes
  fi
fi

if ! pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl start postgresql 2>/dev/null || true
  fi
fi

if ! pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then
  if command -v pg_ctlcluster >/dev/null 2>&1; then
    pg_version="$(ls /etc/postgresql/ 2>/dev/null | sort -n | tail -1 || true)"
    if [ -n "${pg_version}" ]; then
      sudo pg_ctlcluster "${pg_version}" main start 2>/dev/null \
        || sudo pg_ctlcluster "${pg_version}" main restart
    fi
  fi
fi

for _ in $(seq 1 45); do
  if pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then
    break
  fi
  sleep 1
done

if ! pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then
  echo "PostgreSQL did not become ready on 127.0.0.1:5432" >&2
  exit 1
fi

sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='procure_user'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE USER procure_user WITH PASSWORD 'procure_password';"

sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='procuresphere_db'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE procuresphere_db OWNER procure_user;"

sudo -u postgres psql -c "ALTER USER procure_user WITH PASSWORD 'procure_password';" >/dev/null

# shellcheck disable=SC1091
source /workspace/.venv/bin/activate

export DJANGO_SETTINGS_MODULE=config.settings.dev
export PYTHONPATH=/workspace/src

python manage.py migrate --noinput

if ! python manage.py shell -c "from apps.accounts.models import User; raise SystemExit(0 if User.objects.filter(email='admin@hpe.com').exists() else 1)"; then
  python manage.py seed_demo_data
fi

if ! curl -sf "http://127.0.0.1:8000/health/" >/dev/null 2>&1; then
  nohup python manage.py runserver 0.0.0.0:8000 > /tmp/procuresphere-web.log 2>&1 &
  for _ in $(seq 1 45); do
    if curl -sf "http://127.0.0.1:8000/health/" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

if ! curl -sf "http://127.0.0.1:8000/health/" >/dev/null 2>&1; then
  echo "Django health check failed; see /tmp/procuresphere-web.log" >&2
  tail -50 /tmp/procuresphere-web.log >&2 || true
  exit 1
fi

echo "ProcureSphere 360 ready (PostgreSQL, Redis, Django on :8000)"
