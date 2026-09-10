#!/usr/bin/env bash
# Развернуть трекер на ЧИСТОЙ Raspberry Pi.
#
# Ставит зависимости, настраивает камеру и доступ к полётнику, ставит службу
# автозапуска. Повторный запуск безопасен: скрипт проверяет текущее состояние
# и трогает только то, что действительно надо менять.
#
#   git clone git@github.com:AlexandrDekhtiarenko02/fpv_tracker.git
#   cd fpv_tracker && ./setup_pi.sh
#
# Версии пакетов зафиксированы по рабочему борту, чтобы новая малина повторяла
# проверенную конфигурацию, а не «что установится сегодня».
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(id -un)"
CONFIG_TXT="/boot/firmware/config.txt"
[ -f "$CONFIG_TXT" ] || CONFIG_TXT="/boot/config.txt"
REBOOT_NEEDED=0

say()  { echo "==> $*"; }
warn() { echo "!!  $*" >&2; }

# --- проверки до изменений -------------------------------------------------
if ! grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
    warn "Это не Raspberry Pi. Скрипт настраивает камеру и загрузчик, поэтому"
    warn "на другом железе он бесполезен и может навредить. Прерываю."
    exit 1
fi
say "Плата: $(tr -d '\0' < /proc/device-tree/model)"

if [ "$(id -u)" -eq 0 ]; then
    warn "Не запускай от root: служба должна работать под обычным"
    warn "пользователем, иначе доступ к камере и логам будет от чужого имени."
    exit 1
fi

say "Потребуется sudo — сейчас спросит пароль"
sudo -v

# --- системные пакеты ------------------------------------------------------
# picamera2/libcamera берём ТОЛЬКО из apt: на Raspberry Pi они собраны под
# конкретную сборку системы, а pip-версии с ней не стыкуются.
say "Ставлю системные пакеты (это самая долгая часть)"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git python3-picamera2 python3-libcamera python3-numpy \
    python3-pip rpicam-apps

# --- python-пакеты ---------------------------------------------------------
say "Ставлю python-пакеты из requirements.txt"
PIP_FLAGS=""
# Debian 12+ помечает системный python как «externally managed» и запрещает
# pip. Ставим системно осознанно: служба работает от /usr/bin/python3, и venv
# для неё пришлось бы отдельно прописывать в systemd.
python3 -c "import sys; sys.exit(0)" 2>/dev/null
if pip3 install --help 2>/dev/null | grep -q break-system-packages; then
    PIP_FLAGS="--break-system-packages"
fi
sudo pip3 install $PIP_FLAGS -q -r "$DIR/requirements.txt"

# --- права доступа ---------------------------------------------------------
# dialout — доступ к /dev/ttyACM0 (полётник по USB), video — к камере.
for grp in dialout video; do
    if id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$grp"; then
        say "Группа $grp: уже есть"
    else
        say "Добавляю $USER_NAME в группу $grp"
        sudo usermod -aG "$grp" "$USER_NAME"
        REBOOT_NEEDED=1
    fi
done

# --- камера ----------------------------------------------------------------
# Камера подключается ЯВНЫМ оверлеем, а не автоопределением: у IMX219 на
# Zero 2W автоопределение не всегда поднимает сенсор, и тогда трекер стартует
# без камеры и молча ничего не видит.
say "Настраиваю камеру IMX219 в $CONFIG_TXT"
sudo cp -n "$CONFIG_TXT" "${CONFIG_TXT}.backup-tracker" 2>/dev/null || true
set_cfg() {
    local key="$1" line="$2"
    if grep -qE "^${key}" "$CONFIG_TXT"; then
        if ! grep -qxF "$line" "$CONFIG_TXT"; then
            sudo sed -i "s|^${key}.*|${line}|" "$CONFIG_TXT"
            say "  изменено: $line"
            REBOOT_NEEDED=1
        fi
    else
        echo "$line" | sudo tee -a "$CONFIG_TXT" >/dev/null
        say "  добавлено: $line"
        REBOOT_NEEDED=1
    fi
}
set_cfg "camera_auto_detect=" "camera_auto_detect=0"
set_cfg "dtoverlay=imx219"    "dtoverlay=imx219,rotation=0"

# --- служба автозапуска ----------------------------------------------------
say "Ставлю службу автозапуска tracker.service"
sudo cp -n /etc/systemd/system/tracker.service \
           /etc/systemd/system/tracker.service.backup 2>/dev/null || true
sed -e "s|__USER__|${USER_NAME}|g" -e "s|__DIR__|${DIR}|g" \
    "$DIR/tracker.service" | sudo tee /etc/systemd/system/tracker.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable -q tracker.service
say "  запуск: /usr/bin/python3 ${DIR}/tracker.py"

# --- итог ------------------------------------------------------------------
echo
say "Готово."
if [ "$REBOOT_NEEDED" -eq 1 ]; then
    echo
    warn "НУЖНА ПЕРЕЗАГРУЗКА: менялись настройки камеры и/или группы доступа."
    warn "Они вступают в силу только после неё — до перезагрузки трекер"
    warn "может не увидеть камеру или порт полётника."
    echo "    sudo reboot"
else
    echo "    sudo systemctl start tracker    # запустить сейчас"
fi
echo "    ./status.sh                     # проверить, что летит нужный код"

# --- ЗАЩИТА ОТ ПОРЧИ ПРИ ПРОПАЖЕ ПИТАНИЯ ---
# Карта монтирована ext4 с отложенной записью: имя файла попадает на диск
# сразу, содержимое — когда соберётся ядро. Пропажа питания в этом окне
# оставляет файлы нулевого размера с целыми именами. За 9 сентября 2026 это
# случилось трижды: tracker.py обнулялся, служба уходила в цикл перезапусков
# (пустой файл Python выполняет молча), git падал с «object file is empty».
#
# Здесь git заставляют дописывать свои объекты на карту сразу. Рабочие файлы
# это не покрывает — для них есть sync в obnovit.sh.
echo "==> Настраиваю git на немедленную запись объектов"
git -C "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" config core.fsync \
    loose-object,pack,pack-metadata,commit-graph,index,derived-metadata,reference
git -C "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" config core.fsyncMethod fsync
