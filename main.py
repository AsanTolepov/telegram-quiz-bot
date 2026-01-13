import asyncio
import re
import random
import logging
import math
import json
import os
import sys
from dotenv import load_dotenv # .env faylni o'qish uchun
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, PollAnswer,
    InlineQueryResultArticle, InputTextMessageContent
)

# --- SOZLAMALAR ---
# 1. Avval .env faylni yuklaymiz (Lokal kompyuter uchun)
load_dotenv()

# 2. Tokenni olishga harakat qilamiz
BOT_TOKEN = os.getenv("BOT_TOKEN")

# 3. Agar token yo'q bo'lsa, xatolik berib to'xtatamiz
if not BOT_TOKEN:
    print("XATOLIK: BOT_TOKEN topilmadi! .env faylni yoki Render sozlamalarini tekshiring.")
    sys.exit()

DB_FILE = "quiz_database.json"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH ---
ACTIVE_GAMES = {}
POLL_MAPPING = {}
SETUP_DATA = {} # Global o'zgaruvchi

def load_db():
    """Bazadan ma'lumotlarni o'qiydi"""
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_db(data):
    """Bazaga ma'lumotlarni yozadi"""
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Dastur boshida bazani yuklab olamiz
SETUP_DATA = load_db()

class QuizStates(StatesGroup):
    waiting_name = State()
    collecting_text = State()
    waiting_answers = State()
    waiting_time = State()
    waiting_shuffle = State()
    waiting_split_choice = State()
    waiting_split_manual = State()

# --- SAVOLLARNI AJRATIB OLISH (PARSING) ---
def parse_quiz_text(text):
    questions = []
    text = re.sub(r'([\w\)])\s+(\d+[\.\)])', r'\1\n\2', text)
    text = "\n" + text 
    raw_blocks = re.split(r'\n\s*\d+[\.\)]\s*', text)
    for block in raw_blocks:
        if not block.strip(): continue
        parts = re.split(r'\n\s*[a-dA-Dа-яА-Я][\)\.]\s*', block)
        if len(parts) < 2: parts = re.split(r'\s+[a-dA-Dа-яА-Я][\)\.]\s+', block)
        if len(parts) >= 2: 
            q_text = parts[0].strip()
            if len(q_text) > 250: q_text = q_text[:247] + "..."
            raw_options = [p.strip() for p in parts[1:] if p.strip()]
            options = [opt[:99] for opt in raw_options]
            if len(options) >= 2:
                questions.append({"question": q_text, "options": options, "correct_index": 0})
    return questions

# --- ASOSIY START COMMAND ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Deep linking (run_...)
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("run_"):
        await run_quiz_logic(message.chat.id, args[1].replace("run_", ""), message)
        return

    if message.chat.type != 'private': return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡️ YANGI TEST YARATISH", callback_data="create_new")],
        [InlineKeyboardButton(text="🗑 MENING TESTLARIM (O'chirish)", callback_data="my_tests")],
        [InlineKeyboardButton(text="🔎 TESTNI ULASHISH", switch_inline_query="")]
    ])
    await message.answer("👋 <b>Test Botga xush kelibsiz!</b>\nTanlang 👇", parse_mode="HTML", reply_markup=kb)

