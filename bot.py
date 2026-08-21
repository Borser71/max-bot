import os
import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from maxapi import Bot, Dispatcher, F
from maxapi.types import MessageCreated

# --- Переменные окружения ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))

logging.basicConfig(level=logging.INFO)

# --- Клавиатура с кнопкой "Отправить номер" (правильный синтаксис для maxapi) ---
def get_contact_keyboard():
    return {
        "keyboard": [
            [
                {
                    "text": "📱 Отправить номер",
                    "request_contact": True
                }
            ]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

# --- Функция отправки письма ---
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

# --- Функция записи в Google Таблицу ---
def log_to_google_sheet(user_name, user_id, phone):
    import requests
    url = "https://script.google.com/macros/s/AKfycbxMCsGnzNxz-Ah597UO9xO8VZhqntUCKlx9MQwPqZcQDt8ipoqBWfvv7YA7DDgR-Wnr6Q/exec"
    payload = {"username": user_name, "user_id": user_id, "text": phone}
    try:
        r = requests.post(url, json=payload, timeout=5)
        if r.status_code == 200:
            logging.info("Номер записан в Google Таблицу")
        else:
            logging.error(f"Ошибка записи: {r.status_code}")
    except Exception as e:
        logging.error(f"Ошибка соединения с Apps Script: {e}")

# --- Инициализация бота и диспетчера ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Состояние: ждём ли номер ---
waiting_for_phone = {}

# --- Обработчик первого сообщения (отправляем клавиатуру) ---
@dp.message_created(F.message.body.text)
async def ask_contact(event: MessageCreated):
    user_id = event.message.sender.user_id
    text = event.message.body.text

    # Игнорируем команды
    if text.startswith("/"):
        return

    # Если уже ждём номер, не отправляем клавиатуру повторно
    if waiting_for_phone.get(user_id):
        return

    # Отправляем клавиатуру
    waiting_for_phone[user_id] = True
    await event.message.answer(
        "Здравствуйте! 👋\n"
        "Нажмите кнопку ниже, чтобы отправить ваш номер телефона.\n"
        "Сергей свяжется с вами в MAX в ближайшее время.",
        reply_markup=get_contact_keyboard()
    )

# --- Обработчик контакта (когда клиент нажимает кнопку) ---
@dp.message_created(F.message.contact)
async def handle_contact(event: MessageCreated):
    user_id = event.message.sender.user_id
    user = event.message.sender
    user_name = user.first_name or user.username or "Неизвестный"
    contact = event.message.contact
    phone_number = contact.phone_number

    # Отправляем данные
    send_phone_email(user_name, user_id, phone_number)
    log_to_google_sheet(user_name, user_id, phone_number)

    # Благодарим и сбрасываем состояние
    waiting_for_phone[user_id] = False
    await event.message.answer(
        f"Спасибо, {user_name}! Номер получен. Сергей свяжется с вами в ближайшее время.",
        reply_markup=None  # убираем клавиатуру
    )

# --- Запуск бота ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
