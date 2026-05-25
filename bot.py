import os
import re
import logging
import json
import random
import copy
import asyncio
import threading
from typing import Dict, List, Any
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.error import BadRequest

# ----------------------------------------------------------
# 0. PERSISTENCE SETUP
# ----------------------------------------------------------
DATA_FILE = "data.json"

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

# ----------------------------------------------------------
# 1. SOZLAMALAR
# ----------------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@sharofiddinovnurislom"
CHANNEL_URL = "https://t.me/sharofiddinovnurislom"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

raw_data = load_data()
user_states: Dict[int, Any] = {int(k): v for k, v in raw_data.items()}
group_quiz_states: Dict[int, Any] = {}  # chat_id -> guruh quiz holati

# ----------------------------------------------------------
# 2. REGEX
# ----------------------------------------------------------
QUESTION_REGEX = re.compile(
    r"(?P<num>\d+)[.)]\s*(?P<question>[^\n]+)\n"
    r"\s*A[.)]\s*(?P<a>[^\n]+)\n"
    r"\s*B[.)]\s*(?P<b>[^\n]+)\n"
    r"\s*C[.)]\s*(?P<c>[^\n]+)\n"
    r"\s*D[.)]\s*(?P<d>[^\n]+)\n"
    r"\s*Javob:\s*(?P<ans>[A-Da-d])",
    re.IGNORECASE | re.MULTILINE
)

EXAMPLE_FORMAT = (
    "📋 *To'g'ri format namunasi:*\n\n"
    "```\n"
    "1. Savol matni\n"
    "A) Birinchi javob\n"
    "B) Ikkinchi javob\n"
    "C) Uchinchi javob\n"
    "D) To'rtinchi javob\n"
    "Javob: A\n\n"
    "2. Ikkinchi savol\n"
    "A) ...\n"
    "B) ...\n"
    "C) ...\n"
    "D) ...\n"
    "Javob: B\n"
    "```"
)

# ----------------------------------------------------------
# 3. YORDAMCHI FUNKSIYALAR
# ----------------------------------------------------------

def safe_md(text: str) -> str:
    """Markdown v1 uchun faqat xavfli belgilarni escape qiladi."""
    for ch in ['_', '*', '[', '`']:
        text = text.replace(ch, f'\\{ch}')
    return text

async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except BadRequest as e:
        logger.error(f"Kanal topilmadi yoki bot admin emas: {CHANNEL_ID} — {e}")
        return False
    except Exception as e:
        logger.error(f"Obuna tekshirishda xato: {e}")
        return False

