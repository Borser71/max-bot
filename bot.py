import os
import logging
import smtplib
from email.mime.text import MIMEText
from max_chatbot_python import Bot

# --- Переменные окружения ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))

logging.basicConfig(level=logging.INFO)

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

def handle_message(message):
    user = message.from_user
    user_name = user.first_name or user.username or "Неизвестный"
    user_id = user.id
    text = message.text

    if text:
        send_email(text, user_name, user_id)
        log_to_google_sheet(user_name, user_id, text)
        # Отвечаем клиенту
        message.reply("Ваше сообщение получено! Я передам его Сергею. Обычно отвечаю в течение часа.")

if __name__ == "__main__":
    bot = Bot(BOT_TOKEN)           # ← исправлено: без token=
    bot.message_handler(handle_message)
    bot.run_polling()
