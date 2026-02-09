#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarketLink Pro - single-file final

Features:
- Persistent SQLite DB at data/shop.db (aiosqlite)
- Shop creation with trial period
- Products (add/list/edit/delete)
- Links (add/list/edit)
- Orders: customers must send a photo of desired item(s)
- Payments: supports storing payment proof photos (admin approval)
- Admin panel to approve subscription/payments/orders
- Background cleanup task: deletes old photo files and clears references
- Export orders to Excel (requires pandas + openpyxl)

Folder layout expected:
bot-root/
  ├─ marketlink_pro.py   (this file)
  ├─ data/               (created automatically)
  │   └─ shop.db         (auto-created)
  └─ photos/             (saved payment & order photos)

Usage:
- populate .env with BOT_TOKEN and ADMIN_ID
- pip install -r requirements.txt
- python marketlink_pro.py
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import aiosqlite
from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)

# ---------------- CONFIG ----------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except Exception:
    ADMIN_ID = 0

DATA_DIR = "data"
DB_PATH = os.path.join(DATA_DIR, "shop.db")
PHOTOS_DIR = "photos"
PHOTO_RETENTION_DAYS = 30  # background cleanup: remove photos older than this

TRIAL_DAYS = 3
SUBSCRIPTION_EXTEND_DAYS = 30
SUBSCRIPTION_FEE = 5000  # MMK (informational)

# Conversation states
(
    ORDER_NAME,
    ORDER_PHONE,
    ORDER_ADDRESS,
    ORDER_PHOTO,
) = range(4)
(EDIT_LINK_ID, EDIT_LINK_TITLE, EDIT_LINK_URL) = range(4, 7)
(EDIT_PROD_ID, EDIT_PROD_NAME, EDIT_PROD_PRICE) = range(7, 10)
(PAYMENT_WAIT,) = range(10, 11)

# Ensure folders exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)

# ---------------- LOG ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("marketlink_pro")

# ---------------- DB HELPERS (async) ----------------
async def init_db() -> None:
    """Create DB and required tables if missing."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS shops (
            owner_id INTEGER PRIMARY KEY,
            shop_name TEXT,
            expire_date TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            name TEXT,
            price INTEGER,
            FOREIGN KEY(owner_id) REFERENCES shops(owner_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            title TEXT,
            url TEXT,
            FOREIGN KEY(owner_id) REFERENCES shops(owner_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            user_id INTEGER,
            name TEXT,
            phone TEXT,
            address TEXT,
            items TEXT,
            total INTEGER,
            photo_path TEXT,
            status TEXT,
            created_at TEXT,
            FOREIGN KEY(shop_id) REFERENCES shops(owner_id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER,
            kind TEXT,
            ref_id INTEGER,
            photo_path TEXT,
            status TEXT,
            created_at TEXT
        );
        """
        )
        await db.commit()