async def require_subscription(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Obunani tekshiradi. Agar obuna bo'lmasa — xabar yuboradi va False qaytaradi."""
    is_subscribed = await check_subscription(user_id, context)
    if not is_subscribed:
        keyboard = [
            [InlineKeyboardButton("📢 Kanalga obuna bo'lish", url=CHANNEL_URL)],
            [InlineKeyboardButton("✅ Obuna bo'ldim", callback_data="check_sub")]
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ *Botdan foydalanish uchun kanalga obuna bo'lishingiz shart!*\n\n"
                f"📢 Kanal: {CHANNEL_URL}\n\n"
                "Obuna bo'lgandan so'ng, quyidagi tugmani bosing:"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return False
    return True

def parse_text_to_questions(text: str) -> List[Dict]:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.strip() + "\n"
    matches = list(QUESTION_REGEX.finditer(text))
    logger.info(f"Regex {len(matches)} ta savol topdi")
    questions = []
    for match in matches:
        d = match.groupdict()
        ans_letter = d["ans"].upper()
        correct_id = {"A": 0, "B": 1, "C": 2, "D": 3}[ans_letter]
        questions.append({
            "question": d["question"].strip(),
            "options": [
                d["a"].strip(),
                d["b"].strip(),
                d["c"].strip(),
                d["d"].strip()
            ],
            "correct_id": correct_id,
            "answer_letter": ans_letter
        })
    return questions

def initialize_user(user_id: int) -> Dict:
    if user_id not in user_states:
        user_states[user_id] = {
            "groups": [],
            "active_group_id": None,
            "active_questions": [],
            "current_index": 0,
            "score": 0,
            "is_active": False,
            "current_msg_id": None,
            "answered": False,
        }
    else:
        state = user_states[user_id]
        if "groups" not in state:
            old_questions = state.get("questions", [])
            state["groups"] = []
            if old_questions:
                state["groups"].append({
                    "id": 1,
                    "name": "Asosiy testlar",
                    "questions": old_questions
                })
        state.setdefault("active_group_id", None)
        state.setdefault("active_questions", [])
        state.setdefault("current_index", 0)
        state.setdefault("score", 0)
        state.setdefault("is_active", False)
        state.setdefault("current_msg_id", None)
        state.setdefault("answered", False)
        # Eski poll maydonlarini olib tashlash (migration)
        state.pop("timer", None)
        state.pop("current_poll_id", None)
        state.pop("current_poll_msg_id", None)
    return user_states[user_id]

def build_question_message(state: Dict) -> tuple:
    """Savol matni va inline klaviaturasini qaytaradi."""
    idx = state["current_index"]
    questions = state["active_questions"]
    q = questions[idx]
    total = len(questions)

    letters = ["🅰", "🅱", "🅲", "🅳"]
    text = (
        f"❓ *{idx + 1}/{total} — savol:*\n\n"
        f"{safe_md(q['question'])}\n\n"
    )
    for i, opt in enumerate(q["options"]):
        text += f"{letters[i]} {safe_md(opt)}\n"

    keyboard = []
    for i, opt in enumerate(q["options"]):
        keyboard.append([InlineKeyboardButton(
            text=f"{chr(65+i)}) {opt}",
            callback_data=f"ans_{i}"
        )])

    return text, InlineKeyboardMarkup(keyboard)

async def send_question(chat_id: int, state: Dict, context: ContextTypes.DEFAULT_TYPE):
    """Yangi savol xabarini yuboradi."""
    text, markup = build_question_message(state)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=markup
    )
    state["current_msg_id"] = msg.message_id
    state["answered"] = False
    save_data(user_states)

async def finish_quiz(chat_id: int, state: Dict, context: ContextTypes.DEFAULT_TYPE):
    """Shaxsiy quizni yakunlaydi va natija ko'rsatadi."""
    state["is_active"] = False
    save_data(user_states)
    score = state["score"]
    total = len(state["active_questions"])
    percent = int((score / total) * 100) if total > 0 else 0

    if percent >= 90:
        emoji, comment = "🏆", "Zo'r natija!"
    elif percent >= 70:
        emoji, comment = "🥈", "Yaxshi natija!"
    elif percent >= 50:
        emoji, comment = "🥉", "O'rtacha natija."
    else:
        emoji, comment = "📚", "Ko'proq o'qing!"

    stars = "⭐" * (percent // 20) if percent > 0 else ""

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🏁 *Quiz yakunlandi!*\n\n"
            f"{emoji} *{comment}*\n\n"
            f"📊 Natija: *{score}/{total}* ({percent}%)\n"
            f"{stars}\n\n"
            f"Yana boshlash uchun /quiz yuboring."
        ),
        parse_mode="Markdown"
    )

# ----------------------------------------------------------
# 4. GURUH QUIZ FUNKSIYALARI
# ----------------------------------------------------------

async def send_group_question(chat_id: int, gqs: Dict, context: ContextTypes.DEFAULT_TYPE):
    """Guruhda yangi savol yuboradi va taymerni o'rnatadi."""
    idx = gqs["current_index"]
    q = gqs["questions"][idx]
    total = gqs["total"]
    time_limit = gqs["time_limit"]
    letters = ["🅰", "🅱", "🅲", "🅳"]
    if time_limit == 15:
        timer_emoji = "⚡"
    elif time_limit == 30:
        timer_emoji = "⏰"
    else:
        timer_emoji = "⏳"

    time_display = "1 daqiqa" if time_limit == 60 else f"{time_limit} soniya"
    text = (
        f"❓ *Savol {idx + 1}/{total}*\n"
        f"{timer_emoji} Vaqt: *{time_display}*\n\n"
        f"{safe_md(q['question'])}\n\n"
    )
    for i, opt in enumerate(q["options"]):
        text += f"{letters[i]} {safe_md(opt)}\n"

    # Har yangi savol uchun ishtirokchilar javobini tozalash
    for p in gqs["participants"].values():
        p["answered"] = False
        p["last_answer"] = None

    keyboard = [
        [InlineKeyboardButton(f"{chr(65+i)}) {opt}", callback_data=f"gq_ans_{i}")]
        for i, opt in enumerate(q["options"])
    ]
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    gqs["current_msg_id"] = msg.message_id

    # Eski taymerni bekor qilish va yangisini o'rnatish
    job_name = f"gq_timer_{chat_id}"
    for old_job in context.application.job_queue.get_jobs_by_name(job_name):
        old_job.schedule_removal()
    context.application.job_queue.run_once(
        group_question_timeout,
        when=time_limit,
        data={"chat_id": chat_id, "question_index": idx},
        name=job_name
    )


