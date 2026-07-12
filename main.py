"❌ الاسترايك والبريميوم يجب أن يكونا أرقامًا.\n"
            "مثال: /p 7555 3.90"
        )

    except FileNotFoundError:
        logger.exception(
            "PUT template was not found"
        )

        await update.message.reply_text(
            "❌ لم أجد ملف put_card.jpg داخل المشروع."
        )

    except Exception as error:
        logger.exception(
            "Failed to publish PUT card"
        )

        await update.message.reply_text(
            "❌ تعذر إنشاء بطاقة PUT.\n"
            f"نوع الخطأ: {type(error).__name__}"
        )


# =========================================================
# حالة البوت
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    if update.message is None:
        return

    call_exists = CALL_TEMPLATE.exists()
    put_exists = PUT_TEMPLATE.exists()

    if call_exists and put_exists:
        message = (
            "🟢 البوت يعمل.\n"
            "✅ قالب CALL موجود.\n"
            "✅ قالب PUT موجود."
        )

    else:
        missing_templates = []

        if not call_exists:
            missing_templates.append(
                "call_card.jpg"
            )

        if not put_exists:
            missing_templates.append(
                "put_card.jpg"
            )

        message = (
            "🟠 البوت يعمل، لكن القوالب التالية غير موجودة:\n"
            + "\n".join(missing_templates)
        )

    await update.message.reply_text(message)


# =========================================================
# معالجة الأخطاء
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:

    logger.error(
        "Telegram update caused an error",
        exc_info=context.error,
    )


# =========================================================
# تشغيل البوت
# =========================================================

def main() -> None:

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler(
            ["c", "call"],
            call_command,
        )
    )

    application.add_handler(
        CommandHandler(
            ["p", "put"],
            put_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Quiet Alpha Card Bot started successfully"
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if name == "__main__":
    main()
