from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


OUTPUT = Path(__file__).resolve().parent / "squeeze.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw_icon(size: int) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (16, 17, 17, 255))
    draw = ImageDraw.Draw(image)
    inset = round(24 * scale)
    radius = max(1, round(38 * scale))
    draw.rounded_rectangle((inset, inset, size - inset, size - inset), radius=radius, fill=(23, 25, 24, 255))

    stroke = max(1, round(10 * scale))
    left = round(72 * scale)
    right = round(184 * scale)
    top = round(48 * scale)
    bottom = round(208 * scale)
    draw.rectangle((left, top, right, bottom), outline=(226, 184, 107, 255), width=stroke)

    chevron_width = max(1, round(9 * scale))
    center_x = size // 2
    upper_y = round(105 * scale)
    lower_y = round(151 * scale)
    chevron_gap = round(26 * scale)
    points = [
        (center_x - chevron_gap, upper_y + chevron_gap),
        (center_x, upper_y),
        (center_x + chevron_gap, upper_y + chevron_gap),
        (center_x - chevron_gap, lower_y - chevron_gap),
        (center_x, lower_y),
        (center_x + chevron_gap, lower_y - chevron_gap),
    ]
    draw.line(points[:3], fill=(226, 184, 107, 255), width=chevron_width, joint="curve")
    draw.line(points[3:], fill=(226, 184, 107, 255), width=chevron_width, joint="curve")
    return image


def main() -> None:
    # Pillow creates the Windows icon directory from one high-resolution source.
    # Saving a small first frame with append_images can leave only a 16px icon.
    source = draw_icon(256)
    source.save(OUTPUT, format="ICO", sizes=[(size, size) for size in SIZES])
    print(OUTPUT)


if __name__ == "__main__":
    main()
