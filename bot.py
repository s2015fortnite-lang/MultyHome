"""Простой Telegram-бот. Запуск: python bot.py."""

import argparse
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from accounts import Accounts
from capabilities import capability_info, data_shape, describe_capabilities, prepare_action

from yandex_home import DemoHome, HomeError, describe_device, supports_on_off
from station_api import DemoStation


# Сохраняем соединение с Telegram между запросами.
telegram_session = requests.Session()


HELP = """Команды:
/id — ваш Telegram ID
/connect — подключить свой Яндекс Дом
/disconnect — удалить подключение из бота
/devices — список устройств
/status 1 — состояние устройства №1
/on 1 — включить устройство №1
/off 1 — выключить устройство №1
/capabilities 1 — доступные настройки устройства №1
/capability_info 1 — структура возможностей без персональных значений
/brightness 1 50 — яркость
/color 1 FF0000 — красный цвет
/white 1 4000 — температура белого света, К
/temperature 3 22.5 — заданная температура
/mode 3 thermostat cool — режим охлаждения
/volume 4 30 — громкость
/range 1 ИМЯ ЗНАЧЕНИЕ — другая числовая настройка
/scene 1 СЦЕНА — сцена освещения
/station 5 — подключение Станции и состояние эквалайзера
/station_info 5 — структура конфигурации для добавления следующих настроек
/equalizer 5 on — включить эквалайзер; off — выключить
/eqbands 5 -3 0 0 0 3 — пять усилений эквалайзера, от -6 до 6 дБ

Сначала /devices, затем /capabilities НОМЕР.
Числа в примерах условные: настройки зависят от устройства."""


class Commands:
    def __init__(self, home, station=None):
        self.home = home
        self.station = station
        self.devices = []

    def handle(self, text):
        parts = text.split()
        if not parts:
            return HELP
        command = parts[0].split("@")[0].lower()
        if command in ("/start", "/help"):
            return HELP
        if command == "/devices":
            # Очищаем старые номера, даже если обновление списка не удалось.
            self.devices = []
            self.devices = self.home.list_devices()
            if not self.devices:
                return "В этом Яндекс Доме нет устройств."
            lines = []
            for number, device in enumerate(self.devices, start=1):
                available = "вкл/выкл" if supports_on_off(device) else "настройки: /capabilities"
                lines.append(f"{number}. {device.get('name', 'Без названия')} ({available})")
            return "\n".join(lines)
        argument_counts = {
            "/on": 0, "/off": 0, "/status": 0, "/capabilities": 0, "/capability_info": 0,
            "/brightness": 1, "/color": 1, "/white": 1, "/temperature": 1,
            "/volume": 1, "/scene": 1, "/mode": 2, "/range": 2,
            "/station": 0, "/station_info": 0, "/equalizer": 1, "/eqbands": 5,
        }
        if command not in argument_counts:
            return "Неизвестная команда.\n" + HELP
        if not self.devices:
            return "Сначала выполните /devices."
        if len(parts) != 2 + argument_counts[command] or not parts[1].isdigit():
            return "Неверный формат команды. Посмотрите /help и /capabilities НОМЕР."
        number = int(parts[1])
        if number < 1 or number > len(self.devices):
            return "Такого номера нет. Выполните /devices."
        device = self.devices[number - 1]
        if command == "/status":
            return describe_device(self.home.get_device(device["id"]))
        if command == "/capabilities":
            return describe_capabilities(self.home.get_device(device["id"]), number, self.station is not None)
        if command == "/capability_info":
            return capability_info(self.home.get_device(device["id"]))
        if command in ("/station", "/station_info", "/equalizer", "/eqbands"):
            if self.station is None:
                return "Станция ещё не подключена. Выполните /id и локально запустите station_login.py по инструкции STATION_SETUP.md, затем перезапустите бота."
            if command == "/station":
                return self.station.equalizer(device["id"])
            if command == "/station_info":
                config, _ = self.station.get_config(device["id"])
                return "Структура конфигурации Станции (без значений):\n" + json.dumps(data_shape(config), ensure_ascii=False, indent=2)
            if command == "/equalizer":
                if parts[2] not in ("on", "off"):
                    return "Используйте /equalizer НОМЕР on или /equalizer НОМЕР off."
                return self.station.equalizer(device["id"], enabled=parts[2] == "on")
            try:
                gains = [int(value) for value in parts[2:]]
            except ValueError:
                return "Укажите пять целых усилений от -6 до 6 дБ."
            return self.station.equalizer(device["id"], gains=gains)
        # Читаем свежие возможности, чтобы не отправить неподдерживаемую команду.
        current_device = self.home.get_device(device["id"])
        if command in ("/on", "/off") and not supports_on_off(current_device):
            return "Устройство не поддерживает включение/выключение через Яндекс API."
        capability_type, instance, value = prepare_action(current_device, command, parts[2:])
        self.home.set_capability(device["id"], capability_type, instance, value)
        return f"{device.get('name', 'Устройство')}: команда выполнена."


