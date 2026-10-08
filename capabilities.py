"""Читаем возможности из ответа Яндекса и проверяем команды пользователя."""

import colorsys
import re
from decimal import Decimal, InvalidOperation

from yandex_home import HomeError


ON_OFF = "devices.capabilities.on_off"
RANGE = "devices.capabilities.range"
MODE = "devices.capabilities.mode"
COLOR = "devices.capabilities.color_setting"


def find_capability(device, capability_type, instance=None):
    for capability in device.get("capabilities") or []:
        if capability.get("type") != capability_type:
            continue
        parameters = capability.get("parameters") or {}
        if instance is None or parameters.get("instance") == instance:
            return capability
    raise HomeError("Устройство не поддерживает эту настройку. Посмотрите /capabilities НОМЕР.")


def numeric_value(text):
    try:
        value = Decimal(str(text).replace(",", "."))
    except InvalidOperation:
        raise HomeError("Введите число, например 50 или 22.5.") from None
    if not value.is_finite():
        raise HomeError("Введите обычное конечное число в допустимом диапазоне.")
    if value.as_tuple().exponent < -9 or value.as_tuple().exponent > 9 or abs(value) > Decimal("1000000000"):
        raise HomeError("Введите обычное конечное число в допустимом диапазоне.")
    return value


def range_limits(parameters):
    # Эти ограничения заданы протоколом Яндекса для процентных функций.
    limits = {"precision": 1}
    if parameters.get("instance") in ("brightness", "humidity", "open"):
        limits.update({"min": 0, "max": 100})
    limits.update(parameters.get("range") or {})
    return limits


def range_value(capability, text):
    parameters = capability.get("parameters") or {}
    if parameters.get("random_access") is False:
        raise HomeError("Устройство поддерживает только изменение на шаг. Абсолютное значение задать нельзя.")
    limits = range_limits(parameters)
    value = numeric_value(text)
    minimum = numeric_value(limits["min"]) if limits.get("min") is not None else None
    maximum = numeric_value(limits["max"]) if limits.get("max") is not None else None
    if minimum is not None and value < minimum:
        raise HomeError(f"Значение должно быть не меньше {minimum}.")
    if maximum is not None and value > maximum:
        raise HomeError(f"Значение должно быть не больше {maximum}.")
    # Decimal проверяет дробный шаг без погрешности обычных float.
    step = numeric_value(limits.get("precision", 1))
    if step <= 0:
        raise HomeError("Яндекс передал некорректный шаг настройки.")
    base = minimum if minimum is not None else Decimal(0)
    if (value - base) % step != 0:
        raise HomeError(f"Допустимый шаг: {step}. Отсчёт от {base}.")
    return int(value) if value == value.to_integral_value() else float(value)


