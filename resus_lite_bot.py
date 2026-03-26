import logging
import sqlite3
import os
import re
import asyncio
import random            
import time as std_time  
from datetime import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# --- Configuration ---
BOT_TOKEN = "8714395067:AAHs5xclFvkSc5wf_a47Q-6m-O7I2SvWq64" 
ADMIN_IDS = [6102322573] 
FEED_CHAT_ID = "-1003645637131" 
CHANNEL_LINK = "https://t.me/+8XX156VITLplNzQ0" # <--- YOUR CHANNEL LINK
BOT_USERNAME = "ResusLite_Bot" # <--- YOUR BOT'S ACTUAL USERNAME

# --- Anti-Spam Configuration ---
POST_COOLDOWN_SECONDS = 180  
MAX_BURST_MESSAGES = 3       
user_post_history = {}       
CRISIS_MESSAGE = (
    "🛑 *Message Paused*\n\n"
    "I'm keeping this message off the public feed because it sounds like you are carrying an incredibly heavy burden right now, and peer-support isn't enough.\n\n"
    "Your nervous system is overwhelmed, but you do not have to handle this alone. The admin team has been notified.\n\n"
    "**Immediate Support Options:**\n"
    "📞 **Mentally Aware Nigeria (MANI):** 0809 111 6264\n"
    "🏥 **FUOYE Health Centre:** (Go directly to the campus clinic for immediate stabilization)\n"
    "🧘‍♀️ Tap **Quick Relief** on your menu to help slow your heart rate down right now."
)

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

user_ui_states = {} 

# --- Helper function for Markdown ---
def escape_markdown_v2(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)

# --- Database Setup & Persistence ---
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
        
        adjectives = [
            "Calm", "Brave", "Quiet", "Gentle", "Kind", "Warm", "Bright", "Serene", 
            "Steady", "Hopeful", "Peaceful", "Safe", "Mindful", "Grounded", "Patient", 
            "Resilient", "Radiant", "Clear", "Stellar", "Noble", "True", "Pure", 
            "Vivid", "Luminous", "Strong", "Loyal", "Wise", "Earnest", "Tranquil",
            "Mellow", "Lucid", "Sound", "Still", "Adept", "Vigilant", "Humble",
            "Fierce", "Tender", "Sincere", "Aura", "Zen", "Bold", "Candid", 
            "Valid", "Subtle", "Keen", "Prime", "Solid", "Brisk", "Fluid"
        ]
        
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
    text_lower = text.lower()
    
    # Crisis patterns
    crisis_pattern = r"(suicide|k[i!1]ll\s*myself|end\s*it\s*all|want\s*to\s*d[i!1]e|sleep\s*forever|no\s*point\s*in\s*living)"
    somatic_pattern = r"(can\'?t\s*breathe|heart\s*is\s*(exploding|racing)|chest\s*is\s*crushing|completely\s*numb|make\s*it\s*stop|losing\s*my\s*mind)"
    apathy_pattern = r"(giving\s*up|done\s*trying|nothing\s*matters\s*anymore|too\s*exhausted\s*to\s*(live|try))"
    
    # 🚨 Anti-Spam & Anti-Begging Patterns
    scam_pattern = r"(\b\d{10}\b|urgent\s*2k|send\s*money|send\s*funds|account\s*number)"
    link_pattern = r"(http[s]?://|www\.|t\.me/|\.com|\.ng)"

    if re.search(crisis_pattern, text_lower): return True
    if re.search(somatic_pattern, text_lower): return True
    if re.search(apathy_pattern, text_lower): return True
    if re.search(scam_pattern, text_lower): return True
    if re.search(link_pattern, text_lower): return True
    return False

# 🛑 NEW: The Gatekeeper Logic
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id in ADMIN_IDS:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=FEED_CHAT_ID, user_id=user_id)
        if member.status in ['left', 'kicked']:
            return False
        return True
    except Exception as e:
        logger.error(f"Subscription check failed: {e}")
        return False

