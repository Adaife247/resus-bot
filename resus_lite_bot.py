"""
╔══════════════════════════════════════════════════════════════╗
║         RESUS LITE — Anonymous Mental Health Bot             ║
║         Built with python-telegram-bot v20+                  ║
╚══════════════════════════════════════════════════════════════╝

SETUP INSTRUCTIONS:
1. Create a bot via @BotFather on Telegram → copy BOT_TOKEN
2. Add the bot to your channel as an Admin (with "Post Messages" permission)
3. Get your CHANNEL_ID (e.g. "@myresuschannel" or a numeric ID like -1001234567890)
4. Set ADMIN_IDS to the Telegram user ID(s) of your moderators
5. Install deps:  pip install python-telegram-bot apscheduler
6. Run:          python resus_lite_bot.py
"""

import logging
import uuid
import json
import os
from datetime import time
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Bot,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ─────────────────────────────────────────────
#  🔧  CONFIGURATION  ← Edit these values
# ─────────────────────────────────────────────

BOT_TOKEN   = "8714395067:AAHs5xclFvkSc5wf_a47Q-6m-O7I2SvWq64"
CHANNEL_ID  = -1003645637131
ADMIN_IDS   = [6102322573]

# Daily prompt schedule (24-hour UTC time)
DAILY_PROMPT_HOUR   = 9
DAILY_PROMPT_MINUTE = 0

# ─────────────────────────────────────────────
#  🚫  MODERATION — Add words to flag/block
# ─────────────────────────────────────────────

BANNED_WORDS = [
    "kill myself", "end my life", "want to die",
    "suicide", "self harm", "cutting myself",
    # Add more as needed — flagged posts are printed to console for admin review
]

CRISIS_RESPONSE = (
    "💙 It sounds like you might be going through something really difficult. "
    "You are not alone.\n\n"
    "Please reach out to a crisis line:\n"
    "🇳🇬 Nigeria: +234-800-800-2000 (SURPIN)\n"
    "🌍 International: https://findahelpline.com\n\n"
    "Your message has been held — a moderator will review it shortly."
)

# Daily prompts — picked in rotation
DAILY_PROMPTS = [
    "🌤 How are you feeling today? Share honestly — this is a safe space.",
    "🏆 Share one small win from this week, no matter how tiny.",
    "💬 What's one thing you wish someone would ask you right now?",
    "🌱 What's something you're learning about yourself lately?",
    "🤝 What kind of support do you need most right now?",
    "✨ Name one thing you did today that took courage.",
    "🫂 What does 'feeling okay' look like for you today?",
]

# ─────────────────────────────────────────────
#  💾  IN-MEMORY STORAGE
#  (For production, replace with a real database like SQLite or Redis)
# ─────────────────────────────────────────────

# post_id → { "channel_msg_id": int, "text": str, "reactions": {"❤️": set(), "🫂": set()} }
posts: dict[str, dict] = {}

# Simple counter for human-readable post IDs
post_counter = 0

prompt_index = 0  # Tracks which daily prompt to send next

# ─────────────────────────────────────────────
#  📝  LOGGING SETUP
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ResusLiteBot")


# ══════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════

def generate_post_id() -> str:
    """Generate a short, human-readable post ID like RL-0042."""
    global post_counter
    post_counter += 1
    return f"RL-{post_counter:04d}"


def contains_banned_word(text: str) -> Optional[str]:
    """Return the first banned phrase found in text, or None if clean."""
    lower = text.lower()
    for phrase in BANNED_WORDS:
        if phrase in lower:
            return phrase
    return None


