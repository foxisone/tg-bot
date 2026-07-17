"""
Групповой ИИ-бот на базе Google Gemini (БЕСПЛАТНО).

Установка:
    pip3 install python-telegram-bot==21.4 google-generativeai apscheduler

Запуск:
    export TELEGRAM_TOKEN="токен от BotFather"
    export GEMINI_API_KEY="ключ с aistudio.google.com"
    python3 bot_gemini.py
"""

import os
import re
import json
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import google.generativeai as genai

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

DATA_FILE = Path(__file__).parent / "memory.json"

genai.configure(api_key=GEMINI_API_KEY)

# --- характер бота, меняй под себя ---
SYSTEM_PROMPT = """Ты — саркастичный, дерзкий ИИ-агент в групповом чате пары.
Ты подкалываешь, замечаешь двойные стандарты, шутишь, но по делу.
Отвечай коротко, живо, на русском, без занудства. Материшься если в тему.

У тебя есть специальные команды — вставляй их в конец ответа когда нужно:
- Запомнить факт: [ЗАПОМНИ: текст факта]
- Поставить напоминание: [НАПОМНИ: 2026-07-18T19:00:00 | текст напоминания]

Примеры:
  Пользователь говорит что у него день рождения 5 августа → добавь [ЗАПОМНИ: день рождения Егора — 5 августа]
  Просят напомнить купить хлеб в 18:00 → добавь [НАПОМНИ: 2026-07-17T18:00:00 | купить хлеб]

Если в сообщении нет вопроса или задачи для тебя — не отвечай, просто напиши пустую строку."""


def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {}


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def get_chat(chat_id):
    data = load_data()
    chat = data.setdefault(str(chat_id), {"facts": [], "history": []})
    return data, chat


scheduler = AsyncIOScheduler()
tg_app = None


async def send_reminder(chat_id, text):
    await tg_app.bot.send_message(chat_id=chat_id, text=f"⏰ Напоминание: {text}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    user = update.message.from_user.first_name or "Кто-то"
    text = update.message.text

    data, chat = get_chat(chat_id)

    facts = "\n".join(f"- {f}" for f in chat["facts"][-50:]) or "(пока пусто)"
    system = f"{SYSTEM_PROMPT}\n\nЗапомненные факты об участниках:\n{facts}\nТекущее время: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}"

    model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=system)

    # История последних 20 сообщений
    history = [{"role": m["role"], "parts": [m["content"]]} for m in chat["history"][-20:]]
    session = model.start_chat(history=history)

    response = session.send_message(f"{user}: {text}")
    reply = response.text.strip()

    # Сохраняем в историю
    chat["history"].append({"role": "user", "content": f"{user}: {text}"})
    chat["history"].append({"role": "model", "content": reply})

    # Парсим [ЗАПОМНИ: ...]
    for fact in re.findall(r'\[ЗАПОМНИ:\s*(.+?)\]', reply):
        chat["facts"].append(fact.strip())

    # Парсим [НАПОМНИ: дата | текст]
    for when_iso, remind_text in re.findall(r'\[НАПОМНИ:\s*(.+?)\s*\|\s*(.+?)\]', reply):
        try:
            run_date = datetime.fromisoformat(when_iso.strip())
            scheduler.add_job(
                send_reminder, "date", run_date=run_date,
                args=[chat_id, remind_text.strip()],
                misfire_grace_time=3600
            )
        except Exception:
            pass

    save_data(data)

    # Убираем служебные теги из ответа перед отправкой
    clean = re.sub(r'\[ЗАПОМНИ:.*?\]', '', reply)
    clean = re.sub(r'\[НАПОМНИ:.*?\]', '', clean).strip()

    if clean:
        await update.message.reply_text(clean)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("На связи. Пишите — запоминаю, напоминаю, подкалываю.")


def main():
    global tg_app
    tg_app = Application.builder().token(TELEGRAM_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    scheduler.start()
    tg_app.run_polling()


if __name__ == "__main__":
    main()
