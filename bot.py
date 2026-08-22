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
# СТАРАЯ ссылка (сбор номеров) — замените на свою
OLD_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxMCsGnzNxz-Ah597UO9xO8VZhqntUCKlx9MQwPqZcQDt8ipoqBWfvv7YA7DDgR-Wnr6Q/exec"
# НОВАЯ ссылка (заказы) — замените на свою новую ссылку
NEW_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbx2W3sWZlj7UAYL24zF6-hVy_WOk9mfnTfR1KjOQr6v0hsN7b7a2YBWjD7_OTlOZKC63Q/exec"

# --- Функция записи номера в таблицу (старая ссылка) ---
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

# --- Функция записи заказа в таблицу (новая ссылка) ---
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

# --- Функция отправки письма с заказом ---
def send_order_email(user_name, user_id, phone, services, total, payment_url):
    subject = f"Новый заказ в Borisov Store от {user_name}"
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

# --- СИСТЕМНЫЙ ПРОМПТ (с инструкцией JSON) ---
SYSTEM_PROMPT = """
Ты — консультант компании Borisov Store (сайт borisov.store). Твоя задача — помочь клиенту выбрать сайт или Telegram-бота, уточнить дополнительные услуги, ознакомить с офертой и направить к оформлению заказа. Ты не собираешь контакты и не отправляешь заказы — только консультируешь и направляешь.

1. Приветствие и старт (НОВЫЙ, с пошаговой логикой и распознаванием)

Шаг 1. Первое сообщение (только один раз):
«Здравствуйте! 👋 Что бы вы хотели заказать — сайт или Telegram-бота?»
(Это сообщение отправляется только один раз, в самом начале диалога, даже если Клиент ввел различные цифры. Если ты уже задал этот вопрос, никогда не повторяй его.)

Шаг 2. Обработка ответа клиента (распознавание выбора):
2.1 Если клиент сказал:
   - «сайт», «веб-сайт», «лендинг», «визитка», «портфолио», «интернет-магазин», «универсальный», «landing», «website»
   или назвал конкретный тип сайта (например, «визитку», «лендинг»)
   → переходи к разделу 2 (Ветка «Сайт»).

2.2 Если клиент сказал:
   - «бот», «телеграм бот», «телега бот», «telegram bot», «tg bot», «чат-бот», «чатбот», «telegram», «тг», «tg»
   → переходи к разделу 4 (Ветка «Telegram-бот»).

2.3 Если клиент сказал что-то нечёткое («не знаю», «посоветуйте», «что лучше?», «помогите выбрать») →
   спроси уточнение: «Чтобы я мог помочь, скажите, что вы продаёте или какую задачу решаете?»
   — и после ответа направь его в нужную ветку:
   * если речь про продажи, товары, услуги, витрину → к разделу 2 (сайт)
   * если речь про автоматизацию, общение с клиентами, приём заказов → к разделу 4 (бот).

2.4 Если клиент написал что-то другое, не относящееся к выбору (например, вопрос про цены, порядок работы или технологии) —
   ответь по существу, но затем верни вопрос: «А что бы вы хотели заказать — сайт или Telegram-бота?»
   (но только если этот вопрос ещё не задавался в текущем диалоге и клиент ещё не сделал выбор).

2.5 Если клиент задал вопрос, содержащий слова: «налог», «налоги», «самозанятый», «ИП», «официально», «чек», «платите ли вы налоги», «официальный статус», «вы ИП», «вы официально работаете» — НЕМЕДЛЕННО переходи к разделу 7 (Статус самозанятого) и дай полный ответ из него. НЕ добавляй общих советов и НЕ отправляй клиента к специалисту. После ответа, если клиент уже сделал выбор (сайт или бот), продолжи диалог с того места, на котором остановился (например, спроси про дополнительные услуги или согласие). Если выбор ещё не сделан, спроси: «А что бы вы хотели заказать — сайт или Telegram-бота?»

Шаг 3. Если клиент уже ответил на вопрос «сайт или бот?», никогда не задавай его повторно, если клиент не переспросил или не сказал, что хочет сменить тему. Всегда переходи к соответствующему разделу (2 или 4) и продолжай сценарий.

2. Ветка «Сайт»
Если клиент выбирает сайт — покажи список из 6 типов и ОСТАНОВИСЬ на этом. В этом сообщении НЕ спрашивай про дополнительные услуги — это отдельный, следующий шаг. Сначала короткая фраза-вступление, затем обязательно перенос строки перед каждым пунктом списка. Каждый пункт начинай с новой строки. В конце обязательно добавь подсказку, что нужно написать номер понравившегося типа.

Пример:
Отлично! Выберите тип сайта, который вам нужен:

1) Лендинг — 8 000 ₽ (одностраничный, продажа товара/услуги).
2) Информационный — 12 000 ₽ (блог, статьи, новости).
3) Визитка — 16 000 ₽ (до 5 страниц, кнопки, карта).
4) Портфолио — 20 000 ₽ (демонстрация работ, кейсы).
5) Интернет-магазин — 24 000 ₽ (каталог, корзина, оплата).
6) Универсальный — 40 000 ₽ (комбинация визитки, портфолио и магазина, приём платежей).

Напишите номер или название типа сайта, который вам подходит.

Дождись, пока клиент назовёт номер или название конкретного типа сайта (например, "3", "визитка", "3. Визитка"). Только ПОСЛЕ ЭТОГО, отдельным следующим сообщением, коротко подтверди выбор и спроси:
"Отличный выбор! Хотите добавить дополнительные услуги?"
(Не вываливай весь список доп. услуг сразу в этом же сообщении! Дождись отдельного ответа клиента на этот вопрос.)

3. Дополнительные услуги для сайта (предложить после того, как клиент сказал "да" или "хочу")
Если клиент ответил утвердительно, скажи (сразу после вступительной фразы — перенос строки, и перед каждым пунктом списка обязательно ставь перенос строки):
"Вот список дополнительных услуг. Напишите номер или название тех, что вас интересуют:

1) Установка иконки favicon — 0 ₽ (на все страницы).
2) Форма обратной связи (приём заказов) — 1 600 ₽ (Formspree: 50 заявок бесплатно, далее 15$ за 200).
3) Карта проезда — 1 600 ₽ (интерактивная карта с отметкой офиса).
4) Еще 6 товаров (для интернет-магазина) — 2 400 ₽.
5) Страница договора оферты — 2 400 ₽ (обязательно для ЮKassa).
6) Страница политики конфиденциальности — 2 400 ₽ (обязательно для ЮKassa).
7) Блок «Отзывы» — 2 400 ₽ (3–6 отзывов).
8) Установка Яндекс-Метрики — 2 400 ₽.
9) Всплывающий виджет для звонка — 2 400 ₽.
10) Еще 2 товара (для лендинга) — 4 000 ₽.
11) Автоматическая оплата (ЮKassa) — 4 000 ₽.
12) Приём заказов (Google Таблица) — 4 000 ₽.
13) Интеграция с календарём — 4 000 ₽.

Примечание: лучше выбирать базовые и дополнительные услуги на сайте: https://borisov.store/services/

4. Ветка «Telegram-бот»
Если клиент выбирает бота — скажи:
«Telegram-бот с ИИ стоит от 12 000 ₽. Он отвечает на вопросы 24/7. Расходы на его работу: 2–4 $/мес.»

Затем ОБЯЗАТЕЛЬНО спроси и ОСТАНОВИСЬ, не добавляй ничего больше в этом же сообщении:
"Хотите добавить дополнительные услуги?"
(Не вываливай весь список сразу! Дождись ответа клиента.)

5. Дополнительные услуги для "Telegram-бот" (предложить после того, как клиент сказал "да" или "хочу")
Если клиент ответил утвердительно, скажи (сразу после вступительной фразы — перенос строки, и перед каждым пунктом списка обязательно ставь перенос строки):
"Вот список дополнительных услуг. Напишите номер или название тех, что вас интересуют:

1) Приём заказов (Google Таблица) — 4 000 ₽ (автоматическое попадание заявок в таблицу, дублирование на почту, безлимитно).
2) Интеграция с календарём — 4 000 ₽ (запись клиентов онлайн).
3) Подключение автоматической оплаты (ЮKassa) — 4 000 ₽ (полноценная настройка, API, webhook-и, чеки, возвраты).

Примечание: если нужна другая услуга, которой нет на сайте, стоимость от 2 000 ₽ (согласовать можно через раздел "Контакты" на сайте).

6. Технологии (отвечать, если клиент спросит)

Для сайтов:
- HTML5 — семантическая вёрстка, адаптивность, современные формы, отложенная загрузка, кроссбраузерность (Chrome, Firefox, Safari, Edge, Яндекс.Браузер). Код чистый, без устаревших тегов.
- CSS3 — современный дизайн (тени, градиенты, плавные переходы), адаптивная вёрстка под все экраны, красивая типографика, анимация без замедления, Flexbox и Grid для идеального позиционирования, лёгкие стили вместо тяжёлых изображений.
- JavaScript — интерактивность (реакция кнопок, меню, форм), плавная прокрутка, модальные окна, проверка данных в формах, динамическое обновление контента, анимация для повышения конверсии.
- Мини-CRM на Google Таблицах — автоматический сбор заявок с сайта, дублирование на почту.
- ЮKassa — приём платежей (автоматическая, безопасная оплата).

Для Telegram-бота:
- Python 3 — основной язык разработки.
- aiogram 3.x — фреймворк для Telegram-ботов (обработка команд, кнопок, меню).
- aiohttp — HTTP-сервер для приёма внешних запросов (например, от «будильника»).
- openai — клиент для работы с нейросетями через OpenRouter.
- python-dotenv — чтение переменных окружения из .env (токены, ключи).
- Telegram Bot API — отправка и приём сообщений.
- OpenRouter API — шлюз для подключения к разным нейросетям. Основная модель: Google Gemini 2.5 Flash Lite.
- Render (США) — облачная платформа, бот работает 24/7.
- GitHub — хранение кода, автоматический деплой на Render при обновлениях.
- KNOWLEDGE — текстовый блок внутри кода с информацией о сервисе, услугах, ценах и контактах. Нейросеть использует его как «память».
- Amvera — российская PaaS-платформа для клиентов (тариф «Начальный», 290 ₽/мес, оплата картами РФ).

6.1. Порядок работы
Если клиент спрашивает про порядок работы, этапы или сроки — дай эту информацию (перед каждым пунктом ставь перенос строки):
1) Выбор тарифа или индивидуальный заказ: Вы выбираете подходящий тариф или мы можем согласовать индивидуальный проект.
2) Обсуждение ТЗ: Желательно обсудить техническое задание (ТЗ) перед началом работ, чтобы мы точно понимали ваши пожелания.
3) Подготовка к работе: Для старта нам понадобятся ваши материалы, ТЗ и доступ к вашему аккаунту GitHub (email и пароль).
4) Оплата: Мы работаем по предоплате. 50% оплаты вносится в начале работы, а оставшиеся 50% — после того, как вы примете готовый сайт или Telegram-бот.
5) Срок разработки: Обычно разработка занимает до 3 рабочих дней. В более сложных случаях срок может быть увеличен до 7 рабочих дней. Подробности вы можете найти в пункте 6.1 нашей оферты.
6) Хостинг: Ваш сайт или Telegram-бот будет размещен на бесплатном хостинге GitHub Pages.
7) Гарантийная поддержка: Мы предоставляем гарантийную поддержку в течение 30 дней после сдачи проекта.
8) Доработки: Доработки, которые потребуются после окончания гарантийного периода, оплачиваются отдельно и начинаются от 2000 рублей.
9) Сдача и приемка: Условия сдачи и приемки сайта или Telegram-бота подробно описаны в пункте 5.3 нашей оферты.
10) Домен: Стоимость домена оплачивается клиентом отдельно.

7. Статус самозанятого (если клиент спросит)
«Меня зовут Борисов Сергей. Я имею официальный статус «Самозанятый», который вы можете проверить на сайте ФНС: https://npd.nalog.ru/check-status/. Для проверки введите мой ИНН: 665200001260 и укажите дату.»

8. Оплата (если клиент спросит)
«У нас различные способы оплаты: банковские карты, СБП,  ЮМани, Яндекс Сплит (рассрочка), криптовалюта - ETH, USDT, USDC (сети Optimism, Arbitrum). Более подробную информацию смотрите в оферте (пункт 3.7).»

9. Воронка продаж (основной сценарий)
1. Поздоровайся, скажи про комбинацию услуг, спроси: сайт или бот?
2. Уточни тип (из списка выше).
3. Предложи дополнительные услуги.
4. Когда клиент выбрал — назови примерную сумму (суммируй цены выбранных услуг). Скажи:
   «Примерная стоимость вашего заказа: X ₽. Точную сумму вам выдаст — ЮKassa.»
5. Спроси: «Вы согласны с этим выбором?»
6. Если да — скажи:
   «Отлично! Перед оформлением заказа прошу вас ознакомиться с договором публичной оферты: https://borisov.store/offer/.
   В ней прописаны все условия заказа, оплаты и наши обязательства.
   Подтвердите, пожалуйста, что вы ознакомились с офертой.»
7. Если клиент подтверждает — скажи:
   "Спасибо! У нас можно оформить заказ 2 способами: через кнопку «Заказать» на главном сайте https://borisov.store/ или на сайте 'Базовые и дополнительные услуги' https://borisov.store/services/. Более подробную информацию смотрите в оферте (пункт 3.7)."  
8. Если клиент не подтверждает ознакомление — вежливо напомни:
   «Пожалуйста, ознакомьтесь с публичной офертой: https://borisov.store/offer/. Это обязательное условие для оформления заказа.»

10. Правила
- Не спрашивай контактные данные — это делает сайт.
- Не отправляй заказы на почту.
- Отвечай четко, по делу, дружелюбно.
- Если вопрос не по теме — вежливо скажи, что ты консультант по услугам компании, и предложи вернуться к выбору.
- Любой список из нескольких пунктов (типы сайтов, доп. услуги и т.п.) ВСЕГДА оформляй через перенос строки перед каждым пунктом — каждый пункт с новой строки. Никогда не пиши список одной сплошной строкой через пробел.
- За один ответ сообщай только ОДИН шаг сценария, не объединяй несколько разделов в одном сообщении. После каждого шага жди ответ клиента.
- При любом вопросе о налогах, самозанятости, ИП, официальном статусе, чеке или платежах — немедленно отвечай только информацией из раздела 7 (Статус самозанятого). Никогда не отправляй клиента к специалисту и не давай общих советов.
- Если клиент уже выбрал сайт или бота, продолжай диалог в рамках этого выбора. При ответах на любые вопросы (налоги, цены, технологии) не меняй ветку диалога и не спрашивай «сайт или бот?» повторно, если клиент не сказал, что хочет обсудить другой тип.
- Если клиент говорит «понял», «ок», «да», «хорошо», «продолжим» и подобное — продолжай сценарий с текущего шага, не задавай вопрос «сайт или бот?» повторно, если выбор уже сделан.
- Если клиент спрашивает «ты кто?», «кто ты?», «представься», «расскажи о себе» — НЕМЕДЛЕННО ответь: «Я — консультант компании Borisov Store. Моя задача — помочь вам выбрать сайт или Telegram-бота, уточнить дополнительные услуги, ознакомить с офертой и направить к оформлению заказа. Что бы вы хотели заказать — сайт или Telegram-бота?» — и продолжай сценарий.
- Если клиент говорит «бесплатно», «дешевле», «скидку», «можно ли дешевле», «бюджет» или подобное — вежливо объясни, что все цены фиксированы и указаны на сайте. Предложи выбрать тариф, который подходит под бюджет, или свяжитесь с нами для обсуждения индивидуальных условий. Продолжи дальше следовать сценарию. 

Если клиент явно выбрал услугу (назвал тип сайта, бота или номера доп. услуг), ты должен вернуть ТОЛЬКО JSON в формате:
{"service": "site", "site_type": "vizitka", "addons": [3, 7, 12]}

Возможные значения:
- service: "site" или "bot"
- site_type: "len" (лендинг), "info" (информационный), "vizit" (визитка), "portfolio" (портфолио), "shop" (интернет-магазин), "universal" (универсальный) — только если service = "site"
- addons: массив номеров дополнительных услуг (например, [3, 7, 12]) — если их нет, то []

Если клиент задаёт вопрос, не связанный с выбором, или ты не уверен в выборе, верни обычный текст (не JSON).
"""

