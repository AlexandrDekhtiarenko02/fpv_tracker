#!/usr/bin/env bash
# Найти Raspberry Pi в локальной сети.
#
# Два способа, от простого к надёжному:
#   1. mDNS — малина сама объявляет себя как raspberrypi.local
#   2. По MAC-адресу — первые три байта («OUI») закреплены за производителем.
#      Диапазоны ниже принадлежат Raspberry Pi. Это и есть тот самый признак,
#      по которому малина отличается от чужого телефона в той же сети.
set -uo pipefail

# OUI Raspberry Pi (Foundation / Trading Ltd).
PI_OUIS="b8:27:eb dc:a6:32 e4:5f:01 28:cd:c1 d8:3a:dd 2c:cf:67 88:a2:9e"

echo "==> 1. Пробую mDNS (raspberrypi.local)"
if ping -c 1 -W 2000 raspberrypi.local >/dev/null 2>&1; then
    ip=$(ping -c 1 raspberrypi.local 2>/dev/null | sed -n 's/.*(\([0-9.]*\)).*/\1/p' | head -1)
    echo "    НАЙДЕНА: raspberrypi.local -> ${ip}"
    echo "    Заходить можно прямо по имени, IP знать не обязательно."
    exit 0
fi
echo "    не отвечает (переименована, mDNS выключен или не в сети)"

MYIP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
if [ -z "${MYIP}" ]; then
    echo "==> Не удалось определить свою сеть. Подключён ли Wi-Fi?"
    exit 1
fi
SUBNET="${MYIP%.*}"
echo "==> 2. Опрашиваю сеть ${SUBNET}.0/24 (это займёт ~5 секунд)"

# Пингуем всю подсеть параллельно: ARP-таблица заполняется только теми,
# с кем недавно был обмен, поэтому сначала «будим» всех.
for i in $(seq 1 254); do
    ping -c 1 -W 200 "${SUBNET}.${i}" >/dev/null 2>&1 &
done
wait 2>/dev/null

echo "==> 3. Ищу адреса с MAC-ом Raspberry Pi"
# arp печатает MAC без ведущих нулей (0:28:f8 вместо 00:28:f8), поэтому
# нормализуем каждый байт до двух знаков, иначе сравнение промахнётся.
found=0
while read -r ip mac; do
    norm=$(echo "$mac" | awk -F: '{for(i=1;i<=NF;i++)printf "%02s%s",$i,(i<NF?":":"")}')
    for oui in $PI_OUIS; do
        case "$norm" in
            "$oui"*)
                echo "    НАЙДЕНА: ${ip}   (MAC ${norm})"
                found=$((found+1))
                ;;
        esac
    done
done < <(arp -an | sed -n 's/^? (\([0-9.]*\)) at \([0-9a-f:]*\).*/\1 \2/p')

if [ "$found" -eq 0 ]; then
    echo "    Не нашёл. Проверь: малина включена? подключена к этой же сети?"
    echo "    Если да — покажи вывод: arp -an"
else
    echo "==> Проверить, что это она:  ssh pi@<адрес>"
fi
