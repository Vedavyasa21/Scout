"""Generate crisp, high-resolution graphic assets for Scout matching reference images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

DEST_DIR = Path(__file__).resolve().parent


def generate_compass_icon(output_path: Path, size: int = 512) -> Image.Image:
    """Generate high-res anti-aliased gradient compass arrow inside frosted circular badge."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 28
    # Subtle drop shadow behind badge
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)
    s_draw.ellipse([pad + 6, pad + 12, size - pad - 6, size - pad + 12], fill=(139, 92, 246, 50))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    img.paste(shadow, (0, 0), shadow)

    # Frosted circular disc
    draw.ellipse(
        [pad, pad, size - pad, size - pad],
        fill=(255, 255, 255, 250),
        outline=(226, 232, 240, 255),
        width=4,
    )

    # Inner subtle rim
    draw.ellipse(
        [pad + 8, pad + 8, size - pad - 8, size - pad - 8], outline=(241, 245, 249, 180), width=3
    )

    # Gradient compass arrow pointing ~50° toward top-right
    # High-res coordinates (center = 256, 256)
    cx, cy = size // 2, size // 2

    # Gradient polygon by drawing slices or vertical interpolation
    tip = (cx + 86, cy - 86)
    left_wing = (cx - 78, cy + 46)
    notch = (cx - 18, cy + 18)
    right_wing = (cx + 46, cy + 78)

    # We create a gradient texture for the arrow
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(grad)

    # Draw gradient horizontally/diagonally from left_wing/notch (violet) to tip (cyan)
    for i in range(256):
        t = i / 255.0
        # Color: (168, 85, 247) -> (59, 130, 246) -> (14, 165, 233)
        if t < 0.5:
            f = t / 0.5
            r = int(168 * (1 - f) + 59 * f)
            g = int(85 * (1 - f) + 130 * f)
            b = int(247 * (1 - f) + 246 * f)
        else:
            f = (t - 0.5) / 0.5
            r = int(59 * (1 - f) + 14 * f)
            g = int(130 * (1 - f) + 165 * f)
            b = int(246 * (1 - f) + 233 * f)

        # draw concentric or diagonal gradient bands
        x_start = int(cx - 110 + t * 210)
        g_draw.line([(x_start, cy - 140), (x_start + 140, cy + 140)], fill=(r, g, b, 255), width=3)

    # Mask with the arrow polygon
    mask = Image.new("L", (size, size), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.polygon([tip, left_wing, notch, right_wing], fill=255)

    # Smooth the arrow mask slightly
    mask = mask.filter(ImageFilter.GaussianBlur(1.0))
    arrow_img = Image.composite(grad, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask)

    # Subtle inner wing divide (crease line) for 3D paper plane look
    crease = ImageDraw.Draw(arrow_img)
    crease.line([tip, notch], fill=(255, 255, 255, 120), width=3)

    # Composite arrow on badge
    img.alpha_composite(arrow_img)

    # Downsample with Lanczos for ultra-crisp anti-aliased output
    final_icon = img.resize((256, 256), Image.Resampling.LANCZOS)
    final_icon.save(output_path, "PNG")

    # Also save Windows ICO with all standard sizes
    ico_path = output_path.with_suffix(".ico")
    img.save(
        ico_path,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )

    return final_icon


def generate_glowing_orb(output_path: Path, size: int = 512) -> Image.Image:
    """Generate 3D holographic glowing orb matching Reference Image 3."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    cx, cy = size // 2, size // 2
    radius = int(size * 0.40)

    # 1. Outer soft diffused glow
    outer_glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    og_draw = ImageDraw.Draw(outer_glow)
    og_draw.ellipse(
        [cx - radius - 24, cy - radius - 24, cx + radius + 24, cy + radius + 24],
        fill=(139, 92, 246, 70),
    )
    og_draw.ellipse(
        [cx - radius - 10, cy - radius - 10, cx + radius + 10, cy + radius + 10],
        fill=(59, 130, 246, 120),
    )
    outer_glow = outer_glow.filter(ImageFilter.GaussianBlur(28))
    img.paste(outer_glow, (0, 0), outer_glow)

    # 2. Main 3D Sphere with radial gradient
    sphere = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(sphere)

    # Draw shaded rings from edge inward to simulate 3D lit sphere
    # Light source is top-left: (cx - 30, cy - 35)
    lx, ly = cx - int(radius * 0.28), cy - int(radius * 0.28)

    for r in range(radius, 0, -1):
        t = (radius - r) / float(radius)
        # Gradient colors:
        # Edge: Deep indigo/violet (124, 58, 237)
        # Mid: Electric blue (37, 99, 235)
        # Light: Cyan/Sky (14, 165, 233)
        # Hotspot: Pure soft white (240, 249, 255)
        if t < 0.4:
            f = t / 0.4
            red = int(124 * (1 - f) + 37 * f)
            green = int(58 * (1 - f) + 99 * f)
            blue = int(237 * (1 - f) + 235 * f)
        elif t < 0.8:
            f = (t - 0.4) / 0.4
            red = int(37 * (1 - f) + 14 * f)
            green = int(99 * (1 - f) + 165 * f)
            blue = int(235 * (1 - f) + 233 * f)
        else:
            f = (t - 0.8) / 0.2
            red = int(14 * (1 - f) + 240 * f)
            green = int(165 * (1 - f) + 249 * f)
            blue = int(233 * (1 - f) + 255 * f)

        center_offset_x = int(cx * (1 - t) + lx * t)
        center_offset_y = int(cy * (1 - t) + ly * t)
        s_draw.ellipse(
            [center_offset_x - r, center_offset_y - r, center_offset_x + r, center_offset_y + r],
            fill=(red, green, blue, 255),
        )

    # Mask sphere to exact sharp circle
    sphere_mask = Image.new("L", (size, size), 0)
    sm_draw = ImageDraw.Draw(sphere_mask)
    sm_draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=255)
    sphere_mask = sphere_mask.filter(ImageFilter.GaussianBlur(1.0))
    img.paste(sphere, (0, 0), sphere_mask)

    # 3. Specular rim lighting (crisp thin bright white highlight)
    rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    r_draw = ImageDraw.Draw(rim)
    r_draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius], outline=(255, 255, 255, 220), width=3
    )
    r_draw.ellipse(
        [cx - radius + 2, cy - radius + 2, cx + radius - 2, cy + radius - 2],
        outline=(165, 243, 252, 160),
        width=2,
    )
    img.alpha_composite(rim)

    # Downsample
    final_orb = img.resize((240, 240), Image.Resampling.LANCZOS)
    final_orb.save(output_path, "PNG")
    return final_orb


if __name__ == "__main__":
    icon_p = DEST_DIR / "scout_icon.png"
    orb_p = DEST_DIR / "scout_orb.png"
    generate_compass_icon(icon_p)
    generate_glowing_orb(orb_p)
    print("Assets generated successfully at:", DEST_DIR)
