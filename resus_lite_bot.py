import logging
import sqlite3
import os
import re
import asyncio
from datetime import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# --- Configuration ---
BOT_TOKEN = "8714395067:AAHs5xclFvkSc5wf_a47Q-6m-O7I2SvWq64" # Replace if not using Env Variables
ADMIN_IDS = [6102322573] # <--- REPLACE WITH YOUR ACTUAL TELEGRAM ID
FEED_CHAT_ID = "-1003645637131" 

CRISIS_MESSAGE = (
    "⚠️ We noticed your message contains concerning words. "
    "If you are in distress, please know you are not alone. "
    "Reach out to a local crisis hotline or visit an emergency room immediately."
)

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

user_ui_states = {} 

# --- Database Setup & Persistence (RAILWAY READY) ---
def get_db_connection():
    os.makedirs('/app/data', exist_ok=True)
    conn = sqlite3.connect('/app/data/resus_lite.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, handle TEXT UNIQUE)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS posts (post_id INTEGER PRIMARY KEY AUTOINCREMENT, author_chat_id INTEGER, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS reactions (post_id INTEGER, chat_id INTEGER, PRIMARY KEY (post_id, chat_id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS active_sessions (chat_id INTEGER PRIMARY KEY, peer_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS helpers (chat_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'pending')''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS banned_users (chat_id INTEGER PRIMARY KEY)''')
    conn.commit()
    conn.close()

# --- Helper Functions ---
def get_or_create_user(chat_id: int) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT handle FROM users WHERE chat_id = ?', (chat_id,))
    row = cursor.fetchone()
    
    if row:
        handle = row['handle']
    else:
        cursor.execute('SELECT COUNT(*) FROM users')
        count = cursor.fetchone()[0] + 1
        
        # 50 Calming & Empowering Adjectives
        adjectives = [
            "Calm", "Brave", "Quiet", "Gentle", "Kind", "Warm", "Bright", "Serene", 
            "Steady", "Hopeful", "Peaceful", "Safe", "Mindful", "Grounded", "Patient", 
            "Resilient", "Radiant", "Clear", "Stellar", "Noble", "True", "Pure", 
            "Vivid", "Luminous", "Strong", "Loyal", "Wise", "Earnest", "Tranquil",
            "Mellow", "Lucid", "Sound", "Still", "Adept", "Vigilant", "Humble",
            "Fierce", "Tender", "Sincere", "Aura", "Zen", "Bold", "Candid", 
            "Valid", "Subtle", "Keen", "Prime", "Solid", "Brisk", "Fluid"
        ]
        
        # 50 Grounding Nature & Abstract Nouns
        nouns = [
            "River", "Cedar", "Dawn", "Breeze", "Forest", "Brook", "Ocean", "Maple", 
            "Willow", "Star", "Moon", "Sky", "Horizon", "Echo", "Harbor", "Valley", 
            "Peak", "Grove", "Beacon", "Tide", "Oasis", "Aurora", "Nova", "Ray", 
            "Coast", "Ridge", "Summit", "Dune", "Cove", "Glacier", "Haven", "Comet",
            "Orbit", "Pebble", "Stone", "Leaf", "Petal", "Root", "Sprout", "Bloom",
            "Rain", "Mist", "Cloud", "Storm", "Drift", "Current", "Spark", "Flame",
            "Ember", "Ash"
        ]
        
        adj = random.choice(adjectives)
        noun = random.choice(nouns)
        
        handle = f"{adj}-{noun}-{count:02d}"
        
        cursor.execute('INSERT INTO users (chat_id, handle) VALUES (?, ?)', (chat_id, handle))
        conn.commit()
    conn.close()
    return handle

