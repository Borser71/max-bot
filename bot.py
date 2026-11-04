import os
import asyncio
import logging
import smtplib
import re
import uuid
import json
from email.mime.text import MIMEText
from datetime import datetime
from maxapi import Bot, Dispatcher, F
from maxapi.types import MessageCreated
from openai import OpenAI
from yookassa import Configuration, Payment

# --- Переменные окружения ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

if not OPENROUTER_API_KEY:
    raise RuntimeError("Не задан OPENROUTER_API_KEY в переменных окружения")

logging.basicConfig(level=logging.INFO)

# --- Настройка ЮKassa ---
if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY
    logging.info("ЮKassa настроена")
else:
    logging.warning("ЮKassa не настроена: отсутствуют shopId или secretKey")

# --- Клиент OpenRouter ---
client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

# --- Функция создания платежа ---
def create_payment(amount: float, description: str, return_url: str) -> str | None:
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logging.error("ЮKassa не настроена")
        return None

    idempotence_key = str(uuid.uuid4())
    try:
        payment = Payment.create({
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description,
            "capture": True
        }, idempotence_key)
        return payment.confirmation.confirmation_url
    except Exception as e:
        logging.error(f"Ошибка создания платежа: {e}")
        return None

# --- ССЫЛКИ НА APPS SCRIPT ---
OLD_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxMCsGnzNxz-Ah597UO9xO8VZhqntUCKlx9MQwPqZcQDt8ipoqBWfvv7YA7DDgR-Wnr6Q/exec"
NEW_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbx2W3sWZlj7UAYL24zF6-hVy_WOk9mfnTfR1KjOQr6v0hsN7b7a2YBWjD7_OTlOZKC63Q/exec"

def log_phone_to_sheet(user_name, user_id, phone):
    import requests
    payload = {"username": user_name, "user_id": user_id, "text": phone}
    try:
        r = requests.post(OLD_SCRIPT_URL, json=payload, timeout=5)
        if r.status_code == 200:
            logging.info("Номер записан в Google Таблицу")
        else:
            logging.error(f"Ошибка записи номера: {r.status_code}")
    except Exception as e:
        logging.error(f"Ошибка соединения с Apps Script (номер): {e}")

def log_order_to_sheet(user_name, user_id, phone, services, total, status="Ожидает оплаты"):
    import requests
    payload = {
        "username": user_name,
        "user_id": user_id,
        "phone": phone,
        "services": services,
        "total": total,
        "status": status
    }
    try:
        r = requests.post(NEW_SCRIPT_URL, json=payload, timeout=5)
        if r.status_code == 200:
            logging.info("Заказ записан в Google Таблицу")
        else:
            logging.error(f"Ошибка записи заказа: {r.status_code}")
    except Exception as e:
        logging.error(f"Ошибка соединения с Apps Script (заказ): {e}")

