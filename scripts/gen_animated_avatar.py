"""
Генератор анимированной аватарки для канала @benzyn_ryadom.

Создаёт:
  - bot/assets/avatar-channel.gif  (512x512, ~2.5 сек, 12 fps, 30 кадров)
  - bot/assets/avatar-channel.webm (VP9, оптимизированный для TG)
  - bot/assets/avatar-frame.png   (статичная версия для бота)

Анимация:
  1. Капля пульсирует (масштаб 95% ↔ 105%)
  2. Глянцевый блик скользит по капле
  3. Внешний glow дышит
  4. Буква "Б" имеет лёгкий градиентный перелив

Использование:
  python scripts/gen_animated_avatar.py
"""
import math
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from imageio import mimsave


SIZE = 512
CENTER = SIZE // 2
FPS = 12
DURATION_SEC = 2.5
N_FRAMES = int(FPS * DURATION_SEC)


# Палитра (из BRAND.md)
RED_DARK = (200, 16, 46)
RED_LIGHT = (255, 30, 60)
WHITE = (255, 255, 255)
WHITE_DIM = (245, 245, 250)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_droplet_path(scale: float = 1.0) -> list[tuple[float, float]]:
    """Генерирует путь капли (от верхней точки к основанию)."""
    # Параметрическая капля
    points = []
    n = 60
    for i in range(n):
        t = i / n
        # Угол от -PI/2 (верх) до 3*PI/2 (низ)
        angle = -math.pi / 2 + t * 2 * math.pi
        # Радиус: варьируется
        if t < 0.5:
            # Верхняя часть (заострённая)
            r = math.sin(t * math.pi) * 0.6 + 0.1
        else:
            # Нижняя часть (круглая)
            r = math.sin((1 - t) * math.pi) * 0.5 + 0.5
        # Применяем масштаб
        r *= scale
        x = CENTER + r * 130 * math.cos(angle)
        y = CENTER - 30 + r * 150 * math.sin(angle)
        points.append((x, y))
    return points


def draw_frame(frame_idx: int) -> Image.Image:
    """Генерирует один кадр анимации."""
    t = frame_idx / N_FRAMES  # 0..1

    # === Создаём RGBA canvas ===
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    # === 1. Внешний glow (пульсирует) ===
    glow_alpha = int(60 + 30 * math.sin(t * 2 * math.pi))
    glow_size = SIZE - 20
    glow_img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_img, "RGBA")
    glow_draw.ellipse(
        [CENTER - glow_size // 2, CENTER - glow_size // 2 - 10,
         CENTER + glow_size // 2, CENTER + glow_size // 2 - 10],
        fill=(255, 30, 60, glow_alpha),
    )
    glow_img = glow_img.filter(ImageFilter.GaussianBlur(40))
    img = Image.alpha_composite(img, glow_img)
    draw = ImageDraw.Draw(img, "RGBA")

    # === 2. Фон (красный круг со скруглёнными углами) ===
    corner_radius = 100
    # Создаём маску для скруглённого квадрата
    mask = Image.new("L", (SIZE, SIZE), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, SIZE, SIZE], radius=corner_radius, fill=255)
    # Заливаем через маску
    bg_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg_layer, "RGBA")
    # Градиент: красный → тёмно-красный
    for y in range(SIZE):
        ratio = y / SIZE
        color = lerp(RED_LIGHT, RED_DARK, ratio)
        bg_draw.line([(0, y), (SIZE, y)], fill=color)
    # Применяем маску скруглённого квадрата
    rounded = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    rounded.paste(bg_layer, (0, 0), mask)
    img = Image.alpha_composite(img, rounded)
    draw = ImageDraw.Draw(img, "RGBA")

    # === 3. Капля (пульсирует) ===
    scale = 1.0 + 0.05 * math.sin(t * 2 * math.pi * 1.5)
    droplet = make_droplet_path(scale)

    # Сначала тень капли
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow, "RGBA")
    shifted = [(x + 5, y + 10) for x, y in droplet]
    shadow_draw.polygon(shifted, fill=(0, 0, 0, 100))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    img = Image.alpha_composite(img, shadow)

    # Капля с градиентом (белый → серый)
    droplet_img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    droplet_draw = ImageDraw.Draw(droplet_img, "RGBA")
    # Градиент сверху вниз
    min_y = min(p[1] for p in droplet)
    max_y = max(p[1] for p in droplet)
    height = max_y - min_y
    for y in range(int(min_y), int(max_y) + 1):
        ratio = (y - min_y) / height
        color = lerp(WHITE, (220, 220, 230), ratio) + (255,)
        # Горизонтальная линия через все точки капли на этой y
        relevant = [(p[0], p[1]) for p in droplet if abs(p[1] - y) < 1]
        if relevant:
            xs = [p[0] for p in relevant]
            droplet_draw.line([(min(xs), y), (max(xs), y)], fill=color, width=2)
    droplet_draw.polygon(droplet, fill=(255, 255, 255, 240))
    img = Image.alpha_composite(img, droplet_img)
    draw = ImageDraw.Draw(img, "RGBA")

    # === 4. Блик (скользит по капле) ===
    highlight_y = int(min_y + (max_y - min_y) * (0.3 + 0.4 * math.sin(t * 2 * math.pi)))
    for offset in range(-30, 30, 2):
        alpha = max(0, 100 - abs(offset) * 3)
        if alpha > 0:
            draw.ellipse(
                [CENTER - 30 + offset, highlight_y - 8,
                 CENTER + 30 + offset, highlight_y + 8],
                fill=(255, 255, 255, alpha),
            )

    # === 5. Буква "Б" (с лёгкой пульсацией) ===
    # Используем стандартный шрифт
    try:
        # Попробуем системные шрифты
        from PIL import ImageFont
        for font_path in [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ]:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, 230)
                break
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # Пульсация цвета буквы
    letter_color_intensity = 0.7 + 0.3 * math.sin(t * 2 * math.pi * 2)
    letter_color = lerp(RED_DARK, RED_LIGHT, letter_color_intensity)

    # Рисуем букву "Б" (центрируем)
    text = "Б"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = CENTER - text_width // 2 - 30
    text_y = CENTER - text_height // 2 - 30
    # Тень буквы
    draw.text((text_x + 4, text_y + 4), text, fill=(180, 10, 40, 180), font=font)
    # Буква
    draw.text((text_x, text_y), text, fill=letter_color + (255,), font=font)

    return img


