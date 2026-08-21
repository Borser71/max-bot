import os
import asyncio
import logging
import smtplib
import re
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

def count_digits(text: str) -> int:
    """Подсчитывает количество цифр в строке"""
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

waiting_for_phone = {}

@dp.message_created(F.message.body.text)
async def handle_message(event: MessageCreated):
    user_id = event.message.sender.user_id
    text = event.message.body.text.strip()

    # --- Команда /cancel ---
    if text.startswith("/cancel"):
        if waiting_for_phone.get(user_id):
            waiting_for_phone[user_id] = False
            await event.message.answer("Вы отменили ввод номера. Если передумаете, просто напишите ещё раз.")
        else:
            await event.message.answer("Вы не вводили номер, так что отменять нечего.")
        return

    # --- Пропускаем другие команды ---
    if text.startswith("/"):
        return

    # --- Если уже ждём номер ---
    if waiting_for_phone.get(user_id):
        # Проверяем количество цифр
        digit_count = count_digits(text)
        if digit_count < 10:
            await event.message.answer(
                f"Пожалуйста, введите номер полностью (минимум 10 цифр).\n"
                f"Сейчас введено {digit_count} цифр."
            )
            return  # Не сбрасываем состояние — клиент может попробовать снова

        # Номер принят
        phone = text
        user = event.message.sender
        user_name = user.first_name or user.username or "Неизвестный"
        send_phone_email(user_name, user_id, phone)
        log_to_google_sheet(user_name, user_id, phone)
        await event.message.answer(
            "Спасибо! Номер получен. Сергей свяжется с вами в MAX в ближайшее время."
        )
        waiting_for_phone[user_id] = False
        return

    # --- Первое сообщение ---
    user = event.message.sender
    user_name = user.first_name or user.username or "Неизвестный"
    waiting_for_phone[user_id] = True
    await event.message.answer(
        f"Здравствуйте, {user_name}! 👋\n"
        "Пожалуйста, напишите ваш номер телефона в ответном сообщении.\n"
        "(Минимум 10 цифр)"
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
