
import os
import json
import shlex
import threading
import secrets
from datetime import datetime
from flask import Flask, request, abort
import telebot

ADMIN_ID = 8829161471
DATA_FILE = "users.json"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))

OFFLINE_MESSAGE = (
    "Sorry, The owner is offline just leave message "
    "what you want he will see and reply"
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable missing")

if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET environment variable missing")

if not PUBLIC_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL environment variable missing")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

lock = threading.RLock()
owner_online = True


def load_users():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


users = load_users()


def save_users():
    with lock:
        temp = DATA_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        os.replace(temp, DATA_FILE)


def get_name(user):
    name = " ".join(
        x for x in [user.first_name, user.last_name] if x
    )
    return name or "Unknown"


def register_user(user):
    uid = str(user.id)

    with lock:
        info = users.get(uid, {})

        if not info.get("label"):
            numbers = [
                int(item["label"].split("_")[1])
                for item in users.values()
                if item.get("label", "").startswith("USER_")
                and item["label"].split("_")[1].isdigit()
            ]
            info["label"] = f"USER_{max(numbers, default=0) + 1}"

        info["name"] = get_name(user)
        info["username"] = user.username or ""
        info["last_seen"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        users[uid] = info
        save_users()

    return info["label"]


def is_admin(message):
    return (
        message.from_user is not None
        and message.from_user.id == ADMIN_ID
    )


def send_user_info(message):
    user = message.from_user
    label = register_user(user)
    username = f"@{user.username}" if user.username else "Not set"
    text = message.text or message.caption or "[Media / attachment]"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    details = (
        f"📩 New message\n\n"
        f"{label}\n"
        f"Name - {get_name(user)}\n"
        f"Username - {username}\n"
        f"User ID - {user.id}\n"
        f"Time - {now}\n"
        f"Messaged - {text}"
    )

    bot.send_message(ADMIN_ID, details)

    if message.content_type != "text":
        bot.copy_message(
            ADMIN_ID, message.chat.id, message.message_id
        )


def send_offline_reply(chat_id):
    if not owner_online:
        bot.send_message(chat_id, OFFLINE_MESSAGE)


@app.route("/", methods=["GET"])
def home():
    return "BOT_NEUTRIX is running", 200


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    if not secrets.compare_digest(
        request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        ),
        WEBHOOK_SECRET
    ):
        abort(403)

    data = request.get_json(silent=True)
    if not data:
        abort(400)

    update = telebot.types.Update.de_json(data)
    bot.process_new_updates([update])
    return "OK", 200


@bot.message_handler(commands=["start"])
def start_command(message):
    if is_admin(message):
        bot.reply_to(
            message,
            "BOT_NEUTRIX Admin Panel\n\n"
            "/online - Owner online\n"
            "/offline - Owner offline\n"
            '/message "Your message" "@username"\n'
            "/users - Registered users"
        )
        return

    register_user(message.from_user)
    bot.reply_to(message, "Hello! Leave your message here.")
    send_user_info(message)
    send_offline_reply(message.chat.id)


@bot.message_handler(commands=["online", "offline"])
def status_command(message):
    global owner_online

    if not is_admin(message):
        return

    owner_online = message.text.split()[0].lower() == "/online"
    status = "ONLINE 🟢" if owner_online else "OFFLINE 🔴"
    bot.reply_to(message, f"Owner status: {status}")


@bot.message_handler(commands=["users"])
def users_command(message):
    if not is_admin(message):
        return

    with lock:
        entries = list(users.items())

    if not entries:
        bot.reply_to(message, "No registered users yet.")
        return

    lines = ["Registered users:"]
    for uid, info in entries:
        username = (
            "@" + info["username"]
            if info.get("username") else "No username"
        )
        lines.append(
            f'{info.get("label", "USER")} | '
            f'{info.get("name", "Unknown")} | '
            f'{username} | ID: {uid}'
        )

    output = "\n".join(lines)
    for start in range(0, len(output), 3500):
        bot.send_message(ADMIN_ID, output[start:start + 3500])


@bot.message_handler(commands=["message"])
def message_command(message):
    if not is_admin(message):
        return

    try:
        parts = shlex.split(message.text)
        if len(parts) < 3:
            raise ValueError

        text_to_send = parts[1]
        target = " ".join(parts[2:]).strip()
        if not text_to_send or not target:
            raise ValueError
    except ValueError:
        bot.reply_to(
            message,
            'Usage: /message "Hello" "@username"\n'
            'Or: /message "Hello" "USER_1"\n'
            'Or use the numeric Telegram user ID.'
        )
        return

    target_lower = target.lstrip("@").casefold()

    with lock:
        matches = []
        for uid, info in users.items():
            username = info.get("username", "").casefold()
            name = info.get("name", "").casefold()
            label = info.get("label", "").casefold()

            if (
                uid == target
                or label == target_lower
                or (username and username == target_lower)
                or name == target.casefold()
            ):
                matches.append(uid)

    if len(matches) == 0:
        bot.reply_to(
            message,
            "User not found. They must start the bot first. "
            "Use /users to check saved users."
        )
        return

    if len(matches) > 1:
        bot.reply_to(
            message,
            "Duplicate name found. Use the user's numeric ID."
        )
        return

    try:
        bot.send_message(int(matches[0]), text_to_send)
        bot.reply_to(message, "Message sent successfully.")
    except Exception:
        bot.reply_to(
            message,
            "Could not send message. The user may have blocked the bot."
        )


@bot.message_handler(
    content_types=[
        "text", "photo", "video", "document", "audio",
        "voice", "video_note", "animation", "sticker",
        "location", "contact"
    ]
)
def incoming_message(message):
    if is_admin(message):
        return

    if message.content_type == "text" and message.text.startswith("/"):
        return

    register_user(message.from_user)

    try:
        send_user_info(message)
    except Exception as exc:
        print("Forward error:", type(exc).__name__)

    send_offline_reply(message.chat.id)


# Register webhook when Gunicorn imports this module.
bot.set_webhook(
    url=PUBLIC_URL + "/telegram-webhook",
    secret_token=WEBHOOK_SECRET,
    allowed_updates=["message"]
)

print("BOT_NEUTRIX webhook configured.")
