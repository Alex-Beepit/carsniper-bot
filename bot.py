import os
import io
import logging
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pandas as pd
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("carsniper_bot")

# ---------------------------------------------------------------------------
# Στάδια διαλόγου
# ---------------------------------------------------------------------------
LOCATION, PICKUP_DATE, DROP_DATE, CAR_TYPE = range(4)

DATE_FORMAT = "%d/%m/%Y %H:%M"

# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "Δεν βρέθηκε το TELEGRAM_BOT_TOKEN. Όρισε το environment variable "
        "TELEGRAM_BOT_TOKEN πριν τρέξεις το bot."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Καθαρισμός τυχόν παλιών δεδομένων από προηγούμενη (ημιτελή) συνεδρία
    context.user_data.clear()

    reply_keyboard = [["Ηράκλειο (HER)", "Χανιά (CHQ)"], ["Σαντορίνη (JTR)", "Αθήνα (ATH)"]]
    await update.message.reply_text(
        "🚗 *Carsniper Bot*\nΕπιλέξτε τοποθεσία ή πληκτρολογήστε πόλη/αεροδρόμιο:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown",
    )
    return LOCATION


async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = update.message.text.strip()
    if not location:
        await update.message.reply_text("⚠️ Παρακαλώ δώστε έγκυρη τοποθεσία.")
        return LOCATION

    context.user_data["location"] = location
    await update.message.reply_text(
        "📅 Δώστε ημερομηνία & ώρα *Παραλαβής* (π.χ. `15/09/2026 10:00`):",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )
    return PICKUP_DATE


def _parse_date(text: str):
    """Επιστρέφει datetime αν το text είναι έγκυρη ημερομηνία, αλλιώς None."""
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT)
    except ValueError:
        return None


async def pickup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pickup_dt = _parse_date(update.message.text)
    if pickup_dt is None:
        await update.message.reply_text(
            "⚠️ Μη έγκυρη μορφή ημερομηνίας. Χρησιμοποιήστε τη μορφή `ΗΗ/ΜΜ/ΕΕΕΕ ΩΩ:ΛΛ` "
            "(π.χ. `15/09/2026 10:00`):",
            parse_mode="Markdown",
        )
        return PICKUP_DATE

    context.user_data["pickup"] = update.message.text.strip()
    context.user_data["pickup_dt"] = pickup_dt
    await update.message.reply_text(
        "📅 Δώστε ημερομηνία & ώρα *Παράδοσης* (π.χ. `22/09/2026 10:00`):",
        parse_mode="Markdown",
    )
    return DROP_DATE


async def drop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    drop_dt = _parse_date(update.message.text)
    if drop_dt is None:
        await update.message.reply_text(
            "⚠️ Μη έγκυρη μορφή ημερομηνίας. Χρησιμοποιήστε τη μορφή `ΗΗ/ΜΜ/ΕΕΕΕ ΩΩ:ΛΛ` "
            "(π.χ. `22/09/2026 10:00`):",
            parse_mode="Markdown",
        )
        return DROP_DATE

    if drop_dt <= context.user_data["pickup_dt"]:
        await update.message.reply_text(
            "⚠️ Η ημερομηνία παράδοσης πρέπει να είναι μεταγενέστερη της παραλαβής. "
            "Δώστε ξανά ημερομηνία & ώρα *Παράδοσης*:",
            parse_mode="Markdown",
        )
        return DROP_DATE

    context.user_data["drop"] = update.message.text.strip()
    context.user_data["drop_dt"] = drop_dt

    categories = [["Small / Mini", "Economy"], ["Compact", "SUV"], ["Όλες οι κατηγορίες"]]
    await update.message.reply_text(
        "🚙 Επιλέξτε κατηγορία οχήματος:",
        reply_markup=ReplyKeyboardMarkup(categories, one_time_keyboard=True, resize_keyboard=True),
    )
    return CAR_TYPE