def prepare_action(device, command, arguments):
    """Результат: тип возможности, её instance и проверенное значение."""
    if command in ("/on", "/off"):
        find_capability(device, ON_OFF)
        return ON_OFF, "on", command == "/on"
    if command in ("/brightness", "/temperature", "/volume", "/range"):
        if command == "/range":
            instance, text = arguments
        else:
            instance = {"/brightness": "brightness", "/temperature": "temperature", "/volume": "volume"}[command]
            text = arguments[0]
        capability = find_capability(device, RANGE, instance)
        return RANGE, instance, range_value(capability, text)
    if command == "/mode":
        instance, value = arguments
        capability = find_capability(device, MODE, instance)
        modes = (capability.get("parameters") or {}).get("modes") or []
        allowed = [mode.get("value") for mode in modes if mode.get("value")]
        if value not in allowed:
            raise HomeError("Допустимые режимы: " + (", ".join(allowed) or "Яндекс не передал список"))
        return MODE, instance, value
    capability = find_capability(device, COLOR)
    parameters = capability.get("parameters") or {}
    text = arguments[0]
    if command == "/color":
        model = parameters.get("color_model")
        if model not in ("rgb", "hsv"):
            raise HomeError("Устройство не поддерживает произвольный цвет.")
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", text):
            raise HomeError("Цвет задаётся шестью HEX-цифрами. Например: /color 1 FF0000")
        rgb = int(text.lstrip("#"), 16)
        if model == "rgb":
            return COLOR, "rgb", rgb
        red = ((rgb >> 16) & 255) / 255
        green = ((rgb >> 8) & 255) / 255
        blue = (rgb & 255) / 255
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        return COLOR, "hsv", {"h": round(hue * 360), "s": round(saturation * 100), "v": round(value * 100)}
    if command == "/white":
        if not isinstance(parameters.get("temperature_k"), dict):
            raise HomeError("Устройство не поддерживает температуру белого света.")
        limits = parameters.get("temperature_k") or {}
        value = numeric_value(text)
        minimum = limits.get("min", 2000)
        maximum = limits.get("max", 9000)
        if value != value.to_integral_value() or not minimum <= value <= maximum:
            raise HomeError(f"Введите целую температуру света от {minimum} до {maximum} К.")
        return COLOR, "temperature_k", int(value)
    if command == "/scene":
        scenes = (parameters.get("color_scene") or {}).get("scenes") or []
        allowed = [scene.get("id") for scene in scenes if scene.get("id")]
        if text not in allowed:
            raise HomeError("Допустимые сцены освещения: " + (", ".join(allowed) or "нет"))
        return COLOR, "scene", text
    raise HomeError("Неизвестная команда управления.")


def describe_capabilities(device, number):
    lines = [device.get("name", "Без названия") + ": доступные команды"]
    for capability in device.get("capabilities") or []:
        capability_type = capability.get("type")
        parameters = capability.get("parameters") or {}
        instance = parameters.get("instance")
        if capability_type == ON_OFF:
            lines.append(f"Включение: /on {number}; выключение: /off {number}")
        elif capability_type == RANGE and instance:
            if parameters.get("random_access") is False:
                lines.append(f"{instance}: только относительное изменение; пока не реализовано.")
                continue
            limits = range_limits(parameters)
            minimum = limits.get("min", "не указан")
            maximum = limits.get("max", "не указан")
            unit = parameters.get("unit", "")
            unit = {"unit.percent": "%", "unit.temperature.celsius": "°C", "unit.temperature.kelvin": "К"}.get(unit, unit)
            lines.append(f"{instance}: минимум {minimum}, максимум {maximum}, шаг {limits.get('precision', 1)} {unit}".strip())
            command = {"brightness": "/brightness", "temperature": "/temperature", "volume": "/volume"}.get(instance)
            if command:
                lines.append(f"{command} {number} ЗНАЧЕНИЕ")
            else:
                lines.append(f"/range {number} {instance} ЗНАЧЕНИЕ")
        elif capability_type == MODE and instance:
            values = [mode["value"] for mode in parameters.get("modes") or [] if mode.get("value")]
            if values:
                lines.append(f"Режим {instance}: " + ", ".join(values))
                lines.append(f"/mode {number} {instance} РЕЖИМ")
        elif capability_type == COLOR:
            if parameters.get("color_model") in ("rgb", "hsv"):
                lines.append(f"Цвет: /color {number} HEX (FF0000 — красный, 00FF00 — зелёный, 0000FF — синий)")
            if isinstance(parameters.get("temperature_k"), dict):
                limits = parameters.get("temperature_k") or {}
                lines.append(f"Белый свет: {limits.get('min', 2000)}–{limits.get('max', 9000)} К; /white {number} КЕЛЬВИНЫ")
            scenes = (parameters.get("color_scene") or {}).get("scenes") or []
            values = [scene["id"] for scene in scenes if scene.get("id")]
            if values:
                lines.append("Сцены освещения: " + ", ".join(values))
                lines.append(f"/scene {number} СЦЕНА")
        else:
            lines.append(f"{capability_type}: управление пока не реализовано.")
    if len(lines) == 1:
        lines.append("Яндекс не передал поддерживаемые возможности управления.")
    return "\n".join(lines)