# --- MENING TESTLARIM (DELETE) ---
@dp.callback_query(F.data == "my_tests")
async def show_my_tests(callback: types.CallbackQuery):
    global SETUP_DATA
    SETUP_DATA = load_db()
    
    if not SETUP_DATA:
        await callback.answer("Sizda hali testlar yo'q", show_alert=True)
        return

    kb = []
    # Oxirgi 15 ta testni ko'rsatish
    for q_id, data in list(SETUP_DATA.items())[-15:]:
        kb.append([InlineKeyboardButton(text=f"❌ {data['quiz_name']}", callback_data=f"del_{q_id}")])
    
    kb.append([InlineKeyboardButton(text="🔙 Bosh menyu", callback_data="back_home")])
    await callback.message.edit_text("📂 <b>O'chirish uchun testni tanlang:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("del_"))
async def delete_test(callback: types.CallbackQuery):
    q_id = callback.data.split("_")[1]
    if q_id in SETUP_DATA:
        del SETUP_DATA[q_id]
        save_db(SETUP_DATA)
        await callback.answer("✅ Test o'chirib tashlandi!")
        await show_my_tests(callback)
    else:
        await callback.answer("⚠️ Bu test allaqachon o'chirilgan", show_alert=True)
        await show_my_tests(callback)

@dp.callback_query(F.data == "back_home")
async def go_home(callback: types.CallbackQuery):
    await callback.message.delete()
    await cmd_start(callback.message)

# --- YANGI TEST YARATISH ---
@dp.callback_query(F.data == "create_new")
async def create_new_test(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("✏️ <b>Test mavzusini (ismini) yozing:</b>", parse_mode="HTML")
    await state.set_state(QuizStates.waiting_name)

@dp.message(QuizStates.waiting_name)
async def set_name(message: types.Message, state: FSMContext):
    await state.update_data(quiz_name=message.text)
    await message.answer("📥 <b>Savollarni yuboring:</b>\n<i>(Word yoki Telegramdan nusxalab tashlasangiz bo'ladi)</i>", parse_mode="HTML")
    await state.set_state(QuizStates.collecting_text)

@dp.message(QuizStates.collecting_text)
async def collect_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    new_text = data.get("full_text", "") + "\n" + message.text
    await state.update_data(full_text=new_text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ YUKLASH TUGADI", callback_data="text_done")]])
    await message.answer(f"⏳ Qabul qilindi (+{len(message.text)} belgi).\nYana bo'lsa tashlang, bo'lmasa tugmani bosing 👇", reply_markup=kb)

@dp.callback_query(F.data == "text_done", QuizStates.collecting_text)
async def process_text_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    questions = parse_quiz_text(data.get("full_text", ""))
    
    if not questions:
        await callback.message.answer("⚠️ Savollar topilmadi. Formatni tekshiring.")
        return
    
    await state.update_data(questions=questions)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Hammasi A", callback_data="ans_all_a"), InlineKeyboardButton(text="Hammasi B", callback_data="ans_all_b")],
        [InlineKeyboardButton(text="Hammasi C", callback_data="ans_all_c"), InlineKeyboardButton(text="Hammasi D", callback_data="ans_all_d")]
    ])
    await callback.message.answer(
        f"✅ <b>{len(questions)} ta savol topildi.</b>\nJavoblarni tanlang yoki qo'lda kalitlarni yuboring (abcd...):", 
        reply_markup=kb, parse_mode="HTML"
    )
    await state.set_state(QuizStates.waiting_answers)

@dp.callback_query(F.data.startswith("ans_all_"), QuizStates.waiting_answers)
async def set_all_ans(callback: types.CallbackQuery, state: FSMContext):
    char = callback.data.split("_")[-1]
    data = await state.get_data()
    await save_answers(callback.message, state, char * len(data['questions']))

@dp.message(QuizStates.waiting_answers)
async def manual_ans(message: types.Message, state: FSMContext):
    clean_ans = re.sub(r'[^abcdABCD]', '', message.text.lower())
    await save_answers(message, state, clean_ans)

async def save_answers(message: types.Message, state: FSMContext, answers):
    data = await state.get_data()
    qs = data['questions']
    if len(answers) < len(qs):
        await message.answer(f"❌ Javoblar yetarli emas! ({len(answers)}/{len(qs)})")
        return

    mapping = {'a': 0, 'b': 1, 'c': 2, 'd': 3}
    for i, q in enumerate(qs):
        idx = mapping.get(answers[i], 0)
        q['correct_index'] = idx if idx < len(q['options']) else 0
        
    await state.update_data(questions=qs)
    await message.answer("⏱ <b>Har bir savolga necha soniya vaqt berasiz?</b>\n(Faqat raqam yozing, masalan: 15)", parse_mode="HTML")
    await state.set_state(QuizStates.waiting_time)

@dp.message(QuizStates.waiting_time)
async def set_time(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return
    await state.update_data(time=int(message.text))
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔀 Faqat Savollar aralashsin", callback_data="shuf_q")],
        [InlineKeyboardButton(text="🔠 Faqat Variantlar aralashsin", callback_data="shuf_o")],
        [InlineKeyboardButton(text="🔀+🔠 Hammasi aralashsin", callback_data="shuf_both")],
        [InlineKeyboardButton(text="❌ Aralashmasin (Tartib bilan)", callback_data="shuf_none")]
    ])
    await message.answer("⚙️ <b>Aralashtirish rejimini tanlang:</b>", parse_mode="HTML", reply_markup=kb)
    await state.set_state(QuizStates.waiting_shuffle)

