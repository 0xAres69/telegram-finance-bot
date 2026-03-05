import io
import sqlite3
from datetime import datetime

import matplotlib.pyplot as plt
import pandas as pd
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = "8646960371:AAEu5qYIUmN60YVND5wfjYfT6IMEQeMjnFg"  # <-- regenerate in BotFather

DB_PATH = "database.db"
USER_STATE = {}  # chat_id -> "expense" or "income"


# -------------------- DB helpers --------------------
def db_conn():
    return sqlite3.connect(DB_PATH)


def ensure_transactions_schema():
    """Add chat_id column if it doesn't exist (safe to run every startup)."""
    conn = db_conn()
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(transactions)")
    cols = [row[1] for row in cur.fetchall()]

    if "chat_id" not in cols:
        cur.execute("ALTER TABLE transactions ADD COLUMN chat_id INTEGER")
        conn.commit()

    conn.close()


# -------------------- Categories (auto-detect) --------------------
CATEGORY_RULES = {
    "food": ["coffee", "pizza", "lunch", "dinner", "burger", "restaurant"],
    "transport": ["uber", "taxi", "bus", "metro", "train", "fuel", "gas"],
    "shopping": ["amazon", "clothes", "shoes"],
    "bills": ["rent", "electric", "water", "internet", "phone", "netflix"],
}


def guess_category(raw: str) -> str:
    x = raw.lower()
    for cat, words in CATEGORY_RULES.items():
        if x in words:
            return cat
    return raw


# -------------------- UI (buttons) --------------------
def main_menu():
    keyboard = [
        ["➕ Expense", "💰 Income"],
        ["📊 Summary", "📅 Month"],
        ["🧾 Categories", "📁 Export"],
        ["🥧 Pie Chart", "📈 Trend Chart"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# -------------------- Commands --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Finance Tracker ready!\n"
        "Use buttons below or type like: coffee 3",
        reply_markup=main_menu(),
    )


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = db_conn()
    cur = conn.cursor()

    cur.execute("SELECT SUM(amount) FROM transactions WHERE type='expense' AND chat_id=?", (chat_id,))
    expenses = cur.fetchone()[0] or 0

    cur.execute("SELECT SUM(amount) FROM transactions WHERE type='income' AND chat_id=?", (chat_id,))
    income = cur.fetchone()[0] or 0

    conn.close()

    balance = income - expenses
    message = (
        f"📊 Summary\n\n"
        f"Income: {income}\n"
        f"Expenses: {expenses}\n"
        f"Balance: {balance}"
    )
    await update.message.reply_text(message)


async def categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = db_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT category, SUM(amount)
        FROM transactions
        WHERE type='expense' AND chat_id=?
        GROUP BY category
        ORDER BY SUM(amount) DESC
        """,
        (chat_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No expenses yet.")
        return

    msg = "🧾 Spending by Category\n\n"
    for cat, total in rows:
        msg += f"{cat}: {total}\n"

    await update.message.reply_text(msg)


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = db_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT SUM(amount)
        FROM transactions
        WHERE type='expense'
          AND chat_id=?
          AND date >= date('now','start of month')
        """,
        (chat_id,),
    )
    total = cur.fetchone()[0] or 0
    conn.close()

    await update.message.reply_text(f"📅 This month's spending: {total}")


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = db_conn()
    df = pd.read_sql_query("SELECT * FROM transactions WHERE chat_id = ?", conn, params=(chat_id,))
    conn.close()

    filename = "expenses.xlsx"
    df.to_excel(filename, index=False)
    await update.message.reply_document(document=open(filename, "rb"))


