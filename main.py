import os
import sqlite3
import logging

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    FloodWaitError,
)

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ================== ENV ==================
def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"❌ المتغير {name} غير موجود")
    return value


API_ID = int(required_env("API_ID"))
API_HASH = required_env("API_HASH")
BOT_TOKEN = required_env("BOT_TOKEN")
OWNER_ID = int(required_env("OWNER_ID"))

# إذا تريد البوت مفتوح لأي شخص: PUBLIC_MODE=1
# إذا تريد تضيف المستخدمين بنفسك: PUBLIC_MODE=0
PUBLIC_MODE = os.environ.get("PUBLIC_MODE", "0").strip() == "1"

DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
DB_NAME = os.environ.get("DB_NAME", os.path.join(DATA_DIR, "session_bot.db"))

PHONE, CODE, PASSWORD = range(3)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ================== DATABASE ==================
def db_conn():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = db_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            added_by INTEGER,
            added_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS allowed_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            added_by INTEGER,
            added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    """)

    # إضافة المالك كمشرف دائم
    cur.execute("""
        INSERT OR IGNORE INTO admins (user_id, username, full_name, added_by)
        VALUES (?, ?, ?, ?)
    """, (OWNER_ID, "OWNER", "OWNER", OWNER_ID))

    conn.commit()
    conn.close()


def get_user_info_from_update(update: Update):
    user = update.effective_user
    username = f"@{user.username}" if user and user.username else ""
    full_name = user.full_name if user else ""
    user_id = user.id if user else 0
    return user_id, username, full_name


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def is_admin(user_id: int) -> bool:
    if is_owner(user_id):
        return True

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def is_allowed_user(user_id: int) -> bool:
    if PUBLIC_MODE:
        return True

    if is_admin(user_id):
        return True

    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM allowed_users WHERE user_id = ? AND is_active = 1",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def add_admin_to_db(user_id: int, username: str, full_name: str, added_by: int):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO admins (user_id, username, full_name, added_by)
        VALUES (?, ?, ?, ?)
    """, (user_id, username, full_name, added_by))
    conn.commit()
    conn.close()


def remove_admin_from_db(user_id: int):
    if user_id == OWNER_ID:
        return False

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return True


