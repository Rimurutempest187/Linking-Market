#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import Application, CommandHandler

# CONFIG
BOT_TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = int(os.getenv("ADMIN_ID") # 
DB_FILE = "marketlink.db"


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    async def start(update, context):
        await update.message.reply_text("Bot OK ✅")

    app.add_handler(CommandHandler("start", start))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