def send_order_email(user_name, user_id, phone, services, total, payment_url):
    subject = f"Новый заказ в Borisov Store из MAX от {user_name}"
    body = (
        f"Имя: {user_name}\n"
        f"ID: {user_id}\n"
        f"Телефон: {phone}\n"
        f"Заказ: {services}\n"
        f"Сумма: {total} ₽\n"
        f"Ссылка на оплату: {payment_url}\n"
        f"Статус: Ожидает оплаты"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        logging.info("Письмо с заказом отправлено")
    except Exception as e:
        logging.error(f"Ошибка отправки письма с заказом: {e}")

# ==========================================
# ПРОМПТЫ
# ==========================================

# Общая информация (Оплата, Налоги, Порядок, Гарантии)
COMMON_INFO = """
ОБЩАЯ ИНФОРМАЦИЯ:
- Оплата: предоплата 50% в начале, 50% после сдачи. Способы: карты, СБП, ЮМани, Яндекс Сплит, крипта.
- Самозанятый: Борисов Сергей, ИНН 665200001260. Проверка на сайте ФНС.
- Порядок работы: выбор тарифа, ТЗ, доступ к GitHub, предоплата 50%, разработка (до 3 дней), сдача и оплата остатка.
- Гарантийная поддержка: 30 дней после сдачи.
"""
# Промпт для Сайтов
PROMPT_SITE = """
Ты — консультант Borisov Store по сайтам. 
Твоя задача — помочь выбрать тип сайта и доп. услуги.
Ты не считаешь сумму! Ты просто возвращаешь JSON с выбором.

Если клиент спрашивает про технологии сайтов, отвечай: HTML5 (семантическая вёрстка), CSS3 (анимации, адаптивность), JavaScript (интерактивность), Google Таблицы (сбор заявок), ЮKassa (оплата).

Если клиент выбирает сайт, покажи список из 6 типов и ОСТАНОВИСЬ на этом. В этом сообщении НЕ спрашивай про дополнительные услуги.

Пример:
Отлично! Выберите тип сайта, который вам нужен:
1) Лендинг — 6 400 ₽ (одностраничный, продажа товара/услуги).
2) Информационный — 9 600 ₽ (блог, статьи, новости).
3) Визитка — 12 800 ₽ (до 5 страниц, кнопки, карта).
4) Портфолио — 16 000 ₽ (демонстрация работ, кейсы).
5) Интернет-магазин — 19 200 ₽ (каталог, корзина, оплата).
6) Универсальный — 32 000 ₽ (комбинация визитки, портфолио и магазина).
Напишите номер или название типа сайта, который вам подходит.

Дождись, пока клиент назовёт номер или название. Только ПОСЛЕ ЭТОГО, отдельным следующим сообщением, коротко подтверди выбор и спроси:
"Отличный выбор! Хотите добавить дополнительные услуги?"

Если клиент ответил "да" или "хочу" на вопрос о доп. услугах, покажи этот список (каждый пункт с новой строки):
1) Установка иконки favicon — 0 ₽ (на все страницы).
2) Форма обратной связи (приём заказов) — 1 280 ₽ (Formspree).
3) Карта проезда — 1 280 ₽ (интерактивная карта).
4) Еще 6 товаров (для интернет-магазина) — 1 920 ₽.
5) Страница договора оферты — 1 920 ₽ (обязательно для ЮKassa).
6) Страница политики конфиденциальности — 1 920 ₽ (обязательно для ЮKassa).
7) Блок «Отзывы» — 1 920 ₽ (3–6 отзывов).
8) Установка Яндекс-Метрики — 1 920 ₽.
9) Всплывающий виджет для звонка — 1 920 ₽.
10) Еще 2 товара (для лендинга) — 3 200 ₽.
11) Автоматическая оплата (ЮKassa) — 3 200 ₽.
12) Приём заказов (Google Таблица) — 3 200 ₽.
13) Интеграция с календарём — 3 200 ₽.
14) Автоматическая оплата криптовалютой — 4 000 ₽.
Напишите номера или названия тех, что вас интересуют.

Только после того, как клиент выбрал ВСЕ номера, верни ТОЛЬКО JSON:
{"service": "site", "site_type": "len/info/vizit/portfolio/shop/universal", "addons": [1, 2, 3]}

Возможные типы сайтов: len (Лендинг), info (Информационный), vizit (Визитка), portfolio (Портфолио), shop (Интернет-магазин), universal (Универсальный).
"""

# Промпт для Telegram-ботов
PROMPT_TG = """
Ты — консультант Borisov Store по Telegram-ботам.
Твоя задача — предложить 3 пакета.

ТЕХНОЛОГИИ TELEGRAM-БОТОВ (отвечай, если спросят):
- Python 3 — основной язык разработки.
- aiogram 3.x — фреймворк для Telegram-ботов (обработка команд, кнопок, меню).
- aiohttp — HTTP-сервер для приёма внешних запросов (например, от «будильника»).
- openai — клиент для работы с нейросетями через OpenRouter.
- python-dotenv — чтение переменных окружения из .env (токены, ключи).
- Telegram Bot API — отправка и приём сообщений.
- OpenRouter API — шлюз для подключения к разным нейросетям. Основная модель: Google Gemini 2.5 Flash.
- YooKassa — приём платежей.
- Amvera — российская PaaS-платформа, бот работает 24/7.

Пакеты:
1) Сбор контактов — 6 400 ₽ (без ИИ)
2) Консультант — 9 600 ₽ (с ИИ)
3) Консультант + оплата — 16 000 ₽ (с ИИ)

Если клиент выбрал номер пакета (1, 2 или 3), верни ТОЛЬКО JSON:
{"service": "bot", "platform": "telegram", "package": "contact/contact_consult/full"}
"""

# Промпт для MAX-ботов
PROMPT_MAX = """
Ты — консультант Borisov Store по MAX-ботам.
Твоя задача — предложить 3 пакета.

ТЕХНОЛОГИИ MAX-БОТОВ (отвечай, если спросят):
- Python 3 — основной язык разработки.
- maxapi — библиотека для работы с мессенджером MAX.
- openai — клиент для работы с нейросетями через OpenRouter.
- aiohttp — HTTP-сервер.
- YooKassa — приём платежей.
- Amvera — российский хостинг для круглосуточной работы.

Пакеты:
1) Сбор контактов — 8 000 ₽ (без ИИ)
2) Консультант — 16 000 ₽ (с ИИ)
3) Консультант + оплата — 24 000 ₽ (с ИИ)

Если клиент выбрал номер пакета (1, 2 или 3), верни ТОЛЬКО JSON:
{"service": "bot", "platform": "max", "package": "contact/contact_consult/full"}
"""

# Промпт для AI-агентов
PROMPT_AGENT = """
Ты — консультант Borisov Store по AI-агентам на сайт.
AI-агент — это умный консультант 24/7. Стоимость 16 000 ₽.

ТЕХНОЛОГИИ AI-АГЕНТА (отвечай, если спросят):
- Python 3 — основной язык разработки.
- FastAPI — веб-фреймворк для обработки запросов с сайта.
- openai — клиент для работы с нейросетями через OpenRouter.
- aiohttp — HTTP-сервер.
- Amvera/Render — хостинг.

Твоя задача — рассказать про агента и получить согласие.
Если клиент согласен (пишет "да"), верни ТОЛЬКО JSON:
{"service": "bot", "platform": "ai_agent", "package": "full"}
"""
def send_phone_email(user_name, user_id, phone):
    subject = f"Новый контакт из MAX: {user_name}"
    body = f"Имя: {user_name}\nID: {user_id}\nТелефон: {phone}"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        logging.info("Письмо с телефоном отправлено")
    except Exception as e:
        logging.error(f"Ошибка отправки email: {e}")

# ==========================================
# БОТ
# ==========================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные состояния (просто словари)
waiting_for_phone = {}
phone_collected = {}
user_phone = {}
history = {}
waiting_for_confirmation = {}
waiting_for_offer = {}
calculated_total = {}
order_services = {}
user_platform = {}  # Храним выбранную платформу (site, telegram, max, ai_agent)

@dp.message_created(F.message.body.text)
async def handle_message(event: MessageCreated):
    user_id = event.message.sender.user_id
    text = event.message.body.text.strip()
    user = event.message.sender
    user_name = user.first_name or user.username or "Неизвестный"

    # --- Команда /cancel ---
    if text.startswith("/cancel"):
        if waiting_for_phone.get(user_id):
            waiting_for_phone[user_id] = False
            await event.message.answer("Вы отменили ввод номера. Если передумаете, просто напишите ещё раз.")
        elif waiting_for_confirmation.get(user_id) or waiting_for_offer.get(user_id):
            waiting_for_confirmation[user_id] = False
            waiting_for_offer[user_id] = False
            history[user_id] = []
            calculated_total[user_id] = 0.0
            order_services[user_id] = ""
            user_platform[user_id] = None
            await event.message.answer("Вы отменили заказ. Напишите что-нибудь, чтобы начать заново.")
        elif phone_collected.get(user_id):
            phone_collected[user_id] = False
            user_phone[user_id] = ""
            history[user_id] = []
            calculated_total[user_id] = 0.0
            order_services[user_id] = ""
            user_platform[user_id] = None
            await event.message.answer("Диалог сброшен. Напишите что-нибудь, чтобы начать заново.")
        else:
            await event.message.answer("Нечего отменять.")
        return

    if text.startswith("/"):
        return

    # --- Проверка состояний ОФЕРТЫ и ПОДТВЕРЖДЕНИЯ СУММЫ (жестко в коде) ---

    # 1. Ожидание оферты
    if waiting_for_offer.get(user_id):
        if text.lower() in ["да", "согласен", "ок", "подтверждаю", "ознакомился"]:
            total = calculated_total.get(user_id, 0.0)
            if total <= 0:
                await event.message.answer("Ошибка: сумма не определена. Начните заново.")
                waiting_for_offer[user_id] = False
                return

            prepayment = int(total * 0.5)
            payment_url = create_payment(prepayment, "Заказ в Borisov Store", "https://borisov.store/thank-you")

            if payment_url:
                phone = user_phone.get(user_id, "неизвестно")
                services_text = order_services.get(user_id, "Без описания")
                log_order_to_sheet(user_name, user_id, phone, services_text, total)
                send_order_email(user_name, user_id, phone, services_text, total, payment_url)

                await event.message.answer(
                    f"✅ Ваш заказ принят!\n\n"
                    f"👤 Имя: {user_name}\n"
                    f"📋 Состав заказа: {services_text}\n"
                    f"💰 Полная сумма: {int(total)} ₽\n"
                    f"💳 Предоплата (50%): {prepayment} ₽\n\n"
                    f"🔗 Оплатите предоплату по ссылке:\n{payment_url}\n\n"
                    "Оставшиеся 50% вы оплатите после завершения работы.\n"
                    "После оплаты мы начнём работу над вашим заказом."
                )
                waiting_for_offer[user_id] = False
                waiting_for_confirmation[user_id] = False
                history[user_id] = []
                calculated_total[user_id] = 0.0
                order_services[user_id] = ""
                user_platform[user_id] = None
                return
            else:
                await event.message.answer("Извините, не удалось создать платёж. Попробуйте позже.")
                waiting_for_offer[user_id] = False
                return
        else:
            await event.message.answer("Пожалуйста, подтвердите, что вы ознакомились с офертой, написав «да».")
            return

    # 2. Ожидание подтверждения суммы
    if waiting_for_confirmation.get(user_id):
        if text.lower() in ["да", "согласен", "ок"]:
            waiting_for_confirmation[user_id] = False
            waiting_for_offer[user_id] = True
            await event.message.answer(
                "Отлично! Перед оформлением заказа прошу вас ознакомиться с договором публичной оферты:\n"
                "https://borisov.store/offer/\n\n"
                "В ней прописаны все условия заказа, оплаты и наши обязательства.\n"
                "Подтвердите, пожалуйста, что вы ознакомились с офертой (напишите «да»)."
            )
            return
        else:
            await event.message.answer("Пожалуйста, ответьте «да», чтобы перейти к оферте, или /cancel для отмены.")
            return

    # --- СБОР НОМЕРА ТЕЛЕФОНА ---
    if not phone_collected.get(user_id):
        if waiting_for_phone.get(user_id):
            digit_count = len(re.findall(r'\d', text))
            if digit_count < 10:
                await event.message.answer(f"Пожалуйста, введите номер полностью (минимум 10 цифр). Сейчас введено {digit_count}.")
                return

            phone = text
            user_phone[user_id] = phone
            send_phone_email(user_name, user_id, phone)
            log_phone_to_sheet(user_name, user_id, phone)
            phone_collected[user_id] = True
            waiting_for_phone[user_id] = False
            history[user_id] = [{"role": "system", "content": f"Номер получен: {phone}."}]

            await event.message.answer(
                f"Спасибо, {user_name}! Номер получен. Теперь я ваш консультант. Что бы вы хотели заказать — сайт, Telegram-бота, MAX-бота или AI-агента на сайт?"
            )
            return

        waiting_for_phone[user_id] = True
        await event.message.answer(f"Здравствуйте, {user_name}! 👋\nПожалуйста, напишите ваш номер телефона в ответном сообщении.\n(Минимум 10 цифр)")
        return

    # --- ОПРЕДЕЛЕНИЕ ПЛАТФОРМЫ (жестко в коде, до вызова нейросети) ---
    # Если платформа еще не выбрана, пытаемся распознать её из текста
    if user_platform.get(user_id) is None:
        text_lower = text.lower()
        if any(word in text_lower for word in ["сайт", "лендинг", "визитк", "портфолио", "магазин", "универсальн"]):
            user_platform[user_id] = "site"
            history[user_id].append({"role": "system", "content": PROMPT_SITE + COMMON_INFO})
        elif any(word in text_lower for word in ["телеграм", "tg", "телеграмм"]):
            user_platform[user_id] = "telegram"
            history[user_id].append({"role": "system", "content": PROMPT_TG + COMMON_INFO})
        elif any(word in text_lower for word in ["макс", "max"]):
            user_platform[user_id] = "max"
            history[user_id].append({"role": "system", "content": PROMPT_MAX + COMMON_INFO})
        elif any(word in text_lower for word in ["агент", "ai", "ии"]):
            user_platform[user_id] = "ai_agent"
            history[user_id].append({"role": "system", "content": PROMPT_AGENT + COMMON_INFO})
        else:
            # Если не поняли, просим уточнить
            await event.message.answer("Пожалуйста, уточните, что вы хотите заказать: сайт, Telegram-бота, MAX-бота или AI-агента на сайт?")
            return

    # --- ВЫЗОВ НЕЙРОСЕТИ С ПРАВИЛЬНЫМ ПРОМПТОМ ---
    history[user_id].append({"role": "user", "content": text})

    try:
        messages = history[user_id]
        response = client.chat.completions.create(
            model="google/gemini-2.5-flash-lite",
            messages=messages,
            max_tokens=800,
            temperature=0,
        )
        reply = response.choices[0].message.content
        history[user_id].append({"role": "assistant", "content": reply})

        # --- ПАРСИНГ JSON ---
        json_match = re.search(r'\{.*\}', reply, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                if isinstance(data, dict) and "service" in data:
                    service = data.get("service")
                    
                    if service == "site":
                        site_type = data.get("site_type")
                        addons = data.get("addons", [])
                        site_names = {"len": "Лендинг", "info": "Информационный", "vizit": "Визитка", "portfolio": "Портфолио", "shop": "Интернет-магазин", "universal": "Универсальный"}
                        site_prices = {"len": 6400, "info": 9600, "vizit": 12800, "portfolio": 16000, "shop": 19200, "universal": 32000}
                        site_addons = {1:0, 2:1280, 3:1280, 4:1920, 5:1920, 6:1920, 7:1920, 8:1920, 9:1920, 10:3200, 11:3200, 12:3200, 13:3200, 14:4000}
                        addon_names = {1: "favicon", 2: "Форма обратной связи", 3: "Карта проезда", 4: "Еще 6 товаров", 5: "Оферта", 6: "Политика конфиденциальности", 7: "Блок отзывов", 8: "Яндекс-Метрика", 9: "Виджет звонка", 10: "Еще 2 товара", 11: "Автоплатеж", 12: "Google Таблица", 13: "Календарь", 14: "Оплата криптовалютой"}
                        
                        total = site_prices.get(site_type, 0)
                        services_text = site_names.get(site_type, "Сайт")
                        if addons:
                            addon_list = [addon_names.get(a, str(a)) for a in addons if a in site_addons]
                            services_text += " + " + ", ".join(addon_list)
                            for a in addons:
                                total += site_addons.get(a, 0)
                        
                        calculated_total[user_id] = total
                        order_services[user_id] = services_text
                        waiting_for_confirmation[user_id] = True
                        await event.message.answer(f"Стоимость вашего заказа: {int(total)} ₽.\n\nВы согласны с этим выбором? (Ответьте «да» или «нет»)")
                        
                    elif service == "bot":
                        platform = data.get("platform")
                        package = data.get("package")
                        
                        if platform == "telegram":
                            prices = {"contact": 6400, "contact_consult": 9600, "full": 16000}
                            names = {"contact": "Бот для сбора контактов (ТГ)", "contact_consult": "Бот-консультант (ТГ)", "full": "Бот с оплатой (ТГ)"}
                        elif platform == "max":
                            prices = {"contact": 8000, "contact_consult": 16000, "full": 24000}
                            names = {"contact": "Бот для сбора контактов (MAX)", "contact_consult": "Бот-консультант (MAX)", "full": "Бот с оплатой (MAX)"}
                        else: # ai_agent
                            prices = {"full": 16000}
                            names = {"full": "AI-агент на сайте"}
                            
                        total = prices.get(package, 0)
                        services_text = names.get(package, "Бот")
                        
                        calculated_total[user_id] = total
                        order_services[user_id] = services_text
                        waiting_for_confirmation[user_id] = True
                        await event.message.answer(f"Стоимость вашего заказа: {int(total)} ₽.\n\nВы согласны с этим выбором? (Ответьте «да» или «нет»)")
                    else:
                        await event.message.answer(reply)
                else:
                    await event.message.answer(reply)
            except json.JSONDecodeError:
                await event.message.answer(reply)
        else:
            await event.message.answer(reply)

    except Exception as e:
        logging.error(f"Ошибка OpenRouter: {e}")
        await event.message.answer("Секунду, уточняю информацию. Пожалуйста, отправьте сообщение ещё раз.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