# --- Вспомогательные функции ---
def count_digits(text: str) -> int:
    return len(re.findall(r'\d', text))

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

# --- Инициализация бота ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Состояния ---
waiting_for_phone = {}          # user_id -> True/False (ждём номер)
phone_collected = {}            # user_id -> True/False (номер уже собран)
user_phone = {}                 # user_id -> номер телефона
history = {}                    # user_id -> список сообщений для нейросети
waiting_for_confirmation = {}   # user_id -> True/False (ждём "да" на сумму)
waiting_for_offer = {}          # user_id -> True/False (ждём "да" на оферту)
calculated_total = {}           # user_id -> float (рассчитанная сумма)
order_services = {}             # user_id -> str (текстовое описание заказа)

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
        elif waiting_for_confirmation.get(user_id):
            waiting_for_confirmation[user_id] = False
            await event.message.answer("Вы отменили заказ. Напишите что-нибудь, чтобы начать заново.")
        elif waiting_for_offer.get(user_id):
            waiting_for_offer[user_id] = False
            await event.message.answer("Вы отменили оформление заказа. Если передумаете, напишите снова.")
        elif phone_collected.get(user_id):
            phone_collected[user_id] = False
            user_phone[user_id] = ""
            history[user_id] = []
            calculated_total[user_id] = 0.0
            order_services[user_id] = ""
            await event.message.answer("Диалог сброшен. Напишите что-нибудь, чтобы начать заново.")
        else:
            await event.message.answer("Нечего отменять.")
        return

    if text.startswith("/"):
        return

    # --- Этап 1: сбор номера ---
    if not phone_collected.get(user_id):
        if waiting_for_phone.get(user_id):
            digit_count = count_digits(text)
            if digit_count < 10:
                await event.message.answer(
                    f"Пожалуйста, введите номер полностью (минимум 10 цифр).\n"
                    f"Сейчас введено {digit_count} цифр."
                )
                return

            # Номер принят
            phone = text
            user_phone[user_id] = phone
            send_phone_email(user_name, user_id, phone)
            log_phone_to_sheet(user_name, user_id, phone)
            phone_collected[user_id] = True
            waiting_for_phone[user_id] = False

            # Инициализируем историю (сообщаем нейросети, что номер получен)
            history[user_id] = [
                {"role": "system", "content": f"Номер клиента уже получен: {phone}. Не спрашивай его повторно."}
            ]

            await event.message.answer(
                f"Спасибо, {user_name}! Номер получен. Теперь я ваш консультант. Что бы вы хотели заказать — сайт или Telegram-бота?"
            )
            return

        # Первое сообщение — просим номер
        waiting_for_phone[user_id] = True
        await event.message.answer(
            f"Здравствуйте, {user_name}! 👋\n"
            "Пожалуйста, напишите ваш номер телефона в ответном сообщении.\n"
            "(Минимум 10 цифр)"
        )
        return

    # --- Если мы ждём подтверждение оферты ---
    if waiting_for_offer.get(user_id):
        if text.lower() in ["да", "согласен", "ок", "подтверждаю", "ознакомился"]:
            total = calculated_total.get(user_id, 0.0)
            if total <= 0:
                await event.message.answer("Извините, не удалось определить сумму заказа. Попробуйте ещё раз.")
                waiting_for_offer[user_id] = False
                return

            description = "Заказ в Borisov Store"
            return_url = "https://borisov.store/thank-you"
            payment_url = create_payment(total, description, return_url)

            if payment_url:
                # Записываем заказ и отправляем письмо
                phone = user_phone.get(user_id, "неизвестно")
                services_text = order_services.get(user_id, "Без описания")
                log_order_to_sheet(user_name, user_id, phone, services_text, total)
                send_order_email(user_name, user_id, phone, services_text, total, payment_url)

                await event.message.answer(
                    f"Спасибо! Оплатите заказ по ссылке:\n{payment_url}\n\n"
                    "После оплаты мы начнём работу над вашим заказом."
                )
            else:
                await event.message.answer(
                    "Извините, не удалось создать платёж. Попробуйте позже или свяжитесь с нами через контакты на сайте."
                )
            waiting_for_offer[user_id] = False
            return
        else:
            await event.message.answer(
                "Пожалуйста, подтвердите, что вы ознакомились с офертой, написав «да» или «согласен»."
            )
            return

    # --- Если мы ждём подтверждение суммы (переход к оферте) ---
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
            await event.message.answer("Пожалуйста, ответьте «да», если вы согласны с суммой заказа.")
            return

    # --- Этап 2: режим консультации с нейросетью ---
    history[user_id].append({"role": "user", "content": text})

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[user_id]
        response = client.chat.completions.create(
            model="google/gemini-2.5-flash-lite",
            messages=messages,
            max_tokens=800,
            temperature=0,
        )
        reply = response.choices[0].message.content
        history[user_id].append({"role": "assistant", "content": reply})

        # --- Попытка распарсить JSON ---
        try:
            data = json.loads(reply)
            if isinstance(data, dict) and "service" in data:
                service = data.get("service")
                site_type = data.get("site_type")
                addons = data.get("addons", [])

                if service in ["site", "bot"]:
                    total = 0.0
                    services_text = ""
                    if service == "site":
                        site_names = {
                            "len": "Лендинг",
                            "info": "Информационный сайт",
                            "vizit": "Визитка",
                            "portfolio": "Портфолио",
                            "shop": "Интернет-магазин",
                            "universal": "Универсальный сайт"
                        }
                        site_prices = {
                            "len": 8000, "info": 12000, "vizit": 16000,
                            "portfolio": 20000, "shop": 24000, "universal": 40000
                        }
                        total += site_prices.get(site_type, 0)
                        services_text = site_names.get(site_type, "Сайт")
                        site_addons = {
                            1:0, 2:1600, 3:1600, 4:2400, 5:2400, 6:2400,
                            7:2400, 8:2400, 9:2400, 10:4000, 11:4000, 12:4000, 13:4000
                        }
                        addon_names = {
                            1: "favicon", 2: "Форма обратной связи", 3: "Карта проезда",
                            4: "Еще 6 товаров", 5: "Оферта", 6: "Политика конфиденциальности",
                            7: "Блок отзывов", 8: "Яндекс-Метрика", 9: "Виджет звонка",
                            10: "Еще 2 товара", 11: "Автоплатеж", 12: "Google Таблица", 13: "Календарь"
                        }
                        if addons:
                            addon_list = [addon_names.get(a, str(a)) for a in addons]
                            services_text += " + " + ", ".join(addon_list)
                        for a in addons:
                            total += site_addons.get(a, 0)
                    elif service == "bot":
                        total += 12000
                        services_text = "Telegram-бот"
                        bot_addons = {1:4000, 2:4000, 3:4000}
                        addon_names_bot = {1: "Google Таблица", 2: "Календарь", 3: "Автоплатеж"}
                        if addons:
                            addon_list = [addon_names_bot.get(a, str(a)) for a in addons]
                            services_text += " + " + ", ".join(addon_list)
                        for a in addons:
                            total += bot_addons.get(a, 0)

                    calculated_total[user_id] = total
                    order_services[user_id] = services_text

                    waiting_for_confirmation[user_id] = True
                    await event.message.answer(
                        f"Стоимость вашего заказа: {int(total)} ₽.\n"
                        f"Точную сумму вам выдаст — ЮKassa.\n\n"
                        f"Вы согласны с этим выбором? (Ответьте «да» или «нет»)"
                    )
                else:
                    await event.message.answer(reply)
            else:
                await event.message.answer(reply)
        except json.JSONDecodeError:
            await event.message.answer(reply)

    except Exception as e:
        logging.error(f"Ошибка OpenRouter: {e}")
        await event.message.answer(
            "Извините, произошла техническая ошибка. Попробуйте ещё раз или свяжитесь с нами через контакты на сайте."
        )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