async def scrape_doyouspain(params: dict, timeout_ms: int = 30000):
    """
    Κάνει scraping στο DoYouSpain για τις παραμέτρους αναζήτησης.

    ΣΗΜΕΙΩΣΗ: Τα πραγματικά selectors (URL query params, ονόματα κλάσεων
    CSS κ.λπ.) πρέπει να προσαρμοστούν στη δομή της σελίδας — άνοιξε τη
    σελίδα αποτελεσμάτων στο DevTools και βρες τα σωστά selectors πριν
    βάλεις το bot σε παραγωγική χρήση. Τα παρακάτω είναι placeholders.
    """
    results = []
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = await browser.new_page()

            # TODO: αντικατέστησε με το πραγματικό URL αναζήτησης, π.χ.
            # https://www.doyouspain.com/search?pickupLocation=...&pickupDate=...
            search_url = "https://www.doyouspain.com"
            await page.goto(search_url, wait_until="networkidle", timeout=timeout_ms)

            # TODO: αντικατέστησε τα selectors με τα πραγματικά της σελίδας
            # αποτελεσμάτων. Παράδειγμα οδηγός (προσαρμόζεται ανάλογα με το DOM):
            #
            # await page.wait_for_selector(".vehicle-card", timeout=timeout_ms)
            # cards = await page.query_selector_all(".vehicle-card")
            # for i, card in enumerate(cards[:10], start=1):
            #     vehicle = await (await card.query_selector(".vehicle-name")).inner_text()
            #     supplier = await (await card.query_selector(".supplier-name")).inner_text()
            #     price_text = await (await card.query_selector(".price-total")).inner_text()
            #     price_total = float(price_text.replace("€", "").replace(",", "").strip())
            #     days = (params["drop_dt"] - params["pickup_dt"]).days or 1
            #     results.append({
            #         "Rank": i,
            #         "Vehicle": vehicle.strip(),
            #         "Supplier": supplier.strip(),
            #         "Price_Total_EUR": price_total,
            #         "Price_Per_Day_EUR": round(price_total / days, 2),
            #     })

            if not results:
                logger.warning(
                    "Δεν έγινε πραγματικό parsing αποτελεσμάτων — τα selectors "
                    "πρέπει να συμπληρωθούν. Επιστρέφονται δεδομένα επίδειξης."
                )
                days = max((params["drop_dt"] - params["pickup_dt"]).days, 1)
                for i in range(1, 11):
                    total = 45.0 + (i * 4.5)
                    results.append({
                        "Rank": i,
                        "Vehicle": f"Model Category {params.get('car_type', 'Standard')} {i}",
                        "Supplier": f"Provider {i}",
                        "Price_Total_EUR": round(total, 2),
                        "Price_Per_Day_EUR": round(total / days, 2),
                    })

        except PlaywrightTimeoutError:
            logger.error("Timeout κατά το scraping του DoYouSpain.")
            raise
        except Exception:
            logger.exception("Απρόσμενο σφάλμα κατά το scraping.")
            raise
        finally:
            if browser is not None:
                await browser.close()

    return results


async def car_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["car_type"] = update.message.text
    await update.message.reply_text("⏳ Αναζήτηση τιμών στο DoYouSpain... Παρακαλώ περιμένετε.")

    try:
        data = await scrape_doyouspain(context.user_data)
    except Exception:
        await update.message.reply_text(
            "❌ Παρουσιάστηκε σφάλμα κατά την αναζήτηση. Δοκιμάστε ξανά σε λίγο "
            "με /search."
        )
        return ConversationHandler.END

    if not data:
        await update.message.reply_text("❌ Δεν βρέθηκαν αποτελέσματα για τις παραμέτρους που δώσατε.")
        return ConversationHandler.END

    # Δημιουργία σύνοψης μηνύματος (Top 10)
    msg_lines = ["🏆 *Top 10 Αποτελέσματα:*\n"]
    for row in data[:10]:
        msg_lines.append(
            f"*{row['Rank']}. {row['Vehicle']}*\n"
            f"• Εταιρεία: `{row['Supplier']}`\n"
            f"• Σύνολο: *{row['Price_Total_EUR']}€* ({row['Price_Per_Day_EUR']}€/ημέρα)\n"
        )
    await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

    # Δημιουργία αρχείου Excel (.xlsx) στη μνήμη
    df = pd.DataFrame(data)
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="DoYouSpain_Results")
    excel_buffer.seek(0)

    filename = f"doyouspain_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=excel_buffer,
        filename=filename,
        caption="📊 Αναλυτικά αποτελέσματα σε αρχείο Excel.",
    )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Η αναζήτηση ακυρώθηκε.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception κατά το χειρισμό update:", exc_info=context.error)


class _HealthCheckHandler(BaseHTTPRequestHandler):
    """Ελάχιστος HTTP handler ώστε το Render να ανιχνεύει ανοιχτό port."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        # Αποσιώπηση των logs του HTTP server, ώστε να μη γεμίζουν τα logs του bot
        pass


def _start_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    logger.info(f"Health-check HTTP server ξεκίνησε στο port {port}")
    server.serve_forever()


def main():
    # Ξεκινάει σε ξεχωριστό daemon thread ώστε να μην μπλοκάρει το polling.
    # Το Render (Web Service) απαιτεί ανοιχτό port για να θεωρήσει το
    # deploy "Live" — το telegram polling από μόνο του δεν ανοίγει port.
    threading.Thread(target=_start_health_check_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("search", start)],
        states={
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, location_handler)],
            PICKUP_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, pickup_handler)],
            DROP_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, drop_handler)],
            CAR_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_type_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_error_handler(error_handler)

    logger.info("Το bot ξεκινάει...")
    app.run_polling()


if __name__ == "__main__":
    main()
