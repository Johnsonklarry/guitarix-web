#!/bin/sh
set -eu

dry_run=false
if [ "$#" -gt 0 ] && [ "$1" = "--dry-run" ]; then
  dry_run=true
  shift
fi

if [ "$#" -gt 0 ]; then
  echo "Usage: $0 [--dry-run]" >&2
  exit 1
fi

# (1) Check for uncommitted changes
status=$(git status --porcelain)
if [ -n "$status" ]; then
  echo "ERROR: Working directory is not clean. Aborting." >&2
  echo "Dirty files:" >&2
  echo "$status" >&2
  exit 1
fi

before=$(git rev-parse HEAD)
echo "Current commit: $before"

# (3) Pull changes
if [ "$dry_run" = true ]; then
  echo "[DRY-RUN] Would run: git pull --ff-only"
else
  echo "Running: git pull --ff-only"
  git pull --ff-only
fi

# What actually arrived -- before..HEAD, not "the last ten whatever they are"
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ]; then
  echo "Already up to date."
else
  echo "Changes pulled:"
  git log --oneline "$before..$after"
fi

# (4) Compare and install .service files
changed_services=false
for service_file in *.service; do
  if [ -f "$service_file" ]; then
    target="/etc/systemd/system/$service_file"
    if [ ! -f "$target" ] || ! cmp -s "$service_file" "$target"; then
      if [ "$dry_run" = true ]; then
        echo "[DRY-RUN] Would install service: $service_file"
      else
        echo "Installing service: $service_file"
        sudo cp "$service_file" "$target"
        changed_services=true
      fi
    fi
  fi
done

if [ "$changed_services" = true ] && [ "$dry_run" = false ]; then
  echo "Reloading systemd daemon"
  sudo systemctl daemon-reload
fi

# (5) Check for requirements.txt changes and install if needed
# Against the commit we recorded, not HEAD@{1}: with no reflog entry that
# errors, grep finds nothing, and the install is skipped without a word.
if [ "$before" != "$after" ] \
   && git diff --name-only "$before" "$after" | grep -q "^requirements.txt$"; then
  if [ "$dry_run" = true ]; then
    echo "[DRY-RUN] Would run: .venv/bin/pip install -r requirements.txt"
  else
    echo "Installing updated Python requirements"
    .venv/bin/pip install -r requirements.txt
  fi
fi

# (6) Restart service and verify it's active
if [ "$dry_run" = true ]; then
  echo "[DRY-RUN] Would restart guitarix-web.service"
else
  echo "Restarting guitarix-web.service"
  sudo systemctl restart guitarix-web.service

  # Verify it came back
  if ! sudo systemctl is-active --quiet guitarix-web.service; then
    echo "ERROR: guitarix-web.service failed to start" >&2
    sudo journalctl -u guitarix-web.service --no-pager -n 20 >&2
    exit 1
  fi
  echo "guitarix-web.service is active"
fi