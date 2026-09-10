#!/usr/bin/env bash
# Чинит периодические отвалы WiFi и «малина забыла сеть».
#
# Причина отвалов почти всегда одна: энергосбережение радио. Драйвер
# brcmfmac на Zero 2 W усыпляет модуль, и связь пропадает на секунды —
# ровно тогда, когда по ней идёт git pull или ssh. Выглядит как плохой
# сигнал, лечится одним ключом.
#
# Второе: NetworkManager по умолчанию бросает попытки переподключения после
# нескольких неудач. Точка доступа моргнула на минуту — и борт остаётся без
# сети до перезагрузки, хотя сеть давно вернулась.
#
# Файла в bootfs, который делает то же самое, НЕ существует. Старый
# wpa_supplicant.conf на Bookworm не читается вовсе, а custom.toml
# применяется только на самой первой загрузке.
#
# Запускать через sudo:  sudo ./wifi_fix.sh
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Нужны права: sudo ./wifi_fix.sh" >&2
    exit 1
fi

IFACE="${1:-wlan0}"

echo "=== Что сейчас ==="
echo "  система     : $(sed -n 's/^PRETTY_NAME="\(.*\)"/\1/p' /etc/os-release)"
echo "  сеть ведёт  : $(systemctl is-active NetworkManager 2>/dev/null || echo 'не NetworkManager')"
echo "  энергосбер. : $(iw dev "$IFACE" get power_save 2>/dev/null || echo 'не прочитать')"

if ! systemctl is-active --quiet NetworkManager; then
    echo
    echo "NetworkManager не работает — этот скрипт настраивает именно его." >&2
    echo "Проверь вручную, чем управляется сеть." >&2
    exit 1
fi

PROFILI=$(nmcli -t -f NAME,TYPE con show | awk -F: '$2=="802-11-wireless"{print $1}')
if [ -z "$PROFILI" ]; then
    echo
    echo "Профилей WiFi нет — сеть действительно забыта. Подключись один раз:"
    echo
    echo "    sudo nmcli --ask device wifi connect \"ИМЯ_СЕТИ\""
    echo
    echo "Ключ --ask спросит пароль отдельно, и он НЕ попадёт в историю"
    echo "команд. После подключения запусти этот скрипт снова."
    exit 2
fi

echo
echo "=== Настраиваю профили ==="
while IFS= read -r P; do
    [ -z "$P" ] && continue
    echo "  $P"
    # 2 = энергосбережение выключено. Главная причина отвалов.
    nmcli con modify "$P" wifi.powersave 2
    # 0 = пробовать переподключиться бесконечно. По умолчанию NM сдаётся.
    nmcli con modify "$P" connection.autoconnect yes
    nmcli con modify "$P" connection.autoconnect-retries 0
    # Случайный MAC ломает привязку адреса на роутере и мешает найти борт.
    nmcli con modify "$P" wifi.cloned-mac-address permanent
    # Не отключать интерфейс, пока сеть считается «плохой».
    nmcli con modify "$P" connection.autoconnect-priority 100
done <<< "$PROFILI"

# Настройку профиля мало: новый профиль (например, после подключения к другой
# точке) создастся со значением по умолчанию. Здесь — правило для ВСЕХ
# соединений, включая будущие.
#
# Параметры модуля brcmfmac сюда намеренно НЕ трогаем: если ядро не примет
# ключ, WiFi не поднимется вовсе, а у борта нет ни экрана, ни клавиатуры.
cat > /etc/NetworkManager/conf.d/wifi-powersave-off.conf <<'NMC'
[connection]
# 2 = выключено. Действует на все WiFi-соединения, включая будущие.
wifi.powersave = 2
NMC

echo
echo "=== Применяю ==="
iw dev "$IFACE" set power_save off 2>/dev/null && echo "  энергосбережение выключено сейчас"
systemctl restart NetworkManager
sleep 3
echo "  энергосбер. теперь: $(iw dev "$IFACE" get power_save 2>/dev/null || echo '?')"
echo "  адрес            : $(hostname -I | awk '{print $1}')"

echo
echo "Готово. Настройки переживают перезагрузку."
echo "Проверить после ребута:  iw dev $IFACE get power_save"
