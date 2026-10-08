"""Получение устройств и отправка команд в Яндекс Дом."""

import os
import time

import requests


class HomeError(Exception):
    """Ошибка, которую можно безопасно показать пользователю."""


def supports_on_off(device):
    for capability in device.get("capabilities") or []:
        if capability.get("type") == "devices.capabilities.on_off":
            return True
    return False


def describe_device(device):
    lines = [device.get("name", "Без названия")]
    lines.append("Тип: " + device.get("type", "неизвестен"))
    has_readings = False
    for capability in device.get("capabilities") or []:
        # Яндекс может вернуть state: null, если состояние недоступно.
        state = capability.get("state") or {}
        if "value" in state:
            has_readings = True
            value = state["value"]
            if value is None:
                value = "неизвестно"
            elif state.get("instance") == "on":
                value = "включено" if value else "выключено"
            lines.append(f"{state.get('instance', 'состояние')}: {value}")
    for prop in device.get("properties") or []:
        state = prop.get("state") or {}
        if "value" in state:
            has_readings = True
            value = state["value"] if state["value"] is not None else "неизвестно"
            lines.append(f"{state.get('instance', 'показатель')}: {value}")
    if not has_readings:
        lines.append("Яндекс не передал значения состояния этого устройства.")
    if device.get("error_code"):
        lines.append("Устройство недоступно: " + str(device["error_code"]))
    return "\n".join(lines)


class YandexHome:
    def __init__(self, token):
        self.token = token
        # У каждого дома своё соединение. Заголовок с токеном передаём явно.
        self.session = requests.Session()

    def request(self, method, path, data=None):
        started = time.perf_counter()
        try:
            response = self.session.request(
                method,
                "https://api.iot.yandex.net/v1.0" + path,
                headers={"Authorization": "Bearer " + self.token},
                json=data,
                timeout=20,
            )
        except requests.RequestException:
            raise HomeError("Нет связи с Яндекс Домом. Проверьте подключение.") from None
        finally:
            if os.getenv("DIAGNOSTICS") == "1":
                # Не печатаем токен, ID устройства и ответ Яндекса.
                print(f"Яндекс {method}: {time.perf_counter() - started:.2f} с", flush=True)
        if response.status_code in (401, 403):
            raise HomeError("Яндекс отклонил доступ. Проверьте токен и права iot:view, iot:control.")
        if response.status_code != 200:
            raise HomeError(f"Ошибка Яндекс API: HTTP {response.status_code}.")
        try:
            result = response.json()
        except ValueError:
            raise HomeError("Яндекс вернул некорректный ответ.") from None
        if result.get("status") != "ok":
            raise HomeError("Яндекс не смог выполнить запрос.")
        return result

    def list_devices(self):
        return self.request("GET", "/user/info").get("devices", [])

    def get_device(self, device_id):
        return self.request("GET", "/devices/" + device_id)

    def switch(self, device_id, enabled):
        self.set_capability(device_id, "devices.capabilities.on_off", "on", enabled)

    def set_capability(self, device_id, capability_type, instance, value):
        state = {"instance": instance, "value": value}
        if capability_type == "devices.capabilities.range":
            state["relative"] = False
        data = {
            "devices": [{
                "id": device_id,
                "actions": [{
                    "type": capability_type,
                    "state": state,
                }],
            }],
        }
        result = self.request("POST", "/devices/actions", data)
        for device in result.get("devices") or []:
            if device.get("id") != device_id:
                continue
            for capability in device.get("capabilities") or []:
                if capability.get("type") == capability_type:
                    state = capability.get("state") or {}
                    action_result = state.get("action_result") or {}
                    if state.get("instance") == instance and action_result.get("status") == "DONE":
                        return
        raise HomeError("Устройство не подтвердило выполнение команды. Проверьте его состояние.")


class DemoHome:
    """Виртуальный дом. Команды не затрагивают настоящие устройства."""

    def __init__(self):
        self.devices = []
        for device_id, name, device_type in [
            ("demo-lamp", "Учебная лампа", "devices.types.light"),
            ("demo-socket", "Учебная розетка", "devices.types.socket"),
        ]:
            self.devices.append({
                "id": device_id,
                "name": name,
                "type": device_type,
                "capabilities": [{
                    "type": "devices.capabilities.on_off",
                    "state": {"instance": "on", "value": False},
                }],
            })
        self.devices[0]["capabilities"].extend([
            {"type": "devices.capabilities.range", "parameters": {
                "instance": "brightness", "unit": "unit.percent",
                "range": {"min": 1, "max": 100, "precision": 1},
            }, "state": {"instance": "brightness", "value": 50}},
            {"type": "devices.capabilities.color_setting", "parameters": {
                "color_model": "rgb", "temperature_k": {"min": 2700, "max": 6500},
                "color_scene": {"scenes": [{"id": "reading"}, {"id": "party"}]},
            }, "state": {"instance": "rgb", "value": 16777215}},
        ])
        self.devices.append({"id": "demo-climate", "name": "Учебный кондиционер",
            "type": "devices.types.thermostat.ac", "capabilities": [
                {"type": "devices.capabilities.on_off", "state": {"instance": "on", "value": False}},
                {"type": "devices.capabilities.range", "parameters": {
                    "instance": "temperature", "unit": "unit.temperature.celsius",
                    "range": {"min": 16, "max": 30, "precision": 0.5},
                }, "state": {"instance": "temperature", "value": 22}},
                {"type": "devices.capabilities.mode", "parameters": {
                    "instance": "thermostat", "modes": [{"value": "auto"}, {"value": "cool"}, {"value": "heat"}],
                }, "state": {"instance": "thermostat", "value": "auto"}},
            ]})
        self.devices.append({"id": "demo-speaker", "name": "Учебная колонка",
            "type": "devices.types.smart_speaker", "capabilities": [
                {"type": "devices.capabilities.range", "parameters": {
                    "instance": "volume", "range": {"min": 0, "max": 100, "precision": 1},
                }, "state": {"instance": "volume", "value": 30}},
            ]})

    def list_devices(self):
        return self.devices

    def get_device(self, device_id):
        for device in self.devices:
            if device["id"] == device_id:
                return device
        raise HomeError("Устройство не найдено.")

    def switch(self, device_id, enabled):
        self.set_capability(device_id, "devices.capabilities.on_off", "on", enabled)

    def set_capability(self, device_id, capability_type, instance, value):
        device = self.get_device(device_id)
        for capability in device.get("capabilities") or []:
            if capability.get("type") != capability_type:
                continue
            if capability_type in ("devices.capabilities.range", "devices.capabilities.mode"):
                if (capability.get("parameters") or {}).get("instance") != instance:
                    continue
            capability["state"] = {"instance": instance, "value": value}
            return
        raise HomeError("Устройство не поддерживает эту возможность.")
