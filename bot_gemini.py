import os
import re
import json
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from google import genai
from google.genai import types

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

DATA_FILE = Path(__file__).parent / "memory.json"

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """Ты — саркастичный, дерзкий ИИ-агент в групповом чате пары.
Ты подкалываешь, замечаешь двойные стандарты, шутишь, но по делу.
Отвечай коротко, живо, на русском, без занудства. Материшься если в тему.

У тебя есть специальные команды — вставляй их в конец ответа когда нужно:
- Запомнить факт: [ЗАПОМНИ: текст факта]
- Поставить напоминание: [НАПОМНИ: 2026-07-18T19:00:00 | текст напоминания]

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
    system = f"{SYSTEM_PROMPT}\n\nЗапомненные факты:\n{facts}\nТекущее время: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}"

    contents = []
    for msg in chat["history"][-20:]:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=f"{user}: {text}")]))

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system),
    )

    reply = response.text.strip() if response.text else ""

    chat["history"].append({"role": "user", "content": f"{user}: {text}"})
    chat["history"].append({"role": "model", "content": reply})

    for fact in re.findall(r'\[ЗАПОМНИ:\s*(.+?)\]', reply):
        chat["facts"].append(fact.strip())

    for when_iso, remind_text in re.findall(r'\[НАПОМНИ:\s*(.+?)\s*\|\s*(.+?)\]', reply):
        try:
            run_date = datetime.fromisoformat(when_iso.strip())
            scheduler.add_job(send_reminder, "date", run_date=run_date,
                              args=[chat_id, remind_text.strip()], misfire_grace_time=3600)
        except Exception:
            pass

    save_data(data)

    clean = re.sub(r'\[ЗАПОМНИ:.*?\]', '', reply)
    clean = re.sub(r'\[НАПОМНИ:.*?\]', '', clean).strip()

    if clean:
        await update.message.reply_text(clean)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("На связи.")


async def post_init(application):
    scheduler.start()


def main():
    global tg_app
    tg_app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    tg_app.run_polling()


if __name__ == "__main__":
    main()
