  def create_signal_card(
    signal_type: str,
    strike: str,
    premium: str,
) -> BytesIO:

    if signal_type == "CALL":
        template_path = CALL_TEMPLATE
        contract = f"{strike}C"
    else:
        template_path = PUT_TEMPLATE
        contract = f"{strike}P"

    if not template_path.exists():
        raise FileNotFoundError(
            f"Template image was not found: {template_path.name}"
        )

    image = Image.open(template_path).convert("RGB")
    draw = ImageDraw.Draw(image)

    width, height = image.size

    # خط أكبر حتى يظهر بوضوح داخل البطاقة
    value_font = load_font(max(48, int(width * 0.050)))
    date_font = load_font(max(38, int(width * 0.038)))

    text_color = (255, 255, 255)
    stroke_color = (0, 0, 0)

    # مواضع الكتابة داخل الخانات الفارغة
    entry_position = (
        int(width * 0.325),
        int(height * 0.397),
    )

    date_position = (
        int(width * 0.790),
        int(height * 0.397),
    )

    premium_position = (
        int(width * 0.325),
        int(height * 0.500),
    )

    draw.text(
        entry_position,
        contract,
        font=value_font,
        fill=text_color,
        anchor="mm",
        stroke_width=2,
        stroke_fill=stroke_color,
    )

    draw.text(
        date_position,
        get_today(),
        font=date_font,
        fill=text_color,
        anchor="mm",
        stroke_width=2,
        stroke_fill=stroke_color,
    )

    draw.text(
        premium_position,
        f"${premium}",
        font=value_font,
        fill=text_color,
        anchor="mm",
        stroke_width=2,
        stroke_fill=stroke_color,
    )

    output = BytesIO()
    output.name = f"quiet_alpha_{signal_type.lower()}.jpg"

    image.save(
        output,
        format="JPEG",
        quality=95,
        optimize=True,
    )

    output.seek(0)
    return output


async def publish_signal(
    context: ContextTypes.DEFAULT_TYPE,
    signal_type: str,
    strike: str,
    premium: str,
) -> None:

    card = create_signal_card(
        signal_type=signal_type,
        strike=strike,
        premium=premium,
    )

    # إرسال الصورة فقط بدون كتابة القيم تحتها
    await context.bot.send_photo(
        chat_id=SIGNAL_CHAT_ID,
        photo=card,
    )
