"""Локально привязать браузерную сессию Станции к Telegram-пользователю."""

import argparse
import getpass
import json
from pathlib import Path

from station_api import StationAPI, StationStore
from yandex_home import HomeError, YandexHome


def bind_station(user_id, cookie, public_home, store):
    public_devices = public_home.list_devices()
    # Привязываем только устройства, уже доступные этому пользователю через OAuth.
    allowed_ids = {device["id"] for device in public_devices if device.get("id") and any(
        cap.get("type") == "devices.capabilities.equalizer"
        for cap in device.get("capabilities") or []
    )}
    station = StationAPI(cookie)
    native_ids = {device["id"] for device in station.list_devices() if device.get("id")}
    matching_ids = sorted(allowed_ids & native_ids)
    if not matching_ids:
        raise HomeError("В этой браузерной сессии нет Станции с эквалайзером из дома выбранного Telegram-пользователя. Подключение не сохранено.")
    station.device_ids = set(matching_ids)
    # Проверяем чтение конфигурации и CSRF, не изменяя настройки.
    station.get_config(matching_ids[0])
    station.load_csrf()
    store.data[str(user_id)] = {"cookie": cookie, "device_ids": matching_ids}
    store.save()
    return len(matching_ids)


def main():
    parser = argparse.ArgumentParser(description="Локальное подключение к API Станции")
    parser.add_argument("--user-id", required=True, type=int, help="Ваш Telegram ID из /id")
    args = parser.parse_args()
    if args.user_id <= 0:
        raise HomeError("Telegram ID должен быть положительным числом.")
    path = Path(__file__).with_name("users.json")
    try:
        tokens = json.loads(path.read_text())
        token = tokens.get(str(args.user_id))
    except (OSError, ValueError, AttributeError):
        raise HomeError("Сначала подключите свой дом через /connect и /code. Файл users.json не изменён.") from None
    if not isinstance(token, str) or not token:
        raise HomeError("Для этого Telegram ID нет подключённого Яндекс Дома. Выполните /connect и /code.")
    print("Вставьте значение Cookie из авторизованного запроса к yandex.ru/quasar.")
    print("Это секрет сессии: не отправляйте его в чат и не записывайте в команды терминала.")
    cookie = getpass.getpass("Cookie (ввод скрыт): ").strip()
    count = bind_station(args.user_id, cookie, YandexHome(token), StationStore())
    print(f"Подключение сохранено для вашего Telegram ID. Доступных Станций: {count}.")
    print("Перезапустите бота, затем выполните /devices и /station НОМЕР.")


if __name__ == "__main__":
    try:
        main()
    except HomeError as error:
        print(error)
        raise SystemExit(1)
    except OSError:
        print("Не удалось сохранить подключение. Проверьте права на station_sessions.json.")
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nПодключение отменено.")