async def group_question_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Taymer tugaganda avtomatik keyingi savolga o'tadi."""
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    question_index = job_data["question_index"]

    gqs = group_quiz_states.get(chat_id)
    if not gqs or not gqs.get("is_active"):
        return
    # Bu savol allaqachon o'tib ketganmi?
    if gqs["current_index"] != question_index:
        return

    await advance_group_question(chat_id, gqs, context)


async def advance_group_question(chat_id: int, gqs: Dict, context: ContextTypes.DEFAULT_TYPE):
    """Natijani ko'rsatib keyingi savolga o'tadi yoki quizni yakunlaydi."""
    q = gqs["questions"][gqs["current_index"]]
    correct = q["correct_id"]
    letters = ["A", "B", "C", "D"]

    # To'g'ri javob berganlar ro'yxati
    correct_names = [
        p["name"] for p in gqs["participants"].values()
        if p.get("last_answer") == correct
    ]

    # Savol klaviaturasini o'chirish
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=gqs["current_msg_id"],
            reply_markup=None
        )
    except Exception:
        pass

    result_lines = [
        f"⏹ *Savol {gqs['current_index'] + 1} yakunlandi!*\n",
        f"✅ To'g'ri javob: *{letters[correct]}) {safe_md(q['options'][correct])}*\n",
    ]
    if correct_names:
        names_str = ", ".join(safe_md(n) for n in correct_names)
        result_lines.append(f"🎯 To'g'ri javob berganlar: {names_str}")
    else:
        result_lines.append("😔 Hech kim to'g'ri javob bermadi.")

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(result_lines),
        parse_mode="Markdown"
    )

    gqs["current_index"] += 1
    if gqs["current_index"] >= gqs["total"]:
        await finish_group_quiz(chat_id, gqs, context)
    else:
        await asyncio.sleep(2)
        await send_group_question(chat_id, gqs, context)