def is_banned(chat_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM banned_users WHERE chat_id = ?', (chat_id,))
    banned = cursor.fetchone() is not None
    conn.close()
    return banned

def check_moderation(text: str) -> bool:
    """
    Advanced Heuristics Engine for Distress Detection
    Returns True if severe distress is detected, False otherwise.
    """
    text_lower = text.lower()
    
    # LEVEL 1: Immediate Crisis & Self-Harm Intent
    # Catches explicit phrases and common spelling workarounds (e.g., "k!ll", "k1ll")
    crisis_pattern = r"(suicide|k[i!1]ll\s*myself|end\s*it\s*all|want\s*to\s*d[i!1]e|sleep\s*forever|no\s*point\s*in\s*living)"
    
    # LEVEL 2: Severe Somatic / Biological Distress
    # Catches physical symptoms of severe nervous system dysregulation and panic
    somatic_pattern = r"(can\'?t\s*breathe|heart\s*is\s*(exploding|racing)|chest\s*is\s*crushing|completely\s*numb|make\s*it\s*stop|losing\s*my\s*mind)"
    
    # LEVEL 3: Severe Hopelessness & Apathy
    # Catches dangerous depressive states and extreme cognitive exhaustion
    apathy_pattern = r"(giving\s*up|done\s*trying|nothing\s*matters\s*anymore|too\s*exhausted\s*to\s*(live|try))"
    
    # Evaluate the text against the patterns
    if re.search(crisis_pattern, text_lower):
        logger.warning(f"CRISIS FLAG TRIGGERED: {text}")
        return True
        
    if re.search(somatic_pattern, text_lower):
        logger.warning(f"SOMATIC PANIC FLAG TRIGGERED: {text}")
        return True
        
    if re.search(apathy_pattern, text_lower):
        logger.warning(f"APATHY FLAG TRIGGERED: {text}")
        return True
        
    return False

def get_heart_count(post_id: int) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM reactions WHERE post_id = ?', (post_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def build_post_keyboard(post_id: int) -> InlineKeyboardMarkup:
    heart_count = get_heart_count(post_id)
    keyboard = [
        [
            InlineKeyboardButton(f"❤️ {heart_count}", callback_data=f"heart_{post_id}"),
            InlineKeyboardButton("🫂 Support (1:1)", callback_data=f"support_{post_id}")
        ],
        [InlineKeyboardButton("💬 Reply Anonymously", callback_data=f"reply_{post_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_main_menu():
    keyboard = [
        [KeyboardButton("📝 New Post"), KeyboardButton("🛑 End Session")],
        [KeyboardButton("🧘‍♀️ Quick Relief"), KeyboardButton("🤝 Apply as Helper")], # <-- ADDED QUICK RELIEF HERE
        [KeyboardButton("👤 My Handle")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- Standard User Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_banned(chat_id):
        return
        
    handle = get_or_create_user(chat_id)
    user_ui_states.pop(chat_id, None) 
    
    await update.message.reply_text(
        "Welcome to Resus Lite! 🌿\n\n"
        "This is a safe, anonymous space. Use the menu below to navigate.",
        reply_markup=get_main_menu()
    )

# --- Callback & Interactive Menus ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if is_banned(user_id):
        await query.answer("Your account is restricted.", show_alert=True)
        return

    data = query.data

    # --- 1. HEART REACTIONS ---
    if data.startswith("heart_"):
        await query.answer()
        post_id = int(data.split("_")[1])
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT 1 FROM reactions WHERE post_id=? AND chat_id=?', (post_id, user_id))
        if cursor.fetchone():
            cursor.execute('DELETE FROM reactions WHERE post_id=? AND chat_id=?', (post_id, user_id))
        else:
            cursor.execute('INSERT INTO reactions (post_id, chat_id) VALUES (?, ?)', (post_id, user_id))
            
        conn.commit()
        conn.close()
        await query.edit_message_reply_markup(reply_markup=build_post_keyboard(post_id))

    # --- 2. REPLYING ---
    elif data.startswith("reply_"):
        await query.answer()
        post_id = int(data.split("_")[1])
        user_ui_states[user_id] = f"replying_{post_id}"
        await context.bot.send_message(
            chat_id=user_id,
            text="💬 Type your reply below. It will be sent anonymously to the author.\n*(Or type 'cancel' to abort)*"
        )

    # --- 3. 1:1 SUPPORT SESSIONS ---
    elif data.startswith("support_"):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT status FROM helpers WHERE chat_id = ?', (user_id,))
        helper = cursor.fetchone()
        
        if not helper or helper['status'] != 'approved':
            await query.answer("Access Denied", show_alert=True)
            await context.bot.send_message(chat_id=user_id, text="⚠️ Only approved helpers can start 1:1 sessions. Tap '🤝 Apply as Helper' first.")
            conn.close()
            return

        await query.answer()
        post_id = int(data.split("_")[1])
        cursor.execute('SELECT author_chat_id FROM posts WHERE post_id = ?', (post_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return
            
        op_chat_id = row['author_chat_id']
        
        if op_chat_id == user_id:
            await context.bot.send_message(chat_id=user_id, text="You cannot support your own post.")
            conn.close()
            return
            
        cursor.execute('SELECT chat_id FROM active_sessions WHERE chat_id IN (?, ?)', (user_id, op_chat_id))
        if cursor.fetchone():
            await context.bot.send_message(chat_id=user_id, text="One of you is already in an active session.")
            conn.close()
            return

        cursor.execute('INSERT INTO active_sessions (chat_id, peer_id) VALUES (?, ?)', (user_id, op_chat_id))
        cursor.execute('INSERT INTO active_sessions (chat_id, peer_id) VALUES (?, ?)', (op_chat_id, user_id))
        conn.commit()
        conn.close()
        
        user_ui_states.pop(user_id, None)
        user_ui_states.pop(op_chat_id, None)
        
        await context.bot.send_message(chat_id=user_id, text="🟢 1:1 Session started! You are now connected to the author. Tap 🛑 End Session when done.")
        await context.bot.send_message(chat_id=op_chat_id, text="🟢 A vetted helper has connected with you regarding your recent post. Tap 🛑 End Session when done.")

    # --- 4. QUICK RELIEF: BOX BREATHING ---
# --- 4. QUICK RELIEF: BOX BREATHING ---
    elif data == "relief_breathe":
        await query.answer()
        msg = await context.bot.send_message(chat_id=user_id, text="🌬️ Get ready. We will do 3 cycles of Box Breathing to lower your heart rate.")
        await asyncio.sleep(2.5)
        
        try:
            cycles = 3
            for _ in range(cycles):
                await msg.edit_text("🟢 *Inhale* through your nose... (4s)", parse_mode='Markdown')
                await asyncio.sleep(4)
                await msg.edit_text("🟡 *Hold* your breath... (4s)", parse_mode='Markdown')
                await asyncio.sleep(4)
                await msg.edit_text("🔵 *Exhale* slowly through your mouth... (4s)", parse_mode='Markdown')
                await asyncio.sleep(4)
                await msg.edit_text("⚪ *Rest* and hold empty... (4s)", parse_mode='Markdown')
                await asyncio.sleep(4)
                
            await msg.edit_text("✅ Breathing cycle complete. You did great.\n\nTap 🧘‍♀️ Quick Relief on your menu if you need to go again.")
        except Exception as e:
            # If Telegram rejects the edit or hits a timeout, it will print the error directly to you!
            logger.error(f"Breathing visualizer crashed: {e}")
            await context.bot.send_message(chat_id=user_id, text=f"❌ Oops, the visualizer hit a snag: {e}")

    # --- 5. QUICK RELIEF: 5-4-3-2-1 GROUNDING ---
    elif data == "relief_ground_start":
        await query.answer()
        keyboard = [[InlineKeyboardButton("Next Step ➡️", callback_data="ground_5")]]
        await query.edit_message_text(
            "🧠 *5-4-3-2-1 Sensory Grounding*\n\nTake a deep breath. Look around your physical space.\n\n"
            "Find **5 things you can SEE**.\n*(e.g., a pen, a shadow, a cloud)*\n\n"
            "Name them silently to yourself, then tap Next.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    elif data.startswith("ground_"):
        step = data.split("_")[1]
        await query.answer()
        
        if step == "5":
            keyboard = [[InlineKeyboardButton("Next Step ➡️", callback_data="ground_4")]]
            await query.edit_message_text(
                "Find **4 things you can FEEL or TOUCH**.\n*(e.g., the texture of your clothes, your feet on the floor)*\n\nNotice how they physically feel, then tap Next.", 
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
            )
        elif step == "4":
            keyboard = [[InlineKeyboardButton("Next Step ➡️", callback_data="ground_3")]]
            await query.edit_message_text(
                "Find **3 things you can HEAR**.\n*(e.g., a fan, distant traffic, your own breath)*\n\nListen closely, then tap Next.", 
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
            )
        elif step == "3":
            keyboard = [[InlineKeyboardButton("Next Step ➡️", callback_data="ground_2")]]
            await query.edit_message_text(
                "Find **2 things you can SMELL**.\n*(e.g., fresh air, coffee, your soap)*\n\nIf you can't smell anything, just imagine your favorite scent. Then tap Next.", 
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
            )
        elif step == "2":
            keyboard = [[InlineKeyboardButton("Finish ➡️", callback_data="ground_1")]]
            await query.edit_message_text(
                "Find **1 thing you can TASTE**.\n*(e.g., toothpaste, a sip of water, or just notice the state of your mouth)*\n\nThen tap Finish.", 
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
            )
        elif step == "1":
            await query.edit_message_text(
                "✅ Grounding complete. You have successfully anchored your brain back to the present moment.\n\nTake one final deep breath. You are safe."
            )

# --- Central Routing for Text Input ---
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    
    if is_banned(chat_id):
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    if text == "📝 New Post":
        user_ui_states[chat_id] = "posting"
        await update.message.reply_text("✍️ What's on your mind? Type your message below to broadcast it anonymously.\n*(Or type 'cancel' to abort)*")
        conn.close()
        return
        
    elif text == "🛑 End Session":
        cursor.execute('SELECT peer_id FROM active_sessions WHERE chat_id = ?', (chat_id,))
        session = cursor.fetchone()
        if session:
            peer_id = session['peer_id']
            cursor.execute('DELETE FROM active_sessions WHERE chat_id IN (?, ?)', (chat_id, peer_id))
            conn.commit()
            await update.message.reply_text("Session ended.", reply_markup=get_main_menu())
            await context.bot.send_message(chat_id=peer_id, text="The other user has ended the session.", reply_markup=get_main_menu())
        else:
            await update.message.reply_text("You are not in an active session.")
        conn.close()
        return
    elif text == "🧘‍♀️ Quick Relief":
        keyboard = [
            [InlineKeyboardButton("🌬️ Guided Box Breathing", callback_data="relief_breathe")],
            [InlineKeyboardButton("🧠 5-4-3-2-1 Grounding", callback_data="relief_ground_start")]
        ]
        await update.message.reply_text(
            "🌿 *Quick Relief Tools*\n\n"
            "I'm here with you. Choose an exercise below to help regulate your system right now:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        conn.close()
        return
    elif text == "🛡️ Apply as Helper":
        cursor.execute('SELECT status FROM helpers WHERE chat_id = ?', (chat_id,))
        row = cursor.fetchone()
        if row:
            msg = "You are already approved!" if row['status'] == 'approved' else "Your application is pending admin review."
            await update.message.reply_text(msg)
        else:
            cursor.execute("INSERT INTO helpers (chat_id, status) VALUES (?, 'pending')", (chat_id,))
            conn.commit()
            await update.message.reply_text("Your application has been submitted! An admin will review it soon.")
            for admin in ADMIN_IDS:
                try:
                    handle = get_or_create_user(chat_id)
                    await context.bot.send_message(chat_id=admin, text=f"🔔 New helper application from {handle}. Use /approve {handle}")
                except Exception:
                    pass
        conn.close()
        return
        
    elif text == "ℹ️ My Handle":
        handle = get_or_create_user(chat_id)
        await update.message.reply_text(f"Your anonymous handle is: {handle}")
        conn.close()
        return

    if text.lower() == 'cancel':
        user_ui_states.pop(chat_id, None)
        await update.message.reply_text("Action cancelled.", reply_markup=get_main_menu())
        conn.close()
        return

    cursor.execute('SELECT peer_id FROM active_sessions WHERE chat_id = ?', (chat_id,))
    session = cursor.fetchone()
    if session:
        peer_id = session['peer_id']
        conn.close()
        if check_moderation(text):
            await update.message.reply_text(CRISIS_MESSAGE)
            return
        await context.bot.send_message(chat_id=peer_id, text=f"💬: {text}")
        return
        
    current_state = user_ui_states.get(chat_id)
    
    if current_state == "posting":
        if check_moderation(text):
            await update.message.reply_text(CRISIS_MESSAGE)
            return
            
        handle = get_or_create_user(chat_id)
        cursor.execute('INSERT INTO posts (author_chat_id, content) VALUES (?, ?)', (chat_id, text))
        post_id = cursor.lastrowid
        conn.commit()
        
        try:
            await context.bot.send_message(
                chat_id=FEED_CHAT_ID, 
                text=f"*{handle}* shared:\n\n{text}",
                parse_mode='Markdown',
                reply_markup=build_post_keyboard(post_id)
            )
            await update.message.reply_text("Your post has been shared anonymously! 🚀", reply_markup=get_main_menu())
        except Exception:
            await update.message.reply_text("Failed to broadcast. Ensure bot has channel access.")
            
        user_ui_states.pop(chat_id, None)
        
    elif current_state and current_state.startswith("replying_"):
        post_id = int(current_state.split("_")[1])
        cursor.execute('SELECT users.handle, posts.author_chat_id FROM posts JOIN users ON posts.author_chat_id = users.chat_id WHERE posts.post_id = ?', (post_id,))
        post_data = cursor.fetchone()
        
        if post_data:
            sender_handle = get_or_create_user(chat_id)
            target_chat_id = post_data['author_chat_id']
            
            await context.bot.send_message(
                chat_id=target_chat_id, 
                text=f"📩 You have a new anonymous reply from {sender_handle}:\n\n{text}"
            )
            await update.message.reply_text("Your reply has been delivered safely.", reply_markup=get_main_menu())
        else:
            await update.message.reply_text("Sorry, that post no longer exists.")
            
        user_ui_states.pop(chat_id, None)
        
    else:
        await update.message.reply_text("Please use the menu below to interact.", reply_markup=get_main_menu())

    conn.close()

# --- Admin Commands ---
async def approve_helper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS: return
    try:
        target_handle = context.args[0].upper()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM users WHERE handle = ?', (target_handle,))
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE helpers SET status = 'approved' WHERE chat_id = ?", (row['chat_id'],))
            if cursor.rowcount == 0: cursor.execute("INSERT INTO helpers (chat_id, status) VALUES (?, 'approved')", (row['chat_id'],))
            conn.commit()
            await update.message.reply_text(f"{target_handle} approved.")
            await context.bot.send_message(chat_id=row['chat_id'], text="🎉 You are now an approved helper!")
        conn.close()
    except IndexError: pass

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS: return
    try:
        target_handle = context.args[0].upper()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM users WHERE handle = ?', (target_handle,))
        row = cursor.fetchone()
        if row:
            target_id = row['chat_id']
            cursor.execute("INSERT OR IGNORE INTO banned_users (chat_id) VALUES (?)", (target_id,))
            cursor.execute('SELECT peer_id FROM active_sessions WHERE chat_id = ?', (target_id,))
            session = cursor.fetchone()
            if session:
                cursor.execute('DELETE FROM active_sessions WHERE chat_id IN (?, ?)', (target_id, session['peer_id']))
                await context.bot.send_message(chat_id=session['peer_id'], text="Session terminated by admin.")
            conn.commit()
            await update.message.reply_text(f"{target_handle} banned.")
        conn.close()
    except IndexError: pass

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_chat.id
        admin_ids_str = [str(aid) for aid in ADMIN_IDS]
        
        if str(user_id) not in admin_ids_str:
            await update.message.reply_text(f"🔒 Access Denied. Your ID is {user_id}")
            return
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM posts')
        total_posts = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM active_sessions')
        active_sessions = cursor.fetchone()[0] // 2 
        cursor.execute("SELECT COUNT(*) FROM helpers WHERE status = 'pending'")
        pending_helpers = cursor.fetchone()[0]
        
        conn.close()
        
        stats_message = (
            "📊 *Resus Lite Admin Stats* 📊\n\n"
            f"👥 Total Users: {total_users}\n"
            f"📝 Total Posts: {total_posts}\n"
            f"🫂 Active 1:1 Sessions: {active_sessions}\n"
            f"🛡️ Pending Helper Apps: {pending_helpers}"
        )
        await update.message.reply_text(stats_message, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ CRASH DETECTED: {e}")

# --- Main Application ---
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve_helper))
    app.add_handler(CommandHandler("ban", ban_user))
    
    # 🚨 THE MISSING HANDLER IS NOW SECURELY IN PLACE 🚨
    app.add_handler(CommandHandler("stats", admin_stats))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    logger.info("Resus Lite Bot is live with persistent storage and admin dashboard!")
    app.run_polling()

if __name__ == '__main__':
    main()
