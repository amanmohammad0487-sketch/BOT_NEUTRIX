import os
import json
import shlex
import threading
from pathlib import Path

import telebot
from flask import Flask, request, abort

# =========================
# CONFIG
# =========================

ADMIN_ID = 8829161471
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
DATA_FILE = Path(__file__).with_name("users.json")

OFFLINE_MESSAGE = (
    "Sorry, the owner is offline. Leave your message here; "
    "he will see it and reply."
)

RECEIVED_MESSAGE = "Message received. The owner will review it."

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

if not WEBHOOK_SECRET:
    raise RuntimeError("WEBHOOK_SECRET environment variable is missing")

if not PUBLIC_URL.startswith("https://"):
    raise RuntimeError("RENDER_EXTERNAL_URL must be an HTTPS URL")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

lock = threading.RLock()
owner_online = True

# Maps admin-panel message IDs to the user who sent the message.
# This mapping lasts until the service restarts.
message_to_user = {}


# =========================
# USER STORAGE
# =========================

def load_users():
    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


users = load_users()


def save_users():
    with lock:
        temp_file = DATA_FILE.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as file:
            json.dump(users, file, indent=2, ensure_ascii=False)
        temp_file.replace(DATA_FILE)


def register_user(user):
    user_id = str(user.id)

    with lock:
        previous = users.get(user_id, {})
        users[user_id] = {
            "id": user.id,
            "username": user.username or previous.get("username", ""),
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "last_seen": previous.get("last_seen", ""),
        }

    save_users()


def is_admin(message):
    return bool(message.from_user and message.from_user.id == ADMIN_ID)


def user_description(user):
    name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ) or "Unknown"

    username = f"@{user.username}" if user.username else "No username"

    return (
        f"New message from user\n"
        f"Name: {name}\n"
        f"Username: {username}\n"
        f"User ID: {user.id}"
    )


def send_user_info(message):
    user = message.from_user
    register_user(user)

    info = bot.send_message(ADMIN_ID, user_description(user))
    message_to_user[info.message_id] = user.id

    # Copy the user's original text/media to the admin.
    try:
        copied = bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        message_to_user[copied.message_id] = user.id
    except Exception as exc:
        print("Could not copy user message:", type(exc).__name__)


def send_offline_reply(chat_id):
    if not owner_online:
        bot.send_message(chat_id, OFFLINE_MESSAGE)


# =========================
# START COMMAND
# =========================

@bot.message_handler(commands=["start"])
def start_command(message):
    if is_admin(message):
        bot.reply_to(
            message,
            "BOT_NEUTRIX Admin Panel\n\n"
            "/online - Set owner online\n"
            "/offline - Set owner offline\n"
            "/users - Show registered users\n"
            '/message "Your message" "USER_ID or @username"\n\n'
            "You can also reply directly to a forwarded user message."
        )
        return

    register_user(message.from_user)
    bot.reply_to(message, "Hello! Leave your message here.")
    send_user_info(message)
    send_offline_reply(message.chat.id)


# =========================
# ONLINE / OFFLINE
# =========================

@bot.message_handler(commands=["online"])
def online_command(message):
    global owner_online

    if not is_admin(message):
        return

    owner_online = True
    bot.reply_to(message, "Owner status: ONLINE")


@bot.message_handler(commands=["offline"])
def offline_command(message):
    global owner_online

    if not is_admin(message):
        return

    owner_online = False
    bot.reply_to(message, "Owner status: OFFLINE")


# =========================
# USERS COMMAND
# =========================

@bot.message_handler(commands=["users"])
def users_command(message):
    if not is_admin(message):
        return

    with lock:
        entries = list(users.values())

    if not entries:
        bot.reply_to(message, "No registered users yet.")
        return

    lines = [f"Registered users: {len(entries)}"]

    for user in entries[:50]:
        name = " ".join(
            part for part in [
                user.get("first_name", ""),
                user.get("last_name", ""),
            ] if part
        ) or "Unknown"

        username = (
            f"@{user['username']}"
            if user.get("username")
            else "No username"
        )

        lines.append(
            f"\n{name}\n{username}\nID: {user.get('id', '')}"
        )

    bot.reply_to(message, "\n".join(lines))


