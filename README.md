# Telegram Department Bot

بوت Telegram يعمل بـ Python + PostgreSQL، ومجهز للنشر من GitHub على Render.

## مهم جداً

**GitHub هو مكان تخزين الكود، وليس مكان تشغيل البوت بشكل دائم.**
الطريقة الصحيحة:

**GitHub → Render → البوت يعمل 24/7**

لا ترفع ملف `.env` أو توكن Telegram إلى GitHub.

## الملفات

- `bot.py` — كود البوت
- `requirements.txt` — المكتبات
- `Dockerfile` — صورة التشغيل
- `render.yaml` — إعداد Render كـ Background Worker
- `.gitignore`
- `.env.example`

## النشر على GitHub

ارفع جميع الملفات إلى Repository جديد، ولا ترفع `.env`.

## النشر على Render

1. افتح Render.
2. اختر **New → Background Worker**.
3. اربط مستودع GitHub الذي رفعت إليه الملفات.
4. اختر **Docker**.
5. اترك Dockerfile كما هو.
6. أضف Environment Variables:

```text
BOT_TOKEN=توكن البوت من BotFather
ADMIN_IDS=123456789,987654321
DATABASE_URL=رابط PostgreSQL
```

### قاعدة البيانات

أنشئ PostgreSQL على Render أو استخدم PostgreSQL خارجي، ثم ضع رابط الاتصال الكامل في:

```text
DATABASE_URL
```

مثال:

```text
postgresql://USER:PASSWORD@HOST:5432/DATABASE
```

إذا كان مزود قاعدة البيانات يعطي رابطاً جاهزاً، استخدمه كما هو.

## التشغيل

Render سيشغل:

```bash
python bot.py
```

والبوت يستخدم Telegram Polling، لذلك يجب أن يكون نوع الخدمة **Background Worker** وليس Static Site.

## قاعدة البيانات

عند أول تشغيل ينشئ البوت الجداول تلقائياً باستخدام `CREATE TABLE IF NOT EXISTS`.

الجداول:
- users
- sessions_log
- dewan
- tenants
- migrants
- reports
- amn_afrad
- amn_alamlen

## GitHub Codespaces

يمكن تشغيل المشروع للتجربة داخل Codespaces، لكن Codespaces ليس استضافة دائمة للبوت.

## Docker محلياً

ملف `docker-compose.yml` موجود للتشغيل المحلي مع PostgreSQL:

```bash
docker compose up -d --build
```

## الأمان

لا ترفع:
- `.env`
- BOT_TOKEN
- كلمات مرور PostgreSQL

إذا تم تسريب BOT_TOKEN، غيّره فوراً من BotFather.

## الوظائف

### المدير
- إنشاء حساب
- اختيار القسم
- تعطيل/تفعيل الحساب
- التقارير
- الجلسات
- التعميم للمستخدمين الفعّالين

### المستخدم
- تسجيل الدخول
- القسم المخصص له فقط
- تسجيل الخروج

### الأقسام
- DEWAN: وارد / صادر
- AKARAT: المستأجرين / عربي_أجنبي
- TQARER: المطلوب / المنجز / المتعذر
- AMN AFRAD: الجلسات
- AMN ALAMLEN: الجولات ومكان الجولة