def build_reaction_keyboard(post_id: str) -> InlineKeyboardMarkup:
    """
    Builds reaction buttons and a 'Reply' button that opens a private chat with the bot.
    """
    data = posts.get(post_id, {})
    heart_count = len(data.get("reactions", {}).get("❤️", set()))
    hug_count   = len(data.get("reactions", {}).get("🫂", set()))

    keyboard = [
        [
            InlineKeyboardButton(
                f"❤️ Relate ({heart_count})",
                callback_data=f"react|{post_id}|❤️"
            ),
            InlineKeyboardButton(
                f"🫂 Support ({hug_count})",
                callback_data=f"react|{post_id}|🫂"
            ),
        ],
        [
            InlineKeyboardButton(
                "💬 Reply",
                url=f"https://t.me/ResusLite_Bot?start={post_id}"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_post(post_id: str, text: str) -> str:
    return (
        f"🫂 *Someone shared:*\n\n"
        f"“{text}”\n\n"
        f"_Tap below to react ❤️ or show support 🫂_\n"
        f"`{post_id}`"
    )


def format_reply(original_text: str, reply_text: str) -> str:
    """Format an anonymous reply for the channel."""
    # Trim original if too long for preview
    preview = (original_text[:80] + "…") if len(original_text) > 80 else original_text
    return (
        f"🔒 *Anonymous* replied:\n\n"
        f"┊ _Replying to:_ \"{preview}\"\n\n"
        f"{reply_text}"
    )


# ══════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /start command. If user clicks reply button, start_args contains the post_id.
    """
    start_args = context.args  # Telegram passes ?start=<post_id> as args
    if start_args:
        post_id = start_args[0]
        context.user_data["reply_to"] = post_id
        await update.message.reply_text(
            f"💬 You're replying to {post_id}.\n"
            "Send your message now 👇"
        )
    else:
        # Regular welcome message
        await update.message.reply_text(
            "💙 Welcome to Resus Lite!\n\n"
            "Send me a message here and I'll post it anonymously in the channel.\n\n"
            "💬 To reply to a post, click the 'Reply' button under that post — it will open this chat automatically."
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send usage instructions."""
    await update.message.reply_text(
        "📖 *Resus Lite — Help*\n\n"
        "*Posting anonymously:*\n"
        "Just type and send any message to me.\n\n"
        "*Replying to a post:*\n"
        "`Reply to RL-0001: your reply here`\n\n"
        "*Reacting to posts:*\n"
        "Tap ❤️ or 🫂 below any post in the channel.\n\n"
        "*Admin commands:*\n"
        "`/delete RL-0001` — Remove a post from the channel\n"
        "`/listposts` — List recent post IDs (admin only)\n",
        parse_mode="Markdown",
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: delete a post from the channel by post ID."""
    user_id = update.effective_user.id

    # Check admin permission
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You don't have permission to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: `/delete RL-0001`", parse_mode="Markdown")
        return

    post_id = context.args[0].upper()

    if post_id not in posts:
        await update.message.reply_text(f"❌ Post `{post_id}` not found.", parse_mode="Markdown")
        return

    channel_msg_id = posts[post_id]["channel_msg_id"]

    try:
        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_msg_id)
        del posts[post_id]
        await update.message.reply_text(f"✅ Post `{post_id}` has been deleted.", parse_mode="Markdown")
        logger.info(f"[MODERATION] Admin {user_id} deleted post {post_id}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not delete post: {e}")


async def cmd_listposts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: list all tracked post IDs."""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admins only.")
        return

    if not posts:
        await update.message.reply_text("No posts yet.")
        return

    lines = [f"`{pid}` — {data['text'][:40]}…" for pid, data in list(posts.items())[-20:]]
    await update.message.reply_text(
        "*Recent posts (last 20):*\n\n" + "\n".join(lines),
        parse_mode="Markdown",
    )
async def cmd_testprompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Restrict to admins only (important)
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Admins only.")
        return

    await send_daily_prompt(context.bot)
    await update.message.reply_text("✅ Test daily prompt sent.")

# ══════════════════════════════════════════════
#  CALLBACK HANDLER — Reply button
# ══════════════════════════════════════════════
async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        _, post_id = query.data.split("|")
    except:
        return
    # Save the post ID temporarily for this user
    context.user_data["reply_to"] = post_id
    await query.message.reply_text(
        f"💬 You're replying to {post_id}\n\nSend your message now 👇"
    )


# ══════════════════════════════════════════════
#  MESSAGE HANDLER — Core anonymous posting logic
# ══════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main message handler.
    - Detects if the user is replying to a post via private chat.
    - Otherwise treats as a new anonymous post.
    - Flags messages containing banned words.
    """
    user_id  = update.effective_user.id
    raw_text = update.message.text.strip()

    # ── Safety check ──────────────────────────────
    flagged_word = contains_banned_word(raw_text)
    if flagged_word:
        logger.warning(
            f"[FLAGGED] User {user_id} | Trigger: '{flagged_word}' | Message: {raw_text!r}"
        )
        await update.message.reply_text(CRISIS_RESPONSE)
        return

    # ── Check if user is replying via private chat
    if "reply_to" in context.user_data:
        post_id = context.user_data.pop("reply_to")
        await handle_reply_from_button(update, context, post_id)
        return

    # ── Reply detection (old style) ──────────────
    if raw_text.lower().startswith("reply to "):
        await handle_reply(update, context, raw_text)
        return

    # ── New anonymous post
    await handle_new_post(update, context, raw_text)

async def handle_new_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    """Post a new anonymous message to the channel."""
    post_id = generate_post_id()

    # Store the post (reactions start empty)
    posts[post_id] = {
        "channel_msg_id": None,
        "text": text,
        "reactions": {"❤️": set(), "🫂": set()},
    }

    formatted = format_post(post_id, text)
    keyboard   = build_reaction_keyboard(post_id)

    try:
        sent = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatted,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        posts[post_id]["channel_msg_id"] = sent.message_id

        await update.message.reply_text(
            f"✅ Your message has been posted anonymously as `{post_id}`.\n\n"
            f"Others can reply with:\n`Reply to {post_id}: their message`",
            parse_mode="Markdown",
        )
        logger.info(f"[POST] New post {post_id} | Channel msg ID: {sent.message_id}")

    except Exception as e:
        logger.error(f"[ERROR] Failed to post {post_id}: {e}")
        await update.message.reply_text("⚠️ Something went wrong. Please try again.")
        del posts[post_id]


async def handle_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_text: str,
) -> None:
    """
    Parse and post an anonymous reply.
    Expected format: "Reply to RL-0001: message here"
    """
    try:
        # Split on first colon after the post ID
        after_prefix = raw_text[len("reply to "):].strip()  # "RL-0001: message"
        post_id_part, reply_body = after_prefix.split(":", 1)
        post_id    = post_id_part.strip().upper()
        reply_body = reply_body.strip()
    except ValueError:
        await update.message.reply_text(
            "⚠️ Couldn't parse your reply. Use this format:\n"
            "`Reply to RL-0001: your message here`",
            parse_mode="Markdown",
        )
        return

    if not reply_body:
        await update.message.reply_text("⚠️ Your reply message was empty.")
        return

    if post_id not in posts:
        await update.message.reply_text(
            f"❌ Post `{post_id}` doesn't exist or has been removed.",
            parse_mode="Markdown",
        )
        return

    original_text      = posts[post_id]["text"]
    original_msg_id    = posts[post_id]["channel_msg_id"]
    formatted_reply    = format_reply(original_text, reply_body)

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatted_reply,
            parse_mode="Markdown",
            reply_to_message_id=original_msg_id,  # Thread the reply
        )
        await update.message.reply_text("✅ Your anonymous reply has been posted. 🫂")
        logger.info(f"[REPLY] Reply to {post_id} posted.")

    except Exception as e:
        logger.error(f"[ERROR] Failed to post reply to {post_id}: {e}")
        await update.message.reply_text("⚠️ Something went wrong. Please try again.")
async def handle_reply_from_button(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: str):
    """
    Handles replies coming from the 'Reply' button private chat.
    """
    reply_text = update.message.text.strip()
    if not reply_text:
        await update.message.reply_text("⚠️ Your reply message was empty.")
        return

    if post_id not in posts:
        await update.message.reply_text(
            f"❌ Post `{post_id}` doesn't exist or has been removed.",
            parse_mode="Markdown",
        )
        return

    original_text   = posts[post_id]["text"]
    original_msg_id = posts[post_id]["channel_msg_id"]
    formatted_reply = format_reply(original_text, reply_text)

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatted_reply,
            parse_mode="Markdown",
            reply_to_message_id=original_msg_id
        )
        await update.message.reply_text("✅ Your anonymous reply has been posted. 🫂")
        logger.info(f"[REPLY] Reply to {post_id} posted via private chat.")
    except Exception as e:
        logger.error(f"[ERROR] Failed to post reply to {post_id}: {e}")
        await update.message.reply_text("⚠️ Something went wrong. Please try again.")

# ══════════════════════════════════════════════
#  REACTION HANDLER
# ══════════════════════════════════════════════

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle reaction button presses.
    Each user can toggle their reaction (click to add, click again to remove).
    callback_data format: "react|<post_id>|<emoji>"
    """
    query   = update.callback_query
    user_id = query.from_user.id

    await query.answer()  # Acknowledge the button press immediately

    # Parse callback data
    try:
        _, post_id, emoji = query.data.split("|")
    except ValueError:
        return

    if post_id not in posts:
        await query.answer("This post no longer exists.", show_alert=True)
        return

    reactor_set = posts[post_id]["reactions"].setdefault(emoji, set())

    # Toggle: if user already reacted, remove; otherwise add
    if user_id in reactor_set:
        reactor_set.discard(user_id)
    else:
        reactor_set.add(user_id)

    # Update the message keyboard with new counts
    new_keyboard = build_reaction_keyboard(post_id)

    try:
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
    except Exception as e:
        logger.warning(f"[REACTION] Could not update keyboard for {post_id}: {e}")

# ══════════════════════════════════════════════
#  REPLY BUTTON HANDLER
# ══════════════════════════════════════════════


async def handle_reply_from_button(update, context, post_id):
    reply_text = update.message.text.strip()

    if post_id not in posts:
        await update.message.reply_text("❌ Original post not found.")
        return

    original_text   = posts[post_id]["text"]
    original_msg_id = posts[post_id]["channel_msg_id"]

    formatted_reply = format_reply(original_text, reply_text)

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=formatted_reply,
            parse_mode="Markdown",
            reply_to_message_id=original_msg_id,
        )
        await update.message.reply_text("✅ Your reply has been posted 🫂")
    except Exception as e:
        print(e)
        await update.message.reply_text("⚠️ Failed to send reply.")
# ══════════════════════════════════════════════
#  DAILY PROMPTS (Scheduled)
# ══════════════════════════════════════════════

async def send_daily_prompt(bot: Bot) -> None:
    """
    Send a rotating daily prompt to the channel.
    Scheduled to run every day at DAILY_PROMPT_HOUR:DAILY_PROMPT_MINUTE UTC.
    """
    global prompt_index
    prompt = DAILY_PROMPTS[prompt_index % len(DAILY_PROMPTS)]
    prompt_index += 1

    message = (
        f"💬 *Daily Check-In*\n\n"
        f"{prompt}\n\n"
        f"_Reply to the bot privately and your response will be posted here anonymously._"
    )

    try:
prompt_id = f"PROMPT-{prompt_index:04d}"

# Store prompt like a normal post
posts[prompt_id] = {
    "channel_msg_id": None,
    "text": prompt,
    "reactions": {"❤️": set(), "🫂": set()},
}

keyboard = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "💬 Share anonymously",
            callback_data=f"reply|{prompt_id}"
        )
    ]
])

