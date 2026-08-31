#!/usr/bin/env bash
# Отправка кода на малину без git (запасной путь, если её нет в сети/без GitHub).
# Обычный путь — git pull на самой малине, см. README.
#
#   ./deploy.sh                     # на raspberrypi.local, пользователь pi
#   PI=192.168.1.50 ./deploy.sh     # на конкретный адрес
#   PI_USER=alex PI_DIR=~/code ./deploy.sh
set -euo pipefail

PI="${PI:-raspberrypi.local}"
PI_USER="${PI_USER:-pi}"
PI_DIR="${PI_DIR:-~/fpv_tracker}"

echo "==> Отправляю на ${PI_USER}@${PI}:${PI_DIR}"
# --exclude на логи: они живут на малине и не должны затираться копией с Мака.
rsync -avz --delete \
  --exclude 'flight_logs/' \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  ./ "${PI_USER}@${PI}:${PI_DIR}/"
echo "==> Готово"
