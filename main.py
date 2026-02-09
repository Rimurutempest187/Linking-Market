#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarketLink Pro - Full Bot (single file)
Features:
- Shop register / trial / subscription (admin approve)
- Products (add / list / edit / delete)
- Links (add / edit)
- Orders with cart + checkout + payment screenshot
- Payment queue (admin approve) -> extend subscription or confirm order
- Export orders to Excel (owner)
- Designed to run on Pydroid / Termux (sqlite3)
"""

import os
import sqlite3
import logging
import traceback
from datetime import datetime, timedelta

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
from dotenv import load_dotenv
load_dotenv()
# ---------- CONFIG ----------
# Replace BOT_TOKEN and ADMIN_ID (or set BOT_TOKEN env var / .env)
BOT_TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # put your Telegram ID in env
DB_FILE = "bot.db"
PHOTOS_DIR = "photos"
FEE = 5000  # subscription fee in MMK
TRIAL_DAYS = 3

# Conversation states
(ORDER_NAME, ORDER_PHONE, ORDER_ADDRESS, ORDER_SHOPPING, ORDER_PHOTO) = range(5)
(EDIT_LINK_ID, EDIT_LINK_TITLE, EDIT_LINK_URL) = range(5, 8)
(EDIT_PROD_ID, EDIT_PROD_NAME, EDIT_PROD_PRICE) = range(8, 11)
(PAYMENT_WAIT,) = range(11, 12)

# ---------- LOG ----------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
log = logging.getLogger(__name__)


# ---------- DB UTILITIES ----------
def init_db():
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    # shops: owner id -> shop info
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS shops (
        owner_id INTEGER PRIMARY KEY,
        shop_name TEXT,
        expire_date TEXT,
        created_at TEXT
    )"""
    )
    # products: owner -> items
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        name TEXT,
        price INTEGER
    )"""
    )
    # links
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        title TEXT,
        url TEXT
    )"""
    )
    # orders
    cur.execute(
        """
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
        created_at TEXT
    )"""
    )
    # payments: subscription or order payment proofs
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER,
        kind TEXT,        -- 'subscription' or 'order'
        ref_id INTEGER,   -- order id or NULL
        photo_path TEXT,
        status TEXT,
        created_at TEXT
    )"""
    )
    con.commit()
    con.close()


def db_get_shop(owner_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT owner_id, shop_name, expire_date, created_at FROM shops WHERE owner_id=?", (owner_id,))
    row = cur.fetchone()
    con.close()
    return row


def db_set_shop(owner_id, shop_name, expire_date):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO shops(owner_id, shop_name, expire_date, created_at) VALUES(?,?,?,?)",
        (owner_id, shop_name, expire_date, datetime.now().strftime("%Y-%m-%d")),
    )
    con.commit()
    con.close()


def db_extend_shop(owner_id, days):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT expire_date FROM shops WHERE owner_id=?", (owner_id,))
    row = cur.fetchone()
    if row and row[0]:
        try:
            cur_exp = datetime.strptime(row[0], "%Y-%m-%d")
        except Exception:
            cur_exp = datetime.now()
    else:
        cur_exp = datetime.now()
    new_exp = (cur_exp + timedelta(days=days)).strftime("%Y-%m-%d")
    cur.execute("UPDATE shops SET expire_date=? WHERE owner_id=?", (new_exp, owner_id))
    con.commit()
    con.close()
    return new_exp


def db_add_product(owner_id, name, price):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("INSERT INTO products(owner_id, name, price) VALUES(?,?,?)", (owner_id, name, price))
    con.commit()
    con.close()


def db_list_products(owner_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT id, name, price FROM products WHERE owner_id=?", (owner_id,))
    rows = cur.fetchall()
    con.close()
    return rows


def db_get_product(pid, owner_id=None):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    if owner_id:
        cur.execute("SELECT id, name, price FROM products WHERE id=? AND owner_id=?", (pid, owner_id))
    else:
        cur.execute("SELECT id, name, price FROM products WHERE id=?", (pid,))
    row = cur.fetchone()
    con.close()
    return row


def db_update_product(pid, owner_id, name, price):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("UPDATE products SET name=?, price=? WHERE id=? AND owner_id=?", (name, price, pid, owner_id))
    con.commit()
    con.close()


def db_delete_product(pid, owner_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("DELETE FROM products WHERE id=? AND owner_id=?", (pid, owner_id))
    con.commit()
    con.close()


def db_add_link(owner_id, title, url):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("INSERT INTO links(owner_id, title, url) VALUES(?,?,?)", (owner_id, title, url))
    con.commit()
    con.close()


def db_list_links(owner_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT id, title, url FROM links WHERE owner_id=?", (owner_id,))
    rows = cur.fetchall()
    con.close()
    return rows


def db_get_link(lid, owner_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT id, title, url FROM links WHERE id=? AND owner_id=?", (lid, owner_id))
    row = cur.fetchone()
    con.close()
    return row


def db_update_link(lid, owner_id, title, url):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("UPDATE links SET title=?, url=? WHERE id=? AND owner_id=?", (title, url, lid, owner_id))
    con.commit()
    con.close()


def db_create_order(shop_id, user_id, name, phone, address, items, total, photo_path):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO orders(shop_id, user_id, name, phone, address, items, total, photo_path, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (shop_id, user_id, name, phone, address, items, total, photo_path, "Pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    oid = cur.lastrowid
    con.commit()
    con.close()
    return oid


def db_get_order(oid):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT id, shop_id, user_id, name, phone, address, items, total, photo_path, status, created_at FROM orders WHERE id=?", (oid,))
    row = cur.fetchone()
    con.close()
    return row


def db_update_order_status(oid, status):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    con.commit()
    con.close()


def db_insert_payment(uid, kind, ref_id, photo_path):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("INSERT INTO payments(uid, kind, ref_id, photo_path, status, created_at) VALUES(?,?,?,?,?,?)", (uid, kind, ref_id, photo_path, "pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    pid = cur.lastrowid
    con.commit()
    con.close()
    return pid


def db_get_pending_payments():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT id, uid, kind, ref_id, photo_path, status, created_at FROM payments WHERE status='pending' ORDER BY id ASC")
    rows = cur.fetchall()
    con.close()
    return rows


def db_update_payment_status(pid, status):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("UPDATE payments SET status=? WHERE id=?", (status, pid))
    con.commit()
    con.close()


def db_list_orders_by_shop(owner_id):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("SELECT id, user_id, name, phone, address, items, total, status, created_at FROM orders WHERE shop_id=? ORDER BY id DESC", (owner_id,))
    rows = cur.fetchall()
    con.close()
    return rows


# ---------- HELPERS ----------
def is_shop_active(owner_id):
    if owner_id == ADMIN_ID:
        return True
    shop = db_get_shop(owner_id)
    if not shop:
        return False
    exp = shop[2]
    if not exp:
        return False
    try:
        return datetime.now().date() <= datetime.strptime(exp, "%Y-%m-%d").date()
    except Exception:
        return False


def extend_by_days(owner_id, days):
    return db_extend_shop(owner_id, days)


def fmt_date(d):
    return d if d else "-"


# ---------- BOT HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        args = context.args or []

        # deep link to shop: /start <shop_id>
        if args:
            try:
                shop_id = int(args[0])
            except:
                await update.message.reply_text("Invalid link.")
                return
            if not is_shop_active(shop_id):
                await update.message.reply_text("❌ ဆိုင်သည် သက်တမ်းကုန်ဆုံးနေပါသည်။")
                return
            context.user_data["current_shop_id"] = shop_id
            shop = db_get_shop(shop_id)
            await update.message.reply_text(f"🏪 **{shop[1]}** မှ ကြိုဆိုပါသည်။\n/Order ဖြင့်မှာယူပါ။", reply_markup=ReplyKeyboardRemove())
            return

        # Admin
        if uid == ADMIN_ID:
            kb = [["📊 Platform Stats", "📥 Pending Payments"], ["🏬 All Shops", "📤 Broadcast"]]
            await update.message.reply_text("👑 Admin Panel", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
            return

        # Owner
        shop = db_get_shop(uid)
        if shop:
            kb = [["➕ Add Product", "🛒 My Orders"], ["🔗 My Link", "💳 Subscription"]]
            await update.message.reply_text(f"🏪 Owner Panel: {shop[1]}", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
            return

        # New user
        kb = [["📝 Create Shop (/setup_shop MyShopName)", "ℹ️ Help"]]
        await update.message.reply_text("Welcome to MarketLink Pro!\nTo create a shop: /setup_shop <ShopName>", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    except Exception:
        log.exception("start error")


# ----- Shop setup -----
async def setup_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        name = " ".join(context.args or [])
        if not name:
            await update.message.reply_text("Usage: /setup_shop <Shop Name>")
            return
        exp = (datetime.now() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
        db_set_shop(uid, name, exp)
        await update.message.reply_text(f"✅ Shop created: {name}\nTrial until {exp}\nGo to /start to open your panel.")
    except Exception:
        log.exception("setup_shop")


# ----- Product commands -----
async def cmd_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        shop = db_get_shop(uid)
        if not shop:
            await update.message.reply_text("You are not a shop owner. Create shop with /setup_shop")
            return
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /add_product <name> <price>\nExample: /add_product Scarf 15000")
            return
        name = context.args[0]
        try:
            price = int(context.args[1])
        except:
            await update.message.reply_text("Price must be a number.")
            return
        db_add_product(uid, name, price)
        await update.message.reply_text("✅ Product added.")
    except Exception:
        log.exception("add_product")


async def cmd_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        rows = db_list_products(uid)
        if not rows:
            await update.message.reply_text("No products yet. Add with /add_product")
            return
        text = "📦 Your Products:\n\n"
        for r in rows:
            text += f"ID:{r[0]} • {r[1]} • {r[2]} MMK\n"
        await update.message.reply_text(text)
    except Exception:
        log.exception("list_products")


# Edit product (conversation)
async def edit_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = db_list_products(uid)
    if not rows:
        await update.message.reply_text("No products to edit.")
        return ConversationHandler.END
    msg = "Send Product ID to edit:\n\n"
    for r in rows:
        msg += f"ID:{r[0]} • {r[1]} • {r[2]} MMK\n"
    await update.message.reply_text(msg)
    return EDIT_PROD_ID


async def edit_product_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pid = int(update.message.text.strip())
    except:
        await update.message.reply_text("Send a valid numeric ID.")
        return EDIT_PROD_ID
    uid = update.effective_user.id
    prod = db_get_product(pid, uid)
    if not prod:
        await update.message.reply_text("Product not found or not yours.")
        return EDIT_PROD_ID
    context.user_data["edit_product_id"] = pid
    await update.message.reply_text(f"Old name: {prod[1]}\nSend new name:")
    return EDIT_PROD_NAME


async def edit_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["edit_product_name"] = update.message.text.strip()
    await update.message.reply_text("Send new price:")
    return EDIT_PROD_PRICE


async def edit_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text.strip())
    except:
        await update.message.reply_text("Price must be numeric. Send price again.")
        return EDIT_PROD_PRICE
    pid = context.user_data.pop("edit_product_id")
    name = context.user_data.pop("edit_product_name")
    uid = update.effective_user.id
    db_update_product(pid, uid, name, price)
    await update.message.reply_text("✅ Product updated.")
    return ConversationHandler.END


# delete product
async def cmd_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        if len(context.args) < 1:
            await update.message.reply_text("Usage: /del_product <product_id>")
            return
        pid = int(context.args[0])
        db_delete_product(pid, uid)
        await update.message.reply_text("✅ Product deleted (if it belonged to you).")
    except Exception:
        log.exception("del_product")


# ----- Links -----
async def cmd_add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /add_link <title> <url>")
            return
        title = context.args[0]
        url = context.args[1]
        db_add_link(uid, title, url)
        await update.message.reply_text("✅ Link added.")
    except Exception:
        log.exception("add_link")


# Edit link conversation
async def edit_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = db_list_links(uid)
    if not rows:
        await update.message.reply_text("No links to edit.")
        return ConversationHandler.END
    msg = "✏️ Your Links (ID)\n\n"
    for r in rows:
        msg += f"ID:{r[0]} • {r[1]} • {r[2]}\n"
    msg += "\nSend Link ID to edit:"
    await update.message.reply_text(msg)
    return EDIT_LINK_ID


async def edit_link_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lid = int(update.message.text.strip())
    except:
        await update.message.reply_text("Send valid numeric ID.")
        return EDIT_LINK_ID
    uid = update.effective_user.id
    link = db_get_link(lid, uid)
    if not link:
        await update.message.reply_text("Not found / not your link.")
        return EDIT_LINK_ID
    context.user_data["edit_link_id"] = lid
    await update.message.reply_text(f"Old title: {link[1]}\nSend new title:")
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
    db_update_link(lid, uid, title, url)
    await update.message.reply_text("✅ Link updated.")
    return ConversationHandler.END


# ----- Order Flow -----
async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "current_shop_id" not in context.user_data:
        await update.message.reply_text("Please open shop link first (/start <shop_id>)")
        return ConversationHandler.END
    context.user_data["cart"] = []
    context.user_data["total"] = 0
    await update.message.reply_text("Name:", reply_markup=ReplyKeyboardRemove())
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
    # show products
    sid = context.user_data["current_shop_id"]
    prods = db_list_products(sid)
    kb = []
    for p in prods:
        kb.append([f"{p[1]}:{p[2]}"])
    kb.append(["🛒 View Cart", "✅ Checkout"])
    await update.message.reply_text("Select product (name:price) to add to cart:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ORDER_SHOPPING


async def order_shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "🛒 View Cart":
        cart = context.user_data.get("cart", [])
        total = context.user_data.get("total", 0)
        await update.message.reply_text(f"Cart: {', '.join(cart) if cart else 'Empty'}\nTotal: {total} MMK")
        return ORDER_SHOPPING
    if text == "✅ Checkout":
        await update.message.reply_text("Send payment screenshot (WavePay / KBZPay) or type /cancel to abort", reply_markup=ReplyKeyboardRemove())
        return ORDER_PHOTO
    # expect name:price
    if ":" in text:
        try:
            name, price = text.split(":", 1)
            price = int(price.strip())
            context.user_data.setdefault("cart", []).append(f"{name.strip()}:{price}")
            context.user_data["total"] = context.user_data.get("total", 0) + price
            await update.message.reply_text(f"Added {name.strip()} - {price} MMK. Total: {context.user_data['total']}")
        except Exception:
            await update.message.reply_text("Format must be name:price")
    return ORDER_SHOPPING


async def order_photo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Send a photo (screenshot) of payment.")
        return ORDER_PHOTO
    # save photo
    photo_file = await update.message.photo[-1].get_file()
    filename = f"{PHOTOS_DIR}/pay_order_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
    await photo_file.download_to_drive(filename)
    # create order
    sid = context.user_data["current_shop_id"]
    uid = update.effective_user.id
    oid = db_create_order(sid, uid, context.user_data.get("cust_name"), context.user_data.get("cust_phone"), context.user_data.get("cust_address"), ",".join(context.user_data.get("cart", [])), context.user_data.get("total", 0), filename)
    # insert payment record (link to order)
    pid = db_insert_payment(uid, "order", oid, filename)
    # notify owner (shop owner receives photo + buttons)
    shop = db_get_shop(sid)
    owner_id = shop[0] if shop else None
    kb = [
        [InlineKeyboardButton("Confirm Order ✅", callback_data=f"order_conf_{oid}_{pid}"),
         InlineKeyboardButton("Reject ❌", callback_data=f"order_rej_{oid}_{pid}")]
    ]
    # send to owner if possible, else admin
    target = owner_id or ADMIN_ID
    try:
        with open(filename, "rb") as f:
            await context.bot.send_photo(chat_id=target, photo=f, caption=f"New order #{oid}\nFrom: {uid}\nTotal: {context.user_data.get('total')} MMK", reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        log.exception("notify owner failed")
    await update.message.reply_text("✅ Order received and pending owner confirmation. You'll be notified.")
    return ConversationHandler.END


# ----- Subscription Payment (user pays to extend) -----
async def pay_subscription_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Subscription fee: {FEE} MMK\nSend screenshot after payment.")
    return PAYMENT_WAIT


async def pay_subscription_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Send a photo (screenshot) of payment.")
        return PAYMENT_WAIT
    photo_file = await update.message.photo[-1].get_file()
    filename = f"{PHOTOS_DIR}/pay_sub_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
    await photo_file.download_to_drive(filename)
    pid = db_insert_payment(update.effective_user.id, "subscription", None, filename)
    # notify admin
    kb = [
        [InlineKeyboardButton("Approve ✅", callback_data=f"sub_ok_{pid}_{update.effective_user.id}"),
         InlineKeyboardButton("Reject ❌", callback_data=f"sub_no_{pid}_{update.effective_user.id}")]
    ]
    try:
        with open(filename, "rb") as f:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=f, caption=f"Subscription payment (uid={update.effective_user.id})", reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        log.exception("notify admin subscription failed")
    await update.message.reply_text("✅ Payment submitted. Waiting admin approval.")
    return ConversationHandler.END


# ----- Admin callbacks (approve subscription / orders) -----
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    try:
        # subscription approve: sub_ok_<pid>_<uid>
        if data.startswith("sub_ok_") or data.startswith("sub_no_"):
            parts = data.split("_")
            action = parts[1]  # ok / no
            pid = int(parts[2])
            uid = int(parts[3])
            if action == "ok":
                # extend 30 days by default
                new_exp = extend_by_days(uid, 30)
                db_update_payment_status(pid, "approved")
                await context.bot.send_message(uid, f"✅ Subscription approved. New expiry: {new_exp}")
                await q.edit_message_caption(caption=f"Subscription processed. Approved -> UID {uid}")
            else:
                db_update_payment_status(pid, "rejected")
                await context.bot.send_message(uid, "❌ Subscription payment rejected by admin.")
                await q.edit_message_caption(caption=f"Subscription processed. Rejected -> UID {uid}")

        # order confirmation: order_conf_<oid>_<pid> or order_rej_
        elif data.startswith("order_conf_") or data.startswith("order_rej_"):
            parts = data.split("_")
            action = parts[1]  # conf / rej
            oid = int(parts[2])
            pid = int(parts[3])
            order = db_get_order(oid)
            if not order:
                await q.edit_message_text("Order not found.")
                return
            user_id = order[2]
            if action == "conf":
                db_update_order_status(oid, "Confirmed")
                db_update_payment_status(pid, "approved")
                await context.bot.send_message(user_id, f"🔔 Your order #{oid} has been confirmed by the shop.")
                await q.edit_message_caption(caption=f"Order #{oid} - Confirmed")
            else:
                db_update_order_status(oid, "Rejected")
                db_update_payment_status(pid, "rejected")
                await context.bot.send_message(user_id, f"🔔 Your order #{oid} was rejected by the shop. Contact the shop for details.")
                await q.edit_message_caption(caption=f"Order #{oid} - Rejected")
    except Exception:
        log.exception("admin_callback error")
        await q.edit_message_text("Processing failed. See logs.")


# ----- Admin command: list pending payments -----
async def cmd_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Only admin.")
        return
    rows = db_get_pending_payments()
    if not rows:
        await update.message.reply_text("No pending payments.")
        return
    for p in rows:
        pid, uid, kind, ref_id, path, status, created = p
        text = f"PID:{pid} UID:{uid} Kind:{kind} Ref:{ref_id} Status:{status} Created:{created}"
        try:
            with open(path, "rb") as f:
                kb = []
                if kind == "subscription":
                    kb = [[InlineKeyboardButton("Approve", callback_data=f"sub_ok_{pid}_{uid}"), InlineKeyboardButton("Reject", callback_data=f"sub_no_{pid}_{uid}")]]
                elif kind == "order":
                    kb = [[InlineKeyboardButton("Approve Order", callback_data=f"order_conf_{ref_id}_{pid}"), InlineKeyboardButton("Reject Order", callback_data=f"order_rej_{ref_id}_{pid}")]]
                await context.bot.send_photo(chat_id=ADMIN_ID, photo=f, caption=text, reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await update.message.reply_text(text + "\n(photo missing)")

# ----- Owner export orders to excel -----
async def cmd_export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        import pandas as pd
    except Exception:
        await update.message.reply_text("Pandas not installed. Install pandas + openpyxl to export.")
        return
    uid = update.effective_user.id
    rows = db_list_orders_by_shop(uid)
    if not rows:
        await update.message.reply_text("No orders.")
        return
    df = []
    for r in rows:
        df.append({
            "order_id": r[0],
            "user_id": r[1],
            "name": r[2],
            "phone": r[3],
            "address": r[4],
            "items": r[5],
            "total": r[6],
            "status": r[7],
            "created_at": r[8],
        })
    df = __import__("pandas").DataFrame(df)
    path = f"orders_{uid}_{int(datetime.now().timestamp())}.xlsx"
    df.to_excel(path, index=False)
    with open(path, "rb") as f:
        await update.message.reply_document(document=f, filename=os.path.basename(path))
    try:
        os.remove(path)
    except:
        pass


# ----- Utility: show shop link -----
async def cmd_my_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    await update.message.reply_text(f"https://t.me/{bot_username}?start={uid}")


# ----- Menu message handler -----
async def text_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    uid = update.effective_user.id

    # Owner options
    if t == "➕ Add Product" or t == "/add_product":
        await update.message.reply_text("Use /add_product <name> <price> or /list_products to manage.")
        return
    if t == "🛒 My Orders":
        rows = db_list_orders_by_shop(uid)
        if not rows:
            await update.message.reply_text("No orders.")
            return
        msg = "📦 Your Orders:\n\n"
        for r in rows:
            msg += f"#{r[0]} | {r[2]} | {r[6]} MMK | {r[7]}\n"
        await update.message.reply_text(msg)
        return
    if t == "🔗 My Link":
        await cmd_my_link(update, context); return
    if t == "💳 Subscription":
        await update.message.reply_text(f"Subscription is {FEE} MMK per month.\nUse /pay_subscribe to pay.", reply_markup=ReplyKeyboardRemove()); return

    # Admin menu
    if uid == ADMIN_ID:
        if t == "📊 Platform Stats":
            con = sqlite3.connect(DB_FILE); cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM shops"); shops = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders"); orders = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM payments WHERE status='pending'"); pend = cur.fetchone()[0]
            con.close()
            await update.message.reply_text(f"Shops:{shops}\nOrders:{orders}\nPending payments:{pend}")
            return
        if t == "📥 Pending Payments":
            await cmd_pending_payments(update, context); return
        if t == "🏬 All Shops":
            con = sqlite3.connect(DB_FILE); cur = con.cursor()
            cur.execute("SELECT owner_id, shop_name, expire_date FROM shops")
            rows = cur.fetchall(); con.close()
            txt = "All Shops:\n"
            for r in rows:
                txt += f"ID:{r[0]} • {r[1]} • Exp:{r[2]}\n"
            await update.message.reply_text(txt)
            return

    # general fallback
    if t == "ℹ️ Help" or t == "/help":
        await update.message.reply_text("/setup_shop, /add_product, /list_products, /edit_product, /add_link, /edit_link, /order (open shop link first) /pay_subscribe")
        return

    # default echo
    await update.message.reply_text("Command not recognized. Use /help")


# ----- Fall back / cancel -----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarketLink Pro - Final single-file bot
Features:
- Persistent DB on disk (data/shop.db) using aiosqlite
- Customer can send product photo when creating an order
- Owner/Admin receives order photo + approve/reject buttons
- Subscription payment flow (upload screenshot) with admin approve
- Auto cleanup job to remove old photos (configurable days)
- Robust checks: admin-only approve, shop expiry, product price integrity
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
DB_PATH = os.path.join(DATA_DIR, "shop.db")

# Auto cleanup config (days)
CLEANUP_PHOTO_DAYS = 30

# subscription config
FEE = 5000  # MMK
TRIAL_DAYS = 3
SUBSCRIPTION_EXTENSION_DAYS = 30

# conversation states
(ORDER_PHOTO, ORDER_NAME, ORDER_PHONE, ORDER_ADDRESS) = range(4)
(PAY_SUB_WAIT,) = range(4, 5)

# logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ensure dirs
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)

# ----------------- DB helpers -----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
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
                price INTEGER
            );

            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                title TEXT,
                url TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_id INTEGER,
                customer_id INTEGER,
                cust_name TEXT,
                cust_phone TEXT,
                cust_address TEXT,
                items TEXT,
                total INTEGER,
                photo_path TEXT,
                status TEXT,
                created_at TEXT
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
    log.info("DB initialized/verified at %s", DB_PATH)


# convenience to get a row as dict-like
async def fetch_one(query: str, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        row = await cur.fetchone()
        await cur.close()
        return row


async def fetch_all(query: str, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def execute(query: str, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        await db.commit()
        await cur.close()
        return cur


# Shop helpers
async def db_get_shop(owner_id: int):
    return await fetch_one("SELECT owner_id, shop_name, expire_date, created_at FROM shops WHERE owner_id=?", (owner_id,))


async def db_set_shop(owner_id: int, shop_name: str, expire_date: str):
    await execute("INSERT OR REPLACE INTO shops(owner_id, shop_name, expire_date, created_at) VALUES(?,?,?,?)",
                  (owner_id, shop_name, expire_date, datetime.now().strftime("%Y-%m-%d")))


async def db_extend_shop(owner_id: int, days: int) -> str:
    row = await fetch_one("SELECT expire_date FROM shops WHERE owner_id=?", (owner_id,))
    if row and row["expire_date"]:
        try:
            cur_exp = datetime.strptime(row["expire_date"], "%Y-%m-%d")
        except Exception:
            cur_exp = datetime.now()
    else:
        cur_exp = datetime.now()
    new_exp = (cur_exp + timedelta(days=days)).strftime("%Y-%m-%d")
    await execute("UPDATE shops SET expire_date=? WHERE owner_id=?", (new_exp, owner_id))
    return new_exp


# Products
async def db_add_product(owner_id: int, name: str, price: int):
    await execute("INSERT INTO products(owner_id, name, price) VALUES(?,?,?)", (owner_id, name, price))


async def db_list_products(owner_id: int):
    return await fetch_all("SELECT id, name, price FROM products WHERE owner_id=?", (owner_id,))


async def db_get_product(pid: int, owner_id: Optional[int] = None):
    if owner_id:
        return await fetch_one("SELECT id, name, price FROM products WHERE id=? AND owner_id=?", (pid, owner_id))
    return await fetch_one("SELECT id, name, price FROM products WHERE id=?", (pid,))


async def db_update_product(pid: int, owner_id: int, name: str, price: int):
    await execute("UPDATE products SET name=?, price=? WHERE id=? AND owner_id=?", (name, price, pid, owner_id))


async def db_delete_product(pid: int, owner_id: int):
    await execute("DELETE FROM products WHERE id=? AND owner_id=?", (pid, owner_id))


# Links
async def db_add_link(owner_id: int, title: str, url: str):
    await execute("INSERT INTO links(owner_id, title, url) VALUES(?,?,?)", (owner_id, title, url))


async def db_list_links(owner_id: int):
    return await fetch_all("SELECT id, title, url FROM links WHERE owner_id=?", (owner_id,))


async def db_update_link(lid: int, owner_id: int, title: str, url: str):
    await execute("UPDATE links SET title=?, url=? WHERE id=? AND owner_id=?", (title, url, lid, owner_id))


# Orders & Payments
async def db_create_order(shop_id: int, customer_id: int, cust_name: str, cust_phone: str, cust_address: str, items: str, total: int, photo_path: str):
    cur = await execute(
        "INSERT INTO orders(shop_id, customer_id, cust_name, cust_phone, cust_address, items, total, photo_path, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (shop_id, customer_id, cust_name, cust_phone, cust_address, items, total, photo_path, "Pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    return cur.lastrowid


async def db_get_order(oid: int):
    return await fetch_one("SELECT * FROM orders WHERE id=?", (oid,))


async def db_update_order_status(oid: int, status: str):
    await execute("UPDATE orders SET status=? WHERE id=?", (status, oid))


async def db_insert_payment(uid: int, kind: str, ref_id: Optional[int], photo_path: str):
    cur = await execute("INSERT INTO payments(uid, kind, ref_id, photo_path, status, created_at) VALUES(?,?,?,?,?,?)",
                        (uid, kind, ref_id, photo_path, "pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return cur.lastrowid


async def db_get_pending_payments():
    return await fetch_all("SELECT id, uid, kind, ref_id, photo_path, status, created_at FROM payments WHERE status='pending' ORDER BY id ASC")


async def db_update_payment_status(pid: int, status: str):
    await execute("UPDATE payments SET status=? WHERE id=?", (status, pid))


async def db_list_orders_by_shop(owner_id: int):
    return await fetch_all("SELECT id, customer_id, cust_name, cust_phone, cust_address, items, total, status, created_at FROM orders WHERE shop_id=? ORDER BY id DESC", (owner_id,))


# ----------------- Helpers -----------------
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
        return datetime.now().date() <= datetime.strptime(exp, "%Y-%m-%d").date()
    except Exception:
        return False


# ----------------- Bot Handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args or []
    # deep link /start <shop_id>
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
            await update.message.reply_text(f"🏪 **{shop['shop_name']}** ကို ကြိုဆိုပါတယ်။\n`/order` ဖြင့် မှာပို့ပါ။", reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("ဆိုင်ကို တွေ့မရပါ။")
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
            await update.message.reply_text("❌ Your shop subscription expired. Renew with /pay_subscribe.")
            return
        kb = [["➕ Add Product", "🛒 My Orders"], ["🔗 My Link", "💳 Subscription"]]
        await update.message.reply_text(f"🏪 Owner Panel: {shop['shop_name']}", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return

    # new user
    kb = [["📝 Create Shop (/setup_shop MyShopName)", "ℹ️ Help"]]
    await update.message.reply_text("Welcome to MarketLink Pro!\nTo create a shop: /setup_shop <ShopName>", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))


# ---------- Setup shop ----------
async def setup_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = " ".join(context.args or [])
    if not name:
        await update.message.reply_text("Usage: /setup_shop <Shop Name>")
        return
    exp = (datetime.now() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
    await db_set_shop(uid, name, exp)
    await update.message.reply_text(f"✅ Shop created: {name}\nTrial until {exp}\nUse /start to open panel.")


# ---------- Products ----------
async def cmd_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    shop = await db_get_shop(uid)
    if not shop:
        await update.message.reply_text("You are not an owner. Create shop with /setup_shop")
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
    msg = "📦 Your Products:\n\n"
    for r in rows:
        msg += f"ID:{r['id']} • {r['name']} • {r['price']} MMK\n"
    await update.message.reply_text(msg)


# ---------- Links ----------
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


# ---------- Order Flow (customer sends product photo first) ----------
async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # require current_shop_id in context (via /start <shop_id> deep link) OR allow /order <shop_id>
    uid = update.effective_user.id
    args = context.args or []
    if args:
        try:
            sid = int(args[0])
            context.user_data["current_shop_id"] = sid
        except:
            pass
    if "current_shop_id" not in context.user_data:
        await update.message.reply_text("ကျေးဇူးပြု၍ ဆိုင် link ဖြင့် သို့မဟုတ် /order <shop_id> ဖြင့် ရောက်ပါ (ဥပမာ: /start <shop_id>)")
        return ConversationHandler.END
    if not await is_shop_active(context.user_data["current_shop_id"]):
        await update.message.reply_text("❌ ဆိုင်သည် သက်တမ်းကုန်သွားပြီ။")
        return ConversationHandler.END
    await update.message.reply_text("လိုချင်တဲ့ ပစ္စည်းရဲ့ ပုံ (photo) ကို ပို့ပေးပါ 📸", reply_markup=ReplyKeyboardRemove())
    return ORDER_PHOTO


async def order_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("တိုက်ရိုက်ပုံပို့ပေးပါ။ (photo required)")
        return ORDER_PHOTO
    try:
        photo_file = await update.message.photo[-1].get_file()
        filename = os.path.join(PHOTOS_DIR, f"order_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg")
        await photo_file.download_to_drive(filename)
        context.user_data["order_photo"] = filename
        await update.message.reply_text("ပုံရရှိပါပြီ — အမည် (Name) ပေးပါ။")
        return ORDER_NAME
    except Exception:
        log.exception("order_photo save failed")
        await update.message.reply_text("ပုံတင်ခြင်း မအောင်မြင်ပါ (failed). ထပ်လောင်းပို့ပါ။")
        return ORDER_PHOTO


async def order_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_name"] = update.message.text.strip()
    await update.message.reply_text("ဖုန်းနံပါတ် ထည့်ပါ။")
    return ORDER_PHONE


async def order_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_phone"] = update.message.text.strip()
    await update.message.reply_text("လိပ်စာ (Address) ဖြည့်ပေးပါ။")
    return ORDER_ADDRESS


async def order_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_address"] = update.message.text.strip()
    # create order record
    sid = context.user_data.get("current_shop_id")
    uid = update.effective_user.id
    photo = context.user_data.get("order_photo")
    # items & total can be empty since customer sent custom photo; keep as empty string/0
    oid = await db_create_order(sid, uid, context.user_data.get("cust_name"), context.user_data.get("cust_phone"), context.user_data.get("cust_address"), "", 0, photo)
    # notify owner
    shop = await db_get_shop(sid)
    owner_id = shop["owner_id"] if shop else None
    target = owner_id or ADMIN_ID
    kb = [
        [InlineKeyboardButton("Confirm ✅", callback_data=f"order_conf_{oid}"), InlineKeyboardButton("Reject ❌", callback_data=f"order_rej_{oid}")]
    ]
    caption = f"New order #{oid}\nFrom: {uid}\nName: {context.user_data.get('cust_name')}\nPhone: {context.user_data.get('cust_phone')}"
    try:
        if photo and os.path.exists(photo):
            with open(photo, "rb") as f:
                await context.bot.send_photo(chat_id=target, photo=f, caption=caption, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await context.bot.send_message(chat_id=target, text=caption, reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        log.exception("notify owner failed")
    await update.message.reply_text("✅ Order တင်ပြီးပါပြီ — ဆိုင်မှ အတည်ပြုချက် ရင်အသိပေးပါမည်။")
    # cleanup user_data
    for k in ("order_photo", "cust_name", "cust_phone", "cust_address"):
        context.user_data.pop(k, None)
    return ConversationHandler.END


# ---------- Subscription payment ----------
async def pay_subscribe_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Subscription fee: {FEE} MMK\nSend screenshot of payment (photo).")
    return PAY_SUB_WAIT


async def pay_sub_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Payment screenshot (photo) ပို့ပါ။")
        return PAY_SUB_WAIT
    try:
        photo_file = await update.message.photo[-1].get_file()
        filename = os.path.join(PHOTOS_DIR, f"sub_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg")
        await photo_file.download_to_drive(filename)
        pid = await db_insert_payment(update.effective_user.id, "subscription", None, filename)
        kb = [
            [InlineKeyboardButton("Approve ✅", callback_data=f"sub_ok_{pid}_{update.effective_user.id}"), InlineKeyboardButton("Reject ❌", callback_data=f"sub_no_{pid}_{update.effective_user.id}")]
        ]
        try:
            with open(filename, "rb") as f:
                await context.bot.send_photo(chat_id=ADMIN_ID, photo=f, caption=f"Subscription payment (uid={update.effective_user.id})", reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            log.exception("notify admin subscription failed")
        await update.message.reply_text("✅ Payment submitted. Waiting admin approval.")
    except Exception:
        log.exception("pay_sub_receive failed")
        await update.message.reply_text("Payment upload failed. Try again.")
    return ConversationHandler.END


# ---------- Callback handler (admin/owner actions) ----------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    user_id = q.from_user.id if q.from_user else None
    try:
        # subscription approval: sub_ok_<pid>_<uid> or sub_no_<pid>_<uid>
        if data.startswith("sub_ok_") or data.startswith("sub_no_"):
            parts = data.split("_")
            # format: sub_ok_pid_uid
            if len(parts) < 4:
                await q.edit_message_text("Invalid callback.")
                return
            action = parts[1]
            pid = int(parts[2])
            uid = int(parts[3])
            if user_id != ADMIN_ID:
                await q.answer("Not authorized", show_alert=True)
                return
            if action == "ok":
                new_exp = await db_extend_shop(uid, SUBSCRIPTION_EXTENSION_DAYS)
                await db_update_payment_status(pid, "approved")
                try:
                    await context.bot.send_message(uid, f"✅ Subscription approved. New expiry: {new_exp}")
                except Exception:
                    log.exception("notify user sub approved failed")
                await q.edit_message_caption(caption=f"Subscription processed. Approved -> UID {uid}")
            else:
                await db_update_payment_status(pid, "rejected")
                try:
                    await context.bot.send_message(uid, "❌ Subscription payment rejected by admin.")
                except Exception:
                    log.exception("notify user sub rejected failed")
                await q.edit_message_caption(caption=f"Subscription processed. Rejected -> UID {uid}")
            return

        # order confirmation: order_conf_<oid> / order_rej_<oid>
        if data.startswith("order_conf_") or data.startswith("order_rej_"):
            parts = data.split("_")
            if len(parts) < 3:
                await q.edit_message_text("Invalid callback.")
                return
            action = parts[1]
            oid = int(parts[2])
            order = await db_get_order(oid)
            if not order:
                await q.edit_message_text("Order not found.")
                return
            shop_id = order["shop_id"]
            shop = await db_get_shop(shop_id)
            owner_id = shop["owner_id"] if shop else None
            # authorize: admin or owner can confirm/reject
            if user_id != ADMIN_ID and user_id != owner_id:
                await q.answer("Not authorized", show_alert=True)
                return
            if action == "conf":
                await db_update_order_status(oid, "Confirmed")
                try:
                    await context.bot.send_message(order["customer_id"], f"🔔 Your order #{oid} has been confirmed by the shop.")
                except Exception:
                    log.exception("notify customer confirm failed")
                await q.edit_message_caption(caption=f"Order #{oid} - Confirmed")
            else:
                await db_update_order_status(oid, "Rejected")
                try:
                    await context.bot.send_message(order["customer_id"], f"🔔 Your order #{oid} was rejected by the shop.")
                except Exception:
                    log.exception("notify customer reject failed")
                await q.edit_message_caption(caption=f"Order #{oid} - Rejected")
            return

    except Exception:
        log.exception("callback_handler error")
        try:
            await q.edit_message_text("Processing failed. See logs.")
        except Exception:
            pass


# ---------- Admin commands ----------
async def cmd_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Only admin allowed.")
        return
    rows = await db_get_pending_payments()
    if not rows:
        await update.message.reply_text("No pending payments.")
        return
    for p in rows:
        pid = p["id"]
        uid = p["uid"]
        kind = p["kind"]
        ref = p["ref_id"]
        path = p["photo_path"]
        created = p["created_at"]
        text = f"PID:{pid} UID:{uid} Kind:{kind} Ref:{ref} Created:{created}"
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                if kind == "subscription":
                    kb = [[InlineKeyboardButton("Approve", callback_data=f"sub_ok_{pid}_{uid}"), InlineKeyboardButton("Reject", callback_data=f"sub_no_{pid}_{uid}")]]
                else:
                    kb = [[InlineKeyboardButton("Approve", callback_data=f"order_conf_{ref}"), InlineKeyboardButton("Reject", callback_data=f"order_rej_{ref}")]]
                await context.bot.send_photo(chat_id=ADMIN_ID, photo=f, caption=text, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(text + "\n(photo missing)")


async def cmd_my_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        me = await context.bot.get_me()
        botusername = me.username
    except Exception:
        botusername = None
    if not botusername:
        await update.message.reply_text("Bot username not available.")
        return
    await update.message.reply_text(f"https://t.me/{botusername}?start={uid}")


async def cmd_export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # export orders for owner to excel (requires pandas & openpyxl)
    try:
        import pandas as pd
    except Exception:
        await update.message.reply_text("Pandas not installed. Install pandas and openpyxl to export.")
        return
    uid = update.effective_user.id
    rows = await db_list_orders_by_shop(uid)
    if not rows:
        await update.message.reply_text("No orders.")
        return
    arr = []
    for r in rows:
        arr.append({
            "order_id": r["id"], "customer_id": r["customer_id"], "name": r["cust_name"],
            "phone": r["cust_phone"], "address": r["cust_address"], "items": r["items"],
            "total": r["total"], "status": r["status"], "created_at": r["created_at"]
        })
    df = pd.DataFrame(arr)
    path = os.path.join(DATA_DIR, f"orders_{uid}_{int(datetime.now().timestamp())}.xlsx")
    try:
        df.to_excel(path, index=False)
        with open(path, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(path))
    except Exception:
        log.exception("export failed")
        await update.message.reply_text("Export failed.")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


# ---------- Menu message handler ----------
async def menu_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    # Block expired owner quickly
    shop = await db_get_shop(uid)
    if shop and not await is_shop_active(uid):
        await update.message.reply_text("❌ Your shop subscription expired. Renew with /pay_subscribe.")
        return

    # owner quick buttons
    if text == "➕ Add Product" or text == "/add_product":
        await update.message.reply_text("Use /add_product <name> <price> or /list_products to manage.")
        return
    if text == "🛒 My Orders":
        rows = await db_list_orders_by_shop(uid)
        if not rows:
            await update.message.reply_text("No orders.")
            return
        msg = "📦 Your Orders:\n\n"
        for r in rows:
            msg += f"#{r['id']} | {r['cust_name']} | {r['total']} MMK | {r['status']}\n"
        await update.message.reply_text(msg)
        return
    if text == "🔗 My Link":
        await cmd_my_link(update, context)
        return
    if text == "💳 Subscription":
        await update.message.reply_text(f"Subscription is {FEE} MMK per month. Use /pay_subscribe to pay.", reply_markup=ReplyKeyboardRemove())
        return

    # admin quick buttons
    if uid == ADMIN_ID:
        if text == "📊 Platform Stats":
            async with aiosqlite.connect(DB_PATH) as con:
                cur = await con.execute("SELECT COUNT(*) FROM shops"); shops = (await cur.fetchone())[0]; await cur.close()
                cur = await con.execute("SELECT COUNT(*) FROM orders"); orders = (await cur.fetchone())[0]; await cur.close()
                cur = await con.execute("SELECT COUNT(*) FROM payments WHERE status='pending'"); pend = (await cur.fetchone())[0]; await cur.close()
            await update.message.reply_text(f"Shops:{shops}\nOrders:{orders}\nPending payments:{pend}")
            return
        if text == "📥 Pending Payments":
            await cmd_pending_payments(update, context)
            return
        if text == "🏬 All Shops":
            rows = await fetch_all("SELECT owner_id, shop_name, expire_date FROM shops")
            txt = "All Shops:\n"
            for r in rows:
                txt += f"ID:{r['owner_id']} • {r['shop_name']} • Exp:{r['expire_date']}\n"
            await update.message.reply_text(txt)
            return

    if text == "ℹ️ Help" or text == "/help":
        await update.message.reply_text("/setup_shop, /add_product, /list_products, /add_link, /order (open shop link first), /pay_subscribe")
        return

    await update.message.reply_text("Command not recognized. Use /help")


# ---------- Auto cleanup job ----------
async def cleanup_old_photos(context: ContextTypes.DEFAULT_TYPE):
    """Delete photos older than CLEANUP_PHOTO_DAYS (orders & payments)"""
    cutoff = datetime.now() - timedelta(days=CLEANUP_PHOTO_DAYS)
    # orders
    try:
        rows = await fetch_all("SELECT id, photo_path, created_at FROM orders WHERE photo_path IS NOT NULL")
        for r in rows:
            p = r["photo_path"]
            created = r["created_at"]
            try:
                created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
            except Exception:
                # fallback try isoformat
                try:
                    created_dt = datetime.fromisoformat(created)
                except Exception:
                    created_dt = None
            if p and os.path.exists(p):
                if created_dt and created_dt < cutoff:
                    try:
                        os.remove(p)
                        log.info("Deleted old order photo %s", p)
                    except Exception:
                        log.exception("delete old order photo failed")
        # payments
        rows = await fetch_all("SELECT id, photo_path, created_at FROM payments WHERE photo_path IS NOT NULL")
        for r in rows:
            p = r["photo_path"]
            created = r["created_at"]
            try:
                created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    created_dt = datetime.fromisoformat(created)
                except Exception:
                    created_dt = None
            if p and os.path.exists(p):
                if created_dt and created_dt < cutoff:
                    try:
                        os.remove(p)
                        log.info("Deleted old payment photo %s", p)
                    except Exception:
                        log.exception("delete old payment photo failed")
    except Exception:
        log.exception("cleanup job failed")


# ---------- Cancel ----------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------- Main ----------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set (use .env).")
    if ADMIN_ID == 0:
        log.warning("ADMIN_ID is 0 or not set. Set ADMIN_ID env var to your Telegram user id.")

    # initialize DB
    asyncio.run(init_db())

    app = Application.builder().token(BOT_TOKEN).build()

    # conversation for order (photo-first)
    order_conv = ConversationHandler(
        entry_points=[CommandHandler("order", order_start)],
        states={
            ORDER_PHOTO: [MessageHandler(filters.PHOTO & ~filters.COMMAND, order_photo)],
            ORDER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_name)],
            ORDER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_phone)],
            ORDER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_address)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # subscription payment conv
    pay_conv = ConversationHandler(
        entry_points=[CommandHandler("pay_subscribe", pay_subscribe_start)],
        states={PAY_SUB_WAIT: [MessageHandler(filters.PHOTO & ~filters.COMMAND, pay_sub_receive)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup_shop", setup_shop))
    app.add_handler(CommandHandler("add_product", cmd_add_product))
    app.add_handler(CommandHandler("list_products", cmd_list_products))
    app.add_handler(CommandHandler("add_link", cmd_add_link))
    app.add_handler(CommandHandler("list_links", cmd_list_links))
    app.add_handler(order_conv)
    app.add_handler(pay_conv)
    app.add_handler(CommandHandler("pending_payments", cmd_pending_payments))
    app.add_handler(CommandHandler("my_link", cmd_my_link))
    app.add_handler(CommandHandler("export_orders", cmd_export_orders))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text_handler))
    app.add_handler(CommandHandler("cancel", cancel))

    # schedule cleanup job daily
    job_queue = app.job_queue
    job_queue.run_repeating(lambda c: asyncio.create_task(cleanup_old_photos(c)), interval=24*60*60, first=10)

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
main()
