import os
import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from maxapi import Bot, Dispatcher, F
from maxapi.types import MessageCreated, ReplyKeyboardMarkup, KeyboardButton

# --- Переменные окружения ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))

logging.basicConfig(level=logging.INFO)

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

# --- Функция записи в Google Таблицу (через Apps Script) ---
def log_to_google_sheet(user_name, user_id, phone):
    import requests
    url = "https://script.google.com/macros/s/AKfycbxMCsGnzNxz-Ah597UO9xO8VZhqntUCKlx9MQwPqZcQDt8ipoqBWfvv7YA7DDgR-Wnr6Q/exec"
    payload = {"username": user_name, "user_id": user_id, "text": phone}  # поле "текст" используем для телефона
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

# --- Клавиатура с кнопкой "Отправить номер" ---
def get_contact_keyboard():
    button = KeyboardButton(text="📱 Отправить номер", request_contact=True)
    markup = ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True)
    return markup

# --- Обработчик первого сообщения (запрос номера) ---
@dp.message_created(F.message.body.text)
async def ask_contact(event: MessageCreated):
    # Проверяем, что это не команда и не уже обработанный контакт (чтобы не зациклить)
    if event.message.body.text.startswith("/") or event.message.contact:
        return

    user = event.message.sender
    user_name = user.first_name or user.username or "Неизвестный"
    user_id = user.user_id

    # Отправляем сообщение с клавиатурой
    await event.message.answer(
        f"Здравствуйте, {user_name}! 👋\n"
        "Пожалуйста, нажмите кнопку ниже, чтобы отправить ваш номер телефона.\n"
        "Сергей свяжется с вами в MAX в ближайшее время.",
        reply_markup=get_contact_keyboard()
    )

# --- Обработчик контакта (когда клиент нажимает кнопку) ---
@dp.message_created(F.message.contact)
async def handle_contact(event: MessageCreated):
    user = event.message.sender
    user_name = user.first_name or user.username or "Неизвестный"
    user_id = user.user_id
    contact = event.message.contact
    phone_number = contact.phone_number

    # Отправляем данные на почту и в таблицу
    send_phone_email(user_name, user_id, phone_number)
    log_to_google_sheet(user_name, user_id, phone_number)

    # Благодарим клиента
    await event.message.answer(
        f"Спасибо, {user_name}! Номер получен. Сергей свяжется с вами в ближайшее время.",
        reply_markup=None  # убираем клавиатуру
    )

    # Дополнительно можно записать, что клиент отправил номер, чтобы не повторять
    logging.info(f"Контакт получен от {user_name}, телефон: {phone_number}")

# --- Запуск бота ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