async def notify_admins_of_crisis(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE):
    handle = get_or_create_user(chat_id)
    alert_msg = (
        f"🚨 **CRISIS/SPAM ALERT INITIATED** 🚨\n\n"
        f"**User Handle:** `{handle}`\n"
        f"**Intercepted Message:**\n_{text}_\n\n"
        f"This message was blocked from the public feed or 1:1 session."
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=alert_msg, parse_mode='Markdown')
        except Exception:
            pass

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
            InlineKeyboardButton("🫂 Support (1:1)", url=f"https://t.me/{BOT_USERNAME}?start=support_{post_id}")
        ],
        [InlineKeyboardButton("💬 Reply Anonymously", url=f"https://t.me/{BOT_USERNAME}?start=reply_{post_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_main_menu(chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM helpers WHERE chat_id = ?', (chat_id,))
    helper = cursor.fetchone()
    conn.close()

    keyboard = [
        [KeyboardButton("📝 New Post"), KeyboardButton("🛑 End Session")],
        [KeyboardButton("🧘‍♀️ Quick Relief")]
    ]

    if helper and helper['status'] in ['approved', 'offline']:
        keyboard[1].append(KeyboardButton("🔔 Toggle Duty"))
    else:
        keyboard[1].append(KeyboardButton("🤝 Apply as Helper"))

    keyboard.append([KeyboardButton("👤 My Handle")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_banned(chat_id): return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT handle FROM users WHERE chat_id = ?', (chat_id,))
    is_new_user = cursor.fetchone() is None
    
    # 🛑 THE VIP BOUNCER (Locks the bot for new users without the link)
    if is_new_user:
        # Check if they have the secret 'medbeta' password in the link
        if not context.args or context.args[0] != "medbeta":
            await update.message.reply_text(
                "🔒 **Private Beta**\n\nResus Lite is currently in a closed beta exclusively for Medical Students. You need a VIP invite link to enter.",
                parse_mode='Markdown'
            )
            conn.close()
            return
            
    handle = get_or_create_user(chat_id)
    user_ui_states.pop(chat_id, None)
    
    if context.args:
        payload = context.args[0]

        # 🛑 GATEKEEPER FOR INLINE BUTTONS
        is_subbed = await check_subscription(chat_id, context)
        if not is_subbed:
            await update.message.reply_text(
                f"🛑 **Access Denied.**\n\nYou must join the public community before interacting with posts.\n\n👉 [Click Here to Join]({CHANNEL_LINK})", 
                parse_mode='Markdown'
            )
            conn.close()
            return
        
        if payload.startswith("reply_"):
            post_id = int(payload.split("_")[1])
            user_ui_states[chat_id] = f"replying_{post_id}"
            await update.message.reply_text(
                "💬 Type your reply below. It will be sent anonymously to the author.\n*(Or type 'cancel' to abort)*",
                reply_markup=get_main_menu(chat_id)
            )
            conn.close()
            return
            
        elif payload.startswith("support_"):
            cursor.execute('SELECT status FROM helpers WHERE chat_id = ?', (chat_id,))
            helper = cursor.fetchone()
            
            if not helper or helper['status'] != 'approved':
                await update.message.reply_text("⚠️ Only approved helpers can start 1:1 sessions. Tap '🤝 Apply as Helper' first on your menu.")
                conn.close()
                return

            post_id = int(payload.split("_")[1])
            cursor.execute('SELECT author_chat_id FROM posts WHERE post_id = ?', (post_id,))
            row = cursor.fetchone()
            
            if not row:
                await update.message.reply_text("❌ Could not find the original post.")
                conn.close()
                return
                
            op_chat_id = row['author_chat_id']
            
            if op_chat_id == chat_id:
                await update.message.reply_text("You cannot support your own post.")
                conn.close()
                return
                
            cursor.execute('SELECT chat_id FROM active_sessions WHERE chat_id IN (?, ?)', (chat_id, op_chat_id))
            if cursor.fetchone():
                await update.message.reply_text("One of you is already in an active session.")
                conn.close()
                return

            cursor.execute('INSERT INTO active_sessions (chat_id, peer_id) VALUES (?, ?)', (chat_id, op_chat_id))
            cursor.execute('INSERT INTO active_sessions (chat_id, peer_id) VALUES (?, ?)', (op_chat_id, chat_id))
            conn.commit()
            conn.close()
            
            user_ui_states.pop(chat_id, None)
            user_ui_states.pop(op_chat_id, None)
            
            await update.message.reply_text("🟢 1:1 Session started! You are now connected to the author. Tap 🛑 End Session when done.", reply_markup=get_main_menu(chat_id))
            await context.bot.send_message(chat_id=op_chat_id, text="🟢 A vetted helper has connected with you regarding your recent post. Tap 🛑 End Session when done.", reply_markup=get_main_menu(op_chat_id))
            return

    conn.close()

    if is_new_user:
        await update.message.reply_text(
            f"Welcome to Resus Lite! 🌿\n\n"
            f"Your assigned anonymous handle is: `{handle}`\n\n"
            f"📢 **Join the Community:** [Click here to enter the Public Feed]({CHANNEL_LINK}) to read and support other students.\n\n"
            f"⚠️ *Disclaimer: This is a peer-to-peer support space, not a substitute for clinical therapy or emergency medical care. If you are in immediate physical danger, please contact local emergency services.*\n\n"
            f"Use the menu below to navigate, or type /help to learn about your privacy.",
            reply_markup=get_main_menu(chat_id),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"Welcome back to Resus Lite! 🌿\n\n"
            f"📢 **[Go to the Public Feed]({CHANNEL_LINK})**\n\n"
            f"Use the menu below to navigate.",
            reply_markup=get_main_menu(chat_id),
            parse_mode='Markdown'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛡️ *Your Privacy & Safety on Resus Lite*\n\n"
        "**1. Total Anonymity:** Your real Telegram name and phone number are hidden. Other students only ever see your Friendly Handle (like `Calm-River-02`).\n"
        "**2. 1:1 Sessions:** If you tap 'Support' on a post, you enter a private, 2-way anonymous chat. Tap '🛑 End Session' to disconnect instantly.\n"
        "**3. Your Data:** You are in control. If you ever want to erase your account, posts, and history, just type `/deletemydata`.\n\n"
        "Remember, you don't have to carry it all alone. 🌿"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def deletemydata_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT peer_id FROM active_sessions WHERE chat_id = ?', (chat_id,))
    session = cursor.fetchone()
    if session:
        try:
            await context.bot.send_message(chat_id=session['peer_id'], text="The other user has left the platform. Session ended.")
        except Exception:
            pass
            
    cursor.execute('DELETE FROM users WHERE chat_id = ?', (chat_id,))
    cursor.execute('DELETE FROM helpers WHERE chat_id = ?', (chat_id,))
    cursor.execute('DELETE FROM posts WHERE author_chat_id = ?', (chat_id,))
    cursor.execute('DELETE FROM reactions WHERE chat_id = ?', (chat_id,))
    cursor.execute('DELETE FROM active_sessions WHERE chat_id = ? OR peer_id = ?', (chat_id, chat_id))
    
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Success. Your account, handles, posts, and all associated data have been completely erased from the database.", reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if is_banned(user_id):
        await query.answer("Your account is restricted.", show_alert=True)
        return

    data = query.data

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
            await context.bot.send_message(chat_id=user_id, text=f"❌ Oops, the visualizer hit a snag: {e}")

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

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    
    if is_banned(chat_id): return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    if text == "📝 New Post":
        # 🛑 GATEKEEPER FOR NEW POSTS
        is_subbed = await check_subscription(chat_id, context)
        if not is_subbed:
            await update.message.reply_text(
                f"🛑 **Access Denied.**\n\nYou must join the public community to understand how Resus Lite works before posting.\n\n👉 [Click Here to Join]({CHANNEL_LINK})", 
                parse_mode='Markdown'
            )
            conn.close()
            return

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
            await update.message.reply_text("Session ended.", reply_markup=get_main_menu(chat_id))
            await context.bot.send_message(chat_id=peer_id, text="The other user has ended the session.", reply_markup=get_main_menu(peer_id))
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
        
    elif text == "🤝 Apply as Helper":
        cursor.execute('SELECT status FROM helpers WHERE chat_id = ?', (chat_id,))
        row = cursor.fetchone()
        
        if row:
            if row['status'] == 'approved' or row['status'] == 'offline':
                await update.message.reply_text(
                    "You are already an approved helper! Your menu has been refreshed.", 
                    reply_markup=get_main_menu(chat_id) 
                )
            else:
                await update.message.reply_text(
                    "Your application is still pending admin review.",
                    reply_markup=get_main_menu(chat_id)
                )
        else:
            cursor.execute("INSERT INTO helpers (chat_id, status) VALUES (?, 'pending')", (chat_id,))
            conn.commit()
            await update.message.reply_text(
                "Your application has been submitted! An admin will review it soon.",
                reply_markup=get_main_menu(chat_id)
            )
            for admin in ADMIN_IDS:
                try:
                    handle = get_or_create_user(chat_id)
                    await context.bot.send_message(chat_id=admin, text=f"🔔 New helper application from {handle}. Use /approve {handle}")
                except Exception:
                    pass
        conn.close()
        return

    elif text == "🔔 Toggle Duty":
        cursor.execute('SELECT status FROM helpers WHERE chat_id = ?', (chat_id,))
        helper = cursor.fetchone()
        
        if not helper or helper['status'] not in ['approved', 'offline']:
            await update.message.reply_text("❌ You are not registered as an active helper.")
            conn.close()
            return
            
        if helper['status'] == 'approved':
            cursor.execute("UPDATE helpers SET status = 'offline' WHERE chat_id = ?", (chat_id,))
            await update.message.reply_text("🔕 **Status: OFFLINE**\n\nYou are now off duty. Rest up!", parse_mode='Markdown')
        elif helper['status'] == 'offline':
            cursor.execute("UPDATE helpers SET status = 'approved' WHERE chat_id = ?", (chat_id,))
            await update.message.reply_text("🔔 **Status: ACTIVE**\n\nYou are now back on duty and can accept 1:1 sessions.", parse_mode='Markdown')
            
        conn.commit()
        conn.close()
        return
        
    elif text == "👤 My Handle":
        handle = get_or_create_user(chat_id)
        await update.message.reply_text(f"Your anonymous handle is: {handle}")
        conn.close()
        return

    if text.lower() == 'cancel':
        user_ui_states.pop(chat_id, None)
        await update.message.reply_text("Action cancelled.", reply_markup=get_main_menu(chat_id))
        conn.close()
        return

    cursor.execute('SELECT peer_id FROM active_sessions WHERE chat_id = ?', (chat_id,))
    session = cursor.fetchone()
    if session:
        peer_id = session['peer_id']
        conn.close()
        if check_moderation(text):
            await update.message.reply_text(CRISIS_MESSAGE)
            await notify_admins_of_crisis(chat_id, text, context) 
            return
        await context.bot.send_message(chat_id=peer_id, text=f"💬 Anonymous message: {text}")
        return

    # --- THE MASTER GATEKEEPER ---
    current_state = user_ui_states.get(chat_id)

    if current_state == "posting" or (current_state and current_state.startswith("replying_")):
        
        current_time = std_time.time()
        history = user_post_history.get(chat_id, [])
        history = [ts for ts in history if current_time - ts < POST_COOLDOWN_SECONDS]
        
        if len(history) >= MAX_BURST_MESSAGES:
            await update.message.reply_text(
                "⏳ **Cooldown Active:** To keep the platform safe from spam, please wait a few minutes before sending more messages.", 
                parse_mode='Markdown'
            )
            return

        if check_moderation(text):
            await update.message.reply_text(CRISIS_MESSAGE, parse_mode='Markdown')
            await notify_admins_of_crisis(chat_id, text, context)
            return
            
        history.append(current_time)
        user_post_history[chat_id] = history
        
        if current_state == "posting":
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('INSERT INTO posts (author_chat_id, content) VALUES (?, ?)', (chat_id, text))
            post_id = cursor.lastrowid
            conn.commit()
            conn.close()

            user_ui_states.pop(chat_id, None)

            handle = get_or_create_user(chat_id)
            safe_text = escape_markdown_v2(text)

            safe_handle = escape_markdown_v2(handle)
            feed_message = f"👤 *{safe_handle}*\n\n{safe_text}"

            await context.bot.send_message(
                chat_id=FEED_CHAT_ID,
                text=feed_message,
                parse_mode='MarkdownV2',
                reply_markup=build_post_keyboard(post_id)
            )
            await update.message.reply_text(
                f"✅ Your post has been published anonymously to the [Public Feed]({CHANNEL_LINK}).",
                parse_mode='Markdown'
            )

        elif current_state.startswith("replying_"):
            post_id = int(current_state.split("_")[1])

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT author_chat_id FROM posts WHERE post_id = ?', (post_id,))
            row = cursor.fetchone()
            conn.close()

            user_ui_states.pop(chat_id, None)

            if row:
                op_chat_id = row['author_chat_id']
                handle = get_or_create_user(chat_id)
                safe_text = escape_markdown_v2(text)

                safe_handle = escape_markdown_v2(handle)

                await context.bot.send_message(
                    chat_id=op_chat_id,
                    text=f"💬 *New Reply on your post from {safe_handle}:*\n\n_{safe_text}_",
                    parse_mode='MarkdownV2'
                )
                await update.message.reply_text("✅ Your reply was sent securely to the author.")
            else:
                await update.message.reply_text("❌ Could not find the original post.")

# --- Admin Commands ---
async def approve_helper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS: return
    try:
        target_handle = context.args[0]
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM users WHERE handle COLLATE NOCASE = ?', (target_handle,))
        row = cursor.fetchone()
        
        if row:
            cursor.execute("UPDATE helpers SET status = 'approved' WHERE chat_id = ?", (row['chat_id'],))
            if cursor.rowcount == 0: cursor.execute("INSERT INTO helpers (chat_id, status) VALUES (?, 'approved')", (row['chat_id'],))
            conn.commit()
            
            await update.message.reply_text(f"✅ {target_handle} approved.")
            
            welcome_msg = (
                "🎉 **You are officially an Approved Helper!**\n\n"
                "Your menu has updated. Use '🔔 Toggle Duty' to clock in and out.\n\n"
                "**Your 3 Golden Rules:**\n"
                "1️⃣ You are a peer, not a therapist. Just listen and validate.\n"
                "2️⃣ Use 'Reply Anonymously' for quick support, and 'Support (1:1)' for deep conversations.\n"
                "3️⃣ If a user is hostile, tap 🛑 End Session immediately.\n\n"
                "📘 **Required Reading:** Before taking your first session, please read the full [Helper Playbook Here](https://telegra.ph/Resus-Lite-Helper-Playbook-03-26) to understand the safety protocols."
            )
            
            await context.bot.send_message(
                chat_id=row['chat_id'], 
                text=welcome_msg,
                reply_markup=get_main_menu(row['chat_id']),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(f"❌ Could not find handle: {target_handle}")
        conn.close()
    except IndexError: 
        await update.message.reply_text("⚠️ Format: /approve [Handle]")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS: return
    try:
        target_handle = context.args[0]
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM users WHERE handle COLLATE NOCASE = ?', (target_handle,))
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
            await update.message.reply_text(f"✅ {target_handle} banned.")
        else:
            await update.message.reply_text(f"❌ Could not find handle: {target_handle}")
        conn.close()
    except IndexError: 
        await update.message.reply_text("⚠️ Format: /ban [Handle]")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_chat.id
        if user_id not in ADMIN_IDS:
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

async def reachout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.message.from_user.id
    if admin_id not in ADMIN_IDS: return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("⚠️ **Format:** `/reachout [Handle]`", parse_mode='Markdown')
        return

    target_handle = args[0]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id FROM users WHERE handle COLLATE NOCASE = ?', (target_handle,))
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text(f"❌ Could not find a user with the handle: {target_handle}")
        conn.close()
        return

    target_chat_id = row['chat_id']
    if target_chat_id == admin_id:
        await update.message.reply_text("❌ You cannot start a session with yourself.")
        conn.close()
        return

    cursor.execute('DELETE FROM active_sessions WHERE chat_id IN (?, ?) OR peer_id IN (?, ?)', 
                   (admin_id, target_chat_id, admin_id, target_chat_id))
    cursor.execute('INSERT INTO active_sessions (chat_id, peer_id) VALUES (?, ?)', (admin_id, target_chat_id))
    cursor.execute('INSERT INTO active_sessions (chat_id, peer_id) VALUES (?, ?)', (target_chat_id, admin_id))
    
    conn.commit()
    conn.close()

    try:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text="🛡️ **Admin Support Outreach** 🛡️\n\nA campus admin has opened a secure, private chat with you to offer support. Your identity remains completely anonymous.\n\nYou can reply directly to this message to chat with them. Tap 🛑 End Session when you are safe.",
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to reach the user. Error: {e}")
        return

    await update.message.reply_text(
        f"🟢 **Connection Secured.**\n\nYou are now in a live, 1:1 anonymous chat with `{target_handle}`. Anything you type now will go directly to them.\n\nTap 🛑 End Session when the crisis is resolved.",
        parse_mode='Markdown'
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_chat.id
    if admin_id not in ADMIN_IDS: return
    
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("⚠️ **Format:** `/broadcast [Your message here]`", parse_mode='Markdown')
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id FROM users')
    users = cursor.fetchall()
    conn.close()
    
    success_count = 0
    await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user['chat_id'], 
                text=f"📢 **Platform Update:**\n\n{message}", 
                parse_mode='Markdown'
            )
            success_count += 1
            await asyncio.sleep(0.05) 
        except Exception:
            pass 
            
    await update.message.reply_text(f"✅ Broadcast complete. Successfully delivered to {success_count} users.")

async def resetme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in ADMIN_IDS: return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users WHERE chat_id = ?', (user_id,))
    conn.commit()
    conn.close()
    
    await update.message.reply_text("✅ Your old account has been securely wiped. Tap /start to generate your new Friendly Anonymous handle.")

async def wipefeed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    if user_id not in ADMIN_IDS: return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM posts')
    cursor.execute('DELETE FROM reactions')
    
    try:
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='posts'")
    except Exception:
        pass 
        
    conn.commit()
    conn.close()
    
    await update.message.reply_text("🧼 **Clean Slate!** All posts and reactions have been permanently wiped from the database.", parse_mode='Markdown')

# --- Main Application ---
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve_helper))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("reachout", reachout_command))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("resetme", resetme_command))
    app.add_handler(CommandHandler("wipefeed", wipefeed_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("deletemydata", deletemydata_command))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    logger.info("Resus Lite Bot is live with persistent storage and admin dashboard!")
    app.run_polling()

if __name__ == '__main__':
    main()
