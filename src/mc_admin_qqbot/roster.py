import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from math import ceil

from PIL import Image, ImageDraw, ImageFont

if sys.platform == "win32":
    LABEL_FONT = "msyh.ttc"
    TITLE_FONT = "msyhbd.ttc"
    PLAYER_FONT = "consola.ttf"
else:
    LABEL_FONT = TITLE_FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
    PLAYER_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def render_player_list(title: str, server: str, names: list[str]) -> bytes:
    names = sorted(names, key=str.casefold)
    columns = min(4, max(1, ceil(len(names) / 15)))
    rows = max(1, ceil(len(names) / columns))
    title_font = ImageFont.truetype(TITLE_FONT, 54)
    label_font = ImageFont.truetype(LABEL_FONT, 23)
    number_font = ImageFont.truetype(TITLE_FONT, 59)
    player_font = ImageFont.truetype(PLAYER_FONT, 25)

    margin = 24
    gap = 12
    inset = 14
    row_height = 33
    header_bottom = 178
    card_top = header_bottom + 14
    column_header = 46
    caption = f"{server}  ·  按玩家名排序" if names else server
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    count = str(len(names))
    min_width = max(
        title_font.getlength(title) + number_font.getlength(count) + 160,
        label_font.getlength(caption) + label_font.getlength(timestamp) + 128,
    )
    player_width = max(map(player_font.getlength, names), default=0)
    gutters = (columns - 1) * gap + margin * 2
    column_width = max(
        ceil(player_width) + inset * 2, ceil((min_width - gutters) / columns)
    )
    width = columns * column_width + gutters
    height = card_top + column_header + rows * row_height + 12 + margin

    image = Image.new("RGB", (width, height), "#eef2ee")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (margin, margin, width - margin - 1, header_bottom), 19, fill="#173d33"
    )
    draw.text((margin + 29, 48), title, font=title_font, fill="#f7fcf8", anchor="lt")
    draw.text((margin + 30, 125), caption, font=label_font, fill="#c6e2d3", anchor="lt")
    right = width - margin - 30
    draw.text((right - 33, 43), count, font=number_font, fill="#f7fcf8", anchor="rt")
    draw.text((right, 76), "人", font=label_font, fill="#c6e2d3", anchor="rt")
    draw.text((right, 125), timestamp, font=label_font, fill="#c6e2d3", anchor="rt")

    bottom = height - margin - 1
    for index in range(columns):
        column = names[index * rows : (index + 1) * rows]
        left = margin + index * (column_width + gap)
        card_right = left + column_width - 1
        draw.rounded_rectangle(
            (left, card_top, card_right, bottom), 12, fill="#ffffff", outline="#d5dfd8"
        )
        if not column:
            draw.text(
                (width // 2, (card_top + bottom) // 2),
                "暂无记录",
                font=label_font,
                fill="#285c48",
                anchor="mm",
            )
            continue

        first = column[0][0].upper()
        last = column[-1][0].upper()
        span = first if first == last else f"{first} – {last}"
        draw.text(
            (left + inset, card_top + 22),
            span,
            font=label_font,
            fill="#285c48",
            anchor="lm",
        )
        lines_top = card_top + column_header
        draw.line(
            (left + 1, lines_top - 1, card_right - 1, lines_top - 1), fill="#d5dfd8"
        )
        for row, name in enumerate(column):
            top = lines_top + row * row_height
            if row % 2 == 0:
                draw.rectangle(
                    (left + 1, top, card_right - 1, top + row_height - 1),
                    fill="#f4f7f4",
                )
            draw.text(
                (left + inset, top + row_height // 2),
                name,
                font=player_font,
                fill="#172c24",
                anchor="lm",
            )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
