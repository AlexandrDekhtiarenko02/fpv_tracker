# FPV Tracker

Код донаведения для FPV-дрона: Raspberry Pi Zero 2W + камера IMX219,
управление полётным контроллером Betaflight по MSP.

## Запуск на малине

```bash
python3 tracker.py
```

Логирование вшито в код, отдельно ничего запускать не нужно.

## Полётные логи

Пишутся в `flight_logs/` рядом со скриптом. На каждый вылет:

| Файл | Что внутри |
|---|---|
| `flight_<время>.csv` | 81 колонка, строка на кадр |
| `flight_<время>.events.log` | настройки, переходы состояний, вердикты каждые 2 с |
| `flight_<время>.flightN.summary.txt` | разбор вылета, пишется **по дизарму** |

Подробно о колонках: [docs/flight_log.md](docs/flight_log.md).

Быстро посмотреть вердикт прямо на малине:

```bash
grep VERDICT flight_logs/$(ls -t flight_logs | grep events | head -1)
```

Логи в git **не** попадают (это данные, а не код) — забирай их скриптом:

```bash
./fetch_logs.sh
```

## Как править код (обычный порядок)

1. Правишь на компьютере в нормальном редакторе.
2. Сохраняешь изменения в историю и отправляешь на GitHub:

```bash
git add -A
git commit -m "коротко, что поменял"
git push
```

3. На малине забираешь свежую версию:

```bash
cd ~/fpv_tracker && git pull
```

Если малины нет в сети или не хочется возиться с git — есть запасной путь:

```bash
./deploy.sh
```

## Шпаргалка по git

Git хранит **историю версий**: каждый коммит — это снимок проекта, к которому
можно вернуться. Это страховка: сломал — откатился.

```bash
git status                  # что изменено прямо сейчас
git diff                    # какие именно строки поменялись
git add -A                  # пометить все изменения к сохранению
git commit -m "описание"    # сохранить снимок в историю
git log --oneline           # список снимков, свежие сверху
git push                    # отправить на GitHub
git pull                    # забрать с GitHub
```

Откатиться, если что-то сломал:

```bash
git checkout -- tracker.py   # вернуть файл к последнему коммиту
git log --oneline                      # найти нужный снимок, скопировать его номер
git checkout <номер> -- tracker.py   # вернуть файл к тому снимку
```

Правило, которое экономит нервы: **коммить перед каждым вылетом**. Тогда
любую версию можно точно сопоставить с логом этого вылета.

## Что где

```
tracker.py   весь код трекера
deploy.sh              отправить код на малину без git
fetch_logs.sh          забрать логи с малины
docs/flight_log.md     описание колонок лога
flight_logs/           логи (в git не попадают)
```

## Малина

| | |
|---|---|
| Вход | `ssh alex243@Monolith.local` |
| Код | `/home/alex243/fpv_tracker` |
| Логи | `/home/alex243/fpv_tracker/flight_logs` |
| Служба | `tracker.service` (systemd, автозапуск) |

Обновить код на малине.

**С компьютера** — обязателен флаг `-t`, иначе sudo не сможет спросить пароль
(«a terminal is required to read the password»):

```bash
ssh -t alex243@Monolith.local 'cd ~/fpv_tracker && git pull && sudo systemctl restart tracker && sleep 5 && ./status.sh'
```

**Уже находясь на малине** — без ssh:

```bash
cd ~/fpv_tracker && git pull && sudo systemctl restart tracker && sleep 5 && ./status.sh
```

Перезапуск обязателен: `git pull` меняет файл на диске, но служба продолжает
выполнять код, загруженный в память при старте. `status.sh` в конце это и
проверяет — он скажет «СОВПАДАЕТ» только если летит действительно новая
версия.

Полезное для службы:

```bash
sudo systemctl status tracker     # работает ли
sudo systemctl restart tracker    # перезапустить после git pull
journalctl -u tracker -n 50       # последние строки вывода
```

Малине выдан ключ **только на чтение**: она может забирать код, но не может
ничего изменить в репозитории.

### Откат на старую версию

Прежний файл сохранён как `/home/alex243/tracker.py.backup`. Вернуться:

```bash
sudo sed -i 's|/home/alex243/fpv_tracker/tracker.py|/home/alex243/tracker.py.backup|' \
  /etc/systemd/system/tracker.service
sudo systemctl daemon-reload && sudo systemctl restart tracker
```