async def db_get_shop(owner_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT owner_id, shop_name, expire_date, created_at FROM shops WHERE owner_id = ?", (owner_id,))
        row = await cur.fetchone()
        await cur.close()
        return row


async def db_set_shop(owner_id: int, shop_name: str, expire_date: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO shops(owner_id, shop_name, expire_date, created_at) VALUES(?,?,?,?)",
            (owner_id, shop_name, expire_date, datetime.utcnow().strftime("%Y-%m-%d")),
        )
        await db.commit()


async def db_extend_shop(owner_id: int, days: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT expire_date FROM shops WHERE owner_id = ?", (owner_id,))
        row = await cur.fetchone()
        await cur.close()
        if row and row[0]:
            try:
                cur_exp = datetime.strptime(row[0], "%Y-%m-%d")
            except Exception:
                cur_exp = datetime.utcnow()
        else:
            cur_exp = datetime.utcnow()
        new_exp = (cur_exp + timedelta(days=days)).strftime("%Y-%m-%d")
        await db.execute("UPDATE shops SET expire_date = ? WHERE owner_id = ?", (new_exp, owner_id))
        await db.commit()
        return new_exp


async def db_add_product(owner_id: int, name: str, price: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO products(owner_id, name, price) VALUES(?,?,?)", (owner_id, name, price))
        await db.commit()


async def db_list_products(owner_id: int) -> List[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, name, price FROM products WHERE owner_id = ?", (owner_id,))
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def db_get_product(pid: int, owner_id: Optional[int] = None) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if owner_id is not None:
            cur = await db.execute("SELECT id, name, price FROM products WHERE id = ? AND owner_id = ?", (pid, owner_id))
        else:
            cur = await db.execute("SELECT id, name, price FROM products WHERE id = ?", (pid,))
        row = await cur.fetchone()
        await cur.close()
        return row


async def db_update_product(pid: int, owner_id: int, name: str, price: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE products SET name = ?, price = ? WHERE id = ? AND owner_id = ?", (name, price, pid, owner_id))
        await db.commit()


async def db_delete_product(pid: int, owner_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM products WHERE id = ? AND owner_id = ?", (pid, owner_id))
        await db.commit()


async def db_add_link(owner_id: int, title: str, url: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO links(owner_id, title, url) VALUES(?,?,?)", (owner_id, title, url))
        await db.commit()


async def db_list_links(owner_id: int) -> List[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, title, url FROM links WHERE owner_id = ?", (owner_id,))
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def db_get_link(lid: int, owner_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, title, url FROM links WHERE id = ? AND owner_id = ?", (lid, owner_id))
        row = await cur.fetchone()
        await cur.close()
        return row


async def db_update_link(lid: int, owner_id: int, title: str, url: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE links SET title = ?, url = ? WHERE id = ? AND owner_id = ?", (title, url, lid, owner_id))
        await db.commit()


async def db_create_order(shop_id: int, user_id: int, name: str, phone: str, address: str, items: str, total: int, photo_path: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders(shop_id, user_id, name, phone, address, items, total, photo_path, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (shop_id, user_id, name, phone, address, items, total, photo_path, "Pending", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
        )
        await db.commit()
        oid = cur.lastrowid
        await cur.close()
        return oid


async def db_get_order(oid: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM orders WHERE id = ?", (oid,))
        row = await cur.fetchone()
        await cur.close()
        return row


async def db_update_order_status(oid: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, oid))
        await db.commit()


async def db_insert_payment(uid: int, kind: str, ref_id: Optional[int], photo_path: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO payments(uid, kind, ref_id, photo_path, status, created_at) VALUES(?,?,?,?,?,?)",
            (uid, kind, ref_id, photo_path, "pending", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
        )
        await db.commit()
        pid = cur.lastrowid
        await cur.close()
        return pid


async def db_get_pending_payments() -> List[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, uid, kind, ref_id, photo_path, status, created_at FROM payments WHERE status = 'pending' ORDER BY id ASC")
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def db_update_payment_status(pid: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE payments SET status = ? WHERE id = ?", (status, pid))
        await db.commit()


async def db_list_orders_by_shop(owner_id: int) -> List[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, user_id, name, phone, address, items, total, status, created_at FROM orders WHERE shop_id = ? ORDER BY id DESC", (owner_id,))
        rows = await cur.fetchall()
        await cur.close()
        return rows


# ---------------- HELPERS ----------------
def utcnow_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


async def is_shop_active(owner_id: int) -> bool:
    if owner_id == ADMIN_ID:
        return True
    shop = await db_get_shop(owner_id)
    if not shop:
        return False
    exp = shop["expire_date"]
    if not exp:
        return False
    try:
        return datetime.utcnow().date() <= datetime.strptime(exp, "%Y-%m-%d").date()
    except Exception:
        return False


# ---------------- BOT HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args or []
    # deep link example: /start <shop_id>
    if args:
        try:
            shop_id = int(args[0])
        except Exception:
            await update.message.reply_text("Invalid shop link.")
            return
        if not await is_shop_active(shop_id):
            await update.message.reply_text("❌ ဆိုင်သည် သက်တမ်းကုန်ဆုံးနေပါသည်။")
            return
        context.user_data["current_shop_id"] = shop_id
        shop = await db_get_shop(shop_id)
        if shop:
            await update.message.reply_text(f"🏪 **{shop['shop_name']}** မှ ကြိုဆိုပါသည်။\nအမှာစာရန် /order သို့ဝင်ပါ။", reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("Shop not found.")
        return

    # admin panel
    if uid == ADMIN_ID:
        kb = [["📊 Platform Stats", "📥 Pending Payments"], ["🏬 All Shops", "📤 Broadcast"]]
        await update.message.reply_text("👑 Admin Panel", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return

    # owner panel
    shop = await db_get_shop(uid)
    if shop:
        if not await is_shop_active(uid):
            await update.message.reply_text("❌ Your shop subscription has expired. Please renew with /pay_subscribe.")
            return
        kb = [["➕ Add Product", "🛒 My Orders"], ["🔗 My Link", "💳 Subscription"]]
        await update.message.reply_text(f"🏪 Owner Panel: {shop['shop_name']}", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return

    # new user
    kb = [["📝 Create Shop (/setup_shop MyShopName)", "ℹ️ Help"]]
    await update.message.reply_text("Welcome to MarketLink Pro!\nTo create a shop: /setup_shop <ShopName>", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))


# ----- Shop setup -----
async def setup_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = " ".join(context.args or [])
    if not name:
        await update.message.reply_text("Usage: /setup_shop <Shop Name>")
        return
    exp = (datetime.utcnow() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
    await db_set_shop(uid, name, exp)
    await update.message.reply_text(f"✅ Shop created: {name}\nTrial until {exp}\nOpen your panel with /start")


# ----- Product commands -----
async def cmd_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    shop = await db_get_shop(uid)
    if not shop:
        await update.message.reply_text("You are not a shop owner. Create shop with /setup_shop")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /add_product <name> <price>\nExample: /add_product \"Red Scarf\" 15000")
        return
    try:
        price = int(context.args[-1])
    except Exception:
        await update.message.reply_text("Price must be a number.")
        return
    name = " ".join(context.args[:-1])
    await db_add_product(uid, name, price)
    await update.message.reply_text("✅ Product added.")


async def cmd_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = await db_list_products(uid)
    if not rows:
        await update.message.reply_text("No products yet. Add with /add_product")
        return
    text = "📦 Your Products:\n\n"
    for r in rows:
        text += f"ID:{r['id']} • {r['name']} • {r['price']} MMK\n"
    await update.message.reply_text(text)


# Edit product conversation
async def edit_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = await db_list_products(uid)
    if not rows:
        await update.message.reply_text("No products to edit.")
        return ConversationHandler.END
    msg = "Send Product ID to edit:\n\n"
    for r in rows:
        msg += f"ID:{r['id']} • {r['name']} • {r['price']} MMK\n"
    await update.message.reply_text(msg)
    return EDIT_PROD_ID


async def edit_product_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pid = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("Send a valid numeric ID.")
        return EDIT_PROD_ID
    uid = update.effective_user.id
    prod = await db_get_product(pid, uid)
    if not prod:
        await update.message.reply_text("Product not found or not yours.")
        return EDIT_PROD_ID
    context.user_data["edit_product_id"] = pid
    await update.message.reply_text(f"Old name: {prod['name']}\nSend new name:")
    return EDIT_PROD_NAME


async def edit_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["edit_product_name"] = update.message.text.strip()
    await update.message.reply_text("Send new price:")
    return EDIT_PROD_PRICE


async def edit_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("Price must be numeric. Send price again.")
        return EDIT_PROD_PRICE
    pid = context.user_data.pop("edit_product_id")
    name = context.user_data.pop("edit_product_name")
    uid = update.effective_user.id
    await db_update_product(pid, uid, name, price)
    await update.message.reply_text("✅ Product updated.")
    return ConversationHandler.END


async def cmd_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /del_product <product_id>")
        return
    pid = int(context.args[0])
    await db_delete_product(pid, uid)
    await update.message.reply_text("✅ Product deleted (if it belonged to you).")


# ----- Links -----
async def cmd_add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /add_link <title> <url>")
        return
    title = context.args[0]
    url = context.args[1]
    await db_add_link(uid, title, url)
    await update.message.reply_text("✅ Link added.")


async def cmd_list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = await db_list_links(uid)
    if not rows:
        await update.message.reply_text("No links.")
        return
    txt = "🔗 Your Links:\n\n"
    for r in rows:
        txt += f"ID:{r['id']} • {r['title']} • {r['url']}\n"
    await update.message.reply_text(txt)


# Edit link conversation
async def edit_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = await db_list_links(uid)
    if not rows:
        await update.message.reply_text("No links to edit.")
        return ConversationHandler.END
    msg = "✏️ Your Links (ID)\n\n"
    for r in rows:
        msg += f"ID:{r['id']} • {r['title']} • {r['url']}\n"
    msg += "\nSend Link ID to edit:"
    await update.message.reply_text(msg)
    return EDIT_LINK_ID


async def edit_link_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lid = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("Send valid numeric ID.")
        return EDIT_LINK_ID
    uid = update.effective_user.id
    link = await db_get_link(lid, uid)
    if not link:
        await update.message.reply_text("Not found / not your link.")
        return EDIT_LINK_ID
    context.user_data["edit_link_id"] = lid
    await update.message.reply_text(f"Old title: {link['title']}\nSend new title:")
    return EDIT_LINK_TITLE


async def edit_link_get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["edit_link_title"] = update.message.text.strip()
    await update.message.reply_text("Send new URL:")
    return EDIT_LINK_URL


async def edit_link_get_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lid = context.user_data.pop("edit_link_id")
    title = context.user_data.pop("edit_link_title")
    url = update.message.text.strip()
    uid = update.effective_user.id
    await db_update_link(lid, uid, title, url)
    await update.message.reply_text("✅ Link updated.")
    return ConversationHandler.END


# ----- Order Flow (customer must send photo) -----
async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ensure user has a current_shop_id (via deep link /start <shop_id> or explicit /shop command)
    if "current_shop_id" not in context.user_data:
        await update.message.reply_text("Please open the shop first using the bot start link: /start <shop_id>\nExample: /start 123456 (or use owner's link).")
        return ConversationHandler.END
    context.user_data["cart"] = []  # optional textual items
    context.user_data["total"] = 0
    await update.message.reply_text("Your name:", reply_markup=ReplyKeyboardRemove())
    return ORDER_NAME


async def order_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_name"] = update.message.text.strip()
    await update.message.reply_text("Phone:")
    return ORDER_PHONE


async def order_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_phone"] = update.message.text.strip()
    await update.message.reply_text("Address:")
    return ORDER_ADDRESS


async def order_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_address"] = update.message.text.strip()
    await update.message.reply_text("Now, send a photo of the item you want (required). You can also type item description with the photo.")
    return ORDER_PHOTO


async def order_photo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # require a photo
    if not update.message.photo:
        await update.message.reply_text("Please send a photo of the item (required).")
        return ORDER_PHOTO

    # save largest photo
    photo_file = await update.message.photo[-1].get_file()
    filename = os.path.join(PHOTOS_DIR, f"order_{update.effective_user.id}_{int(datetime.utcnow().timestamp())}.jpg")
    await photo_file.download_to_drive(filename)

    # optional: the user can include items text in the message caption or previous messages
    items_text = ""
    if update.message.caption:
        items_text = update.message.caption.strip()
    else:
        items_text = ", ".join(context.user_data.get("cart", [])) or ""

    sid = context.user_data["current_shop_id"]
    uid = update.effective_user.id
    name = context.user_data.get("cust_name", "")
    phone = context.user_data.get("cust_phone", "")
    address = context.user_data.get("cust_address", "")

    # total is optional (0 if not provided)
    total = context.user_data.get("total", 0)

    # create order record
    oid = await db_create_order(sid, uid, name, phone, address, items_text, total, filename)

    # insert a payment record placeholder (kind = 'order', photo is the customer's item photo)
    pid = await db_insert_payment(uid, "order", oid, filename)

    # notify shop owner (if shop exists) else admin
    shop = await db_get_shop(sid)
    owner_id = shop["owner_id"] if shop else None

    kb = [
        [
            InlineKeyboardButton("Confirm Order ✅", callback_data=f"order_conf_{oid}_{pid}"),
            InlineKeyboardButton("Reject ❌", callback_data=f"order_rej_{oid}_{pid}"),
        ]
    ]
    target = owner_id or ADMIN_ID
    try:
        with open(filename, "rb") as fh:
            await context.bot.send_photo(chat_id=target, photo=fh, caption=f"New order #{oid}\nFrom: {uid}\nName: {name}\nPhone: {phone}\nTotal: {total} MMK", reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        log.exception("Failed to notify owner/admin about new order")

    await update.message.reply_text("✅ Your order has been submitted and is pending confirmation from the shop. We'll notify you when it's processed.")
    # cleanup conversation state
    for k in ("cart", "total", "cust_name", "cust_phone", "cust_address"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


# ----- Subscription payment (user uploads payment screenshot) -----
async def pay_subscription_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Subscription fee: {SUBSCRIPTION_FEE} MMK per {SUBSCRIPTION_EXTEND_DAYS} days.\nSend a screenshot of payment (WavePay/KBZ/etc.).")
    return PAYMENT_WAIT


async def pay_subscription_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Please send a photo (payment screenshot).")
        return PAYMENT_WAIT
    photo_file = await update.message.photo[-1].get_file()
    filename = os.path.join(PHOTOS_DIR, f"pay_sub_{update.effective_user.id}_{int(datetime.utcnow().timestamp())}.jpg")
    await photo_file.download_to_drive(filename)
    pid = await db_insert_payment(update.effective_user.id, "subscription", None, filename)
    kb = [
        [
            InlineKeyboardButton("Approve ✅", callback_data=f"sub_ok_{pid}_{update.effective_user.id}"),
            InlineKeyboardButton("Reject ❌", callback_data=f"sub_no_{pid}_{update.effective_user.id}"),
        ]
    ]
    try:
        with open(filename, "rb") as fh:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=fh, caption=f"Subscription payment (uid={update.effective_user.id})", reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        log.exception("Failed to notify admin about subscription payment")
    await update.message.reply_text("✅ Payment submitted. Waiting admin approval.")
    return ConversationHandler.END


# ----- Callback handler (admin/owner actions) -----
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    caller = q.from_user.id if q.from_user else None

    try:
        # subscription approve/reject: sub_ok_<pid>_<uid> or sub_no_<pid>_<uid>
        if data.startswith("sub_ok_") or data.startswith("sub_no_"):
            # only admin
            if caller != ADMIN_ID:
                await q.answer("Not authorized", show_alert=True)
                return
            parts = data.split("_")
            action = parts[1]
            pid = int(parts[2])
            uid = int(parts[3])
            if action == "ok":
                new_exp = await db_extend_shop(uid, SUBSCRIPTION_EXTEND_DAYS)
                await db_update_payment_status(pid, "approved")
                try:
                    await context.bot.send_message(uid, f"✅ Subscription approved. New expiry: {new_exp}")
                except Exception:
                    log.exception("notify user subscription approved failed")
                await q.edit_message_caption(caption=f"Subscription processed. Approved -> UID {uid}")
            else:
                await db_update_payment_status(pid, "rejected")
                try:
                    await context.bot.send_message(uid, f"❌ Subscription payment rejected by admin.")
                except Exception:
                    log.exception("notify user subscription rejected failed")
                await q.edit_message_caption(caption=f"Subscription processed. Rejected -> UID {uid}")

        # order confirmation: order_conf_<oid>_<pid> or order_rej_<oid>_<pid>
        elif data.startswith("order_conf_") or data.startswith("order_rej_"):
            parts = data.split("_")
            action = parts[1]
            oid = int(parts[2])
            pid = int(parts[3])
            order = await db_get_order(oid)
            if not order:
                await q.edit_message_text("Order not found.")
                return
            user_id = order["user_id"]
            shop_id = order["shop_id"]
            shop = await db_get_shop(shop_id)
            owner_id = shop["owner_id"] if shop else None
            # only owner or admin can approve
            if caller != ADMIN_ID and caller != owner_id:
                await q.answer("Not authorized", show_alert=True)
                return
            if action == "conf":
                await db_update_order_status(oid, "Confirmed")
                await db_update_payment_status(pid, "approved")
                try:
                    await context.bot.send_message(user_id, f"🔔 Your order #{oid} has been confirmed by the shop.")
                except Exception:
                    log.exception("notify user order confirmed failed")
                await q.edit_message_caption(caption=f"Order #{oid} - Confirmed")
            else:
                await db_update_order_status(oid, "Rejected")
                await db_update_payment_status(pid, "rejected")
                try:
                    await context.bot.send_message(user_id, f"🔔 Your order #{oid} was rejected by the shop. Contact the shop for details.")
                except Exception:
                    log.exception("notify user order rejected failed")
                await q.edit_message_caption(caption=f"Order #{oid} - Rejected")

    except Exception:
        log.exception("admin_callback error")
        try:
            await q.edit_message_text("Processing failed. See logs.")
        except Exception:
            pass


# ----- Admin: list pending payments -----
async def cmd_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Only admin.")
        return
    rows = await db_get_pending_payments()
    if not rows:
        await update.message.reply_text("No pending payments.")
        return
    for p in rows:
        pid = p["id"]
        uid = p["uid"]
        kind = p["kind"]
        ref_id = p["ref_id"]
        path = p["photo_path"]
        status = p["status"]
        created = p["created_at"]
        text = f"PID:{pid} UID:{uid} Kind:{kind} Ref:{ref_id} Status:{status} Created:{created}"
        try:
            if path and os.path.exists(path):
                with open(path, "rb") as fh:
                    kb = []
                    if kind == "subscription":
                        kb = [[InlineKeyboardButton("Approve", callback_data=f"sub_ok_{pid}_{uid}"), InlineKeyboardButton("Reject", callback_data=f"sub_no_{pid}_{uid}")]]
                    elif kind == "order":
                        kb = [[InlineKeyboardButton("Approve Order", callback_data=f"order_conf_{ref_id}_{pid}"), InlineKeyboardButton("Reject Order", callback_data=f"order_rej_{ref_id}_{pid}")]]
                    await context.bot.send_photo(chat_id=ADMIN_ID, photo=fh, caption=text, reply_markup=InlineKeyboardMarkup(kb))
            else:
                await update.message.reply_text(text + "\n(photo missing)")
        except Exception:
            log.exception("cmd_pending_payments send failed")
            await update.message.reply_text(text + "\n(send failed)")


# ----- Export orders to Excel (owner) -----
async def cmd_export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        import pandas as pd
    except Exception:
        await update.message.reply_text("Pandas not installed. Install pandas + openpyxl to export.")
        return
    uid = update.effective_user.id
    rows = await db_list_orders_by_shop(uid)
    if not rows:
        await update.message.reply_text("No orders.")
        return
    df = []
    for r in rows:
        df.append(
            {
                "order_id": r["id"],
                "user_id": r["user_id"],
                "name": r["name"],
                "phone": r["phone"],
                "address": r["address"],
                "items": r["items"],
                "total": r["total"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
        )
    df = __import__("pandas").DataFrame(df)
    path = f"orders_{uid}_{int(datetime.utcnow().timestamp())}.xlsx"
    try:
        df.to_excel(path, index=False)
        with open(path, "rb") as fh:
            await update.message.reply_document(document=fh, filename=os.path.basename(path))
    except Exception:
        log.exception("export orders failed")
        await update.message.reply_text("Failed to export orders.")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


# ----- Utility: show shop link -----
async def cmd_my_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        bot_username = (await context.bot.get_me()).username
    except Exception:
        bot_username = None
    if not bot_username:
        await update.message.reply_text("Bot username not available.")
        return
    await update.message.reply_text(f"https://t.me/{bot_username}?start={uid}")


# ----- Menu message handler ----- (keyboard shortcuts)
async def text_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    uid = update.effective_user.id

    # block expired owner
    shop = await db_get_shop(uid)
    if shop and not await is_shop_active(uid):
        await update.message.reply_text("❌ Your subscription expired. Please renew with /pay_subscribe.")
        return

    # Owner options
    if t == "➕ Add Product" or t == "/add_product":
        await update.message.reply_text("Use /add_product <name> <price> or /list_products to manage.")
        return
    if t == "🛒 My Orders":
        rows = await db_list_orders_by_shop(uid)
        if not rows:
            await update.message.reply_text("No orders.")
            return
        msg = "📦 Your Orders:\n\n"
        for r in rows:
            msg += f"#{r['id']} | {r['name']} | {r['total']} MMK | {r['status']}\n"
        await update.message.reply_text(msg)
        return
    if t == "🔗 My Link":
        await cmd_my_link(update, context)
        return
    if t == "💳 Subscription":
        await update.message.reply_text(f"Subscription is {SUBSCRIPTION_FEE} MMK per {SUBSCRIPTION_EXTEND_DAYS} days.\nUse /pay_subscribe to pay.", reply_markup=ReplyKeyboardRemove())
        return

    # Admin menu
    if uid == ADMIN_ID:
        if t == "📊 Platform Stats":
            async with aiosqlite.connect(DB_PATH) as con:
                cur = await con.execute("SELECT COUNT(*) FROM shops")
                shops = (await cur.fetchone())[0]
                await cur.close()
                cur = await con.execute("SELECT COUNT(*) FROM orders")
                orders = (await cur.fetchone())[0]
                await cur.close()
                cur = await con.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")
                pend = (await cur.fetchone())[0]
                await cur.close()
            await update.message.reply_text(f"Shops:{shops}\nOrders:{orders}\nPending payments:{pend}")
            return
        if t == "📥 Pending Payments":
            await cmd_pending_payments(update, context)
            return
        if t == "🏬 All Shops":
            async with aiosqlite.connect(DB_PATH) as con:
                con.row_factory = aiosqlite.Row
                cur = await con.execute("SELECT owner_id, shop_name, expire_date FROM shops")
                rows = await cur.fetchall()
                await cur.close()
            txt = "All Shops:\n"
            for r in rows:
                txt += f"ID:{r['owner_id']} • {r['shop_name']} • Exp:{r['expire_date']}\n"
            await update.message.reply_text(txt)
            return

    # Fallback
    if t == "ℹ️ Help" or t == "/help":
        await update.message.reply_text("/setup_shop, /add_product, /list_products, /edit_product, /add_link, /edit_link, /order (open shop link first) /pay_subscribe")
        return

    await update.message.reply_text("Command not recognized. Use /help")


# ----- Cancel / fallback -----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------------- Background cleanup ----------------
async def cleanup_old_photos_task(app: Application, interval_hours: int = 24):
    """Background task that periodically removes photo files older than retention days.
    When a file is removed, any order/payment referencing it will have photo_path set to NULL
    to avoid broken references.
    """
    log.info("Photo cleanup task started (retention %d days)", PHOTO_RETENTION_DAYS)
    while True:
        try:
            cutoff = datetime.utcnow() - timedelta(days=PHOTO_RETENTION_DAYS)
            removed = 0
            async with aiosqlite.connect(DB_PATH) as db:
                # find payment photo files older than cutoff
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT id, photo_path FROM payments WHERE photo_path IS NOT NULL")
                payment_rows = await cur.fetchall()
                await cur.close()
                for p in payment_rows:
                    path = p["photo_path"]
                    try:
                        if path and os.path.exists(path):
                            mtime = datetime.utcfromtimestamp(os.path.getmtime(path))
                            if mtime < cutoff:
                                os.remove(path)
                                await db.execute("UPDATE payments SET photo_path = NULL WHERE id = ?", (p["id"],))
                                removed += 1
                    except Exception:
                        log.exception("cleanup payment photo error for %s", path)
                # orders
                cur = await db.execute("SELECT id, photo_path FROM orders WHERE photo_path IS NOT NULL")
                order_rows = await cur.fetchall()
                await cur.close()
                for o in order_rows:
                    path = o["photo_path"]
                    try:
                        if path and os.path.exists(path):
                            mtime = datetime.utcfromtimestamp(os.path.getmtime(path))
                            if mtime < cutoff:
                                os.remove(path)
                                await db.execute("UPDATE orders SET photo_path = NULL WHERE id = ?", (o["id"],))
                                removed += 1
                    except Exception:
                        log.exception("cleanup order photo error for %s", path)
                if removed:
                    await db.commit()
            if removed:
                log.info("Cleanup removed %d old photo files", removed)
        except Exception:
            log.exception("cleanup task failed")
        # sleep
        await asyncio.sleep(interval_hours * 3600)


# ---------------- MAIN ----------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Set it in .env or environment.")
    if ADMIN_ID == 0:
        log.warning("ADMIN_ID is 0 or not set. Set ADMIN_ID in .env for admin functions.")

    # build app
    app = Application.builder().token(BOT_TOKEN).build()

    # register handlers (conversations)
    order_conv = ConversationHandler(
        entry_points=[CommandHandler("order", order_start)],
        states={
            ORDER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_name)],
            ORDER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_phone)],
            ORDER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_address)],
            ORDER_PHOTO: [MessageHandler(filters.PHOTO, order_photo_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    edit_prod_conv = ConversationHandler(
        entry_points=[CommandHandler("edit_product", edit_product_start)],
        states={
            EDIT_PROD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_get_id)],
            EDIT_PROD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_name)],
            EDIT_PROD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    edit_link_conv = ConversationHandler(
        entry_points=[CommandHandler("edit_link", edit_link_start)],
        states={
            EDIT_LINK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_link_get_id)],
            EDIT_LINK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_link_get_title)],
            EDIT_LINK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_link_get_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    pay_conv = ConversationHandler(
        entry_points=[CommandHandler("pay_subscribe", pay_subscription_start)],
        states={PAYMENT_WAIT: [MessageHandler(filters.PHOTO, pay_subscription_receive)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # register command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup_shop", setup_shop))
    app.add_handler(CommandHandler("add_product", cmd_add_product))
    app.add_handler(CommandHandler("list_products", cmd_list_products))
    app.add_handler(CommandHandler("del_product", cmd_delete_product))
    app.add_handler(edit_prod_conv)
    app.add_handler(CommandHandler("add_link", cmd_add_link))
    app.add_handler(CommandHandler("list_links", cmd_list_links))
    app.add_handler(edit_link_conv)
    app.add_handler(order_conv)
    app.add_handler(pay_conv)
    app.add_handler(CommandHandler("pending_payments", cmd_pending_payments))
    app.add_handler(CommandHandler("export_orders", cmd_export_orders))
    app.add_handler(CommandHandler("my_link", cmd_my_link))
    app.add_handler(CallbackQueryHandler(admin_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_menu_handler))
    app.add_handler(CommandHandler("cancel", cancel))

    # run DB init and start cleanup background task
    async def _startup():
        await init_db()
        # start cleanup background task (runs forever)
        app.create_task(cleanup_old_photos_task(app, interval_hours=24))

    app.post_init = lambda _: None  # placeholder
    # run startup tasks then start polling
    log.info("Initializing DB and starting bot...")
    # run init_db and then start the bot
    asyncio.run(_startup())  # ensure DB created before run_polling (safe)

    app.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
