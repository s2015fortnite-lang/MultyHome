"""Получение устройств и отправка команд в Яндекс Дом."""

import requests


class HomeError(Exception):
    """Ошибка, которую можно безопасно показать пользователю."""


def supports_on_off(device):
    for capability in device.get("capabilities", []):
        if capability.get("type") == "devices.capabilities.on_off":
            return True
    return False


def describe_device(device):
    lines = [device.get("name", "Без названия")]
    lines.append("Тип: " + device.get("type", "неизвестен"))
    for capability in device.get("capabilities", []):
        state = capability.get("state", {})
        if "value" in state:
            value = state["value"]
            if state.get("instance") == "on":
                value = "включено" if value else "выключено"
            lines.append(f"{state.get('instance', 'состояние')}: {value}")
    for prop in device.get("properties", []):
        state = prop.get("state", {})
        if "value" in state:
            lines.append(f"{state.get('instance', 'показатель')}: {state['value']}")
    if device.get("error_code"):
        lines.append("Устройство недоступно: " + str(device["error_code"]))
    return "\n".join(lines)


class YandexHome:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, data=None):
        try:
            response = requests.request(
                method,
                "https://api.iot.yandex.net/v1.0" + path,
                headers={"Authorization": "Bearer " + self.token},
                json=data,
                timeout=20,
            )
        except requests.RequestException:
            raise HomeError("Нет связи с Яндекс Домом. Проверьте подключение.") from None
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
        data = {
            "devices": [{
                "id": device_id,
                "actions": [{
                    "type": "devices.capabilities.on_off",
                    "state": {"instance": "on", "value": enabled},
                }],
            }],
        }
        result = self.request("POST", "/devices/actions", data)
        for device in result.get("devices", []):
            if device.get("id") != device_id:
                continue
            for capability in device.get("capabilities", []):
                if capability.get("type") == "devices.capabilities.on_off":
                    state = capability.get("state", {})
                    action_result = state.get("action_result", {})
                    if state.get("instance") == "on" and action_result.get("status") == "DONE":
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

    def list_devices(self):
        return self.devices

    def get_device(self, device_id):
        for device in self.devices:
            if device["id"] == device_id:
                return device
        raise HomeError("Устройство не найдено.")

    def switch(self, device_id, enabled):
        device = self.get_device(device_id)
        device["capabilities"][0]["state"]["value"] = enabled