# =========================
# ADMIN SEND MESSAGE
# Usage:
# /message "Hello" "123456789"
# /message "Hello" "@username"
# =========================

@bot.message_handler(commands=["message"])
def message_command(message):
    if not is_admin(message):
        return

    try:
        parts = shlex.split(message.text or "")
    except ValueError:
        bot.reply_to(
            message,
            'Usage: /message "Your message" "USER_ID or @username"',
        )
        return

    if len(parts) < 3:
        bot.reply_to(
            message,
            'Usage: /message "Your message" "USER_ID or @username"',
        )
        return

    text_to_send = parts[1]
    target = parts[2]

    if target.startswith("@"):
        wanted = target[1:].lower()

        with lock:
            match = next(
                (
                    data for data in users.values()
                    if data.get("username", "").lower() == wanted
                ),
                None,
            )

        if not match:
            bot.reply_to(
                message,
                "User not found. They must start the bot first.",
            )
            return

        target_id = int(match["id"])
    else:
        try:
            target_id = int(target)
        except ValueError:
            bot.reply_to(message, "Invalid user ID or username.")
            return

    try:
        bot.send_message(target_id, text_to_send)
        bot.reply_to(message, "Message sent successfully.")
    except Exception as exc:
        print("Admin message error:", type(exc).__name__)
        bot.reply_to(
            message,
            "Could not send the message. Check the user ID and whether "
            "the user has started the bot.",
        )


# =========================
# USER MESSAGES / ADMIN REPLIES
# =========================

@bot.message_handler(
    content_types=[
        "text", "photo", "video", "document", "audio",
        "voice", "video_note", "animation", "sticker",
        "location", "contact",
    ]
)
def incoming_message(message):
    if is_admin(message):
        # Reply to a copied/forwarded user message to contact that user.
        replied = message.reply_to_message

        if replied and replied.message_id in message_to_user:
            target_id = message_to_user[replied.message_id]

            try:
                if message.content_type == "text":
                    bot.send_message(target_id, message.text)
                else:
                    bot.copy_message(
                        chat_id=target_id,
                        from_chat_id=message.chat.id,
                        message_id=message.message_id,
                    )

                bot.reply_to(message, "Reply sent to user.")
            except Exception as exc:
                print("Admin reply error:", type(exc).__name__)
                bot.reply_to(message, "Could not send reply.")

        return

    if message.content_type == "text" and (message.text or "").startswith("/"):
        return

    try:
        send_user_info(message)
    except Exception as exc:
        print("Forward error:", type(exc).__name__)

    try:
        bot.reply_to(message, RECEIVED_MESSAGE)
        send_offline_reply(message.chat.id)
    except Exception as exc:
        print("User reply error:", type(exc).__name__)


# =========================
# FLASK WEBHOOK
# =========================

@app.route("/", methods=["GET"])
def home():
    return "BOT_NEUTRIX is running", 200


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    supplied_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if supplied_secret != WEBHOOK_SECRET:
        abort(403)

    data = request.get_json(silent=True)

    if not data:
        abort(400)

    try:
        update = telebot.types.Update.de_json(data)
        bot.process_new_updates([update])
    except Exception as exc:
        print("Webhook processing error:", type(exc).__name__)
        return "Processing error", 500

    return "OK", 200


# =========================
# REGISTER WEBHOOK
# =========================

webhook_url = PUBLIC_URL + "/telegram-webhook"

bot.remove_webhook()
bot.set_webhook(
    url=webhook_url,
    secret_token=WEBHOOK_SECRET,
    allowed_updates=["message"],
)

print("BOT_NEUTRIX webhook configured.")