async def finish_group_quiz(chat_id: int, gqs: Dict, context: ContextTypes.DEFAULT_TYPE):
    """Guruh quizini yakunlaydi va natijalar jadvalini chiqaradi."""
    gqs["is_active"] = False
    participants = gqs["participants"]
    total = gqs["total"]

    if not participants:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🏁 *Quiz yakunlandi!*\n\n😔 Hech bir ishtirokchi qatnashmadi.",
            parse_mode="Markdown"
        )
        group_quiz_states.pop(chat_id, None)
        return

    sorted_p = sorted(participants.items(), key=lambda x: x[1]["score"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]

    lines = [f"🏆 *NATIJALAR JADVALI — {safe_md(gqs['group_name'])}*\n"]
    for i, (uid, p) in enumerate(sorted_p):
        medal = medals[i] if i < 3 else f"{i + 1}."
        score = p["score"]
        percent = int((score / total) * 100)
        stars = "⭐" * (percent // 20) if percent > 0 else "—"
        lines.append(f"{medal} {safe_md(p['name'])}: *{score}/{total}* ({percent}%) {stars}")

    winner = sorted_p[0][1]["name"] if sorted_p else "—"
    lines.append(f"\n🎉 G'olib: *{safe_md(winner)}*!")
    lines.append(f"👥 Ishtirokchilar: *{len(participants)}*")

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="Markdown"
    )
    group_quiz_states.pop(chat_id, None)


# ----------------------------------------------------------
# 5. HANDLERLAR
# ----------------------------------------------------------

# ── Callback data prefikslari ──
# share:GROUP_ID          → ulashish havolasini ko'rsatish
# startquiz:GROUP_ID      → quizni boshlash
# ans:INDEX               → javob tugmasi
# newgroup                → yangi guruh yaratish
# showgroups              → barcha guruhlarni ko'rsatish
# nextq                   → keyingi savol
# done                    → o'chirilgan tugma

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # Obunani tekshirish (deep link bo'lsa ham)
    if not await require_subscription(user.id, update.effective_chat.id, context):
        return

    # Deep link orqali ulashilgan test
    if args and args[0].startswith("share-"):
        # Format: share-AUTHORID-GROUPID
        parts = args[0].split("-")
        if len(parts) == 3:
            try:
                author_id = int(parts[1])
                group_id = int(parts[2])
                author_state = user_states.get(author_id)
                if author_state:
                    shared_group = next(
                        (g for g in author_state.get("groups", []) if g["id"] == group_id), None
                    )
                    if shared_group:
                        user_id = user.id
                        state = initialize_user(user_id)
                        new_group_id = max((g["id"] for g in state["groups"]), default=0) + 1
                        state["groups"].append({
                            "id": new_group_id,
                            "name": f"{shared_group['name']} (Ulashilgan)",
                            "questions": copy.deepcopy(shared_group["questions"])
                        })
                        save_data(user_states)
                        keyboard = [[InlineKeyboardButton(
                            "🚀 Testni boshlash",
                            callback_data=f"startquiz:{new_group_id}"
                        )]]
                        await update.message.reply_text(
                            f"🎉 *Ulashilgan test qabul qilindi!*\n\n"
                            f"📁 Guruh: *{shared_group['name']}*\n"
                            f"📊 Savollar soni: *{len(shared_group['questions'])}* ta\n\n"
                            f"Test sizning ro'yxatingizga saqlandi:",
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        return
            except ValueError:
                pass
        await update.message.reply_text("❌ Ulashilgan test havolasi yaroqsiz.")
        return

    # Deep link orqali guruhda quiz boshlash
    if args and args[0].startswith("startquiz_"):
        parts = args[0].split("_")
        if len(parts) == 3:
            try:
                host_user_id = int(parts[1])
                group_id = int(parts[2])
                
                # Guruhda ishga tushayotganini tekshirish
                if update.effective_chat.type == "private":
                    await update.message.reply_text("❌ Bu havola guruhda quiz boshlash uchun mo'ljallangan. Iltimos, havolani guruhga yuboring yoki guruhni tanlang.")
                    return
                
                # Boshlagan odam host ekanligiga ishonch hosil qilish
                if user.id != host_user_id:
                    await update.message.reply_text("❌ Faqat quiz egasi guruhda quizni boshlashi mumkin!")
                    return
                
                if update.effective_chat.id in group_quiz_states and group_quiz_states[update.effective_chat.id].get("is_active"):
                    await update.message.reply_text("⚠️ Guruhda allaqachon faol quiz bor! Avval tugasin.")
                    return

                keyboard = [
                    [InlineKeyboardButton("⚡ 15 soniya (tezkor)", callback_data=f"gq_time:{host_user_id}:{group_id}:15")],
                    [InlineKeyboardButton("⏰ 30 soniya (oddiy)",  callback_data=f"gq_time:{host_user_id}:{group_id}:30")],
                    [InlineKeyboardButton("⏳ 1 daqiqa (uzoq)",    callback_data=f"gq_time:{host_user_id}:{group_id}:60")],
                ]
                await update.message.reply_text(
                    "⏱ *Har bir savol uchun vaqt limitini tanlang:*\n\n"
                    "• ⚡ 15 soniya — tezkor rejim 🚀\n"
                    "• ⏰ 30 soniya — oddiy rejim 🧘\n"
                    "• ⏳ 1 daqiqa — uzoq rejim 🐢",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            except ValueError:
                pass

    if update.effective_chat.type != "private":
        return

    await show_dashboard(update, context)


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_message=False):
    """Bosh menyuni (dashboard) ko'rsatadi."""
    user = update.effective_user
    text = (
        f"👋 *Salom, {safe_md(user.first_name)}!*\n\n"
        "Quiz botga xush kelibsiz! Nima qilamiz?"
    )
    keyboard = [
        [InlineKeyboardButton("➕ Test yaratish (/new_quiz)", callback_data="dash_new")],
        [InlineKeyboardButton("🗂 Mening quizlarim", callback_data="dash_myquizzes")],
        [InlineKeyboardButton("📢 Kanalimiz", url=CHANNEL_URL)]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    
    if edit_message and update.callback_query:
        await update.callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        if update.message:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
        elif update.callback_query:
            await update.callback_query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(EXAMPLE_FORMAT, parse_mode="Markdown")

async def new_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.effective_chat.type != "private":
        return
    if not await require_subscription(user_id, update.effective_chat.id, context):
        return
    await update.message.reply_text(
        "➕ *Test yaratish*\n\n"
        "Menga testlarni quyidagi formatda yuboring va men yangi quiz yarataman:\n\n" + EXAMPLE_FORMAT +
        "\n\n_Eslatma: Har bir yuborgan matningiz yangi quiz guruhini yaratadi._",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bosh menyu", callback_data="dash_main")]])
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat = update.effective_chat

    # Faqat shaxsiy chatda ishlaydi
    if chat.type != "private":
        return

    # Obunani tekshirish
    if not await require_subscription(user_id, chat.id, context):
        return

    text = update.message.text

    logger.info(f"Matn qabul qilindi (user_id={user_id}), uzunlik={len(text)}")
    new_questions = parse_text_to_questions(text)

    if not new_questions:
        await update.message.reply_text(
            "❌ *Xato:* Siz yuborgan matndan test formati topilmadi.\n\n" + EXAMPLE_FORMAT,
            parse_mode="Markdown"
        )
        return

    state = initialize_user(user_id)

    if not state.get("groups"):
        new_group_id = 1
    else:
        new_group_id = max(g["id"] for g in state["groups"]) + 1
        
    group_name = f"Quiz #{new_group_id}"
    new_group = {
        "id": new_group_id,
        "name": group_name,
        "questions": new_questions
    }
    state.setdefault("groups", []).append(new_group)
    current_group = new_group

    save_data(user_states)

    keyboard = [
            [InlineKeyboardButton("⚙️ Quizni boshqarish", callback_data=f"dash_quiz:{current_group['id']}")],
            [InlineKeyboardButton("⬅️ Bosh menyu", callback_data="dash_main")]
    ]
    await update.message.reply_text(
        f"✅ *Yangi quiz yaratildi!*\n\n"
        f"📁 Nomi: *{current_group['name']}*\n"
        f"📊 Savollar soni: *{len(new_questions)}* ta.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def start_quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        return
    await show_dashboard(update, context)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data

    await query.answer()

    logger.info(f"Callback: user={user_id}, chat={chat_id}, data={data}")

    # ── Obuna tekshirish tugmasi ─────────────────────────────
    if data == "check_sub":
        is_subscribed = await check_subscription(user_id, context)
        if is_subscribed:
            await query.message.edit_text(
                "✅ *Obuna tasdiqlandi!*\n\n"
                "Endi botdan erkin foydalanishingiz mumkin.\n"
                "Testlarni yuboring yoki /start_quiz buyrug'ini bosing.",
                parse_mode="Markdown"
            )
        else:
            keyboard = [
                [InlineKeyboardButton("📢 Kanalga obuna bo'lish", url=CHANNEL_URL)],
                [InlineKeyboardButton("✅ Obuna bo'ldim", callback_data="check_sub")]
            ]
            await query.message.edit_text(
                "❌ *Siz hali kanalga obuna bo'lmagansiz!*\n\n"
                f"📢 Kanal: {CHANNEL_URL}\n\n"
                "Iltimos, avval kanalga obuna bo'ling, keyin tugmani bosing.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    # ── Boshqa barcha tugmalar uchun obuna tekshirish ─────────
    if not await require_subscription(user_id, query.message.chat_id, context):
        return

    # ── Dashboard callbacklar ─────────────────────────────────
    if data == "dash_main":
        await show_dashboard(update, context, edit_message=True)
        return
        
    if data == "dash_new":
        await query.message.edit_text(
            "➕ *Test yaratish*\n\n"
            "Menga testlarni quyidagi formatda yuboring va men yangi quiz yarataman:\n\n" + EXAMPLE_FORMAT +
            "\n\n_Eslatma: Har bir yuborgan matningiz yangi quiz guruhini yaratadi._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bosh menyu", callback_data="dash_main")]])
        )
        return
        
    if data == "dash_myquizzes":
        state = initialize_user(user_id)
        groups = state.get("groups", [])
        
        if not groups:
            await query.message.edit_text(
                "📭 Sizda hali quizlar yo'q.\n\n"
                "Yangi quiz yaratish uchun bosh menyuga qaytib, test matnini yuboring.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Bosh menyu", callback_data="dash_main")]])
            )
            return
            
        keyboard = []
        for g in groups:
            count = len(g.get("questions", []))
            keyboard.append([
                InlineKeyboardButton(f"📁 {g['name']} ({count} ta savol)", callback_data=f"dash_quiz:{g['id']}")
            ])
        keyboard.append([InlineKeyboardButton("⬅️ Bosh menyu", callback_data="dash_main")])
        
        await query.message.edit_text(
            "🗂 *Mening quizlarim*\n\nBoshqarish uchun quizni tanlang:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
        
    if data.startswith("dash_quiz:"):
        group_id = int(data.split(":")[1])
        state = initialize_user(user_id)
        group = next((g for g in state.get("groups", []) if g["id"] == group_id), None)
        
        if not group:
            await query.answer("❌ Quiz topilmadi.")
            return
            
        count = len(group.get("questions", []))
        bot_me = await context.bot.get_me()
        startgroup_url = f"https://t.me/{bot_me.username}?startgroup=startquiz_{user_id}_{group_id}"
        
        keyboard = [
            [InlineKeyboardButton("🚀 Shaxsiy chatda ishlash", callback_data=f"startquiz:{group_id}")],
            [InlineKeyboardButton("👥 Guruhda boshlash", url=startgroup_url)],
            [InlineKeyboardButton("🗑 O'chirish", callback_data=f"dash_del:{group_id}")],
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="dash_myquizzes")]
        ]
        await query.message.edit_text(
            f"📁 *Quiz:* {safe_md(group['name'])}\n"
            f"📊 *Savollar:* {count} ta\n\n"
            "Nima qilmoqchisiz?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
        
    if data.startswith("dash_del:"):
        group_id = int(data.split(":")[1])
        state = initialize_user(user_id)
        groups = state.get("groups", [])
        
        new_groups = [g for g in groups if g["id"] != group_id]
        state["groups"] = new_groups
        save_data(user_states)
        
        await query.answer("✅ Quiz o'chirildi!")
        
        # Ro'yxatni yangilash
        keyboard = []
        for g in new_groups:
            count = len(g.get("questions", []))
            keyboard.append([
                InlineKeyboardButton(f"📁 {g['name']} ({count} ta savol)", callback_data=f"dash_quiz:{g['id']}")
            ])
        keyboard.append([InlineKeyboardButton("⬅️ Bosh menyu", callback_data="dash_main")])
        
        text = "🗂 *Mening quizlarim*\n\nBoshqarish uchun quizni tanlang:" if new_groups else "📭 Sizda hali quizlar yo'q."
        await query.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if data.startswith("gq_group:"):
        parts = data.split(":")
        if len(parts) != 3:
            return
        try:
            host_user_id = int(parts[1])
            group_id = int(parts[2])
        except ValueError:
            return

        if user_id != host_user_id:
            await query.answer("❌ Faqat quiz tashkilotchisi tanlashi mumkin!", show_alert=True)
            return

        keyboard = [
            [InlineKeyboardButton("⚡ 15 soniya (tezkor)", callback_data=f"gq_time:{host_user_id}:{group_id}:15")],
            [InlineKeyboardButton("⏰ 30 soniya (oddiy)",  callback_data=f"gq_time:{host_user_id}:{group_id}:30")],
            [InlineKeyboardButton("⏳ 1 daqiqa (uzoq)",    callback_data=f"gq_time:{host_user_id}:{group_id}:60")],
        ]
        try:
            await query.message.edit_text(
                "⏱ *Har bir savol uchun vaqt limitini tanlang:*\n\n"
                "• ⚡ 15 soniya — tezkor rejim 🚀\n"
                "• ⏰ 30 soniya — oddiy rejim 🧘\n"
                "• ⏳ 1 daqiqa — uzoq rejim 🐢",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass
        return

    # ── Guruh quiz: vaqt tanlash va quiz boshlash ─────────────
    if data.startswith("gq_time:"):
        parts = data.split(":")
        if len(parts) != 4:
            return
        try:
            host_user_id = int(parts[1])
            group_id = int(parts[2])
            time_limit = int(parts[3])
        except ValueError:
            return

        if user_id != host_user_id:
            await query.answer("❌ Faqat quiz tashkilotchisi boshlashi mumkin!", show_alert=True)
            return

        host_state = user_states.get(host_user_id)
        if not host_state:
            await query.message.reply_text("❌ Test ma'lumotlari topilmadi.")
            return

        group = next((g for g in host_state.get("groups", []) if g["id"] == group_id), None)
        if not group or not group.get("questions"):
            await query.message.reply_text("❌ Bu guruhda savollar topilmadi.")
            return

        questions = copy.deepcopy(group["questions"])
        random.shuffle(questions)
        for q_item in questions:
            correct_text = q_item["options"][q_item["correct_id"]]
            random.shuffle(q_item["options"])
            q_item["correct_id"] = q_item["options"].index(correct_text)
            q_item["answer_letter"] = {0: "A", 1: "B", 2: "C", 3: "D"}[q_item["correct_id"]]

        group_quiz_states[chat_id] = {
            "host_user_id": host_user_id,
            "group_name": group["name"],
            "questions": questions,
            "total": len(questions),
            "current_index": 0,
            "time_limit": time_limit,
            "participants": {},
            "is_active": True,
            "current_msg_id": None,
        }
        try:
            await query.message.edit_text(
                f"🚀 *{safe_md(group['name'])}* boshlanmoqda!\n\n"
                f"⏱ Har savol: *{time_limit} soniya*\n"
                f"📊 Savollar: *{len(questions)}* ta\n\n"
                "⏳ Tayyor bo'ling...",
                parse_mode="Markdown"
            )
        except Exception:
            pass
        await asyncio.sleep(3)
        await send_group_question(chat_id, group_quiz_states[chat_id], context)
        return

    # ── Guruh quiz: javob ─────────────────────────────────────
    if data.startswith("gq_ans_"):
        try:
            chosen = int(data.split("_")[2])
        except (IndexError, ValueError):
            return

        gqs = group_quiz_states.get(chat_id)
        if not gqs or not gqs.get("is_active"):
            await query.answer("⚠️ Quiz faol emas.", show_alert=True)
            return

        # Ishtirokchini ro'yxatdan o'tkazish
        if user_id not in gqs["participants"]:
            gqs["participants"][user_id] = {
                "name": query.from_user.first_name,
                "score": 0,
                "answered": False,
                "last_answer": None,
            }

        p = gqs["participants"][user_id]
        if p.get("answered"):
            await query.answer("✋ Siz allaqachon javob berdingiz!", show_alert=True)
            return

        q_item = gqs["questions"][gqs["current_index"]]
        correct = q_item["correct_id"]
        is_correct = (chosen == correct)

        p["answered"] = True
        p["last_answer"] = chosen
        if is_correct:
            p["score"] += 1
            await query.answer("✅ To'g'ri javob!", show_alert=False)
        else:
            letters = ["A", "B", "C", "D"]
            await query.answer(f"❌ Noto'g'ri! To'g'ri: {letters[correct]}", show_alert=False)

        # Agar barcha ishtirokchilar javob bersa — darhol keyingiga o'tish
        if (len(gqs["participants"]) > 1 and
                all(p2.get("answered") for p2 in gqs["participants"].values())):
            job_name = f"gq_timer_{chat_id}"
            for old_job in context.application.job_queue.get_jobs_by_name(job_name):
                old_job.schedule_removal()
            await advance_group_question(chat_id, gqs, context)
        return

    # ── Yangi guruh ──────────────────────────────────────────
    if data == "newgroup":
        state = initialize_user(user_id)
        new_group_id = max((g["id"] for g in state["groups"]), default=0) + 1
        group_name = f"{new_group_id}-guruh"
        state["groups"].append({"id": new_group_id, "name": group_name, "questions": []})
        save_data(user_states)
        await query.message.reply_text(
            f"✅ *{group_name}* yaratildi! Endi bu guruh uchun testlarni yuboring.",
            parse_mode="Markdown"
        )
        return

    # ── Barcha guruhlar ──────────────────────────────────────
    if data == "showgroups":
        state = initialize_user(user_id)
        if not state.get("groups"):
            await query.message.reply_text("📭 Guruhlar yo'q.")
            return
        keyboard = []
        for g in state["groups"]:
            count = len(g.get("questions", []))
            keyboard.append([
                InlineKeyboardButton(
                    f"📁 {g['name']} ({count} ta)",
                    callback_data=f"startquiz:{g['id']}"
                ),
                InlineKeyboardButton(
                    "🔗 Ulashish",
                    callback_data=f"share:{g['id']}"
                )
            ])
        await query.message.reply_text(
            "📚 *Guruhni tanlang:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ── Ulashish havolasi ─────────────────────────────────────
    # callback_data format: "share:GROUP_ID"
    if data.startswith("share:"):
        try:
            group_id = int(data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("❌ Noto'g'ri so'rov.")
            return

        bot_username = context.bot.username
        # Deep link uchun tire ishlatamiz (underscore emas), chunki
        # Telegram start parametri faqat a-z, A-Z, 0-9, _, - qo'llaydi
        share_link = f"https://t.me/{bot_username}?start=share-{user_id}-{group_id}"

        keyboard = [
            [InlineKeyboardButton("📤 Havolani ulashish", url=share_link)]
        ]
        await query.message.reply_text(
            f"🔗 Testni ulashish havolasi:\n\n"
            f"{share_link}\n\n"
            f"👆 Tugmani bosing yoki havolani nusxalab do'stlaringizga yuboring.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # ── Quizni boshlash ───────────────────────────────────────
    # callback_data format: "startquiz:GROUP_ID"
    if data.startswith("startquiz:"):
        try:
            group_id = int(data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("❌ Noto'g'ri so'rov.")
            return

        state = initialize_user(user_id)
        group = next((g for g in state["groups"] if g["id"] == group_id), None)

        if not group or not group.get("questions"):
            await query.message.reply_text("❌ Bu guruhda savollar topilmadi.")
            return

        active_questions = copy.deepcopy(group["questions"])
        random.shuffle(active_questions)
        for q in active_questions:
            correct_text = q["options"][q["correct_id"]]
            random.shuffle(q["options"])
            q["correct_id"] = q["options"].index(correct_text)
            q["answer_letter"] = {0: "A", 1: "B", 2: "C", 3: "D"}[q["correct_id"]]

        state["active_group_id"] = group_id
        state["active_questions"] = active_questions
        state["current_index"] = 0
        state["score"] = 0
        state["is_active"] = True
        state["current_msg_id"] = None
        state["answered"] = False
        save_data(user_states)

        total = len(active_questions)
        await query.message.reply_text(
            f"🚀 *{safe_md(group['name'])}* boshlandi!\n"
            f"📊 Jami: *{total}* ta savol\n\n"
            f"Javobni bosing — keyingi savol darhol keladi!",
            parse_mode="Markdown"
        )
        await send_question(chat_id, state, context)
        return

    # ── Javob tugmasi ─────────────────────────────────────────
    # callback_data format: "ans_INDEX"
    if data.startswith("ans_"):
        state = user_states.get(user_id)
        if not state or not state.get("is_active"):
            return

        if state.get("answered"):
            return  # Ikki marta bosilmaslik uchun

        try:
            chosen = int(data.split("_")[1])
        except (IndexError, ValueError):
            return

        state["answered"] = True
        idx = state["current_index"]
        questions = state["active_questions"]
        q = questions[idx]
        correct = q["correct_id"]
        total = len(questions)
        letters = ["A", "B", "C", "D"]

        is_correct = (chosen == correct)
        if is_correct:
            state["score"] += 1
            result_text = "✅ *To'g'ri!*"
        else:
            result_text = f"❌ *Noto'g'ri!* To'g'ri javob: *{letters[correct]}\\) {q['options'][correct]}*"

        # Tugmalarni yangilash — to'g'ri/noto'g'ri ranglar
        new_keyboard = []
        for i, opt in enumerate(q["options"]):
            if i == correct:
                icon = "✅"
            elif i == chosen:
                icon = "❌"
            else:
                icon = "◾"
            new_keyboard.append([InlineKeyboardButton(
                f"{icon} {letters[i]}) {opt}",
                callback_data="done"
            )])

        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(new_keyboard)
            )
        except Exception:
            pass

        state["current_index"] += 1
        save_data(user_states)

        # Natija xabari + keyingi savol
        if state["current_index"] >= total:
            await query.message.reply_text(result_text, parse_mode="Markdown")
            await finish_quiz(chat_id, state, context)
        else:
            next_keyboard = [[InlineKeyboardButton(
                "➡️ Keyingi savol",
                callback_data="nextq"
            )]]
            await query.message.reply_text(
                result_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(next_keyboard)
            )
        return

    # ── Keyingi savol tugmasi ─────────────────────────────────
    if data == "nextq":
        state = user_states.get(user_id)
        if not state or not state.get("is_active"):
            return
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await send_question(chat_id, state, context)
        return

    # ── done (tugmalar o'chirilgan) ───────────────────────────
    if data == "done":
        return

async def mygroups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchining test guruhlarini ko'rsatadi (faqat shaxsiy chatda)."""
    user_id = update.effective_user.id
    chat = update.effective_chat

    if chat.type != "private":
        return

    if not await require_subscription(user_id, chat.id, context):
        return

    state = initialize_user(user_id)
    groups = state.get("groups", [])

    if not groups:
        await update.message.reply_text(
            "📭 Sizda hali guruhlar yo'q.\n\nTest matnini yuboring!"
        )
        return

    keyboard = []
    for g in groups:
        count = len(g.get("questions", []))
        keyboard.append([
            InlineKeyboardButton(
                f"📁 {g['name']} ({count} ta)",
                callback_data=f"startquiz:{g['id']}"
            ),
            InlineKeyboardButton("🔗 Ulashish", callback_data=f"share:{g['id']}")
        ])
    keyboard.append([InlineKeyboardButton("➕ Yangi guruh", callback_data="newgroup")])

    await update.message.reply_text(
        "📚 *Sizning test guruhlaringiz:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def keep_alive_ping(context: ContextTypes.DEFAULT_TYPE):
    logger.info("📡 Keep-alive: Bot faol holatda...")
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if url:
        try:
            import urllib.request
            urllib.request.urlopen(url)
            logger.info(f"Ping yuborildi: {url}")
        except Exception as e:
            logger.error(f"Ping xatosi: {e}")

# ----------------------------------------------------------
# 5. DUMMY HTTP SERVER (Render uchun)
# ----------------------------------------------------------

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    logger.info(f"Dummy server port {port} da ishga tushdi")
    server.serve_forever()

# ----------------------------------------------------------
# 6. MAIN
# ----------------------------------------------------------

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN topilmadi! .env faylini tekshiring.")
        return

    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    if app.job_queue:
        app.job_queue.run_repeating(keep_alive_ping, interval=900, first=10)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("new_quiz", new_quiz_cmd))
    # Quiz buyruqlari: shaxsiy chatda ham, guruhda ham ishlaydi
    app.add_handler(CommandHandler("quiz",       start_quiz_cmd))
    app.add_handler(CommandHandler("start_quiz", start_quiz_cmd))
    app.add_handler(CommandHandler("startquiz",  start_quiz_cmd))
    # Faqat shaxsiy chat buyruqlari
    app.add_handler(CommandHandler("mygroups", mygroups_cmd))
    # Shaxsiy chatda kelgan test matni
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_text
    ))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("✅ Bot ishga tushdi!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
