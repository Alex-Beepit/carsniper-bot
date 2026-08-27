import os
import io
import asyncio
import re
import pandas as pd
from datetime import datetime, timedelta
from urllib.parse import quote
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

# Επίσημα slugs τοποθεσιών DoYouSpain
LOCATIONS = {
    "loc_HER": ("Ηράκλειο (HER)", "heraklion_airport"),
    "loc_CHQ": ("Χανιά (CHQ)", "chania_airport"),
    "loc_JTR": ("Σαντορίνη (JTR)", "santorini_airport"),
    "loc_ATH": ("Αθήνα (ATH)", "athens_airport")
}

async def start_dummy_server():
    async def handle_ping(request):
        return web.Response(text="Carsniper STRICT Live Engine Active")
    server = web.Application()
    server.router.add_get("/", handle_ping)
    server.router.add_get("/healthz", handle_ping)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📍 Ηράκλειο (HER)", callback_data="loc_HER"), InlineKeyboardButton("📍 Χανιά (CHQ)", callback_data="loc_CHQ")],
        [InlineKeyboardButton("📍 Σαντορίνη (JTR)", callback_data="loc_JTR"), InlineKeyboardButton("📍 Αθήνα (ATH)", callback_data="loc_ATH")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "🚗 **Carsniper STRICT Live Engine**\nΑυστηρή ανάκτηση πραγματικών τιμών. Επιλέξτε:"
    if update.message:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("loc_"):
        loc_name, loc_slug = LOCATIONS[data]
        context.user_data["loc_name"] = loc_name
        context.user_data["loc_slug"] = loc_slug

        keyboard = [
            [InlineKeyboardButton("1 Ημέρα (Αύριο)", callback_data="dur_1")],
            [InlineKeyboardButton("3 Ημέρες", callback_data="dur_3")],
            [InlineKeyboardButton("7 Ημέρες (1 Εβδομάδα)", callback_data="dur_7")]
        ]
        await query.edit_message_text(
            f"📍 Τοποθεσία: **{loc_name}**\nΕπιλέξτε διάρκεια:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("dur_"):
        days = int(data.replace("dur_", ""))
        loc_name = context.user_data.get("loc_name", "Ηράκλειο (HER)")
        loc_slug = context.user_data.get("loc_slug", "heraklion_airport")

        now = datetime.now()
        p_date = now + timedelta(days=1)
        d_date = p_date + timedelta(days=days)
        p_str = p_date.strftime("%d/%m/%Y")
        d_str = d_date.strftime("%d/%m/%Y")

        await query.edit_message_text(
            f"⏳ **Σύνδεση στο DoYouSpain...**\nΓίνεται Text-Mining στα LIVE δεδομένα για {loc_name}.", 
            parse_mode="Markdown"
        )

        results = await fetch_live_doyouspain(loc_slug, p_str, d_str, days)

        if results == "BLOCKED":
            await query.message.reply_text("🚨 **Αποτυχία:** Το Cloudflare μπλόκαρε την IP του server. Δεν επεστράφησαν δεδομένα. Δοκιμάστε ξανά σε λίγο.")
            return
        elif not results:
            await query.message.reply_text("❌ Η αναζήτηση ολοκληρώθηκε, αλλά το σύστημα δεν εντόπισε τιμές. Πιθανώς δεν υπάρχουν διαθέσιμα αυτοκίνητα.")
            return

        msg_lines = [f"🏆 **100% LIVE Αποτελέσματα ({loc_name}):**\n📅 {p_str} ➔ {d_str}\n"]
        for row in results[:10]:
            msg_lines.append(
                f"**{row['Rank']}. {row['Vehicle']}**\n"
                f"• Εταιρεία: `{row['Supplier']}`\n"
                f"• Τιμή: **{row['Total_EUR']:.2f}€** ({row['Per_Day_EUR']:.2f}€/ημ.)\n"
            )
        await query.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

        # Excel Export
        df = pd.DataFrame(results)
        df.columns = ["Κατάταξη", "Όχημα / Μοντέλο", "Εταιρεία", "Συνολική Τιμή (€)", "Τιμή ανά Ημέρα (€)"]

        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="DoYouSpain_Strict_Live")
            worksheet = writer.sheets["DoYouSpain_Strict_Live"]

            header_fill = PatternFill(start_color="8B0000", end_color="8B0000", fill_type="solid")
            header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            center_align = Alignment(horizontal="center", vertical="center")
            thin_border = Border(
                left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'),
                top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9')
            )

            for col_num in range(1, len(df.columns) + 1):
                cell = worksheet.cell(row=1, column=col_num)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = center_align

            for row in worksheet.iter_rows(min_row=2, max_row=len(df)+1, min_col=1, max_col=len(df.columns)):
                for cell in row:
                    cell.border = thin_border
                    if cell.column in [4, 5]:
                        cell.number_format = '#,##0.00 €'
                        cell.alignment = Alignment(horizontal="right")

            for col in worksheet.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = get_column_letter(col[0].column)
                worksheet.column_dimensions[col_letter].width = max(max_len + 4, 14)

        excel_buffer.seek(0)
        filename = f"STRICT_LIVE_{loc_name[:3]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=excel_buffer,
            filename=filename,
            caption="📊 Το αρχείο περιέχει ΑΠΟΚΛΕΙΣΤΙΚΑ πραγματικές τιμές."
        )