sent = await bot.send_message(
    chat_id=CHANNEL_ID,
    text=message,
    parse_mode="Markdown",
    reply_markup=keyboard,
)

posts[prompt_id]["channel_msg_id"] = sent.message_id
        logger.info(f"[PROMPT] Daily prompt sent: {prompt[:40]}…")
    except Exception as e:
        logger.error(f"[PROMPT] Failed to send daily prompt: {e}")


# ══════════════════════════════════════════════
#  BOT STARTUP
# ══════════════════════════════════════════════

def main() -> None:
    """Build and start the bot application."""

    # Validate config before starting
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise ValueError("❌ Please set BOT_TOKEN in the script before running.")
    if CHANNEL_ID == "@your_channel_username":
        raise ValueError("❌ Please set CHANNEL_ID in the script before running.")

    # Build the Application
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Register command handlers ──────────────
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("delete",    cmd_delete))
    app.add_handler(CommandHandler("listposts", cmd_listposts))
    app.add_handler(CommandHandler("testprompt", cmd_testprompt))

    # ── Register message handler (private chats only) ──
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_message,
    ))

    # ── Register reaction button handler ──────
    app.add_handler(CallbackQueryHandler(handle_reaction, pattern=r"^react\|"))

    # ── Schedule daily prompts ─────────────────
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        send_daily_prompt,
        trigger="cron",
        hour=DAILY_PROMPT_HOUR,
        minute=DAILY_PROMPT_MINUTE,
        args=[app.bot],
    )
    scheduler.start()
    logger.info(
        f"[SCHEDULER] Daily prompts scheduled at "
        f"{DAILY_PROMPT_HOUR:02d}:{DAILY_PROMPT_MINUTE:02d} UTC"
    )

    # ── Start polling ──────────────────────────
    logger.info("🚀 Resus Lite Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
