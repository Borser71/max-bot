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

last_client_id = None

def send_email(text, user_name, user_id):
    subject = f"Новое сообщение из MAX от {user_name} (ID: {user_id})"
    body = f"От: {user_name}\nID: {user_id}\nСообщение:\n{text}"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        logging.info("Email отправлен")
    except Exception as e:
        logging.error(f"Ошибка отправки email: {e}")

def log_to_google_sheet(user_name, user_id, text):
    import requests
    url = "https://script.google.com/macros/s/AKfycbxMCsGnzNxz-Ah597UO9xO8VZhqntUCKlx9MQwPqZcQDt8ipoqBWfvv7YA7DDgR-Wnr6Q/exec"
    payload = {"username": user_name, "user_id": user_id, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=5)
        if r.status_code == 200:
            logging.info("Запись в Google Таблицу выполнена")
        else:
            logging.error(f"Ошибка записи в таблицу: {r.status_code}")
    except Exception as e:
        logging.error(f"Ошибка соединения с Apps Script: {e}")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message_created(F.message.body.text.startswith("/reply"))
async def reply_to_client(event: MessageCreated):
    global last_client_id
    text = event.message.body.text
    reply_text = text.replace("/reply", "").strip()
    if not reply_text:
        await event.message.answer("Напишите: /reply Текст ответа")
        return
    if not last_client_id:
        await event.message.answer("Нет недавних клиентов для ответа.")
        return
    try:
        await bot.send_message(chat_id=last_client_id, text=reply_text)
        await event.message.answer(f"Ответ отправлен клиенту {last_client_id}")
        logging.info(f"Ответ отправлен клиенту {last_client_id}")
    except Exception as e:
        await event.message.answer(f"Ошибка отправки: {e}")
        logging.error(f"Ошибка отправки клиенту: {e}")

@dp.message_created(F.message.body.text)
async def handle_message(event: MessageCreated):
    global last_client_id
    user = event.message.sender
    user_name = user.first_name or user.username or "Неизвестный"
    user_id = user.user_id
    text = event.message.body.text
    last_client_id = user_id
    if text:
        send_email(text, user_name, user_id)
        log_to_google_sheet(user_name, user_id, text)
        await event.message.answer("Ваше сообщение получено! Я передам его Сергею. Обычно отвечаю в течение часа.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
