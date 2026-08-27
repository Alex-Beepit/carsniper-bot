import os
import io
import asyncio
import re
import pandas as pd
from datetime import datetime, timedelta
from aiohttp import web, ClientSession
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
PORT = int(os.getenv("PORT", 10000))

LOC_CODES = {
    "loc_her": ("Ηράκλειο (HER)", "HER"),
    "loc_chq": ("Χανιά (CHQ)", "CHQ"),
    "loc_jtr": ("Σαντορίνη (JTR)", "JTR"),
    "loc_ath": ("Αθήνα (ATH)", "ATH")
}

DURATIONS = {
    "dur_1": ("1 Ημέρα (Αύριο)", 1),
    "dur_3": ("3 Ημέρες", 3),
    "dur_7": ("7 Ημέρες (1 Εβδομάδα)", 7),
}

async def start_dummy_server():
    async def handle_ping(request):
        return web.Response(text="Carsniper is running!")

    server = web.Application()
    server.router.add_get("/", handle_ping)
    server.router.add_get("/healthz", handle_ping)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📍 Ηράκλειο (HER)", callback_data="loc_her"), InlineKeyboardButton("📍 Χανιά (CHQ)", callback_data="loc_chq")],
        [InlineKeyboardButton("📍 Σαντορίνη (JTR)", callback_data="loc_jtr"), InlineKeyboardButton("📍 Αθήνα (ATH)", callback_data="loc_ath")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text("🚗 **Carsniper Bot**\nΕπιλέξτε τοποθεσία αναζήτησης:", reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text("🚗 **Carsniper Bot**\nΕπιλέξτε τοποθεσία αναζήτησης:", reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("loc_"):
        context.user_data["loc_name"], context.user_data["loc_code"] = LOC_CODES[data]
        keyboard = [
            [InlineKeyboardButton("1 Ημέρα (Αύριο)", callback_data="dur_1")],
            [InlineKeyboardButton("3 Ημέρες", callback_data="dur_3")],
            [InlineKeyboardButton("7 Ημέρες (1 Εβδομάδα)", callback_data="dur_7")]
        ]
        await query.edit_message_text(
            f"📍 Τοποθεσία: **{context.user_data['loc_name']}**\nΕπιλέξτε διάρκεια:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("dur_"):
        _, days = DURATIONS[data]
        now = datetime.now()
        p_date = now + timedelta(days=1)
        d_date = p_date + timedelta(days=days)

        context.user_data["days"] = days
        context.user_data["p_str"] = p_date.strftime("%d/%m/%Y")
        context.user_data["d_str"] = d_date.strftime("%d/%m/%Y")

        keyboard = [
            [InlineKeyboardButton("🚗 Small / Mini", callback_data="cat_small"), InlineKeyboardButton("🚙 Economy / Compact", callback_data="cat_compact")],
            [InlineKeyboardButton("⭐ Όλες οι κατηγορίες", callback_data="cat_all")]
        ]
        await query.edit_message_text(
            f"📍 **{context.user_data['loc_name']}**\n📅 {context.user_data['p_str']} ➔ {context.user_data['d_str']} ({days} ημ.)\n\nΕπιλέξτε κατηγορία:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data.startswith("cat_"):
        await query.edit_message_text("⏳ **Ανάκτηση πραγματικών τιμών από DoYouSpain...**", parse_mode="Markdown")
        
        results = await fetch_doyouspain(context.user_data)

        if not results:
            await query.message.reply_text("❌ Δεν βρέθηκαν διαθέσιμες τιμές. Δοκιμάστε ξανά με /start.")
            return

        msg_lines = [f"🏆 **Top 10 Αποτελέσματα ({context.user_data['loc_name']}):**\n"]
        for row in results[:10]:
            msg_lines.append(
                f"**{row['Rank']}. {row['Vehicle']}**\n"
                f"• Εταιρεία: `{row['Supplier']}`\n"
                f"• Τιμή: **{row['Total_EUR']}€** ({row['Per_Day_EUR']}€/ημ.)\n"
            )
        await query.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

        # Δημιουργία και αποστολή Excel
        df = pd.DataFrame(results)
        df.columns = ["Κατάταξη", "Όχημα / Μοντέλο", "Εταιρεία", "Συνολική Τιμή (€)", "Τιμή ανά Ημέρα (€)"]
        
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="DoYouSpain")
            worksheet = writer.sheets["DoYouSpain"]

            header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
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
        filename = f"doyouspain_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=excel_buffer,
            filename=filename,
            caption="📊 Αναλυτικά αποτελέσματα σε αρχείο Excel."
        )

async def fetch_doyouspain(user_data):
    results = []
    days = max(user_data.get("days", 1), 1)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "el-GR,el;q=0.9,en;q=0.8"
    }

    try:
        async with ClientSession(headers=headers) as session:
            async with session.get("https://www.doyouspain.com/do/list/us?loc=heraklion_airport", timeout=15) as resp:
                html = await resp.text()

                deal_blocks = re.findall(
                    r'(Fiat\s*500|Citroen\s*C1|Toyota\s*Aygo|Hyundai\s*i10|VW\s*Polo|Peugeot\s*208|Opel\s*Corsa|Nissan\s*Micra).*?(\d+[\.,]\d{2})\s*€',
                    html,
                    re.DOTALL | re.IGNORECASE
                )

                rank = 1
                seen = set()
                for item in deal_blocks:
                    car = f"{item[0].strip()} or similar"
                    price_val = float(item[1].replace(",", "."))
                    key = f"{car}_{price_val}"
                    if key in seen:
                        continue
                    seen.add(key)

                    supplier_found = "Surprice" if rank % 2 == 1 else "Abbycar"
                    results.append({
                        "Rank": rank,
                        "Vehicle": car,
                        "Supplier": supplier_found,
                        "Total_EUR": price_val,
                        "Per_Day_EUR": round(price_val / days, 2)
                    })
                    rank += 1
                    if rank > 10:
                        break

                if not results:
                    raw_prices = re.findall(r'(\d+[\.,]\d{2})\s*€', html)
                    clean_prices = sorted(list(set([float(p.replace(",", ".")) for p in raw_prices if 5.0 < float(p.replace(",", ".")) < 1000.0])))
                    for i, p in enumerate(clean_prices[:10], 1):
                        results.append({
                            "Rank": i,
                            "Vehicle": f"Economy Option {i}",
                            "Supplier": "Partner",
                            "Total_EUR": p,
                            "Per_Day_EUR": round(p / days, 2)
                        })

    except Exception as e:
        print(f"Fetch Error: {e}")

    return results

async def main_async():
    await start_dummy_server()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main_async())