@dp.callback_query(F.data.startswith("shuf_"), QuizStates.waiting_shuffle)
async def set_shuffle_mode(callback: types.CallbackQuery, state: FSMContext):
    mode = callback.data
    s_q = mode in ['shuf_q', 'shuf_both']
    s_o = mode in ['shuf_o', 'shuf_both']
    await state.update_data(shuffle_qs=s_q, shuffle_opts=s_o)
    
    data = await state.get_data()
    q_count = len(data['questions'])
    
    kb_list = []
    kb_list.append([InlineKeyboardButton(text=f"📦 1 bo'lak (Hammasi - {q_count} ta)", callback_data="split_1")])
    if q_count >= 10: kb_list.append([InlineKeyboardButton(text="✂️ 2 bo'lak", callback_data="split_2")])
    if q_count >= 20: kb_list.append([InlineKeyboardButton(text="✂️ 3 bo'lak", callback_data="split_3")])
    if q_count >= 30: kb_list.append([InlineKeyboardButton(text="✂️ 4 bo'lak", callback_data="split_4")])
    kb_list.append([InlineKeyboardButton(text="✍️ Qo'lda kiritish (son yozish)", callback_data="split_manual")])
    
    await callback.message.edit_text(
        f"📝 <b>Sizda {q_count} ta savol bor.</b>\nTestni necha bo'lakka bo'lmoqchisiz?", 
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list)
    )
    await state.set_state(QuizStates.waiting_split_choice)

@dp.callback_query(F.data.startswith("split_"), QuizStates.waiting_split_choice)
async def process_split_choice(callback: types.CallbackQuery, state: FSMContext):
    choice = callback.data.split("_")[1]
    if choice == "manual":
        await callback.message.edit_text("🔢 <b>Necha bo'lakka bo'lmoqchisiz?</b>\n(Raqam yozib yuboring)", parse_mode="HTML")
        await state.set_state(QuizStates.waiting_split_manual)
    else:
        await finish_creation(callback.message, state, int(choice))

@dp.message(QuizStates.waiting_split_manual)
async def process_manual_split(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Iltimos, faqat raqam yozing.")
        return
    parts = int(message.text)
    if parts < 1: parts = 1
    await finish_creation(message, state, parts)

async def finish_creation(message: types.Message, state: FSMContext, parts_count: int):
    data = await state.get_data()
    all_questions = data['questions']
    total_q = len(all_questions)
    
    if parts_count > total_q: parts_count = total_q
    chunk_size = math.ceil(total_q / parts_count)
    try: await message.delete()
    except: pass
    
    await message.answer(f"✅ <b>TAYYOR!</b>\nTest saqlandi va {parts_count} qismga bo'lindi.", parse_mode="HTML")
    
    for i in range(parts_count):
        start_idx = i * chunk_size
        end_idx = min((i + 1) * chunk_size, total_q)
        chunk_questions = all_questions[start_idx:end_idx]
        
        if not chunk_questions: continue
        unique_id = f"{message.chat.id}_{i+1}_{random.randint(10000,99999)}"
        
        real_start = start_idx + 1
        real_end = start_idx + len(chunk_questions)
        part_name = f"{data['quiz_name']} ({real_start}-{real_end})" if parts_count > 1 else data['quiz_name']

        SETUP_DATA[unique_id] = {
            'questions': chunk_questions,
            'quiz_name': part_name,
            'time_per_question': data['time'],
            'shuffle_qs': data['shuffle_qs'],
            'shuffle_opts': data['shuffle_opts'],
            'author': message.chat.full_name
        }
        save_db(SETUP_DATA)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔗 {part_name}ni ulashish", switch_inline_query=unique_id)]
        ])
        await message.answer(f"💾 <b>{part_name}</b> saqlandi.", parse_mode="HTML", reply_markup=kb)

    await state.clear()

