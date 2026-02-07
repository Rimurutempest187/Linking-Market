#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# MarketLink Pro - simplified single-file (safe header, no large docstring)

import os
import sqlite3
import logging
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler

# CONFIG - replace with your values

BOT_TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID") # <-- change to your Telegram ID
DB_FILE = "marketlink.db"
PHOTOS_DIR = "photos"
FEE = 5000
TRIAL_DAYS = 3

# STATES
(ORDER_NAME, ORDER_PHONE, ORDER_ADDR, ORDER_SHOP, ORDER_PAY) = range(5)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

# DB init
def init_db():
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS shops(owner_id INTEGER PRIMARY KEY, shop_name TEXT, expire_date TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, name TEXT, price INTEGER)")
    cur.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER, user_id INTEGER, name TEXT, phone TEXT, address TEXT, items TEXT, total INTEGER, photo_path TEXT, status TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, kind TEXT, ref_id INTEGER, photo_path TEXT, status TEXT, created_at TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS links(id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, title TEXT, url TEXT)")
    con.commit()
    con.close()

# Simple helpers
def get_shop(owner_id):
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("SELECT owner_id, shop_name, expire_date FROM shops WHERE owner_id=?", (owner_id,))
    r = cur.fetchone(); con.close(); return r

def is_active(owner_id):
    if owner_id == ADMIN_ID:
        return True
    shop = get_shop(owner_id)
    if not shop:
        return False
    try:
        return datetime.now().date() <= datetime.strptime(shop[2], "%Y-%m-%d").date()
    except Exception:
        return False

