#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Raspberry Pi Zero 2W FPV tracker — оптимизированная отладочная сборка.

Архитектура управления:

 - Поток MSP override в fc_io_loop никогда не прерывается, пока есть свежие
   RC-данные. Это лечит зависание тротла при выключении AUX, на которое нарвались
   ранее: Betaflight ожидает непрерывный поток MSP override-кадров.

 - Per-axis OVERRIDE_* флаги. Все False по умолчанию = чистый passthrough,
   AUX вообще ничего не меняет на FC. Включаешь по одной оси при отладке.
   В норме (после проверки осей) выставляешь OVERRIDE_ROLL/PITCH/YAW = True,
   OVERRIDE_THROTTLE опционально.

 - AIM_OFFSET_Y действует только на pitch (вертикальная прицельная точка),
   на расчёт газа НЕ применяется. Иначе постоянный фантомный dy = +10
   уносил бы I-компонент газа к нулю.

 - Жёсткие ограничители дефлекции (MAX_*_DEFLECT) предохраняют от
   срабатывания runaway_takeoff_prevention в BF при тестах без аэродинамики.

Трекинг:

 - Захват ровно в позиции прицела (ACQ_LOCK_AT_CROSSHAIR_EXACTLY) — размер берётся
   из связной компоненты под прицелом, а центр прибит к (CENTER_X, CENTER_Y).

 - Lucas-Kanade flow + template-matching с distance penalty.
   Веса 0.78 flow + 0.22 match — flow устойчивее к освещению и переходным
   фоновым артефактам, match даёт абсолютный якорь к шаблону.

 - Субпиксельная параболическая интерполяция пика matchTemplate.
   Точность позиции ~0.1 px вместо ±0.5 px пиксельной квантовки матчера.

 - Медленная адаптация шаблона (TEMPLATE_UPDATE_ALPHA = 0.010) — постепенно
   подбирает текущий вид цели на сближении, не давая фону «утянуть» якорь.

PID:

 - Roll/Pitch — P+D+I через _pid_axis_step, conditional anti-windup:
   I-компонент не накапливается, если выход уже саттурирован в том же знаке.

 - Yaw — отдельная ветка из-за фильтра filtered_dx_yaw и yaw_weight
   (ослабление при больших adx), но та же логика anti-windup.

 - Компенсация тангажа от MSP_ATTITUDE. fc_pitch_deg сглаживается фильтром
   и сдвигает прицельную точку так, чтобы стационарный наклонный полёт
   к цели не вызывал лишних pitch-корректировок.

 - Все накопители сбрасываются при переходе non-controllable → controllable
   (TRACKED → HOLD/LOST/AUX_OFF → TRACKED), windup между сессиями не переносится.