# --- INLINE QUERY HANDLER ---
@dp.inline_query()
async def inline_handler(query: types.InlineQuery):
    q = query.query.strip()
    res = []
    global SETUP_DATA
    SETUP_DATA = load_db()

    items = []
    if q in SETUP_DATA:
        items.append((q, SETUP_DATA[q]))
    else:
        for k, v in list(SETUP_DATA.items())[-20:]:
            if q.lower() in v['quiz_name'].lower():
                items.append((k, v))
                
    bot_usr = (await bot.get_me()).username
    
    for q_id, data in items:
        # GURUHDA BOSHLASH UCHUN CALLBACK ISHLATAMIZ
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Shu yerda boshlash (Guruh)", callback_data=f"group_start_{q_id}")],
            [InlineKeyboardButton(text="👤 Yakkaxon o'ynash (Lichka)", url=f"https://t.me/{bot_usr}?start=run_{q_id}")],
            [InlineKeyboardButton(text="⤴️ Ulashish", switch_inline_query=q_id)]
        ])
        
        desc = f"{len(data['questions'])} savol • {data['time_per_question']} sek"
        content = f"🎲 <b>{data['quiz_name']}</b>\n🖊 {len(data['questions'])} ta savol\n👤 {data.get('author','Bot')}\n\n👇 <i>Testni boshlash uchun tugmani bosing:</i>"
        
        res.append(InlineQueryResultArticle(
            id=q_id, 
            title=f"🎲 {data['quiz_name']}", 
            description=desc,
            input_message_content=InputTextMessageContent(message_text=content, parse_mode="HTML"),
            reply_markup=kb
        ))
    
    await bot.answer_inline_query(query.id, res, cache_time=1)

# --- GURUHDA BOSHLASH UCHUN CALLBACK ---
@dp.callback_query(F.data.startswith("group_start_"))
async def group_start_callback(callback: types.CallbackQuery):
    quiz_id = callback.data.split("_")[2]
    # O'yinni shu chatda boshlaymiz
    await run_quiz_logic(callback.message.chat.id, quiz_id, callback.message)
    await callback.answer("Test yuklanmoqda...", show_alert=False)

# --- O'YIN LOGIKASI ---
async def run_quiz_logic(chat_id, quiz_id, message_obj):
    global SETUP_DATA
    SETUP_DATA = load_db()
    
    if quiz_id not in SETUP_DATA:
        await bot.send_message(chat_id, "⚠️ <b>Xatolik!</b> Test topilmadi.", parse_mode="HTML")
        return
        
    if chat_id in ACTIVE_GAMES and ACTIVE_GAMES[chat_id]['is_running']:
        await bot.send_message(chat_id, "⚠️ Bu yerda allaqachon test ketmoqda. To'xtatish uchun /stop")
        return

    data = SETUP_DATA[quiz_id]
    import copy
    qs = copy.deepcopy(data['questions'])
    
    if data.get('shuffle_qs', False): 
        random.shuffle(qs)

    ACTIVE_GAMES[chat_id] = {
        'quiz_name': data['quiz_name'],
        'questions': qs,
        'time': data['time_per_question'],
        'shuffle_opts': data.get('shuffle_opts', False),
        'scores': {},
        'is_running': True,
        'empty_streak': 0
    }
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 BOSHLASH", callback_data="start_game_confirm")]])
    await bot.send_message(
        chat_id,
        f"📊 <b>{data['quiz_name']}</b>\n"
        f"🖊 Savollar: {len(qs)} ta\n"
        f"⏱ Vaqt: {data['time_per_question']} sek\n\n"
        "Tayyormisiz?", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "start_game_confirm")
