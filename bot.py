
import os
import json
import shlex
import getpass
import threading
from datetime import datetime

import telebot
from telebot import types

ADMIN_ID = 8829161471
DATA_FILE = "users.json"
OFFLINE_MESSAGE = (
    "Sorry, The owner is offline just leave message what you want "
    "he will see and reply"
)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    TOKEN = getpass.getpass("Enter BotFather token (hidden): ").strip()

bot = telebot.TeleBot(TOKEN)
lock = threading.Lock()
owner_online = True


def load_users():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


users = load_users()


def save_users():
    with lock:
        temp_file = DATA_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, DATA_FILE)


def get_name(user):
    name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    )
    return name or "Unknown"


def register_user(user):
    uid = str(user.id)
    if uid not in users:
        numbers = [
            int(info["label"].split("_")[1])
            for info in users.values()
            if info.get("label", "").startswith("USER_")
            and info["label"].split("_")[1].isdigit()
        ]
        label = f"USER_{max(numbers, default=0) + 1}"
        users[uid] = {"label": label}

    users[uid]["name"] = get_name(user)
    users[uid]["username"] = user.username or ""
    users[uid]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_users()
    return users[uid]["label"]


def user_details(user, message_text):
    label = register_user(user)
    username = f"@{user.username}" if user.username else "Not set"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return (
        f"📩 New message received\n\n"
        f"User: {label}\n"
        f"Name: {get_name(user)}\n"
        f"Username: {username}\n"
        f"User ID: {user.id}\n"
        f"Time: {now}\n"
        f"Message: {message_text or '[media or other content]'}"
    )


def is_admin(message):
    return message.from_user and message.from_user.id == ADMIN_ID


@bot.message_handler(commands=["start"])
def start_command(message):
    if is_admin(message):
        bot.reply_to(
            message,
            "Admin panel ready.\n"
            "/online - mark owner online\n"
            "/offline - mark owner offline\n"
            '/message "Your message" "username or name or ID"\n'
            "/users - list registered users"
        )
        return

    register_user(message.from_user)
    bot.reply_to(message, "Hello! Your message can be sent to the owner.")
    bot.send_message(ADMIN_ID, user_details(message.from_user, "/start"))

    if not owner_online:
        bot.send_message(message.chat.id, OFFLINE_MESSAGE)


@bot.message_handler(commands=["online", "offline"])
def set_status(message):
    global owner_online

    if not is_admin(message):
        return

    owner_online = message.text.split()[0].lower() == "/online"
    status = "ONLINE 🟢" if owner_online else "OFFLINE 🔴"
    bot.reply_to(message, f"Owner status: {status}")


@bot.message_handler(commands=["users"])
def list_users(message):
    if not is_admin(message):
        return

    if not users:
        bot.reply_to(message, "No registered users yet.")
        return

    lines = ["Registered users:"]
    for uid, info in users.items():
        username = f"@{info['username']}" if info.get("username") else "No username"
        lines.append(
            f"{info.get('label', 'USER')} | {info.get('name', 'Unknown')} "
            f"| {username} | ID: {uid}"
        )

    output = "\n".join(lines)
    for start in range(0, len(output), 3500):
        bot.send_message(ADMIN_ID, output[start:start + 3500])


@bot.message_handler(commands=["message"])
def send_private_message(message):
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
            'Usage:\n/message "Hello there" "@username"\n'
            'Or:\n/message "Hello there" "USER_1"\n'
            'You can also use a registered user ID.'
        )
        return

    target_lower = target.lstrip("@").casefold()
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
            "Try /users to check registered users."
        )
        return

    if len(matches) > 1:
        bot.reply_to(
            message,
            "More than one user has that name. Use their numeric Telegram ID."
        )
        return

    try:
        bot.send_message(int(matches[0]), text_to_send)
        bot.reply_to(message, "Message delivered successfully.")
    except Exception as exc:
        bot.reply_to(
            message,
            f"Could not deliver the message: {type(exc).__name__}. "
            "The user may have blocked the bot."
        )


@bot.message_handler(
    func=lambda m: bool(m.from_user)
    and m.from_user.id != ADMIN_ID
    and not (m.text or "").startswith("/")
    ,
    content_types=[
        "text", "photo", "video", "document", "audio", "voice",
        "video_note", "animation", "sticker", "location", "contact"
    ]
)
def receive_user_message(message):
    register_user(message.from_user)

    text_content = message.text or message.caption or ""
    details = user_details(message.from_user, text_content)

    try:
        bot.send_message(ADMIN_ID, details)
        bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id
        )
    except Exception:
        try:
            bot.send_message(
                ADMIN_ID,
                "Could not copy the original message. "
                "Check the bot's permissions and supported message type."
            )
        except Exception:
            pass

    if not owner_online:
        bot.send_message(message.chat.id, OFFLINE_MESSAGE)


print("Admin Inbox Bot is running...")
bot.infinity_polling(skip_pending=True)