def telegram_request(token, method, data):
    started = time.perf_counter()
    try:
        response = telegram_session.post(
            f"https://api.telegram.org/bot{token}/{method}", json=data, timeout=40
        )
        if response.status_code == 409:
            raise HomeError("Telegram: конфликт запуска. Остановите второй экземпляр этого бота и проверьте webhook.")
        if response.status_code != 200:
            raise HomeError(f"Ошибка Telegram API: HTTP {response.status_code}.")
        result = response.json()
    except (requests.RequestException, ValueError):
        # Не выводим текст исключения: URL Telegram содержит токен бота.
        raise HomeError("Нет связи с Telegram или получен некорректный ответ.") from None
    finally:
        if os.getenv("DIAGNOSTICS") == "1":
            # URL, токен и содержимое сообщений не записываются.
            print(f"Telegram {method}: {time.perf_counter() - started:.2f} с", flush=True)
    if not result.get("ok"):
        raise HomeError("Telegram отклонил запрос.")
    return result["result"]


def message_reply(message, accounts, sessions):
    # Каждый пользователь управляет только своим домом в личном чате.
    if message.get("chat", {}).get("type") != "private":
        return None
    user_id = message.get("from", {}).get("id")
    text = message.get("text", "")
    parts = text.split()
    command = parts[0].split("@")[0].lower() if parts else ""
    if command == "/id":
        return f"Ваш Telegram ID: {user_id}"
    if command in ("/start", "/help"):
        return HELP
    if command == "/connect":
        return accounts.connect(user_id)
    if command == "/disconnect":
        sessions.pop(user_id, None)
        return accounts.disconnect(user_id)
    if command == "/code":
        if len(parts) != 2:
            return "Формат: /code КОД_ИЗ_ЯНДЕКСА"
        sessions.pop(user_id, None)
        return accounts.authorize(user_id, parts[1])
    if not text:
        return "Отправьте текстовую команду. /help — список команд."
    # Сначала проверяем наличие подключения, затем берём персональный список.
    if user_id not in sessions:
        sessions[user_id] = Commands(accounts.home(user_id), accounts.station(user_id))
    return sessions[user_id].handle(text)


def run_bot(token, accounts):
    # getMe проверяет токен. Удаление webhook требуется для режима getUpdates.
    telegram_request(token, "getMe", {})
    telegram_request(token, "deleteWebhook", {"drop_pending_updates": True})
    sessions = {}
    offset = 0
    print("Бот запущен. Откройте личный чат с ним в Telegram. Остановка: Ctrl+C.", flush=True)
    while True:
        try:
            updates = telegram_request(token, "getUpdates", {
                "offset": offset, "timeout": 25, "allowed_updates": ["message"]
            })
            for update in updates:
                # Запоминаем событие до отправки ответа, чтобы при сетевой ошибке
                # случайно не выполнить одну команду управления дважды.
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                try:
                    parts = message.get("text", "").split()
                    if parts and parts[0].split("@")[0].lower() == "/code":
                        # Пытаемся убрать одноразовый код из истории переписки.
                        try:
                            telegram_request(token, "deleteMessage", {
                                "chat_id": message["chat"]["id"],
                                "message_id": message["message_id"],
                            })
                        except HomeError:
                            pass
                    started = time.perf_counter()
                    try:
                        reply = message_reply(message, accounts, sessions)
                    finally:
                        if os.getenv("DIAGNOSTICS") == "1":
                            print(f"Обработка команды: {time.perf_counter() - started:.2f} с", flush=True)
                except HomeError as error:
                    reply = str(error)
                if reply:
                    # Telegram ограничивает длину сообщения; длинный список делим.
                    for start in range(0, len(reply), 4000):
                        telegram_request(token, "sendMessage", {
                            "chat_id": message["chat"]["id"], "text": reply[start:start + 4000]
                        })
        except HomeError as error:
            print(error, flush=True)
            time.sleep(3)


def main():
    parser = argparse.ArgumentParser(description="Учебный бот для Яндекс Дома")
    parser.add_argument("--demo-console", action="store_true", help="Проверка без Telegram и токенов")
    args = parser.parse_args()
    load_dotenv(Path(__file__).with_name(".env"))
    if args.demo_console:
        home = DemoHome()
        commands = Commands(home, DemoStation(home))
        print("Учебный режим. /help — справка, exit — выход.")
        while True:
            try:
                text = input("> ")
            except EOFError:
                break
            if text.strip() == "exit":
                break
            try:
                print(commands.handle(text))
            except HomeError as error:
                print(error)
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise HomeError("Заполните TELEGRAM_BOT_TOKEN в файле .env.")
    demo_mode = os.getenv("DEMO_MODE", "1")
    if demo_mode not in ("0", "1"):
        raise HomeError("DEMO_MODE должен быть 0 или 1.")
    if demo_mode == "1":
        print("Режим: виртуальные устройства.")
    else:
        print("Режим: каждый пользователь подключает свой настоящий дом.")
    accounts = Accounts(
        demo_mode == "1", os.getenv("YANDEX_CLIENT_ID", "").strip(),
        os.getenv("YANDEX_CLIENT_SECRET", "").strip(),
    )
    if demo_mode == "0" and (not accounts.client_id or not accounts.client_secret):
        raise HomeError("Заполните YANDEX_CLIENT_ID и новый YANDEX_CLIENT_SECRET в .env.")
    run_bot(token, accounts)


if __name__ == "__main__":
    try:
        main()
    except HomeError as error:
        print(error)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nБот остановлен.")