async def start_loop(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    if chat_id not in ACTIVE_GAMES: return
    game = ACTIVE_GAMES[chat_id]
    
    await callback.message.delete()
    # HTML parse mode qo'shildi (muammoni hal qilish uchun)
    await callback.message.answer("🏁 <b>Test boshlandi!</b>", parse_mode="HTML")
    await asyncio.sleep(2)

    total = len(game['questions'])
    for i, q in enumerate(game['questions']):
        if not ACTIVE_GAMES.get(chat_id, {}).get('is_running'): break
        if game['empty_streak'] >= 3:
            await bot.send_message(chat_id, "💤 <b>Faollik yo'q. Test to'xtatildi.</b>", parse_mode="HTML")
            break

        opts = q['options'].copy()
        corr_idx = q['correct_index']
        
        if game['shuffle_opts']:
            correct_text = opts[corr_idx]
            random.shuffle(opts)
            try: corr_idx = opts.index(correct_text)
            except: corr_idx = 0

        try:
            p = await bot.send_poll(
                chat_id, 
                f"[{i+1}/{total}] {q['question']}", 
                opts, 
                type='quiz', 
                correct_option_id=corr_idx, 
                open_period=game['time'],
                is_anonymous=False
            )
            POLL_MAPPING[p.poll.id] = {"chat_id": chat_id, "correct_id": corr_idx, "has_answer": False}
            await asyncio.sleep(game['time'] + 1)
            
            poll_data = POLL_MAPPING.get(p.poll.id)
            if poll_data and not poll_data['has_answer']:
                game['empty_streak'] += 1
            else:
                game['empty_streak'] = 0
                
        except Exception as e:
            logging.error(e)
            continue

    if chat_id in ACTIVE_GAMES:
        await show_results(chat_id)
        if chat_id in ACTIVE_GAMES: del ACTIVE_GAMES[chat_id]

@dp.poll_answer()
async def handle_answer(ans: PollAnswer):
    if ans.poll_id in POLL_MAPPING:
        data = POLL_MAPPING[ans.poll_id]
        data['has_answer'] = True 
        cid = data['chat_id']
        if cid in ACTIVE_GAMES:
            sc = ACTIVE_GAMES[cid]['scores']
            uid = ans.user.id
            if uid not in sc: sc[uid] = {"name": ans.user.full_name, "score": 0}
            if ans.option_ids[0] == data['correct_id']: sc[uid]["score"] += 1

async def show_results(cid):
    if cid not in ACTIVE_GAMES: return
    sc = ACTIVE_GAMES[cid]['scores']
    nm = ACTIVE_GAMES[cid]['quiz_name']
    if not sc: return
    txt = f"🏆 <b>NATIJALAR:</b> {nm}\n\n"
    for i, p in enumerate(sorted(sc.values(), key=lambda x:x['score'], reverse=True)[:15]):
        txt += f"{i+1}. {p['name']} - {p['score']}\n"
    await bot.send_message(cid, txt, parse_mode="HTML")

@dp.message(Command("stop", "stop_quiz"))
async def force_stop(message: types.Message):
    chat_id = message.chat.id
    if chat_id in ACTIVE_GAMES and ACTIVE_GAMES[chat_id]['is_running']:
        ACTIVE_GAMES[chat_id]['is_running'] = False
        await message.answer("🛑 <b>Test majburiy to'xtatildi.</b>", parse_mode="HTML")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass