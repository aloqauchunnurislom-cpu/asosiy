# 🚀 Telegram Advanced Quiz Bot

Ushbu bot foydalanuvchi yuborgan matnli testlarni avtomatik tarzda tahlil qilib, ularni Telegram Quiz (viktorina) so'rovnomalariga aylantiradi va taymer asosida ketma-ket yuboradi.

## ✨ Xususiyatlari
- **Avto-parse:** Regex orqali matndan savol va variantlarni ajratib olish.
- **Batch Processing:** Savollarni yig'ib, so'ngra birga boshlash.
- **Timer (JobQueue):** Savollar orasidagi vaqtni sozlash (sukut bo'yicha 15s).
- **Natijalar:** Har bir foydalanuvchi uchun alohida ball hisoblash.
- **State Management:** Bir vaqtning o'zida ko'plab foydalanuvchilar bilan ishlash.

## 🛠 O'rnatish

1. **Virtual muhitni yarating:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Kutubxonalarni o'rnating:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Tokenni sozlang:**
   `.env` fayliga @BotFather-dan olgan tokeningizni yozing.

4. **Ishga tushirish:**
   ```bash
   python3 bot.py
   ```

## 📖 Komandalar
- `/start` - Bot haqida ma'lumot.
- `/start_quiz` - Yig'ilgan savollarni boshlash.
- `/stop_quiz` - Joriy quizni to'xtatish.
- `/set_timer N` - Vaqtni sozlash (masalan: `/set_timer 20`).
- `/my_score` - To'g'ri javoblar sonini ko'rish.
- `/help` - Yordam menyusi.

## 📝 Test formati
```
1. Ona plata qanday vazifani bajaradi?
A) Faqat ma'lumot saqlaydi
B) Qurilmalarni bog'laydi va boshqaradi
C) Faqat grafikani oshiradi
D) Internet tezligini oshiradi
Javob: B
```
Savollar orasida bo'sh qator tashlash tavsiya etiladi.
