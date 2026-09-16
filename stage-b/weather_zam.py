#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weather_zam.py — общий модуль этапа B (roadmap B2/B3):
- classify_trend(d_mmhg): барическая тенденция по ТЗ §4 (пороги ±0.5/±1.5 мм рт.ст.);
- zambretti(p_rel_mmhg, trend_mmhg_3h, month): канонический Zambretti (Beteljuice-вариант,
  северное полушарие; вход — давление ПРИВЕДЁННОЕ к уровню моря, мм рт.ст.) -> (letter, "en — ru");
- sager_day(...): упрощённый Sager (ТЗ §7.3: облачность аппроксимируется через solar),
  только день (вызывающий проверяет локальный час 10..15);
- SOLAR_POT: типичный максимум освещённости при ясном небе, ~61°N (Видлица).
stdlib-only."""

MMHG_TO_HPA = 1.33322387415

ZAM = [  # (letter, en, ru)
    ("A", "Settled fine", "Устойчивое ясно"),
    ("B", "Fine weather", "Хорошая погода"),
    ("C", "Becoming fine", "К прояснению"),
    ("D", "Rather cloudy, fog patches", "Облачно, местами туман"),
    ("E", "Showery, becoming less settled", "Ливни, к неустойчивости"),
    ("F", "Settled fine", "Устойчивое ясно"),
    ("G", "Fairly fine, improving", "Довольно ясно, улучшение"),
    ("H", "Fairly fine, possibly showers early", "Довольно ясно, возможны ливни рано"),
    ("I", "Fairly fine, showery later", "Довольно ясно, позже ливни"),
    ("J", "Showery, improving", "Ливни, улучшение"),
    ("K", "Showery, becoming more settled", "Ливни, к успокоению"),
    ("L", "Changeable, mending", "Переменно, налаживается"),
    ("M", "Fairly fine, showers likely", "Довольно ясно, вероятны ливни"),
    ("N", "Rather unsettled", "Неустойчиво"),
    ("O", "Unsettled, occasional rain", "Неустойчиво, временами дождь"),
    ("P", "Changeable, some rain", "Переменно, дожди"),
    ("Q", "Unsettled, short fine spells", "Неустойчиво, короткие прояснения"),
    ("R", "Changeable, rain", "Переменно, дождь"),
    ("S", "Rather unsettled, clearing later", "Неустойчиво, позже прояснится"),
    ("T", "Unsettled, rain likely", "Неустойчиво, вероятен дождь"),
    ("U", "Rain at times, rather unsettled", "Временами дождь, неустойчиво"),
    ("V", "Rain at frequent intervals", "Частые дожди"),
    ("W", "Rain, becoming more unsettled", "Дождь, к усилению неустойчивости"),
    ("X", "Stormy, rain possible", "Штормово, возможен дождь"),
    ("Y", "Stormy, much rain", "Штормово, сильные дожди"),
    ("Z", "Stormy, much rain, squally", "Штормово, сильный дождь, шквалы"),
]

# Диапазоны (гПа): (lower..hi] -> индекс ZAM (Beteljuice-вариант); lower = следующий hi
_RISING = [(1050, 0), (1030, 1), (1020, 2), (1010, 6), (1000, 10),
           (990, 12), (980, 16), (970, 19), (-1, 24)]
_FALLING = [(1050, 1), (1022, 4), (1013, 9), (1008, 11), (1003, 12),
            (998, 14), (993, 16), (988, 19), (983, 20), (978, 21),
            (973, 22), (968, 23), (-1, 24)]
_STEADY = [(1050, 0), (1023, 1), (1015, 7), (1010, 11), (1003, 12),
           (996, 14), (988, 17), (980, 19), (-1, 20)]


def _lookup(table, p):
    """Первый диапазон (lower..hi], содержащий p."""
    for i, (hi, idx) in enumerate(table):
        lo = table[i + 1][0] if i + 1 < len(table) else -1e9
        if lo < p <= hi:
            letter, en, ru = ZAM[idx]
            return letter, f"{en} — {ru}"
    letter, en, ru = ZAM[24]
    return letter, f"{en} — {ru}"


def classify_trend(d_mmhg):
    """Классификация барической тенденции (ТЗ §4), d — мм рт.ст. за 3 ч."""
    if d_mmhg is None:
        return "unknown"
    if d_mmhg > 1.5:
        return "rapid_rise"
    if d_mmhg > 0.5:
        return "rising"
    if d_mmhg < -1.5:
        return "rapid_fall"
    if d_mmhg < -0.5:
        return "falling"
    return "steady"


def zambretti(p_rel_mmhg, trend_mmhg_3h, month):
    """-> (letter, "en — ru"). month 1..12 (локальный). None на входе -> (None, None)."""
    if p_rel_mmhg is None or trend_mmhg_3h is None:
        return None, None
    p = float(p_rel_mmhg) * MMHG_TO_HPA
    d = float(trend_mmhg_3h)
    if d > 0.5:                                   # rising
        if p < 1000:
            p += 12
        if 4 <= month <= 9:
            p += 8
        return _lookup(_RISING, p)
    if d < -0.5:                                  # falling
        if p > 1020:
            p -= 12
        if 4 <= month <= 9:
            p -= 8
        return _lookup(_FALLING, p)
    return _lookup(_STEADY, p)                    # steady


# Типичный максимум освещённости при ясном небе (Вт/м²) для ~61°N, грубая оценка
SOLAR_POT = {1: 60, 2: 130, 3: 250, 4: 380, 5: 480, 6: 540,
             7: 520, 8: 430, 9: 300, 10: 170, 11: 80, 12: 45}


def sager_day(p_rel_mmhg, trend_mmhg_3h, wind_ms, cloud_frac):
    """Упрощённый Sager (ТЗ §7.3). cloud_frac: 1=ясно, 0=сплошная облачность.
    -> (text_ru, applicable). Вызывающий гарантирует: локальный час 10..15."""
    if p_rel_mmhg is None or trend_mmhg_3h is None or cloud_frac is None:
        return None, False
    band = "high" if p_rel_mmhg >= 767 else ("low" if p_rel_mmhg < 745 else "mid")
    cloud = "clear" if cloud_frac >= 0.65 else ("overcast" if cloud_frac < 0.3 else "partly")
    windy = (wind_ms or 0) >= 8
    rapid = abs(trend_mmhg_3h) > 1.5
    if band == "high" and cloud == "clear" and trend_mmhg_3h >= -0.5:
        t = "Ясно и стабильно; существенных изменений не ожидается"
    elif band == "high" and cloud == "clear":
        t = "Ясно; к вечеру возможна переменная облачность"
    elif band == "high" and cloud == "partly":
        t = "Переменная облачность, без осадков"
    elif band == "mid" and cloud in ("clear", "partly") and trend_mmhg_3h >= -0.5:
        t = "Малооблачно, преимущественно сухо"
    elif band == "mid" and cloud == "overcast":
        t = "Облачно, без существенных осадков; возможна морось"
    elif band == "mid" and (rapid or windy):
        t = "Облачность растёт, возможен дождь; ветер усилится"
    elif band == "low" and trend_mmhg_3h < -0.5:
        t = "Неустойчиво: вероятны осадки в ближайшие часы"
    elif band == "low":
        t = "Пасмурно, свежо; местами возможны осадки"
    else:
        t = "Переменно, без резких изменений"
    return t, True
