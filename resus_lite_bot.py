import logging
import sqlite3
import re
from datetime import time
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# --- Configuration ---
BOT_TOKEN = "8714395067:AAHs5xclFvkSc5wf_a47Q-6m-O7I2SvWq64"
ADMIN_IDS = [6102322573] # Replace with actual admin Telegram user IDs
FEED_CHAT_ID = "-1003645637131" # Channel where posts are broadcasted

BANNED_WORDS = ['suicide', 'kill myself', 'end it all']
CRISIS_MESSAGE = (
    "⚠️ We noticed your message contains concerning words. "
    "If you are in distress, please know you are not alone. "
    "Reach out to a local crisis hotline or visit an emergency room immediately."
)

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- In-Memory State ---
# reactions[post_id] = {'hearts': set(chat_ids)}
reactions_db = defaultdict(lambda: {'hearts': set()})
# sessions[chat_id] = peer_chat_id
active_sessions = {}

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect('resus_lite.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            handle TEXT UNIQUE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            post_id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_chat_id INTEGER,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_or_create_user(chat_id: int) -> str:
    conn = sqlite3.connect('resus_lite.db')
    cursor = conn.cursor()
    cursor.execute('SELECT handle FROM users WHERE chat_id = ?', (chat_id,))
    row = cursor.fetchone()
    
    if row:
        handle = row[0]
    else:
        cursor.execute('SELECT COUNT(*) FROM users')
        count = cursor.fetchone()[0] + 1
        handle = f"RL-{count:04d}"
        cursor.execute('INSERT INTO users (chat_id, handle) VALUES (?, ?)', (chat_id, handle))
        conn.commit()
    
    conn.close()
    return handle

def get_chat_id_by_handle(handle: str):
    conn = sqlite3.connect('resus_lite.db')
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id FROM users WHERE handle = ?', (handle,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

# --- Helper Functions ---
def check_moderation(text: str) -> bool:
    text_lower = text.lower()
    return any(word in text_lower for word in BANNED_WORDS)

def build_post_keyboard(post_id: int, heart_count: int = 0) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(f"❤️ {heart_count}", callback_data=f"heart_{post_id}"),
            InlineKeyboardButton("🫂 Support (1:1)", callback_data=f"support_{post_id}")
        ],
        [
            InlineKeyboardButton("Reply", callback_data=f"reply_{post_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    handle = get_or_create_user(chat_id)
    await update.message.reply_text(
        f"Welcome to Resus Lite. Your anonymous handle is {handle}.\n"
        "Use /post <message> to share your thoughts safely."
    )

async def post_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.replace('/post', '', 1).strip()
    
    if not text:
        await update.message.reply_text("Please include a message. Example: /post feeling overwhelmed today.")
        return

    if check_moderation(text):
        logger.warning(f"Flagged content from {chat_id}: {text}")
        await update.message.reply_text(CRISIS_MESSAGE)
        return

    handle = get_or_create_user(chat_id)
    
    # Save to DB
    conn = sqlite3.connect('resus_lite.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO posts (author_chat_id, content) VALUES (?, ?)', (chat_id, text))
    post_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Broadcast to feed or return to user
    formatted_post = f"*{handle}* shared:\n\n{text}"
    
    try:
        # Assuming FEED_CHAT_ID is set up and bot is admin there. 
        # If testing locally without a channel, replace FEED_CHAT_ID with chat_id
        await context.bot.send_message(
            chat_id=FEED_CHAT_ID, # Change to chat_id for local testing DMs
            text=formatted_post,
            parse_mode='Markdown',
            reply_markup=build_post_keyboard(post_id)
        )
        await update.message.reply_text("Your post has been shared anonymously.")
    except Exception as e:
        logger.error(f"Failed to send post: {e}")
        await update.message.reply_text("Failed to broadcast post. Ensure bot has channel access.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("heart_"):
        post_id = int(data.split("_")[1])
        hearts = reactions_db[post_id]['hearts']
        
        # Toggle reaction
        if user_id in hearts:
            hearts.remove(user_id)
        else:
            hearts.add(user_id)
            
        await query.edit_message_reply_markup(
            reply_markup=build_post_keyboard(post_id, len(hearts))
        )
        
    elif data.startswith("reply_"):
        post_id = int(data.split("_")[1])
        # Fetch author handle
        conn = sqlite3.connect('resus_lite.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT users.handle FROM posts 
            JOIN users ON posts.author_chat_id = users.chat_id 
            WHERE posts.post_id = ?
        ''', (post_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            handle = row[0]
            await context.bot.send_message(
                chat_id=user_id,
                text=f"To reply, copy and paste this template:\n\nReply to {handle}: [your message]"
            )

    elif data.startswith("support_"):
        post_id = int(data.split("_")[1])
        conn = sqlite3.connect('resus_lite.db')
        cursor = conn.cursor()
        cursor.execute('SELECT author_chat_id FROM posts WHERE post_id = ?', (post_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            op_chat_id = row[0]
            if op_chat_id == user_id:
                await context.bot.send_message(chat_id=user_id, text="You cannot support your own post.")
                return
                
            if user_id in active_sessions or op_chat_id in active_sessions:
                await context.bot.send_message(chat_id=user_id, text="One of you is already in an active session.")
                return

            # Establish session
            active_sessions[user_id] = op_chat_id
            active_sessions[op_chat_id] = user_id
            
            await context.bot.send_message(chat_id=user_id, text="🫂 1:1 Session started with the author. Messages you send here will be relayed. Type /end to finish.")
            await context.bot.send_message(chat_id=op_chat_id, text="🫂 A helper has connected with you regarding your recent post. Messages you send here will be relayed. Type /end to finish.")

async def handle_general_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    # Handle active 1:1 sessions
    if chat_id in active_sessions:
        peer_id = active_sessions[chat_id]
        if check_moderation(text):
            await update.message.reply_text(CRISIS_MESSAGE)
            return
        await context.bot.send_message(chat_id=peer_id, text=f"💬: {text}")
        return

    # Handle direct replies (e.g., "Reply to RL-0001: message")
    reply_match = re.match(r"^Reply to (RL-\d{4}):\s*(.*)", text, re.IGNORECASE)
    if reply_match:
        target_handle = reply_match.group(1).upper()
        message_content = reply_match.group(2)
        
        target_chat_id = get_chat_id_by_handle(target_handle)
        if target_chat_id:
            sender_handle = get_or_create_user(chat_id)
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=f"📩 Reply from {sender_handle}:\n\n{message_content}"
            )
            await update.message.reply_text("Reply sent.")
        else:
            await update.message.reply_text("Handle not found.")
        return

async def end_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_sessions:
        peer_id = active_sessions.pop(chat_id)
        active_sessions.pop(peer_id, None)
        
        await update.message.reply_text("Session ended.")
        await context.bot.send_message(chat_id=peer_id, text="The other user has ended the session.")
    else:
        await update.message.reply_text("You are not in an active session.")

# --- Admin Commands ---
async def delete_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    
    try:
        post_id = int(context.args[0])
        conn = sqlite3.connect('resus_lite.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM posts WHERE post_id = ?', (post_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"Post {post_id} deleted from database.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /delete <post_id>")

async def list_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
        
    conn = sqlite3.connect('resus_lite.db')
    cursor = conn.cursor()
    cursor.execute('SELECT post_id, author_chat_id, content FROM posts ORDER BY timestamp DESC LIMIT 10')
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await update.message.reply_text("No posts found.")
        return
        
    response = "Recent posts:\n"
    for row in rows:
        response += f"ID: {row[0]} | Auth: {row[1]}\n{row[2][:30]}...\n---\n"
    await update.message.reply_text(response)

# --- Scheduled Tasks ---
async def send_daily_prompt(context: ContextTypes.DEFAULT_TYPE):
    prompt_text = "🌱 Daily Prompt: What is one small win you had today, no matter how minor?"
    
    conn = sqlite3.connect('resus_lite.db')
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id FROM users')
    users = cursor.fetchall()
    conn.close()
    
    for (chat_id,) in users:
        try:
            await context.bot.send_message(chat_id=chat_id, text=prompt_text)
        except Exception as e:
            logger.error(f"Failed to send prompt to {chat_id}: {e}")

async def test_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id in ADMIN_IDS:
        await send_daily_prompt(context)
        await update.message.reply_text("Test prompt triggered.")

# --- Main Application ---
def main():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("post", post_message))
    app.add_handler(CommandHandler("end", end_session))
    app.add_handler(CommandHandler("delete", delete_post))
    app.add_handler(CommandHandler("listposts", list_posts))
    app.add_handler(CommandHandler("testprompt", test_prompt))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_messages))

    # Job Queue for Daily Prompts (e.g., 10:00 AM UTC)
    job_queue = app.job_queue
    job_queue.run_daily(send_daily_prompt, time=time(hour=10, minute=0, second=0))

    logger.info("Resus Lite Bot is starting...")
    app.run_polling()

if __name__ == '__main__':
    main()