def main():
    print("=== Генерация анимированной аватарки ===")
    print(f"Размер: {SIZE}x{SIZE}, FPS: {FPS}, кадров: {N_FRAMES}")

    out_dir = Path("bot/assets")
    out_dir.mkdir(parents=True, exist_ok=True)

    # === Генерируем кадры ===
    frames = []
    for i in range(N_FRAMES):
        frames.append(draw_frame(i))
        print(f"  Кадр {i+1}/{N_FRAMES}", end="\r")
    print()

    # === Сохраняем GIF ===
    gif_path = out_dir / "avatar-channel.gif"
    # Конвертируем в палитру 256 цветов
    frames_p = [f.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT) for f in frames]
    mimsave(gif_path, frames_p, duration=1000 // FPS, loop=0)
    print(f"✓ GIF: {gif_path} ({gif_path.stat().st_size / 1024:.1f} KB)")

    # === Конвертируем в WebM (через ffmpeg) ===
    # Сначала сохраняем кадры в PNG
    tmp_dir = Path("/tmp/avatar_frames")
    tmp_dir.mkdir(exist_ok=True)
    for i, frame in enumerate(frames):
        frame.convert("RGB").save(tmp_dir / f"frame_{i:03d}.png")
    # ffmpeg
    webm_path = out_dir / "avatar-channel.webm"
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", str(tmp_dir / "frame_%03d.png"),
        "-c:v", "libvpx-vp9",
        "-crf", "30",
        "-b:v", "0",
        "-pix_fmt", "yuva420p",
        str(webm_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"✓ WebM: {webm_path} ({webm_path.stat().st_size / 1024:.1f} KB)")
    else:
        print(f"⚠ WebM не создан: {result.stderr[:200]}")
    # Чистим tmp
    for f in tmp_dir.glob("*.png"):
        f.unlink()
    tmp_dir.rmdir()

    # === Сохраняем статичный кадр (frame 0) для бота ===
    static_path = out_dir / "avatar-static.png"
    frames[0].convert("RGB").save(static_path, optimize=True)
    print(f"✓ Static: {static_path} ({static_path.stat().st_size / 1024:.1f} KB)")

    print()
    print("Готово!")
    print(f"  - {gif_path}")
    print(f"  - {webm_path}")
    print(f"  - {static_path}")


if __name__ == "__main__":
    main()