def extend_shop(owner_id, days):
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("SELECT expire_date FROM shops WHERE owner_id=?", (owner_id,))
    r = cur.fetchone()
    if r and r[0]:
        try:
            base = datetime.strptime(r[0], "%Y-%m-%d")
        except:
            base = datetime.now()
    else:
        base = datetime.now()
    new_exp = (base + timedelta(days=days)).strftime("%Y-%m-%d")
    cur.execute("INSERT OR REPLACE INTO shops(owner_id, shop_name, expire_date, created_at) VALUES(?,?,?,COALESCE((SELECT created_at FROM shops WHERE owner_id=?),?))", (owner_id, get_shop(owner_id)[1] if get_shop(owner_id) else f"Shop{owner_id}", new_exp, owner_id, datetime.now().strftime("%Y-%m-%d")))
    con.commit(); con.close(); return new_exp

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args or []
    if args:
        try:
            sid = int(args[0])
        except:
            await update.message.reply_text("Invalid link.")
            return
        if not is_active(sid):
            await update.message.reply_text("❌ ဆိုင် သက်တမ်းကုန်နေပါသည်။")
            return
        context.user_data["shop"] = sid
        await update.message.reply_text("🏪 ဆိုင်ထဲဝင်ပြီးပါပြီ။ /order ဖြင့်မှာယူပါ။", reply_markup=ReplyKeyboardRemove())
        return
    if uid == ADMIN_ID:
        kb = [["📊 Stats", "📥 Pending"], ["🏬 Shops", "📤 Broadcast"]]
        await update.message.reply_text("Admin Panel", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return
    shop = get_shop(uid)
    if shop:
        kb = [["➕ Add Product", "🛒 My Orders"], ["🔗 My Link", "💳 Subscribe"]]
        await update.message.reply_text(f"Owner Panel: {shop[1]}", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return
    await update.message.reply_text("Welcome! Create a shop: /setup_shop <Name>")

async def setup_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = " ".join(context.args or [])
    if not name:
        await update.message.reply_text("Usage: /setup_shop ShopName")
        return
    exp = (datetime.now() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO shops(owner_id, shop_name, expire_date, created_at) VALUES(?,?,?,?)", (uid, name, exp, datetime.now().strftime("%Y-%m-%d")))
    con.commit(); con.close()
    await update.message.reply_text(f"✅ Shop created: {name}\nTrial until {exp}")

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    shop = get_shop(uid)
    if not shop:
        await update.message.reply_text("Create shop first: /setup_shop")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /add_product name price")
        return
    name = context.args[0]; 
    try:
        price = int(context.args[1])
    except:
        await update.message.reply_text("Price must be number")
        return
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("INSERT INTO products(owner_id, name, price) VALUES(?,?,?)", (uid, name, price))
    con.commit(); con.close()
    await update.message.reply_text("✅ Product added")

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("SELECT id, name, price FROM products WHERE owner_id=?", (uid,))
    rows = cur.fetchall(); con.close()
    if not rows:
        await update.message.reply_text("No products yet.")
        return
    txt = "Products:\n"
    for r in rows:
        txt += f"ID:{r[0]} • {r[1]} • {r[2]} MMK\n"
    await update.message.reply_text(txt)

# minimal order flow (open shop link first)
async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "shop" not in context.user_data:
        await update.message.reply_text("Open shop link first: /start <shop_id>")
        return ConversationHandler.END
    context.user_data["cart"] = []; context.user_data["total"] = 0
    await update.message.reply_text("Name:", reply_markup=ReplyKeyboardRemove())
    return ORDER_NAME

async def order_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_name"] = update.message.text.strip(); await update.message.reply_text("Phone:"); return ORDER_PHONE

async def order_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_phone"] = update.message.text.strip(); await update.message.reply_text("Address:"); return ORDER_ADDR

async def order_addr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_addr"] = update.message.text.strip()
    sid = context.user_data["shop"]
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("SELECT name, price FROM products WHERE owner_id=?", (sid,))
    rows = cur.fetchall(); con.close()
    kb = []
    for n,p in rows: kb.append([f"{n}:{p}"])
    kb.append(["🛒 View Cart", "✅ Checkout"])
    await update.message.reply_text("Choose:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ORDER_SHOP

async def order_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if t == "🛒 View Cart":
        await update.message.reply_text(f"Cart: {', '.join(context.user_data.get('cart',[]))}\nTotal: {context.user_data.get('total',0)}")
        return ORDER_SHOP
    if t == "✅ Checkout":
        await update.message.reply_text("Send payment screenshot (WavePay/KBZ) or /cancel", reply_markup=ReplyKeyboardRemove()); return ORDER_PAY
    if ":" in t:
        try:
            name,price = t.split(":",1); price=int(price.strip())
            context.user_data.setdefault("cart",[]).append(f"{name.strip()}:{price}"); context.user_data["total"]=context.user_data.get("total",0)+price
            await update.message.reply_text(f"Added {name} - {price} MMK")
        except:
            await update.message.reply_text("Format must be name:price")
    return ORDER_SHOP

async def order_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Send photo of payment.")
        return ORDER_PAY
    file = await update.message.photo[-1].get_file()
    fn = f"{PHOTOS_DIR}/pay_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
    await file.download_to_drive(fn)
    sid = context.user_data["shop"]; uid = update.effective_user.id
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("INSERT INTO orders(shop_id, user_id, name, phone, address, items, total, photo_path, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (sid, uid, context.user_data.get("cust_name"), context.user_data.get("cust_phone"), context.user_data.get("cust_addr"), ",".join(context.user_data.get("cart",[])), context.user_data.get("total",0), fn, "Pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    oid = cur.lastrowid
    con.commit(); con.close()
    # insert payment record
    con = sqlite3.connect(DB_FILE); cur = con.cursor(); cur.execute("INSERT INTO payments(uid, kind, ref_id, photo_path, status, created_at) VALUES(?,?,?,?,?,?)", (uid, "order", oid, fn, "pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))); con.commit(); con.close()
    await update.message.reply_text("✅ Order submitted, waiting for owner confirmation.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# admin callback simplified
async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = q.data or ""
    await q.edit_message_text("Processed by admin.")

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    order_conv = ConversationHandler(entry_points=[CommandHandler("order", order_start)],
                                     states={ORDER_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, order_name)],
                                             ORDER_PHONE:[MessageHandler(filters.TEXT & ~filters.COMMAND, order_phone)],
                                             ORDER_ADDR:[MessageHandler(filters.TEXT & ~filters.COMMAND, order_addr)],
                                             ORDER_SHOP:[MessageHandler(filters.TEXT & ~filters.COMMAND, order_shop)],
                                             ORDER_PAY:[MessageHandler(filters.PHOTO, order_pay), MessageHandler(filters.TEXT & ~filters.COMMAND, order_pay)]
                                             },
                                     fallbacks=[CommandHandler("cancel", cancel)]
                                     )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup_shop", setup_shop))
    app.add_handler(CommandHandler("add_product", add_product))
    app.add_handler(CommandHandler("list_products", list_products))
    app.add_handler(order_conv)
    app.add_handler(CallbackQueryHandler(admin_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("Command not recognized. Use /help")))

    print("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