def add_user_to_db(user_id: int, username: str, full_name: str, added_by: int):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO allowed_users
        (user_id, username, full_name, added_by, is_active)
        VALUES (?, ?, ?, ?, 1)
    """, (user_id, username, full_name, added_by))
    conn.commit()
    conn.close()


def remove_user_from_db(user_id: int):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE allowed_users SET is_active = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def list_admins_from_db():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, full_name FROM admins ORDER BY added_date")
    rows = cur.fetchall()
    conn.close()
    return rows


def list_users_from_db():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, username, full_name
        FROM allowed_users
        WHERE is_active = 1
        ORDER BY added_date
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def parse_target_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        try:
            return int(context.args[0])
        except ValueError:
            return None

    if update.message and update.message.reply_to_message:
        return update.message.reply_to_message.from_user.id

    return None


# ================== ACCESS ==================
async def deny_access(update: Update):
    await update.message.reply_text(
        "❌ ليس لديك صلاحية لاستخدام هذا البوت.\n"
        "تواصل مع المشرف لإضافتك."
    )
    return ConversationHandler.END


# ================== USER COMMANDS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id, username, full_name = get_user_info_from_update(update)

    if not is_allowed_user(user_id):
        return await deny_access(update)

    context.user_data.clear()

    await update.message.reply_text(
        "👋 أهلاً بك في بوت استخراج جلسة Telethon.\n\n"
        "أرسل رقم الهاتف بالصيغة الدولية.\n"
        "مثال:\n"
        "+967xxxxxxxxx\n\n"
        "للإلغاء أرسل /cancel"
    )
    return PHONE


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, username, full_name = get_user_info_from_update(update)
    await update.message.reply_text(
        f"🆔 آيديك:\n"
        f"<code>{user_id}</code>\n\n"
        f"👤 الاسم: {full_name}\n"
        f"🔗 اليوزر: {username or 'لا يوجد'}",
        parse_mode="HTML"
    )


async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id, _, _ = get_user_info_from_update(update)

    if not is_allowed_user(user_id):
        return await deny_access(update)

    phone = update.message.text.strip()

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    context.user_data["client"] = client

    try:
        await client.connect()
        sent_code = await client.send_code_request(phone)

        context.user_data["phone"] = phone
        context.user_data["sent_code"] = sent_code

        await update.message.reply_text(
            "✅ تم إرسال كود التحقق إلى تطبيق تيليجرام.\n\n"
            "أرسل الكود الآن بدون مسافات.\n"
            "مثال: 12345\n\n"
            "للإلغاء أرسل /cancel"
        )
        return CODE

    except FloodWaitError as e:
        await client.disconnect()
        context.user_data.clear()
        await update.message.reply_text(
            f"⏳ تيليجرام طلب الانتظار مؤقتًا.\n"
            f"المدة: {e.seconds} ثانية."
        )
        return ConversationHandler.END

    except Exception as e:
        await client.disconnect()
        context.user_data.clear()
        await update.message.reply_text(f"❌ خطأ أثناء إرسال الكود:\n{str(e)}")
        return ConversationHandler.END


async def code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id, _, _ = get_user_info_from_update(update)

    if not is_allowed_user(user_id):
        return await deny_access(update)

    code = update.message.text.strip()

    client: TelegramClient = context.user_data.get("client")
    phone = context.user_data.get("phone")
    sent_code = context.user_data.get("sent_code")

    if not client or not phone or not sent_code:
        await update.message.reply_text("❌ الجلسة غير مكتملة. ابدأ من جديد /start")
        context.user_data.clear()
        return ConversationHandler.END

    try:
        await client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=sent_code.phone_code_hash,
        )

    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔐 الحساب مفعّل عليه التحقق بخطوتين.\n"
            "أرسل كلمة المرور الآن.\n\n"
            "للإلغاء أرسل /cancel"
        )
        return PASSWORD

    except PhoneCodeInvalidError:
        await client.disconnect()
        context.user_data.clear()
        await update.message.reply_text("❌ الكود غير صحيح. ابدأ من جديد /start")
        return ConversationHandler.END

    except PhoneCodeExpiredError:
        await client.disconnect()
        context.user_data.clear()
        await update.message.reply_text("❌ انتهت صلاحية الكود. ابدأ من جديد /start")
        return ConversationHandler.END

    except Exception as e:
        await client.disconnect()
        context.user_data.clear()
        await update.message.reply_text(f"❌ خطأ أثناء تسجيل الدخول:\n{str(e)}")
        return ConversationHandler.END

    return await finish_login(update, context)


async def password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id, _, _ = get_user_info_from_update(update)

    if not is_allowed_user(user_id):
        return await deny_access(update)

    password = update.message.text.strip()

    client: TelegramClient = context.user_data.get("client")
    if not client:
        await update.message.reply_text("❌ الجلسة غير مكتملة. ابدأ من جديد /start")
        context.user_data.clear()
        return ConversationHandler.END

    try:
        await client.sign_in(password=password)

    except Exception as e:
        await client.disconnect()
        context.user_data.clear()
        await update.message.reply_text(f"❌ خطأ في كلمة مرور التحقق بخطوتين:\n{str(e)}")
        return ConversationHandler.END

    return await finish_login(update, context)


async def finish_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    client: TelegramClient = context.user_data.get("client")

    if not client:
        await update.message.reply_text("❌ لم يتم العثور على عميل Telethon. ابدأ من جديد /start")
        context.user_data.clear()
        return ConversationHandler.END

    session_string = client.session.save()
    await client.disconnect()

    await update.message.reply_text(
        "✅ تم استخراج جلسة Telethon بنجاح.\n\n"
        f"<code>{session_string}</code>\n\n"
        "⚠️ هذه الجلسة مثل كلمة مرور للحساب. لا تشاركها مع أي شخص.",
        parse_mode="HTML",
    )

    logger.info("Session extracted for user_id=%s", update.effective_user.id)
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    client = context.user_data.get("client")
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

    context.user_data.clear()
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END


# ================== ADMIN COMMANDS ==================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, _, _ = get_user_info_from_update(update)

    text = (
        "📌 أوامر البوت:\n\n"
        "/start - استخراج جلسة Telethon\n"
        "/cancel - إلغاء العملية\n"
        "/myid - معرفة آيدي حسابك\n"
    )

    if is_admin(user_id):
        text += (
            "\n👮 أوامر المشرف:\n"
            "/adduser USER_ID - إضافة مستخدم\n"
            "/deluser USER_ID - حذف مستخدم\n"
            "/users - عرض المستخدمين\n"
        )

    if is_owner(user_id):
        text += (
            "\n👑 أوامر المالك:\n"
            "/addadmin USER_ID - إضافة مشرف\n"
            "/deladmin USER_ID - حذف مشرف\n"
            "/admins - عرض المشرفين\n"
        )

    if PUBLIC_MODE:
        text += "\n\n🌍 الوضع الحالي: مفتوح لأي مستخدم."
    else:
        text += "\n\n🔒 الوضع الحالي: مغلق للمستخدمين المضافين فقط."

    await update.message.reply_text(text)


async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id, _, _ = get_user_info_from_update(update)

    if not is_admin(admin_id):
        return await deny_access(update)

    target_id = parse_target_user_id(update, context)
    if not target_id:
        await update.message.reply_text("❌ الاستخدام الصحيح:\n/adduser USER_ID")
        return

    add_user_to_db(target_id, "", "", admin_id)
    await update.message.reply_text(f"✅ تم إضافة المستخدم:\n<code>{target_id}</code>", parse_mode="HTML")


async def deluser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id, _, _ = get_user_info_from_update(update)

    if not is_admin(admin_id):
        return await deny_access(update)

    target_id = parse_target_user_id(update, context)
    if not target_id:
        await update.message.reply_text("❌ الاستخدام الصحيح:\n/deluser USER_ID")
        return

    remove_user_from_db(target_id)
    await update.message.reply_text(f"✅ تم حذف/تعطيل المستخدم:\n<code>{target_id}</code>", parse_mode="HTML")


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id, _, _ = get_user_info_from_update(update)

    if not is_admin(admin_id):
        return await deny_access(update)

    rows = list_users_from_db()
    if not rows:
        await update.message.reply_text("لا يوجد مستخدمون مضافون.")
        return

    text = "👥 المستخدمون المسموح لهم:\n\n"
    for user_id, username, full_name in rows:
        text += f"• <code>{user_id}</code> {username or ''} {full_name or ''}\n"

    await update.message.reply_text(text, parse_mode="HTML")


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id, _, _ = get_user_info_from_update(update)

    if not is_owner(owner_id):
        return await deny_access(update)

    target_id = parse_target_user_id(update, context)
    if not target_id:
        await update.message.reply_text("❌ الاستخدام الصحيح:\n/addadmin USER_ID")
        return

    add_admin_to_db(target_id, "", "", owner_id)
    await update.message.reply_text(f"✅ تم إضافة المشرف:\n<code>{target_id}</code>", parse_mode="HTML")


async def deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id, _, _ = get_user_info_from_update(update)

    if not is_owner(owner_id):
        return await deny_access(update)

    target_id = parse_target_user_id(update, context)
    if not target_id:
        await update.message.reply_text("❌ الاستخدام الصحيح:\n/deladmin USER_ID")
        return

    ok = remove_admin_from_db(target_id)
    if ok:
        await update.message.reply_text(f"✅ تم حذف المشرف:\n<code>{target_id}</code>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ لا يمكن حذف المالك الرئيسي.")


async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_id, _, _ = get_user_info_from_update(update)

    if not is_owner(owner_id):
        return await deny_access(update)

    rows = list_admins_from_db()
    text = "👮 قائمة المشرفين:\n\n"
    for user_id, username, full_name in rows:
        label = "👑 OWNER" if user_id == OWNER_ID else "👮 ADMIN"
        text += f"{label} - <code>{user_id}</code> {username or ''} {full_name or ''}\n"

    await update.message.reply_text(text, parse_mode="HTML")


# ================== MAIN ==================
def main() -> None:
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, code_handler)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)

    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid))

    app.add_handler(CommandHandler("adduser", adduser))
    app.add_handler(CommandHandler("deluser", deluser))
    app.add_handler(CommandHandler("users", users))

    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("deladmin", deladmin))
    app.add_handler(CommandHandler("admins", admins))

    logger.info("🤖 Telethon Session Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