# -------------------- Charts --------------------
async def chartpie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT category, SUM(amount)
        FROM transactions
        WHERE type='expense'
          AND chat_id=?
          AND date >= date('now','start of month')
        GROUP BY category
        ORDER BY SUM(amount) DESC
        """,
        (chat_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No expenses this month to chart.")
        return

    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]

    plt.figure()
    plt.pie(values, labels=labels, autopct="%1.0f%%")
    plt.title("This Month: Expense Breakdown")

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close()
    buf.seek(0)

    await update.message.reply_photo(photo=buf)


async def charttrend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date, SUM(amount)
        FROM transactions
        WHERE type='expense'
          AND chat_id=?
          AND date >= date('now','-30 day')
        GROUP BY date
        ORDER BY date ASC
        """,
        (chat_id,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No expenses in the last 30 days to chart.")
        return

    dates = [r[0] for r in rows]
    totals = [r[1] for r in rows]

    plt.figure()
    plt.plot(dates, totals, marker="o")
    plt.title("Last 30 Days: Daily Spending")
    plt.xlabel("Date")
    plt.ylabel("Total")
    plt.xticks(rotation=45)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close()
    buf.seek(0)

    await update.message.reply_photo(photo=buf)


# -------------------- Button flows + logging --------------------
async def expense_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    USER_STATE[chat_id] = "expense"
    await update.message.reply_text("Type: category amount\nExample: coffee 3")


async def income_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    USER_STATE[chat_id] = "income"
    await update.message.reply_text("Type: source amount\nExample: salary 2000")


async def log_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.split()

    if len(text) != 2:
        await update.message.reply_text("⚠️ Format: category amount\nExample: coffee 3")
        return

    raw_category = text[0]
    category = guess_category(raw_category)

    try:
        amount = float(text[1])
    except:
        await update.message.reply_text("⚠️ Amount must be a number")
        return

    chat_id = update.effective_chat.id
    forced = USER_STATE.get(chat_id)

    if forced in ["expense", "income"]:
        ttype = forced
        USER_STATE[chat_id] = None
    else:
        if raw_category.lower() in ["salary", "income", "pay"]:
            ttype = "income"
        else:
            ttype = "expense"

    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (type, category, amount, date, chat_id) VALUES (?, ?, ?, ?, ?)",
        (ttype, category, amount, datetime.now().strftime("%Y-%m-%d"), chat_id),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ {ttype.capitalize()} saved! ({category}: {amount})")


# -------------------- Edit / delete --------------------
async def last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, type, category, amount, date
        FROM transactions
        WHERE chat_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (chat_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        await update.message.reply_text("No transactions yet.")
        return

    tid, ttype, cat, amt, dt = row
    await update.message.reply_text(
        f"🧾 Last transaction:\n"
        f"ID: {tid}\nType: {ttype}\nCategory: {cat}\nAmount: {amt}\nDate: {dt}\n\n"
        f"Undo last: /undo\nDelete by id: /delete {tid}\nEdit amount: /edit {tid} {amt}"
    )


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, type, category, amount, date
        FROM transactions
        WHERE chat_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (chat_id,),
    )
    row = cur.fetchone()

    if not row:
        conn.close()
        await update.message.reply_text("Nothing to undo.")
        return

    tid, ttype, cat, amt, dt = row
    cur.execute("DELETE FROM transactions WHERE id=? AND chat_id=?", (tid, chat_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Undone. Deleted #{tid} ({ttype}, {cat}, {amt}, {dt})")


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split()
    if len(parts) != 2:
        await update.message.reply_text("Usage: /delete <id>\nExample: /delete 12")
        return

    chat_id = update.effective_chat.id

    try:
        tid = int(parts[1])
    except:
        await update.message.reply_text("ID must be a number.")
        return

    conn = db_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM transactions WHERE id=? AND chat_id=?",
        (tid, chat_id),
    )
    row = cur.fetchone()

    if not row:
        conn.close()
        await update.message.reply_text("Transaction not found (or not yours).")
        return

    cur.execute("DELETE FROM transactions WHERE id=? AND chat_id=?", (tid, chat_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Deleted transaction #{tid}.")


async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.split()
    if len(parts) != 3:
        await update.message.reply_text("Usage: /edit <id> <new_amount>\nExample: /edit 12 9.5")
        return

    chat_id = update.effective_chat.id

    try:
        tid = int(parts[1])
        new_amount = float(parts[2])
    except:
        await update.message.reply_text("ID must be integer and amount must be a number.")
        return

    conn = db_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM transactions WHERE id=? AND chat_id=?",
        (tid, chat_id),
    )
    row = cur.fetchone()

    if not row:
        conn.close()
        await update.message.reply_text("Transaction not found (or not yours).")
        return

    cur.execute(
        "UPDATE transactions SET amount=? WHERE id=? AND chat_id=?",
        (new_amount, tid, chat_id),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Updated #{tid} amount to {new_amount}.")


# -------------------- Router --------------------
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()

    if txt == "➕ Expense":
        await expense_button(update, context)
        return
    if txt == "💰 Income":
        await income_button(update, context)
        return
    if txt == "📊 Summary":
        await summary(update, context)
        return
    if txt == "📅 Month":
        await month(update, context)
        return
    if txt == "🧾 Categories":
        await categories(update, context)
        return
    if txt == "📁 Export":
        await export(update, context)
        return
    if txt == "🥧 Pie Chart":
        await chartpie(update, context)
        return
    if txt == "📈 Trend Chart":
        await charttrend(update, context)
        return

    await log_transaction(update, context)


# -------------------- Run --------------------
def main():
    ensure_transactions_schema()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("categories", categories))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(CommandHandler("export", export))
    app.add_handler(CommandHandler("chartpie", chartpie))
    app.add_handler(CommandHandler("charttrend", charttrend))

    app.add_handler(CommandHandler("last", last))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("edit", edit))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.run_polling()


if __name__ == "__main__":
    main()