"""

from picamera2 import Picamera2, Preview, MappedArray
from libcamera import Transform
import cv2
import numpy as np
import serial
import struct
import time
import threading
import os
import math

cv2.setNumThreads(1)

# =========================================================
# 1. UI / КАРТИНКА
# =========================================================
CAMERA_ROTATE_180 = False

MAIN_W, MAIN_H = 640, 480
LORES_W, LORES_H = 320, 240

# DRM preview — окно картинки на физическом дисплее/VTX.
# PREVIEW_W / PREVIEW_H — размер окна. На FPV-выходах часто полезно слегка
# уменьшить, чтобы интерфейс не уходил за safe area дисплея.
# PREVIEW_X / PREVIEW_Y — смещение окна на дисплее. Если сверху видна тонкая
# чёрная полоса в пару пикселей — это offset композитора DRM, лечится
# отрицательным PREVIEW_Y (сдвигаем картинку вверх).
# Подбирай эмпирически: чёрная полоса сверху → уменьшай PREVIEW_Y,
# обрезается снизу → уменьшай PREVIEW_H, обрезается справа → PREVIEW_W.
PREVIEW_W, PREVIEW_H = 720, 576
PREVIEW_X, PREVIEW_Y = 0, 0
CENTER_X, CENTER_Y = MAIN_W // 2, MAIN_H // 2
CENTER_X_LORES, CENTER_Y_LORES = LORES_W // 2, LORES_H // 2

COLOR_GREEN = (0, 255, 0, 0)
COLOR_RED = (0, 0, 255, 0)
COLOR_WHITE = (255, 255, 255, 0)
COLOR_YELLOW = (0, 255, 255, 0)
COLOR_CYAN = (255, 255, 0, 0)
CROSS_COLOR = COLOR_WHITE

MAG_ENABLED = True
MAG_SIZE = 170
MAG_ZOOM = 3.0
MAG_MARGIN = 6
MAG_SRC_MIN_SIZE = 8
MAG_ONLY_WHEN_AUX = False

# =========================================================
# 2. LOCK-ON ПАРАМЕТРЫ (трекинг не трогаем)
# =========================================================
ACQ_RADIUS_MAIN = 18
ACQ_RADIUS_LORES = max(8, int(round(ACQ_RADIUS_MAIN * LORES_W / MAIN_W)))

ACQ_LOCK_AT_CROSSHAIR_EXACTLY = True
ACQ_SIZE_SEARCH_RADIUS = 12

ACQ_FORCE_CENTER_LOCK = True
ACQ_DEFAULT_LOCK_W = 8
ACQ_DEFAULT_LOCK_H = 8
ACQ_MIN_PEAK = 1.6
ACQ_MIN_STD = 1.2

LOCK_MIN_W = 5
LOCK_MIN_H = 5
LOCK_MAX_W = 38
LOCK_MAX_H = 38
LOCK_PAD = 2.05
TEMPLATE_SCALE = 2.0       # было 2.9 — меньше фона в шаблоне, лучше держит на пёстром фоне
TEMPLATE_MIN = 14
TEMPLATE_MAX = 58

SEARCH_MARGIN = 24         # было 28 — чуть тише search-окно, меньше шансов уцепиться за фоновый паттерн
MATCH_MIN_SCORE = 0.22
MATCH_GOOD_SCORE = 0.40    # было 0.34 — выше планка «уверенного» матча
DIST_PENALTY = 0.55        # было 0.42 — сильнее штраф за матч далеко от предсказания flow
MAX_LOCK_STEP = 32

FLOW_MIN_POINTS = 3
FLOW_ERR_MAX = 20.0
FLOW_MAX_STEP = 30.0
FLOW_REFRESH_EVERY = 1

HOLD_FRAMES = 10
LOST_LIMIT = 28

ACQ_SETTLE_FRAMES = 1
REQUIRE_AUX_TOGGLE_AFTER_LOST = True

FREEZE_TEMPLATE = False    # было True — шаблон плавно адаптируется под рост цели на сближении
TEMPLATE_UPDATE_ALPHA = 0.010   # доля нового кадра в шаблоне на каждом «уверенном» матче

# =========================================================
# 3. УПРАВЛЕНИЕ
# =========================================================

# --- ПО-ОСНЫЕ ПЕРЕОПРЕДЕЛЕНИЯ ---
# По умолчанию ВСЁ passthrough (живой стик). На локе ничего не меняется,
# моторы должны реагировать ровно как при выключенном AUX4.
# Используй это как нулевую точку:
#   1) убеждаешься, что MSP override-канал чистый (моторы спокойны на локе,
#      газ корректно возвращается после AUX OFF);
#   2) потом включаешь по ОДНОЙ оси и смотришь, какая именно вызывает
#      «спул моторов на локе» — обычно это PID-windup FC по неправильной
#      команде угла, лечится не у нас, а в Betaflight (см. README ниже).
OVERRIDE_ROLL = False
OVERRIDE_PITCH = False
OVERRIDE_YAW = False
OVERRIDE_THROTTLE = False        # True = динамика газа по dy, иначе живой стик

# Знак pitch. Признак неправильного знака: задние моторы греются,
# на низком стике моторы раскручиваются.
# -1 = стандартный Betaflight (PWM<1500 = нос вниз).
# +1 = инвертированный (как было в старом iNav-сетапе).
PITCH_SIGN = -1
ROLL_SIGN = +1
YAW_SIGN = +1

# Максимальное отклонение каждой оси от 1500 (в PWM).
# На стенде раньше стояло 120 ради подстраховки от runaway_takeoff_prevention
# при отсутствии аэродинамики. В реальном полёте этого мало:
# при P_GAIN_ROLL=11 потолок 120 достигается уже на adx=11 — авторитета не хватает
# для агрессивных манёвров на терминальной фазе.
# Сейчас выставлено 400 / 400 / 300 = 80% / 80% / 60% полного стика.
# Это даёт квадрату полную скорость вращения по rate-профилю Betaflight,
# но всё ещё оставляет запас «до края». Если хочешь полный авторитет — ставь 500.
MAX_ROLL_DEFLECT = 400
MAX_PITCH_DEFLECT = 400
MAX_YAW_DEFLECT = 300

# --- ГЭЙНЫ ---
P_GAIN_YAW = 2.5
P_GAIN_ROLL = 11.0
D_GAIN_ROLL = 6.0
P_GAIN_PITCH = 3.5
D_GAIN_PITCH = 1.2

# I-компоненты для прицеливания: добивают остаточную ошибку P+D,
# чтобы крестик стоял в центре рамки, а не у её границы.
# Затухание 0.985 за кадр = половина за ~1.5 сек при 30 FPS — медленно тянет к нулю,
# но быстро отпускает, если цель ушла.
# Anti-windup встроен в хелпер: I не накапливается, если выход уже саттурирован
# в том же направлении.
I_GAIN_ROLL = 0.10
I_GAIN_PITCH = 0.08
I_GAIN_YAW = 0.05
ROLL_INTEGRAL_MAX = 40.0
PITCH_INTEGRAL_MAX = 40.0
YAW_INTEGRAL_MAX = 30.0
ROLL_INTEGRAL_DECAY = 0.985
PITCH_INTEGRAL_DECAY = 0.985
YAW_INTEGRAL_DECAY = 0.985

DEADBAND_X = 1
DEADBAND_Y = 1
AIM_OFFSET_X = 0
AIM_OFFSET_Y = 10                # ТОЛЬКО для pitch (статический), для газа не применяется

# --- КОМПЕНСАЦИЯ ТАНГАЖА ОТ MSP_ATTITUDE ---
# В моменте разгона квад завален носом вниз, цель уезжает вверх в кадре.
# Если этого не учитывать, control пытается «выровнять» нос на цель,
# квад теряет горизонтальное ускорение и тормозит. С компенсацией прицельная
# точка автоматически сдвигается так, чтобы стационарный наклонный полёт
# к цели не вызывал отработки pitch.
PITCH_ATTITUDE_COMP_ENABLED = True
# IMX219 в 640×480 (биннинг до 1640×1232, потом scale): VFOV ≈ 41° → 11.7 px/°.
# Подбери под свой реальный масштаб, если разрешение / crop другой.
PIXELS_PER_PITCH_DEG = 11.7
# Не даём компенсации улететь на полкадра при дёргании квада.
MAX_PITCH_COMP_PX = 100.0
# Низкочастотный фильтр на fc_pitch_deg — без него aim-точка прыгает на каждом твике.
PITCH_COMP_ALPHA = 0.30
# Если данные с FC старше этого порога — компенсация не применяется.
FC_PITCH_TIMEOUT = 0.20

# --- УПРЕЖДЕНИЕ ПО СКОРОСТИ ЦЕЛИ (LEAD / PREDICTIVE AIM) ---
# Считаем скорость центра коробки в пикселях/кадр, экстраполируем позицию
# цели на LEAD_FRAMES кадров вперёд и целимся в неё, а не в текущее положение.
# Это компенсирует суммарную задержку петли управления (~150-200 мс):
# трекинг → MSP → FC → моторы → инерция квада → следующий кадр камеры.
LEAD_AIM_ENABLED = True
# Кадров упреждения. 0 = выключено. 3-4 консервативно, 5-7 агрессивно.
# При FPS≈30 и реальной задержке петли ~150-200 мс «честное» упреждение
# должно быть 4-6 кадров; чуть выше = упреждение по реальной скорости + запас на разворот.
LEAD_FRAMES = 4
# Низкочастотный фильтр на скорость цели. Без него прицел прыгает от пиксельных
# шумов трекера (особенно от субпиксельной интерполяции на покоящейся цели).
LEAD_VEL_ALPHA = 0.25
# Если межкадровая скорость больше этого порога (px/кадр) — кадр считается
# глитчем (re-lock / occlusion) и не идёт в фильтр.
LEAD_MAX_VEL_JUMP = 25.0
# Сколько кадров после первого LOCK игнорируем упреждение — даём фильтру скорости
# набрать корректное значение, иначе на первом кадре прицел улетит в сторону.
LEAD_MIN_STABLE_FRAMES = 5
# Жёсткий потолок на величину упреждения по каждой оси (PX). Без него
# при шумном видении (например, дождь) можно получить смещение на полкадра.
LEAD_MAX_PX = 80.0

# --- VELOCITY FEEDFORWARD ---
# Прямой проактивный вклад скорости цели в PWM-команду, поверх PID по позиции.
# Лечит ту же системную задержку, что и LEAD, но действует по-другому:
# LEAD сдвигает ЦЕЛЬ (предсказанную позицию), FF сдвигает выход PWM
# (мотор начинает работать ДО появления позиционной ошибки).
# Работает в паре с LEAD — они не конкурируют, а дополняют друг друга.
# Подбор: 1.5-3.0 для roll, в полёте калибровать на пересекающей цели.
FF_GAIN_ROLL = 2.0      # PWM на (px/кадр) скорости цели по X
FF_GAIN_PITCH = 1.5     # PWM на (px/кадр) скорости цели по Y
FF_GAIN_YAW = 1.0       # PWM на (px/кадр) скорости цели по X (для yaw тоже X)

# --- ТЕРМИНАЛЬНЫЙ РЕЖИМ ---
# Когда коробка занимает заметную часть кадра — мы в финальной фазе.
# Времени на интеграл нет, нужны рефлексы: повышаем P, поджимаем I.
# Триггер: площадь коробки относительно всего кадра.
TERMINAL_MODE_ENABLED = True
TERMINAL_BOX_FRAC_THRESHOLD = 0.18   # 18% площади кадра = «уже близко»
TERMINAL_P_MULTIPLIER = 1.6          # P-гэйны умножаем на это в терминале
TERMINAL_I_MULTIPLIER = 0.3          # I-гэйны срезаем (нет времени интегрировать)
TERMINAL_FF_MULTIPLIER = 1.3         # FF тоже приподнимаем — реакция должна быть резче

# --- АДАПТИВНЫЙ SEARCH_MARGIN ---
# При неподвижной цели бессмысленно искать в большом окне — больше шансов
# уцепиться за фоновый паттерн. При быстрой — наоборот нужно расширить,
# иначе цель может выскочить за окно за один кадр.
ADAPTIVE_SEARCH_MARGIN = True
SEARCH_MARGIN_MIN = 14               # при неподвижной цели
SEARCH_MARGIN_MAX = 36               # при цели на полной скорости трекера
SEARCH_MARGIN_VEL_REF = 12.0         # flow-motion (px/кадр) для перехода к MAX

# --- АДАПТАЦИЯ РАЗМЕРА КОРОБКИ ---
# Раз в N кадров при высоком матч-скоре заново оцениваем связную компоненту
# под текущим центром лока и сдвигаем lock_w/lock_h в её сторону.
# Это решает «дрейф зацепом за край» при сближении — коробка растёт вместе
# с целью, и шаблон-матч продолжает якориться по центру, а не по краю.
SIZE_ADAPT_ENABLED = True
SIZE_ADAPT_EVERY_FRAMES = 10         # период проверки
SIZE_ADAPT_MIN_SCORE = 0.55          # минимальный score для доверия размеру
SIZE_ADAPT_ALPHA = 0.20              # доля нового размера в старом (per update)
SIZE_ADAPT_MIN_W = 5                 # нижний предел осмысленного размера
SIZE_ADAPT_MAX_W = 55                # верхний — больше LOCK_MAX, потому что на сближении ОК

# --- УМНОЕ ВОССТАНОВЛЕНИЕ ПОСЛЕ LOST ---
# Без него после потери цели нужно отщёлкнуть AUX4 и заново лочить. Это
# безопасно, но в реальном бою лишний жест. Если в течение нескольких десятых
# секунды после LOST шаблон находится в расширенной зоне с уверенным скором,
# автоматически возвращаемся в TRACKED.
AUTO_REACQ_ENABLED = True
AUTO_REACQ_FRAMES = 30               # окно поиска после LOST (1 сек при 30 FPS)
AUTO_REACQ_SEARCH_MARGIN = 60        # расширенное окно поиска
AUTO_REACQ_MIN_SCORE = 0.50          # выше обычного MATCH_GOOD_SCORE — нужна высокая уверенность

# --- LAUNCH BOOST (ускорение после захвата) ---
# Когда цель захвачена почти по курсу и pitch-ошибка мала, чистый PID не даст
# квадру значимого разгона — он просто будет висеть, целясь правильно.
# Launch добавляет фиксированное pitch-вниз смещение поверх PID на несколько
# секунд после первого LOCK. PID при этом продолжает работать — курсовая
# коррекция активна, но физика разгоняет квад вперёд за счёт постоянного
# наклона носа. После RAMP_DOWN всё возвращается в обычное наведение.
LAUNCH_ENABLED = True
# Структура манёвра: 0.5 сек плавного нарастания → 2 сек полной тяги → 1 сек спада.
# В кадрах при 30 FPS:
LAUNCH_RAMP_UP_FRAMES = 15
LAUNCH_HOLD_FRAMES = 60
LAUNCH_RAMP_DOWN_FRAMES = 30
# Величина дополнительного pitch-вниз смещения. 80 PWM соответствует примерно
# 15-20° наклона (от стика). Подбери под свой rcrate в Betaflight.
LAUNCH_NOSE_DOWN_PWM = 80
# Дополнительный газ во время launch (в % от текущего стика). Применяется
# ТОЛЬКО если OVERRIDE_THROTTLE=True; иначе газ остаётся под управлением пилота.
# При LAUNCH_NOSE_DOWN_PWM ≠ 0 квад при низком тротле трейдит высоту на скорость
# (дайвит и разгоняется), что для камикадзе нормально. Если хочешь сохранять
# высоту во время launch — включай boost.
LAUNCH_THR_BOOST_PCT = 10.0

# --- CRUISE PITCH (постоянная тяга вперёд) ---
# После завершения launch квад теряет тягу вперёд, если aim-ошибка мала
# (компенсация тангажа от FC именно это и обеспечивает: «всё правильно, не
# меняй атaku»). Cruise оставляет небольшой постоянный нос-вниз — квад
# поддерживает крейсерскую скорость.
# По умолчанию ВЫКЛЮЧЕН — включай если после launch квад заметно замедляется.
# Работает в паре с pitch_attitude_compensation: aim-точка сама учитывает
# наклон, лишних корректировок не возникает.
CRUISE_ENABLED = False
CRUISE_NOSE_DOWN_PWM = 25         # ~5° постоянный наклон

YAW_FILTER_ALPHA = 0.05
YAW_ROLL_CROSSOVER = 25
YAW_ROLL_BLEND_RANGE = 35

# --- ДИНАМИЧЕСКИЙ ГАЗ (работает только если OVERRIDE_THROTTLE=True) ---
THROTTLE_MIN_PWM = 1000
THROTTLE_MAX_PWM = 2000
THROTTLE_PERCENT = 15.0          # макс отклонение от живого стика, %
THROTTLE_DEADBAND = 4
THROTTLE_OUT_ALPHA = 0.25

DY_THROTTLE_GAIN = 1.0
DY_THROTTLE_D_GAIN = 2.0
DY_INTEGRAL_RATE = 0.04
DY_INTEGRAL_MAX = 60.0
DY_INTEGRAL_DECAY = 0.985

# --- ОТЛАДКА ---
MOTOR_DEBUG_ENABLED = True
MOTOR_DEBUG_PERIOD = 0.10
DEBUG_PERIOD = 0.50

# =========================================================
# 4. SERIAL / MSP
# =========================================================
PORT_BY_ID = "/dev/serial/by-id/usb-Betaflight_Betaflight_STM32F405_0x8000000-if00"
PORT = PORT_BY_ID if os.path.exists(PORT_BY_ID) else "/dev/ttyACM0"
BAUD = 115200

MSP_RC_PERIOD = 0.04

fc = None
try:
    fc = serial.Serial(PORT, baudrate=BAUD, timeout=0.05)
except Exception:
    fc = None

state_lock = threading.Lock()
io_thread_stop = threading.Event()

app_state = {
    # Безопасные старты: throttle минимум, остальные центр.
    # До первого успешного MSP_RC лучше не слать FC «среднее» 1500 на throttle.
    "rc_channels": [1500, 1500, 1500, 1000, 1500, 1500, 1500, 1500],
    "rc_throttle": 1000,
    "rc_throttle_ts": 0.0,
    "dyn_throttle": 1500,
    "fc_pitch_deg": None,
    "fc_pitch_ts": 0.0,
    "motors": [],
    "motors_ts": 0.0,
    "last_sent_channels": [1500] * 8,
}

aux4_state = False
prev_aux_on = False
acq_wait_left = 0
lock_sequence = 0

global_yaw_cmd = 1500.0
global_pitch_cmd = 1500.0
global_roll_cmd = 1500.0
global_throttle_cmd = 1500.0
override_active = False

smooth_throttle_out = None
throttle_integral = 0.0
prev_ady = 0.0

# Интеграторы прицельных осей. Копят остаточную ошибку P+D, чтобы крестик
# держался в центре рамки, а не у её края.
roll_integral = 0.0
pitch_integral = 0.0
yaw_integral = 0.0

# Сглаженное значение fc_pitch_deg для расчёта компенсации тангажа.
smoothed_pitch_deg = 0.0

# Состояние упреждения. prev_box_* — позиция в прошлом кадре, target_v*_smoothed —
# сглаженная межкадровая скорость цели в пикселях/кадр. stable_track_frames
# гарантирует, что упреждение включается только после стабилизации фильтра скорости.
prev_box_cx = None
prev_box_cy = None
target_vx_smoothed = 0.0
target_vy_smoothed = 0.0
stable_track_frames = 0

# Счётчик попыток авто-восстановления после LOST. Сбрасывается на каждое
# успешное возобновление или явное снятие AUX4.
auto_reacq_attempts = 0

# Launch state-машина. prev_controllable_for_launch — был ли controllable
# в предыдущем кадре. Когда переходит False→True, запускается RAMP_UP.
launch_phase = "NONE"            # "NONE" / "RAMP_UP" / "HOLD" / "RAMP_DOWN"
launch_counter = 0
prev_controllable_for_launch = False

# =========================================================
# 5. TRACK STATE
# =========================================================
TRACK_STATE_IDLE = "IDLE"
TRACK_STATE_ACQ = "ACQ"
TRACK_STATE_TRACKED = "TRACKED"
TRACK_STATE_HOLD = "HOLD"
TRACK_STATE_LOST = "LOST"

track_state = TRACK_STATE_IDLE
target_visible = False
target_controllable = False
overlay_text = "IDLE"
overlay_color = COLOR_WHITE
target_box_main = None

lock_cx = None
lock_cy = None
lock_w = None
lock_h = None
tmpl_w = None
tmpl_h = None
template_gray = None
template_std = 0.0
prev_gray = None
prev_pts = None
lost_frames = 0
frame_index = 0
last_match_score = 0.0
last_flow_ok = False

filtered_dx_yaw = 0.0
prev_adx = 0.0
prev_ady_ctrl = 0.0
prev_target_pitch = 1500

fps_t0 = time.monotonic()
fps_frames = 0
fps_current = 0.0
DEBUG_PRINT = True

# =========================================================
# 6. MSP
# =========================================================
def msp_request(cmd):
    if fc is None:
        return None
    try:
        # Чистим вход. ACK от SET_RAW_RC иначе копятся и парсер ловит их
        # как ответ на MSP_RC (cmd=200 vs ожидаемые 105/108).
        try:
            fc.reset_input_buffer()
        except Exception:
            pass
        packet = b'$M<' + struct.pack('<BBB', 0, cmd, cmd)
        fc.write(packet)

        sync = b''
        for _ in range(64):
            b = fc.read(1)
            if not b:
                return None
            sync += b
            if sync.endswith(b'$M>'):
                break
        else:
            return None

        size_b = fc.read(1)
        cmd_b = fc.read(1)
        if not size_b or not cmd_b:
            return None
        size_ = size_b[0]
        resp_cmd = cmd_b[0]
        data_ = fc.read(size_)
        crc_b = fc.read(1)
        if resp_cmd != cmd or len(data_) != size_ or len(crc_b) != 1:
            return None
        checksum = size_ ^ resp_cmd
        for b in data_:
            checksum ^= b
        if checksum != crc_b[0]:
            return None
        return data_
    except Exception:
        return None


def send_msp_set_raw_rc(channels):
    if fc is None:
        return
    try:
        channels = (list(channels) + [1500] * 8)[:8]
        channels = [max(885, min(2115, int(x))) for x in channels]
        data = struct.pack('<8H', *channels)
        size = len(data)
        cmd = 200  # MSP_SET_RAW_RC
        checksum = size ^ cmd
        for b in data:
            checksum ^= b
        packet = b'$M<' + struct.pack('<BB', size, cmd) + data + struct.pack('<B', checksum)
        fc.write(packet)
    except Exception:
        pass


def fc_io_loop():
    """Read: MSP_RC возвращает RPYT (внутренний порядок Betaflight) →
       ch[0]=Roll, ch[1]=Pitch, ch[2]=Yaw, ch[3]=Throttle.
       Write: MSP_SET_RAW_RC ждёт rcmap-порядок (default AETR) →
       channels[0]=Roll, [1]=Pitch, [2]=Throttle, [3]=Yaw.
    """
    global aux4_state
    next_t = time.monotonic()
    next_motor_t = 0.0

    while not io_thread_stop.is_set():
        if fc is None:
            time.sleep(0.1)
            continue

        try:
            rc_data = msp_request(105)  # MSP_RC
            if rc_data is not None and len(rc_data) >= 16:
                ch = struct.unpack('<' + 'H' * (len(rc_data) // 2), rc_data)
                if len(ch) >= 8:
                    with state_lock:
                        app_state["rc_channels"] = list(ch[:8])
                        aux4_state = ch[7] > 1500
                        app_state["rc_throttle"] = ch[3]
                        app_state["rc_throttle_ts"] = time.monotonic()

            att_data = msp_request(108)  # MSP_ATTITUDE
            if att_data is not None and len(att_data) >= 6:
                _roll_t, pitch_t, _heading = struct.unpack('<3h', att_data[:6])
                with state_lock:
                    app_state["fc_pitch_deg"] = pitch_t / 10.0
                    app_state["fc_pitch_ts"] = time.monotonic()

            now = time.monotonic()
            if MOTOR_DEBUG_ENABLED and now >= next_motor_t:
                mot_data = msp_request(104)  # MSP_MOTOR
                if mot_data is not None and len(mot_data) >= 2:
                    vals = struct.unpack('<' + 'H' * (len(mot_data) // 2), mot_data)
                    with state_lock:
                        app_state["motors"] = list(vals)
                        app_state["motors_ts"] = now
                next_motor_t = now + MOTOR_DEBUG_PERIOD

            with state_lock:
                ov = override_active
                aux_now = aux4_state
                live = list(app_state.get("rc_channels", [1500] * 8))
                rc_ts = app_state.get("rc_throttle_ts", 0.0)
                r_cmd = global_roll_cmd
                p_cmd = global_pitch_cmd
                y_cmd = global_yaw_cmd
                t_cmd = global_throttle_cmd

            # ВСЕГДА шлём поток MSP override, как только пришёл хотя бы один
            # успешный MSP_RC. Это лечит «после AUX OFF тротл не возвращается»:
            # Betaflight с включённым msp_override_channels_mask ожидает
            # непрерывный поток MSP-кадров. Если мы вдруг перестаём слать,
            # он держит последние значения / уходит в failsafe.
            #
            # По умолчанию всё passthrough — живой стик во все 4 канала.
            # Per-axis флаги OVERRIDE_* подменяют ось только когда AUX4 ON
            # и трекер в TRACKED (override_active=True).
            have_fresh_rc = (time.monotonic() - rc_ts) <= 0.50
            if have_fresh_rc:
                # MSP_RC возвращает RPYT internal: ch0=R, ch1=P, ch2=Y, ch3=T.
                live_roll  = int(live[0]) if len(live) > 0 else 1500
                live_pitch = int(live[1]) if len(live) > 1 else 1500
                live_yaw   = int(live[2]) if len(live) > 2 else 1500
                live_thr   = int(live[3]) if len(live) > 3 else 1000

                apply_ov = ov and aux_now
                out_roll  = int(r_cmd) if (apply_ov and OVERRIDE_ROLL)     else live_roll
                out_pitch = int(p_cmd) if (apply_ov and OVERRIDE_PITCH)    else live_pitch
                out_yaw   = int(y_cmd) if (apply_ov and OVERRIDE_YAW)      else live_yaw
                out_thr   = int(t_cmd) if (apply_ov and OVERRIDE_THROTTLE) else live_thr

                # MSP_SET_RAW_RC ждёт rcmap external (default AETR):
                # ch0=R, ch1=P, ch2=T, ch3=Y.
                channels = (live + [1500] * 8)[:8]
                channels[0] = out_roll
                channels[1] = out_pitch
                channels[2] = out_thr
                channels[3] = out_yaw
                with state_lock:
                    app_state["last_sent_channels"] = list(channels[:8])
                send_msp_set_raw_rc(channels)
            # Без свежих RC-данных вообще ничего не шлём — иначе FC получит
            # стартовое «среднее» из app_state.
        except Exception:
            pass

        next_t += MSP_RC_PERIOD
        sleep_for = next_t - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_t = time.monotonic()

# =========================================================
# 7. ГЕОМЕТРИЯ
# =========================================================
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def clamp_rect_center(cx, cy, w, h, max_w, max_h):
    w = max(2, int(round(w)))
    h = max(2, int(round(h)))
    x1 = int(round(cx - w / 2))
    y1 = int(round(cy - h / 2))
    x1 = clamp(x1, 0, max_w - w)
    y1 = clamp(y1, 0, max_h - h)
    return x1, y1, x1 + w, y1 + h


def crop_center(gray, cx, cy, w, h):
    x1, y1, x2, y2 = clamp_rect_center(cx, cy, w, h, gray.shape[1], gray.shape[0])
    return gray[y1:y2, x1:x2].copy(), (x1, y1, x2, y2)


def subpixel_peak(score_map, mx, my):
    """3-точечная параболическая интерполяция пика в score_map вокруг (mx, my).
    Возвращает (dx, dy) — субпиксельный сдвиг истинного пика относительно (mx, my),
    в диапазоне примерно [-0.5, 0.5]. Если пик на границе или в плоской зоне —
    возвращает (0, 0).
    Стоимость: 6 индексаций + 2 деления, в районе микросекунды.
    """
    h, w = score_map.shape[:2]
    dx = 0.0
    dy = 0.0
    if 0 < mx < w - 1:
        l = float(score_map[my, mx - 1])
        c = float(score_map[my, mx])
        r = float(score_map[my, mx + 1])
        denom = l - 2.0 * c + r
        if abs(denom) > 1e-6:
            dx = 0.5 * (l - r) / denom
            if dx < -0.5:
                dx = -0.5
            elif dx > 0.5:
                dx = 0.5
    if 0 < my < h - 1:
        t = float(score_map[my - 1, mx])
        c = float(score_map[my, mx])
        b = float(score_map[my + 1, mx])
        denom = t - 2.0 * c + b
        if abs(denom) > 1e-6:
            dy = 0.5 * (t - b) / denom
            if dy < -0.5:
                dy = -0.5
            elif dy > 0.5:
                dy = 0.5
    return dx, dy


def lores_box_to_main(cx, cy, w, h):
    sx = MAIN_W / LORES_W
    sy = MAIN_H / LORES_H
    x1 = int(round((cx - w / 2) * sx))
    y1 = int(round((cy - h / 2) * sy))
    x2 = int(round((cx + w / 2) * sx))
    y2 = int(round((cy + h / 2) * sy))
    x1 = clamp(x1, 0, MAIN_W - 1)
    y1 = clamp(y1, 0, MAIN_H - 1)
    x2 = clamp(x2, x1 + 1, MAIN_W)
    y2 = clamp(y2, y1 + 1, MAIN_H)
    return x1, y1, x2, y2


def refresh_flow_points(gray, cx, cy, w, h):
    patch, (x1, y1, x2, y2) = crop_center(gray, cx, cy, max(w * 2.2, 12), max(h * 2.2, 12))
    if patch.size == 0:
        return None
    pts = cv2.goodFeaturesToTrack(
        patch, maxCorners=25, qualityLevel=0.004, minDistance=2, blockSize=3
    )
    if pts is not None:
        pts = pts.astype(np.float32)
        pts[:, 0, 0] += x1
        pts[:, 0, 1] += y1
    return pts


def flow_predict(prev_g, cur_g, pts, cx, cy):
    if prev_g is None or pts is None or len(pts) < FLOW_MIN_POINTS:
        return False, cx, cy
    try:
        nxt, st, err = cv2.calcOpticalFlowPyrLK(
            prev_g, cur_g, pts, None,
            winSize=(15, 15), maxLevel=2,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 14, 0.03),
        )
        if nxt is None or st is None:
            return False, cx, cy
        st = st.reshape(-1).astype(bool)
        old = pts[st].reshape(-1, 2)
        new = nxt[st].reshape(-1, 2)
        if err is not None:
            keep = err[st].reshape(-1) < FLOW_ERR_MAX
            old, new = old[keep], new[keep]
        if len(new) < FLOW_MIN_POINTS:
            return False, cx, cy
        dx = float(np.median(new[:, 0] - old[:, 0]))
        dy = float(np.median(new[:, 1] - old[:, 1]))
        if math.hypot(dx, dy) > FLOW_MAX_STEP:
            return False, cx, cy
        return True, cx + dx, cy + dy
    except Exception:
        return False, cx, cy


def estimate_size_at_position(gray, cx, cy):
    """Оценка размера цели вокруг точки (cx, cy) по связной компоненте.
    Возвращает (lw, lh) или (default_w, default_h) если ничего не нашли.
    Используется и при первом локе (с центром = прицел), и при периодической
    адаптации размера в TRACKED-фазе.
    """
    R = ACQ_SIZE_SEARCH_RADIUS
    cxi = int(round(cx))
    cyi = int(round(cy))
    x1 = max(0, cxi - R)
    y1 = max(0, cyi - R)
    x2 = min(gray.shape[1], cxi + R + 1)
    y2 = min(gray.shape[0], cyi + R + 1)
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return float(ACQ_DEFAULT_LOCK_W), float(ACQ_DEFAULT_LOCK_H)

    roi_f = roi.astype(np.float32)
    med = float(np.median(roi_f))
    std = float(np.std(roi_f))

    diff = np.abs(roi_f - med).astype(np.uint8)
    thr = int(max(3, min(30, std * 0.55 + 2)))
    _, mask = cv2.threshold(diff, thr, 255, cv2.THRESH_BINARY)
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    cx_local = cxi - x1
    cy_local = cyi - y1
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    label = 0
    for r_search in (0, 1, 2):
        for dy in range(-r_search, r_search + 1):
            for dx in range(-r_search, r_search + 1):
                if abs(dx) != r_search and abs(dy) != r_search and r_search != 0:
                    continue
                ny = cy_local + dy
                nx = cx_local + dx
                if 0 <= ny < labels.shape[0] and 0 <= nx < labels.shape[1]:
                    lab = int(labels[ny, nx])
                    if lab > 0:
                        label = lab
                        break
            if label > 0:
                break
        if label > 0:
            break

    if label == 0 or label >= num_labels:
        return float(ACQ_DEFAULT_LOCK_W), float(ACQ_DEFAULT_LOCK_H)

    x, y, w, h, area = stats[label]
    if area < 1 or w > R * 1.6 or h > R * 1.6:
        return float(ACQ_DEFAULT_LOCK_W), float(ACQ_DEFAULT_LOCK_H)

    lw = clamp(max(float(w) * LOCK_PAD, LOCK_MIN_W), LOCK_MIN_W, LOCK_MAX_W)
    lh = clamp(max(float(h) * LOCK_PAD, LOCK_MIN_H), LOCK_MIN_H, LOCK_MAX_H)
    return float(lw), float(lh)


def estimate_size_at_crosshair(gray):
    """Обёртка вокруг estimate_size_at_position для acquisition-фазы:
    оценивает размер ровно под прицелом.
    """
    return estimate_size_at_position(gray, CENTER_X_LORES, CENTER_Y_LORES)


def estimate_initial_target(gray):
    if ACQ_LOCK_AT_CROSSHAIR_EXACTLY:
        lw, lh = estimate_size_at_crosshair(gray)
        return float(CENTER_X_LORES), float(CENTER_Y_LORES), float(lw), float(lh), True
    return (float(CENTER_X_LORES), float(CENTER_Y_LORES),
            float(ACQ_DEFAULT_LOCK_W), float(ACQ_DEFAULT_LOCK_H), True)


def build_template(gray, cx, cy, box_w, box_h):
    global tmpl_w, tmpl_h, template_std
    tw = clamp(max(box_w * TEMPLATE_SCALE, TEMPLATE_MIN), TEMPLATE_MIN, TEMPLATE_MAX)
    th = clamp(max(box_h * TEMPLATE_SCALE, TEMPLATE_MIN), TEMPLATE_MIN, TEMPLATE_MAX)
    tmpl, rect = crop_center(gray, cx, cy, tw, th)
    template_std = float(np.std(tmpl)) if tmpl.size else 0.0
    tmpl_w = tmpl.shape[1]
    tmpl_h = tmpl.shape[0]
    return tmpl


def template_match_locked(gray, pred_cx, pred_cy, flow_motion=0.0):
    """Темплейт-матч с distance-penalty + субпиксельная интерполяция пика.

    flow_motion: модуль смещения flow-предсказания за кадр (px). Используется
    для адаптивного выбора SEARCH_MARGIN: на медленной цели окно меньше
    (быстрее матч, меньше шансов уцепиться за фон), на быстрой — больше.
    """
    global template_gray, tmpl_w, tmpl_h
    if template_gray is None or tmpl_w is None or tmpl_h is None:
        return False, pred_cx, pred_cy, 0.0

    # Адаптивный margin по скорости движения цели.
    if ADAPTIVE_SEARCH_MARGIN:
        vel_norm = flow_motion / SEARCH_MARGIN_VEL_REF
        if vel_norm > 1.0:
            vel_norm = 1.0
        margin = int(round(SEARCH_MARGIN_MIN + (SEARCH_MARGIN_MAX - SEARCH_MARGIN_MIN) * vel_norm))
    else:
        margin = SEARCH_MARGIN

    sw = int(tmpl_w + margin * 2)
    sh = int(tmpl_h + margin * 2)
    search, (sx1, sy1, sx2, sy2) = crop_center(gray, pred_cx, pred_cy, sw, sh)
    if search.shape[0] < tmpl_h or search.shape[1] < tmpl_w:
        return False, pred_cx, pred_cy, 0.0

    try:
        if template_std < 3.0:
            res = cv2.matchTemplate(search, template_gray, cv2.TM_SQDIFF_NORMED)
            score_map = 1.0 - res
        else:
            score_map = cv2.matchTemplate(search, template_gray, cv2.TM_CCOEFF_NORMED)
    except Exception:
        return False, pred_cx, pred_cy, 0.0

    if score_map.size == 0:
        return False, pred_cx, pred_cy, 0.0

    rh, rw = score_map.shape[:2]
    yy, xx = np.mgrid[0:rh, 0:rw]
    centers_x = sx1 + xx + tmpl_w / 2.0
    centers_y = sy1 + yy + tmpl_h / 2.0
    dist = np.sqrt((centers_x - pred_cx) ** 2 + (centers_y - pred_cy) ** 2)
    # Нормируем штраф по фактическому margin, не по константе.
    norm = max(margin, 1)
    penalized = score_map - DIST_PENALTY * (dist / norm) ** 2

    _, max_val, _, max_loc = cv2.minMaxLoc(penalized.astype(np.float32))
    mx, my = max_loc
    raw_score = float(score_map[my, mx])

    # Субпиксельное уточнение пика: убирает пиксельную квантовку матчера
    # (точность теперь ~0.1 px вместо ±0.5 px), особенно важно с маленьким
    # DEADBAND_X/Y — снимает «дребезг» крестика на покоящейся цели.
    sub_dx, sub_dy = subpixel_peak(score_map, mx, my)
    new_cx = sx1 + mx + sub_dx + tmpl_w / 2.0
    new_cy = sy1 + my + sub_dy + tmpl_h / 2.0

    if raw_score < MATCH_MIN_SCORE:
        return False, pred_cx, pred_cy, raw_score
    if math.hypot(new_cx - pred_cx, new_cy - pred_cy) > MAX_LOCK_STEP:
        return False, pred_cx, pred_cy, raw_score
    return True, float(new_cx), float(new_cy), raw_score


def reset_tracking(to_acq=False):
    global track_state, target_visible, target_controllable, overlay_text, overlay_color, target_box_main
    global lock_cx, lock_cy, lock_w, lock_h, tmpl_w, tmpl_h, template_gray, template_std
    global prev_gray, prev_pts, lost_frames, last_match_score, last_flow_ok
    global acq_wait_left
    global filtered_dx_yaw, prev_adx, prev_ady_ctrl, prev_target_pitch
    global smooth_throttle_out, throttle_integral, prev_ady
    global roll_integral, pitch_integral, yaw_integral
    global smoothed_pitch_deg
    global prev_box_cx, prev_box_cy, target_vx_smoothed, target_vy_smoothed, stable_track_frames
    global launch_phase, launch_counter, prev_controllable_for_launch

    track_state = TRACK_STATE_ACQ if to_acq else TRACK_STATE_IDLE
    target_visible = False
    target_controllable = False
    overlay_text = "ACQ" if to_acq else "IDLE"
    overlay_color = COLOR_WHITE
    target_box_main = None

    lock_cx = lock_cy = lock_w = lock_h = None
    tmpl_w = tmpl_h = None
    template_gray = None
    template_std = 0.0
    prev_gray = None
    prev_pts = None
    lost_frames = 0
    last_match_score = 0.0
    last_flow_ok = False
    acq_wait_left = 0

    filtered_dx_yaw = 0.0
    prev_adx = 0.0
    prev_ady_ctrl = 0.0
    prev_target_pitch = 1500

    smooth_throttle_out = None
    throttle_integral = 0.0
    prev_ady = 0.0

    roll_integral = 0.0
    pitch_integral = 0.0
    yaw_integral = 0.0

    smoothed_pitch_deg = 0.0

    prev_box_cx = None
    prev_box_cy = None
    target_vx_smoothed = 0.0
    target_vy_smoothed = 0.0
    stable_track_frames = 0

    launch_phase = "NONE"
    launch_counter = 0
    prev_controllable_for_launch = False

# =========================================================
# 8. CONTROL — главные исправления здесь
# =========================================================
def _pid_axis_step(error, prev_error, integral, ff_value,
                   p_gain, d_gain, i_gain, ff_gain,
                   integral_max, integral_decay,
                   sign, max_deflect):
    """Один шаг PID+FF по одной оси с условным anti-windup.

    Anti-windup: I-компонент НЕ накапливается, если P+D+FF+I-выход уже
    саттурирован в том же направлении, в котором новая I-добавка его
    толкала бы дальше. Это предотвращает overshoot, когда цель наконец-то
    оказывается в зоне досягаемости.

    ff_value: упреждающий сигнал (обычно скорость цели в пикселях/кадр).
    ff_gain: коэффициент пересчёта ff_value в PWM-вклад.
    Если ff_gain=0 — FF выключен, ведёт себя как чистый PID.

    Возвращает (offset_clamped, new_prev_error, new_integral).
    """
    err_f = float(error)
    d_err = err_f - prev_error

    pd_part = err_f * p_gain + d_err * d_gain
    ff_part = float(ff_value) * ff_gain

    # Сначала смотрим, где бы оказался выход с ТЕКУЩИМ интегратором.
    total_unsat = sign * (pd_part + ff_part + integral)
    sat_pos = total_unsat >= max_deflect
    sat_neg = total_unsat <= -max_deflect
    # Знак, в котором новая I-добавка толкнула бы выход.
    i_dir = sign * err_f
    push_further = (sat_pos and i_dir > 0) or (sat_neg and i_dir < 0)

    if err_f != 0.0 and not push_further:
        integral += err_f * i_gain
        if integral > integral_max:
            integral = integral_max
        elif integral < -integral_max:
            integral = -integral_max
    integral *= integral_decay

    out = sign * (pd_part + ff_part + integral)
    if out > max_deflect:
        out = max_deflect
    elif out < -max_deflect:
        out = -max_deflect

    return out, err_f, integral


def _compute_pitch_attitude_comp_px(now_mono):
    """Возвращает (compensation_px, обновлено?). Compensation добавляется
    к aim_y. Положительное значение = aim сдвигается ВНИЗ в кадре (бóльшая y),
    что соответствует ситуации «квад завален носом вниз → цель в кадре сверху,
    но это нормально для текущей траектории».

    Использует и обновляет глобал smoothed_pitch_deg.
    """
    global smoothed_pitch_deg
    if not PITCH_ATTITUDE_COMP_ENABLED:
        return 0.0

    with state_lock:
        fc_pitch = app_state.get("fc_pitch_deg")
        fc_pitch_ts = app_state.get("fc_pitch_ts", 0.0)

    if fc_pitch is None or (now_mono - fc_pitch_ts) > FC_PITCH_TIMEOUT:
        return 0.0

    smoothed_pitch_deg += PITCH_COMP_ALPHA * (float(fc_pitch) - smoothed_pitch_deg)
    comp_px = -smoothed_pitch_deg * PIXELS_PER_PITCH_DEG
    if comp_px > MAX_PITCH_COMP_PX:
        comp_px = MAX_PITCH_COMP_PX
    elif comp_px < -MAX_PITCH_COMP_PX:
        comp_px = -MAX_PITCH_COMP_PX
    return comp_px


def update_control_from_target():
    """Roll/Pitch/Yaw — P+D+I через _pid_axis_step с anti-windup.
       Прицельная точка по pitch учитывает текущий тангаж квада (MSP_ATTITUDE).
       Throttle:
         - OVERRIDE_THROTTLE=False (default) → target_throttle = live стик.
         - OVERRIDE_THROTTLE=True            → P+I+D по dy ОТ ЦЕНТРА КОРОБКИ
           (без AIM_OFFSET_Y, иначе I унесёт газ к нулю).

       Реальная отправка на FC решается в fc_io_loop по флагам OVERRIDE_*.
    """
    global global_yaw_cmd, global_pitch_cmd, global_roll_cmd, global_throttle_cmd, override_active
    global filtered_dx_yaw, prev_adx, prev_ady_ctrl, prev_target_pitch
    global smooth_throttle_out, throttle_integral, prev_ady
    global roll_integral, pitch_integral, yaw_integral
    global smoothed_pitch_deg
    global prev_box_cx, prev_box_cy, target_vx_smoothed, target_vy_smoothed, stable_track_frames
    global launch_phase, launch_counter, prev_controllable_for_launch
    global overlay_text, overlay_color

    with state_lock:
        box = target_box_main
        controllable = target_controllable
        live_thr = app_state.get("rc_throttle", 1500)
        live_thr_ts = app_state.get("rc_throttle_ts", 0.0)

    now_mono = time.monotonic()
    rc_fresh = ((now_mono - live_thr_ts) <= 0.35) and (885 <= int(live_thr) <= 2115)
    base_thr = int(live_thr) if rc_fresh else int(app_state.get("dyn_throttle", 1500))
    if base_thr < THROTTLE_MIN_PWM:
        base_thr = THROTTLE_MIN_PWM
    elif base_thr > THROTTLE_MAX_PWM:
        base_thr = THROTTLE_MAX_PWM

    if not controllable or box is None:
        # Сбрасываем ВСЕ накопители, иначе windup из прошлой сессии вылезет
        # на следующем TRACKED-кадре как «моторы сами раскрутились».
        filtered_dx_yaw = 0.0
        prev_adx = 0.0
        prev_ady_ctrl = 0.0
        prev_ady = 0.0
        prev_target_pitch = 1500
        smooth_throttle_out = None
        throttle_integral = 0.0
        roll_integral = 0.0
        pitch_integral = 0.0
        yaw_integral = 0.0
        smoothed_pitch_deg = 0.0
        prev_box_cx = None
        prev_box_cy = None
        target_vx_smoothed = 0.0
        target_vy_smoothed = 0.0
        stable_track_frames = 0
        # Launch тоже сбрасывается — следующее controllable=True переоткроет манёвр.
        launch_phase = "NONE"
        launch_counter = 0
        prev_controllable_for_launch = False
        with state_lock:
            app_state["dyn_throttle"] = base_thr
            override_active = False
            global_roll_cmd = 1500.0
            global_pitch_cmd = 1500.0
            global_yaw_cmd = 1500.0
            global_throttle_cmd = base_thr
        return

    # --- Геометрия. РАЗДЕЛЬНЫЕ dy для pitch и для газа. ---
    box_cx = (box[0] + box[2]) / 2.0
    box_cy = (box[1] + box[3]) / 2.0

    # --- Launch state machine ---
    # Триггер: первый кадр, в котором мы controllable, после серии не-controllable
    # кадров (новый LOCK / возобновление после LOST). Прогон по фазам идёт
    # каждый кадр, не привязан к содержимому коробки или к ошибкам.
    trigger_launch = LAUNCH_ENABLED and not prev_controllable_for_launch
    prev_controllable_for_launch = True

    if trigger_launch:
        launch_phase = "RAMP_UP"
        launch_counter = 0

    launch_intensity = 0.0
    if launch_phase == "RAMP_UP":
        launch_intensity = launch_counter / float(max(LAUNCH_RAMP_UP_FRAMES, 1))
        if launch_intensity > 1.0:
            launch_intensity = 1.0
        launch_counter += 1
        if launch_counter >= LAUNCH_RAMP_UP_FRAMES:
            launch_phase = "HOLD"
            launch_counter = 0
    elif launch_phase == "HOLD":
        launch_intensity = 1.0
        launch_counter += 1
        if launch_counter >= LAUNCH_HOLD_FRAMES:
            launch_phase = "RAMP_DOWN"
            launch_counter = 0
    elif launch_phase == "RAMP_DOWN":
        launch_intensity = 1.0 - launch_counter / float(max(LAUNCH_RAMP_DOWN_FRAMES, 1))
        if launch_intensity < 0.0:
            launch_intensity = 0.0
        launch_counter += 1
        if launch_counter >= LAUNCH_RAMP_DOWN_FRAMES:
            launch_phase = "NONE"
            launch_counter = 0
            launch_intensity = 0.0
    # else: NONE — launch_intensity остаётся 0

    # Pitch-вклад launch: нос вниз с тем же знаком, что и нормальное наведение
    # на «цель снизу» (PITCH_SIGN — корректирующий знак для PWM).
    launch_pitch_pwm = PITCH_SIGN * LAUNCH_NOSE_DOWN_PWM * launch_intensity
    # Cruise — постоянная тяга вперёд, действует только когда launch неактивен.
    if CRUISE_ENABLED and launch_phase == "NONE":
        cruise_pitch_pwm = PITCH_SIGN * CRUISE_NOSE_DOWN_PWM
    else:
        cruise_pitch_pwm = 0.0

    # --- Упреждение по скорости цели ---
    # Считаем межкадровую скорость, прогоняем через фильтр, отбрасываем глитчи.
    # Применяется только после LEAD_MIN_STABLE_FRAMES — даём фильтру стабилизироваться.
    lead_x = 0.0
    lead_y = 0.0
    if prev_box_cx is not None and prev_box_cy is not None:
        inst_vx = box_cx - prev_box_cx
        inst_vy = box_cy - prev_box_cy
        # Глитч-фильтр: re-lock / occlusion даёт скачок сильно больше реальной скорости цели.
        if abs(inst_vx) < LEAD_MAX_VEL_JUMP and abs(inst_vy) < LEAD_MAX_VEL_JUMP:
            target_vx_smoothed += LEAD_VEL_ALPHA * (inst_vx - target_vx_smoothed)
            target_vy_smoothed += LEAD_VEL_ALPHA * (inst_vy - target_vy_smoothed)
            stable_track_frames += 1
        else:
            # Подозрительный скачок — сбрасываем счётчик стабильности.
            stable_track_frames = 0
    prev_box_cx = box_cx
    prev_box_cy = box_cy

    if LEAD_AIM_ENABLED and stable_track_frames >= LEAD_MIN_STABLE_FRAMES and LEAD_FRAMES > 0:
        lead_x = target_vx_smoothed * float(LEAD_FRAMES)
        lead_y = target_vy_smoothed * float(LEAD_FRAMES)
        # Жёсткий потолок чтобы шумы по скорости не унесли прицел через полкадра.
        if lead_x > LEAD_MAX_PX:
            lead_x = LEAD_MAX_PX
        elif lead_x < -LEAD_MAX_PX:
            lead_x = -LEAD_MAX_PX
        if lead_y > LEAD_MAX_PX:
            lead_y = LEAD_MAX_PX
        elif lead_y < -LEAD_MAX_PX:
            lead_y = -LEAD_MAX_PX

    # Динамическая компенсация наклона квада.
    pitch_comp_px = _compute_pitch_attitude_comp_px(now_mono)

    # Прицельная точка: текущая позиция + статический оффсет + компенсация тангажа + упреждение.
    dx_aim = (box_cx + AIM_OFFSET_X + lead_x) - CENTER_X
    dy_aim = (box_cy + AIM_OFFSET_Y + pitch_comp_px + lead_y) - CENTER_Y

    # Для газа — чистая позиция коробки, БЕЗ AIM_OFFSET, БЕЗ компенсации, БЕЗ упреждения.
    # Высоту мы корректируем по реальному положению цели, а не по предсказанному.
    dy_alt = box_cy - CENTER_Y

    adx = 0.0 if abs(dx_aim) < DEADBAND_X else (dx_aim - DEADBAND_X if dx_aim > 0 else dx_aim + DEADBAND_X)
    ady = 0.0 if abs(dy_aim) < DEADBAND_Y else (dy_aim - DEADBAND_Y if dy_aim > 0 else dy_aim + DEADBAND_Y)

    # --- Терминальный режим: близко к цели — другие гэйны ---
    # Когда коробка занимает заметную часть кадра, мы в финальных секундах.
    # Поднимаем P (нужны рефлексы), срезаем I (нет времени интегрировать),
    # чуть повышаем FF (резче следуем за движением цели).
    box_w_main = box[2] - box[0]
    box_h_main = box[3] - box[1]
    box_frac = (box_w_main * box_h_main) / float(MAIN_W * MAIN_H)
    in_terminal = TERMINAL_MODE_ENABLED and box_frac >= TERMINAL_BOX_FRAC_THRESHOLD

    if in_terminal:
        p_roll_eff = P_GAIN_ROLL * TERMINAL_P_MULTIPLIER
        p_pitch_eff = P_GAIN_PITCH * TERMINAL_P_MULTIPLIER
        i_roll_eff = I_GAIN_ROLL * TERMINAL_I_MULTIPLIER
        i_pitch_eff = I_GAIN_PITCH * TERMINAL_I_MULTIPLIER
        ff_roll_eff = FF_GAIN_ROLL * TERMINAL_FF_MULTIPLIER
        ff_pitch_eff = FF_GAIN_PITCH * TERMINAL_FF_MULTIPLIER
        ff_yaw_eff = FF_GAIN_YAW * TERMINAL_FF_MULTIPLIER
    else:
        p_roll_eff = P_GAIN_ROLL
        p_pitch_eff = P_GAIN_PITCH
        i_roll_eff = I_GAIN_ROLL
        i_pitch_eff = I_GAIN_PITCH
        ff_roll_eff = FF_GAIN_ROLL
        ff_pitch_eff = FF_GAIN_PITCH
        ff_yaw_eff = FF_GAIN_YAW

    # --- ROLL: P+D+I+FF через хелпер с anti-windup ---
    roll_offset, prev_adx, roll_integral = _pid_axis_step(
        adx, prev_adx, roll_integral,
        target_vx_smoothed,
        p_roll_eff, D_GAIN_ROLL, i_roll_eff, ff_roll_eff,
        ROLL_INTEGRAL_MAX, ROLL_INTEGRAL_DECAY,
        ROLL_SIGN, MAX_ROLL_DEFLECT,
    )
    target_roll = max(1000, min(2000, 1500 + roll_offset))

    # --- PITCH: P+D+I+FF через хелпер с anti-windup ---
    pitch_offset, prev_ady_ctrl, pitch_integral = _pid_axis_step(
        ady, prev_ady_ctrl, pitch_integral,
        target_vy_smoothed,
        p_pitch_eff, D_GAIN_PITCH, i_pitch_eff, ff_pitch_eff,
        PITCH_INTEGRAL_MAX, PITCH_INTEGRAL_DECAY,
        PITCH_SIGN, MAX_PITCH_DEFLECT,
    )
    # Поверх PID — launch boost и cruise. Они могут вытолкнуть target_pitch
    # за MAX_PITCH_DEFLECT, но финальный clamp 1000-2000 остаётся.
    combined_pitch = pitch_offset + launch_pitch_pwm + cruise_pitch_pwm
    target_pitch = max(1000, min(2000, 1500 + combined_pitch))
    prev_target_pitch = target_pitch

    # --- YAW: фильтр + ослабление при больших adx + I + FF с anti-windup ---
    # Yaw не использует хелпер потому что error — это filtered_dx_yaw × yaw_weight,
    # а I-добавка тоже взвешивается. Логика та же, но с весом.
    filtered_dx_yaw += YAW_FILTER_ALPHA * (float(adx) - filtered_dx_yaw)
    abs_adx = abs(adx)
    if abs_adx < YAW_ROLL_CROSSOVER:
        yaw_weight = 1.0
    else:
        yaw_weight = max(0.3, 1.0 - (abs_adx - YAW_ROLL_CROSSOVER) / max(YAW_ROLL_BLEND_RANGE, 1.0))

    yaw_error_weighted = filtered_dx_yaw * yaw_weight
    yaw_pd = yaw_error_weighted * P_GAIN_YAW
    yaw_ff = target_vx_smoothed * ff_yaw_eff * yaw_weight
    yaw_total_unsat = YAW_SIGN * (yaw_pd + yaw_ff + yaw_integral)
    yaw_sat_pos = yaw_total_unsat >= MAX_YAW_DEFLECT
    yaw_sat_neg = yaw_total_unsat <= -MAX_YAW_DEFLECT
    yaw_i_dir = YAW_SIGN * yaw_error_weighted
    yaw_push_further = (yaw_sat_pos and yaw_i_dir > 0) or (yaw_sat_neg and yaw_i_dir < 0)
    if adx != 0 and not yaw_push_further:
        yaw_integral += yaw_error_weighted * I_GAIN_YAW
        if yaw_integral > YAW_INTEGRAL_MAX:
            yaw_integral = YAW_INTEGRAL_MAX
        elif yaw_integral < -YAW_INTEGRAL_MAX:
            yaw_integral = -YAW_INTEGRAL_MAX
    yaw_integral *= YAW_INTEGRAL_DECAY

    yaw_offset = YAW_SIGN * (yaw_pd + yaw_ff + yaw_integral)
    if yaw_offset > MAX_YAW_DEFLECT:
        yaw_offset = MAX_YAW_DEFLECT
    elif yaw_offset < -MAX_YAW_DEFLECT:
        yaw_offset = -MAX_YAW_DEFLECT
    target_yaw = max(1000, min(2000, 1500 + yaw_offset))

    # --- THROTTLE ---
    if not OVERRIDE_THROTTLE:
        target_throttle = base_thr
        smooth_throttle_out = float(base_thr)
        throttle_integral = 0.0
        prev_ady = float(dy_alt)
    else:
        # Динамика по dy_alt (БЕЗ AIM_OFFSET).
        thr_adjust = 0.0
        if abs(dy_alt) > THROTTLE_DEADBAND:
            active_dy = float(dy_alt - THROTTLE_DEADBAND if dy_alt > 0 else dy_alt + THROTTLE_DEADBAND)
            thr_adjust += -active_dy * DY_THROTTLE_GAIN
            throttle_integral += -active_dy * DY_INTEGRAL_RATE
            throttle_integral = max(-DY_INTEGRAL_MAX, min(DY_INTEGRAL_MAX, throttle_integral))
        else:
            throttle_integral *= 0.85

        throttle_integral *= DY_INTEGRAL_DECAY
        thr_adjust += throttle_integral

        d_dy = float(dy_alt) - prev_ady
        prev_ady = float(dy_alt)
        thr_adjust += d_dy * DY_THROTTLE_D_GAIN

        max_adj = max(30.0, base_thr * THROTTLE_PERCENT / 100.0)
        thr_adjust = max(-max_adj, min(max_adj, thr_adjust))

        raw_throttle = base_thr + thr_adjust
        raw_throttle = max(THROTTLE_MIN_PWM, min(THROTTLE_MAX_PWM, raw_throttle))

        if smooth_throttle_out is None:
            smooth_throttle_out = float(raw_throttle)
        else:
            smooth_throttle_out += THROTTLE_OUT_ALPHA * (raw_throttle - smooth_throttle_out)
        target_throttle = int(round(smooth_throttle_out))
        target_throttle = max(THROTTLE_MIN_PWM, min(THROTTLE_MAX_PWM, target_throttle))

    # Launch throttle boost — применяется ТОЛЬКО при OVERRIDE_THROTTLE=True,
    # чтобы не наступать на ручное управление пилота.
    if OVERRIDE_THROTTLE and launch_intensity > 0.0 and LAUNCH_THR_BOOST_PCT > 0.0:
        boost = base_thr * (LAUNCH_THR_BOOST_PCT / 100.0) * launch_intensity
        boosted = target_throttle + boost
        if boosted > THROTTLE_MAX_PWM:
            boosted = THROTTLE_MAX_PWM
        elif boosted < THROTTLE_MIN_PWM:
            boosted = THROTTLE_MIN_PWM
        target_throttle = int(round(boosted))

    # Индикация фазы launch в overlay (имеет приоритет над TRACKED/LOCK).
    if launch_phase in ("RAMP_UP", "HOLD", "RAMP_DOWN"):
        with state_lock:
            overlay_text = "LAUNCH"
            overlay_color = COLOR_CYAN

    with state_lock:
        app_state["dyn_throttle"] = target_throttle
        override_active = True
        global_roll_cmd = target_roll
        global_pitch_cmd = target_pitch
        global_yaw_cmd = target_yaw
        global_throttle_cmd = target_throttle

# =========================================================
# 9. DRAWING
# =========================================================
def draw_crosshair(frame):
    cv2.line(frame, (CENTER_X-12, CENTER_Y), (CENTER_X-4, CENTER_Y), CROSS_COLOR, 1)
    cv2.line(frame, (CENTER_X+4, CENTER_Y), (CENTER_X+12, CENTER_Y), CROSS_COLOR, 1)
    cv2.line(frame, (CENTER_X, CENTER_Y-12), (CENTER_X, CENTER_Y-4), CROSS_COLOR, 1)
    cv2.line(frame, (CENTER_X, CENTER_Y+4), (CENTER_X, CENTER_Y+12), CROSS_COLOR, 1)
    cv2.circle(frame, (CENTER_X, CENTER_Y), 1, CROSS_COLOR, -1)


def draw_corners(frame, box, color, thickness=2):
    x1, y1, x2, y2 = box
    bcx, bcy = (x1 + x2) // 2, (y1 + y2) // 2
    DIAMOND_R = 14
    pts = np.array([
        [bcx, bcy - DIAMOND_R],
        [bcx + DIAMOND_R, bcy],
        [bcx, bcy + DIAMOND_R],
        [bcx - DIAMOND_R, bcy],
    ], dtype=np.int32).reshape((-1, 1, 2))
    cv2.polylines(frame, [pts], True, COLOR_WHITE, 2, cv2.LINE_8)


def draw_magnifier(frame):
    if not MAG_ENABLED:
        return
    h, w = frame.shape[:2]
    src = max(MAG_SRC_MIN_SIZE, int(round(MAG_SIZE / MAG_ZOOM)))
    sx1 = max(0, CENTER_X - src // 2)
    sy1 = max(0, CENTER_Y - src // 2)
    sx2 = min(w, sx1 + src)
    sy2 = min(h, sy1 + src)
    sx1 = max(0, sx2 - src)
    sy1 = max(0, sy2 - src)
    roi = frame[sy1:sy2, sx1:sx2].copy()
    if roi.size == 0:
        return
    zoom = cv2.resize(roi, (MAG_SIZE, MAG_SIZE), interpolation=cv2.INTER_LINEAR)
    dx2 = w - MAG_MARGIN
    dy1 = MAG_MARGIN
    dx1 = dx2 - MAG_SIZE
    dy2 = dy1 + MAG_SIZE
    if dx1 >= 0 and dy2 <= h:
        frame[dy1:dy2, dx1:dx2] = zoom
        mx, my = (dx1 + dx2) // 2, (dy1 + dy2) // 2
        cv2.line(frame, (mx-4, my), (mx+4, my), COLOR_RED, 1)
        cv2.line(frame, (mx, my-4), (mx, my+4), COLOR_RED, 1)


def draw_overlay_on_frame(frame):
    draw_crosshair(frame)
    with state_lock:
        box = target_box_main
        vis = target_visible
        aux_for_mag = aux4_state
    if vis and box is not None:
        draw_corners(frame, box, COLOR_WHITE, 2)
    if MAG_ENABLED and ((not MAG_ONLY_WHEN_AUX) or aux_for_mag):
        draw_magnifier(frame)

# =========================================================
# 10. TRACKING CORE (без изменений)
# =========================================================
def process_locked_tracker(gray):
    global track_state, target_visible, target_controllable, overlay_text, overlay_color, target_box_main
    global lock_cx, lock_cy, lock_w, lock_h, template_gray, prev_gray, prev_pts
    global lost_frames, frame_index, last_match_score, last_flow_ok
    global fps_t0, fps_frames, fps_current
    global prev_aux_on, acq_wait_left, lock_sequence
    global auto_reacq_attempts

    frame_index += 1

    with state_lock:
        aux_on = aux4_state
        live_thr = app_state.get("rc_throttle", 1500)

    rising_aux = aux_on and not prev_aux_on
    falling_aux = (not aux_on) and prev_aux_on
    prev_aux_on = aux_on

    if not aux_on:
        if falling_aux or track_state != TRACK_STATE_IDLE:
            reset_tracking(to_acq=False)
        auto_reacq_attempts = 0
        with state_lock:
            global global_roll_cmd, global_pitch_cmd, global_yaw_cmd, global_throttle_cmd, override_active
            override_active = False
            global_roll_cmd = 1500.0
            global_pitch_cmd = 1500.0
            global_yaw_cmd = 1500.0
            global_throttle_cmd = live_thr
        return

    if rising_aux:
        reset_tracking(to_acq=True)
        acq_wait_left = ACQ_SETTLE_FRAMES
        lock_sequence += 1
        auto_reacq_attempts = 0

    if track_state == TRACK_STATE_IDLE:
        reset_tracking(to_acq=True)
        acq_wait_left = ACQ_SETTLE_FRAMES

    if acq_wait_left > 0:
        acq_wait_left -= 1
        with state_lock:
            track_state = TRACK_STATE_ACQ
            target_visible = False
            target_controllable = False
            target_box_main = None
            overlay_text = "ACQ"
            overlay_color = COLOR_WHITE
        update_control_from_target()
        return

    if track_state == TRACK_STATE_LOST and REQUIRE_AUX_TOGGLE_AFTER_LOST:
        # Авто-восстановление: в течение AUTO_REACQ_FRAMES после LOST
        # пробуем найти цель в расширенном окне с высоким порогом матча.
        # Если получилось — без снятия AUX возвращаемся в TRACKED.
        # Иначе остаёмся в LOST/TOGGLE и ждём явного действия оператора.
        if (AUTO_REACQ_ENABLED and template_gray is not None
                and lock_cx is not None and auto_reacq_attempts < AUTO_REACQ_FRAMES):
            auto_reacq_attempts += 1
            # Расширенный матч в окне AUTO_REACQ_SEARCH_MARGIN px вокруг последнего lock.
            # Используем flow_motion=AUTO_REACQ_SEARCH_MARGIN, чтобы template_match_locked
            # автоматически развернул search-окно до максимума.
            match_ok_r, mcx, mcy, score_r = template_match_locked(
                gray, lock_cx, lock_cy, flow_motion=float(AUTO_REACQ_SEARCH_MARGIN)
            )
            if match_ok_r and score_r >= AUTO_REACQ_MIN_SCORE:
                # Нашли с уверенностью — возвращаемся в TRACKED, переинициализируем flow.
                lock_cx = float(mcx)
                lock_cy = float(mcy)
                prev_gray = gray.copy()
                prev_pts = refresh_flow_points(gray, lock_cx, lock_cy, lock_w, lock_h)
                lost_frames = 0
                last_match_score = score_r
                last_flow_ok = False
                auto_reacq_attempts = 0
                box = lores_box_to_main(lock_cx, lock_cy, lock_w, lock_h)
                with state_lock:
                    track_state = TRACK_STATE_TRACKED
                    target_visible = True
                    target_controllable = True
                    target_box_main = box
                    overlay_text = "REACQ"
                    overlay_color = COLOR_RED
                update_control_from_target()
                return
            # Не нашли в этом кадре — продолжаем висеть в LOST, повторим в следующем.
            with state_lock:
                target_visible = False
                target_controllable = False
                target_box_main = None
                overlay_text = "REACQ?"
                overlay_color = COLOR_YELLOW
            update_control_from_target()
            return

        # Окно auto-reacq исчерпано или выключено — обычное поведение LOST/TOGGLE.
        with state_lock:
            target_visible = False
            target_controllable = False
            target_box_main = None
            overlay_text = "LOST/TOGGLE"
            overlay_color = COLOR_WHITE
        update_control_from_target()
        return

    if track_state == TRACK_STATE_ACQ or (track_state == TRACK_STATE_LOST and not REQUIRE_AUX_TOGGLE_AFTER_LOST):
        acq_cx, acq_cy, lw, lh, ok = estimate_initial_target(gray)
        if ok:
            lock_cx = float(acq_cx)
            lock_cy = float(acq_cy)
            lock_w = float(lw)
            lock_h = float(lh)
            template_gray = build_template(gray, lock_cx, lock_cy, lock_w, lock_h)
            prev_gray = gray.copy()
            prev_pts = refresh_flow_points(gray, lock_cx, lock_cy, lock_w, lock_h)
            lost_frames = 0
            last_match_score = 1.0
            last_flow_ok = False
            box = lores_box_to_main(lock_cx, lock_cy, lock_w, lock_h)
            with state_lock:
                track_state = TRACK_STATE_TRACKED
                target_visible = True
                target_controllable = True
                target_box_main = box
                overlay_text = "LOCK"
                overlay_color = COLOR_RED
            update_control_from_target()
        else:
            with state_lock:
                track_state = TRACK_STATE_ACQ
                target_visible = False
                target_controllable = False
                target_box_main = None
                overlay_text = "ACQ"
                overlay_color = COLOR_WHITE
            update_control_from_target()
        return

    if lock_cx is None or lock_w is None or template_gray is None:
        reset_tracking(to_acq=True)
        return

    flow_ok, pred_cx, pred_cy = flow_predict(prev_gray, gray, prev_pts, lock_cx, lock_cy)
    # Модуль flow-предсказанного смещения цели за кадр — используется
    # template_match_locked для адаптивного выбора search-окна.
    flow_motion = math.hypot(pred_cx - lock_cx, pred_cy - lock_cy) if flow_ok else 0.0
    match_ok, match_cx, match_cy, score = template_match_locked(gray, pred_cx, pred_cy, flow_motion)
    last_match_score = score
    last_flow_ok = flow_ok

    new_cx, new_cy = lock_cx, lock_cy
    tracked_ok = False

    if flow_ok and match_ok:
        dist_fm = math.hypot(match_cx - pred_cx, match_cy - pred_cy)
        if score >= MATCH_GOOD_SCORE and dist_fm <= MAX_LOCK_STEP:
            new_cx = 0.78 * pred_cx + 0.22 * match_cx
            new_cy = 0.78 * pred_cy + 0.22 * match_cy
            tracked_ok = True
        elif dist_fm <= MAX_LOCK_STEP * 0.55:
            new_cx = pred_cx
            new_cy = pred_cy
            tracked_ok = True
    elif flow_ok:
        new_cx = pred_cx
        new_cy = pred_cy
        tracked_ok = True
    elif match_ok and score >= MATCH_GOOD_SCORE:
        new_cx = match_cx
        new_cy = match_cy
        tracked_ok = True

    if tracked_ok:
        step = math.hypot(new_cx - lock_cx, new_cy - lock_cy)
        if step > MAX_LOCK_STEP:
            k = MAX_LOCK_STEP / max(step, 1e-6)
            new_cx = lock_cx + (new_cx - lock_cx) * k
            new_cy = lock_cy + (new_cy - lock_cy) * k

        lock_cx = float(clamp(new_cx, 0, LORES_W - 1))
        lock_cy = float(clamp(new_cy, 0, LORES_H - 1))
        lost_frames = 0
        auto_reacq_attempts = 0   # успешно идём — сбрасываем счётчик восстановлений

        if frame_index % FLOW_REFRESH_EVERY == 0:
            prev_pts = refresh_flow_points(gray, lock_cx, lock_cy, lock_w, lock_h)
        prev_gray = gray.copy()

        if not FREEZE_TEMPLATE and match_ok and score >= 0.60:
            cur_tmpl = build_template(gray, lock_cx, lock_cy, lock_w, lock_h)
            if cur_tmpl.shape == template_gray.shape:
                template_gray = cv2.addWeighted(template_gray, 1 - TEMPLATE_UPDATE_ALPHA,
                                                cur_tmpl, TEMPLATE_UPDATE_ALPHA, 0)

        # Периодическая адаптация размера коробки. Раз в SIZE_ADAPT_EVERY_FRAMES
        # при уверенном матче запрашиваем заново связную компоненту под текущим
        # центром лока и сдвигаем lock_w/lock_h в её сторону. Решает «дрейф
        # зацепом за край» при сближении — коробка растёт вместе с целью.
        if (SIZE_ADAPT_ENABLED and frame_index % SIZE_ADAPT_EVERY_FRAMES == 0
                and match_ok and score >= SIZE_ADAPT_MIN_SCORE):
            est_w, est_h = estimate_size_at_position(gray, lock_cx, lock_cy)
            # Sanity: оцениваем только если в разумных пределах.
            if (SIZE_ADAPT_MIN_W <= est_w <= SIZE_ADAPT_MAX_W
                    and SIZE_ADAPT_MIN_W <= est_h <= SIZE_ADAPT_MAX_W):
                lock_w = lock_w * (1.0 - SIZE_ADAPT_ALPHA) + est_w * SIZE_ADAPT_ALPHA
                lock_h = lock_h * (1.0 - SIZE_ADAPT_ALPHA) + est_h * SIZE_ADAPT_ALPHA

        box = lores_box_to_main(lock_cx, lock_cy, lock_w, lock_h)
        with state_lock:
            track_state = TRACK_STATE_TRACKED
            target_visible = True
            target_controllable = True
            target_box_main = box
            overlay_text = "TRACKED"
            overlay_color = COLOR_RED
        update_control_from_target()
    else:
        lost_frames += 1
        prev_gray = gray.copy()
        if lost_frames <= HOLD_FRAMES:
            box = lores_box_to_main(lock_cx, lock_cy, lock_w, lock_h)
            with state_lock:
                track_state = TRACK_STATE_HOLD
                target_visible = True
                target_controllable = False
                target_box_main = box
                overlay_text = "HOLD"
                overlay_color = COLOR_YELLOW
            update_control_from_target()
        elif lost_frames <= LOST_LIMIT:
            with state_lock:
                track_state = TRACK_STATE_LOST
                target_visible = False
                target_controllable = False
                target_box_main = None
                overlay_text = "LOST"
                overlay_color = COLOR_WHITE
            update_control_from_target()
        else:
            if REQUIRE_AUX_TOGGLE_AFTER_LOST:
                with state_lock:
                    track_state = TRACK_STATE_LOST
                    target_visible = False
                    target_controllable = False
                    target_box_main = None
                    overlay_text = "LOST/TOGGLE"
                    overlay_color = COLOR_WHITE
                update_control_from_target()
            else:
                reset_tracking(to_acq=True)
                update_control_from_target()


def print_debug_once_per_second():
    """Раз в DEBUG_PERIOD секунд:
       FPS, state, override, RC со стика (RPYT), наш CMD, что РЕАЛЬНО ушло в FC,
       motor outputs (если Betaflight отдаёт MSP_MOTOR).
    """
    global fps_t0, fps_frames, fps_current
    fps_frames += 1
    now = time.monotonic()
    if now - fps_t0 < DEBUG_PERIOD:
        return

    fps_current = fps_frames / max(now - fps_t0, 1e-6)
    fps_frames = 0
    fps_t0 = now

    if not DEBUG_PRINT:
        return

    with state_lock:
        ch = list(app_state.get("rc_channels", [1500] * 8))
        motors = list(app_state.get("motors", []))
        ov = override_active
        st = track_state
        r_out = int(global_roll_cmd)
        p_out = int(global_pitch_cmd)
        y_out = int(global_yaw_cmd)
        t_out = int(global_throttle_cmd)
        sent = list(app_state.get("last_sent_channels", [1500] * 8))

    c0 = int(ch[0]) if len(ch) > 0 else 1500
    c1 = int(ch[1]) if len(ch) > 1 else 1500
    c2 = int(ch[2]) if len(ch) > 2 else 1500
    c3 = int(ch[3]) if len(ch) > 3 else 1000
    s0 = int(sent[0]) if len(sent) > 0 else 1500
    s1 = int(sent[1]) if len(sent) > 1 else 1500
    s2 = int(sent[2]) if len(sent) > 2 else 1500
    s3 = int(sent[3]) if len(sent) > 3 else 1000

    mot_s = " ".join([f"M{i+1}:{int(v)}" for i, v in enumerate(motors[:4])]) if motors else "M:NA"

    print(
        f"FPS:{fps_current:5.1f} | {st:<7} | OV:{ov} | "
        f"RPYT in: R{c0:4d} P{c1:4d} Y{c2:4d} T{c3:4d} | "
        f"CMD R{r_out:4d} P{p_out:4d} Y{y_out:4d} T{t_out:4d} | "
        f"SENT AETR R{s0:4d} P{s1:4d} T{s2:4d} Y{s3:4d} | "
        f"{mot_s}",
        flush=True
    )

# =========================================================
# 11. FAST IDLE + CAMERA CALLBACK
# =========================================================
def fast_idle_update():
    global prev_aux_on, track_state, override_active
    global global_roll_cmd, global_pitch_cmd, global_yaw_cmd, global_throttle_cmd

    with state_lock:
        live_thr = app_state.get("rc_throttle", 1500)

    if prev_aux_on or track_state != TRACK_STATE_IDLE:
        reset_tracking(to_acq=False)
    prev_aux_on = False

    with state_lock:
        app_state["dyn_throttle"] = live_thr
        override_active = False
        global_roll_cmd = 1500.0
        global_pitch_cmd = 1500.0
        global_yaw_cmd = 1500.0
        global_throttle_cmd = live_thr


def camera_callback(request):
    try:
        with state_lock:
            aux_snapshot = aux4_state

        if not aux_snapshot:
            fast_idle_update()
            print_debug_once_per_second()
            with MappedArray(request, "main") as mm:
                draw_overlay_on_frame(mm.array)
            return

        with MappedArray(request, "lores") as lm:
            yuv = lm.array
            gray = yuv[:LORES_H, :LORES_W].copy()

        process_locked_tracker(gray)
        print_debug_once_per_second()

        with MappedArray(request, "main") as mm:
            draw_overlay_on_frame(mm.array)
    except Exception:
        pass

# =========================================================
# 12. START
# =========================================================
def main():
    global picam2
    picam2 = Picamera2()

    sensor_mode = None
    preferred = [
        ((1640, 1232), 8),
        ((1640, 1232), 10),
        ((3280, 2464), 8),
        ((3280, 2464), 10),
        ((1920, 1080), 8),
        ((1920, 1080), 10),
        ((640, 480), 8),
        ((640, 480), 10),
    ]
    for target_size, target_bit in preferred:
        for m in picam2.sensor_modes:
            if tuple(m.get("size", ())) == target_size and int(m.get("bit_depth", 0)) == target_bit:
                sensor_mode = m
                break
        if sensor_mode is not None:
            break
    if sensor_mode is None:
        sensor_mode = picam2.sensor_modes[0] if picam2.sensor_modes else None

    kwargs = {
        "main": {"size": (MAIN_W, MAIN_H), "format": "XRGB8888"},
        "lores": {"size": (LORES_W, LORES_H), "format": "YUV420"},
        "buffer_count": 4,
        "queue": False,
        "transform": Transform(hflip=CAMERA_ROTATE_180, vflip=CAMERA_ROTATE_180),
    }
    if sensor_mode is not None:
        kwargs["sensor"] = {
            "output_size": sensor_mode["size"],
            "bit_depth": sensor_mode["bit_depth"],
        }

    config = picam2.create_preview_configuration(**kwargs)
    picam2.configure(config)
    picam2.pre_callback = camera_callback
    picam2.start_preview(Preview.DRM, x=PREVIEW_X, y=PREVIEW_Y,
                         width=PREVIEW_W, height=PREVIEW_H)
    picam2.start()

    try:
        picam2.set_controls({"FrameDurationLimits": (16666, 33333)})
    except Exception:
        pass

    time.sleep(0.5)
    try:
        md = picam2.capture_metadata()
        exp = int(md.get("ExposureTime", 8000))
        exp = min(exp, 33000)
        ctrl = {
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": exp,
            "AnalogueGain": float(md.get("AnalogueGain", 1.0)),
        }
        colour = md.get("ColourGains", None)
        if colour is not None:
            ctrl["ColourGains"] = tuple(colour)
        picam2.set_controls(ctrl)
    except Exception:
        pass

    io_thread = threading.Thread(target=fc_io_loop, daemon=True)
    io_thread.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        io_thread_stop.set()
        try:
            if io_thread.is_alive():
                io_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            picam2.stop()
        except Exception:
            pass
        try:
            if fc is not None:
                fc.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