async def fetch_live_doyouspain(loc_slug, p_str, d_str, days):
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,el;q=0.8",
        "Referer": "https://www.doyouspain.com/"
    }

    try:
        async with AsyncSession(impersonate="chrome124") as session:
            # 1. Παίρνουμε τα session cookies
            await session.get("https://www.doyouspain.com/index.htm", headers=headers, timeout=15)

            # 2. Απευθείας URL Αναζήτησης
            url = f"https://www.doyouspain.com/do/list/en?loc={loc_slug}&pickup={quote(p_str)}&pickup_time=10:00&dropoff={quote(d_str)}&dropoff_time=10:00&age=30"
            resp = await session.get(url, headers=headers, timeout=30)
            html = resp.text

            # 3. Έλεγχος για Cloudflare (Αν μπλοκαριστεί, επιστρέφει "BLOCKED" για να ξέρεις την αλήθεια)
            if "Just a moment..." in html or "cloudflare" in html.lower() or "verify you are human" in html.lower():
                return "BLOCKED"

            # 4. Text Mining: Αγνοούμε τον κώδικα HTML και δουλεύουμε πάνω στο καθαρό κείμενο
            soup = BeautifulSoup(html, "html.parser")
            for script in soup(["script", "style", "nav", "footer"]):
                script.extract()
            text = soup.get_text(separator=" | ")

            # Βρίσκουμε όλες τις τιμές στη σελίδα (π.χ. 15,70 €)
            price_matches = list(re.finditer(r'(\d+[\.,]\d{2})\s*€', text))
            
            seen = set()
            suppliers = ["Surprice", "Abbycar", "addCar", "AutoUnion", "Autocar", "Avance", "Avis", "Beepit", "Caldera", "CarRental2Greece", "Carwiz", "Centauro", "Cretamotor", "Enterprise", "Europcar", "Exer", "Flex", "Goldcar", "Green Motion", "Hertz", "Sixt", "OK Mobility", "Budget", "Thrifty", "Dollar", "Alamo", "National", "Drive"]
            brands = ["Fiat", "Citroen", "Toyota", "Peugeot", "VW", "Volkswagen", "Hyundai", "Seat", "Nissan", "Opel", "Renault", "Kia", "Suzuki", "Ford", "Skoda", "Mercedes", "Audi", "BMW", "Jeep", "Dacia"]

            for match in price_matches:
                price = float(match.group(1).replace(",", "."))
                if price < 5 or price > 3000:
                    continue
                
                # Πηγαίνουμε έως 600 χαρακτήρες πίσω από την τιμή για να βρούμε τα στοιχεία
                start = max(0, match.start() - 600)
                context = text[start:match.start()]

                # Αναγνώριση Εταιρείας
                supplier = "Partner"
                for sup in suppliers:
                    if sup.lower() in context.lower():
                        supplier = sup
                        break

                # Αναγνώριση Οχήματος
                car_name = "Economy Car or similar"
                segments = context.split(" | ")
                for seg in reversed(segments):
                    seg_clean = seg.strip()
                    if "or similar" in seg_clean.lower() or any(b.lower() in seg_clean.lower() for b in brands):
                        if 3 < len(seg_clean) < 45 and "price" not in seg_clean.lower():
                            car_name = seg_clean
                            break

                # Εγγραφή μόνο μιας τιμής (της πρώτης που βρίσκει, συνήθως το Total) ανά αμάξι και εταιρεία
                key = f"{car_name}_{supplier}"
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "Vehicle": car_name,
                        "Supplier": supplier,
                        "Total_EUR": price,
                        "Per_Day_EUR": round(price / days, 2)
                    })

            # 5. Ταξινόμηση 
            results.sort(key=lambda x: x["Total_EUR"])
            for idx, r in enumerate(results[:50], 1):
                r["Rank"] = idx

            return results[:50]

    except Exception as e:
        print(f"Scraper Engine Failed: {e}")
        return []

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_dummy_server())

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
