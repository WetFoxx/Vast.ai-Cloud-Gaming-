# balance.py — показать баланс аккаунта Vast.ai.
# Запуск: .venv/bin/python balance.py

import sys

from vastapi import call   # общий код запросов к Vast — в vastapi.py

data = call("GET", "users/current/")
# Деньги на счету — поле credit (сверено с консолью Vast).
# Выводим только его, остальной ответ не печатаем
if "credit" not in data:
    sys.exit("В ответе нет поля credit")
print(f"Баланс Vast: ${data['credit']:.2f}")
