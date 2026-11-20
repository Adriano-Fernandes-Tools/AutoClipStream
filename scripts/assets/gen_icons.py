"""Generate AutoClipStream logo assets using Pillow only (no cairo required).

Produces:
  logo_512.png  — master PNG
  logo_256.png
  logo_128.png
  logo_64.png
  logo_32.png
  logo_16.png
  favicon.ico   — multi-size ICO (256, 128, 64, 32, 16)

Run:  py gen_icons.py
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent


# ---------------------------------------------------------------------------
# Colour palette (matches the SVG / app theme)
# ---------------------------------------------------------------------------
BG_OUTER    = (13,  15,  30)   # #0d0f1e
BG_INNER    = (30,  27,  75)   # #1e1b4b
ACCENT_LO   = (124, 92,  255)  # #7c3aed (violet)
ACCENT_HI   = (196, 181, 253)  # #c4b5fd (lavender)
PIVOT_RING  = (167, 139, 250)  # #a78bfa
REC_RED     = (239, 68,  68)   # #ef4444
REC_LIGHT   = (252, 165, 165)  # #fca5a5
WHITE       = (255, 255, 255)


def lerp_color(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def radial_bg(img: Image.Image, cx: int, cy: int, r: int) -> None:
    """Paint a radial gradient background on img."""
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            t = min(dist / r, 1.0)
            r_, g_, b_ = lerp_color(BG_INNER, BG_OUTER, t)
            # keep existing alpha
            px[x, y] = (r_, g_, b_, px[x, y][3])


def draw_line_aa(draw: ImageDraw.ImageDraw, x0, y0, x1, y1, width, color):
    """Draw a thick anti-aliased line using ellipse caps."""
    draw.line([(x0, y0), (x1, y1)], fill=color, width=width)
    r = width // 2
    draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=color)
    draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=color)


def render_logo(size: int) -> Image.Image:
    S = size
    scale = S / 512.0

    # Work at 2x for better anti-aliasing, then downsample
    W = S * 2
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    sc = scale * 2  # drawing scale

    # --- Rounded-rect background (clipping mask) ---
    mask = Image.new("L", (W, W), 0)
    md = ImageDraw.Draw(mask)
    rx = int(96 * sc)
    md.rounded_rectangle([0, 0, W - 1, W - 1], radius=rx, fill=255)

    # --- Radial gradient background ---
    bg = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    # fill solid base, then we'll overlay a vignette
    bg_draw.rectangle([0, 0, W, W], fill=(*BG_INNER, 255))

    # simulate radial gradient with concentric bands (fast enough for icon sizes)
    cx = cy = W // 2
    num_bands = 60
    for i in range(num_bands, -1, -1):
        t = i / num_bands
        r_color = lerp_color(BG_INNER, BG_OUTER, t)
        radius = int(W * 0.72 * t)
        bg_draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=(*r_color, 255),
        )

    bg.putalpha(mask)
    img.alpha_composite(bg)

    draw = ImageDraw.Draw(img)

    # --- Scissor geometry ---
    px_cx = int(256 * sc)
    px_cy = int(256 * sc)

    # blade width proportional to size
    bw = max(2, int(20 * sc))

    # Accent colour for blades (simple gradient via two layers)
    blade_lo = (*ACCENT_LO, 240)
    blade_hi = (*ACCENT_HI, 240)

    def blade_color(t: float):
        c = lerp_color(ACCENT_HI, ACCENT_LO, t)
        return (*c, 240)

    # -- Blade 1: top-left arm (pivot → handle) --
    hx1, hy1 = int(136 * sc), int(134 * sc)
    draw_line_aa(draw, px_cx, int(248 * sc), hx1, hy1, bw, blade_color(0.0))
    # Handle ring 1
    r_ring = int(38 * sc)
    rw = max(2, int(20 * sc))
    draw.ellipse(
        [hx1 - r_ring, hy1 - r_ring, hx1 + r_ring, hy1 + r_ring],
        outline=blade_color(0.1), width=rw,
    )

    # -- Blade 2: bottom-left arm --
    hx2, hy2 = int(136 * sc), int(378 * sc)
    draw_line_aa(draw, px_cx, int(264 * sc), hx2, hy2, bw, blade_color(0.0))
    draw.ellipse(
        [hx2 - r_ring, hy2 - r_ring, hx2 + r_ring, hy2 + r_ring],
        outline=blade_color(0.1), width=rw,
    )

    # -- Open blades going right (slightly transparent) --
    bw_open = max(1, int(18 * sc))
    open_alpha = int(0.65 * 255)
    open_color = (*lerp_color(ACCENT_HI, ACCENT_LO, 0.4), open_alpha)
    draw_line_aa(draw, px_cx, int(248 * sc), int(390 * sc), int(200 * sc), bw_open, open_color)
    draw_line_aa(draw, px_cx, int(264 * sc), int(390 * sc), int(312 * sc), bw_open, open_color)

    # -- Pivot ring --
    pr = int(22 * sc)
    pr_inner = int(13 * sc)
    pr_core = int(6 * sc)
    # outer glow (blurred in a separate layer)
    glow_layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.ellipse(
        [px_cx - pr, px_cy - pr, px_cx + pr, px_cy + pr],
        fill=(*PIVOT_RING, 220),
    )
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(int(6 * sc)))
    img.alpha_composite(glow_layer)
    # solid rings
    draw.ellipse([px_cx - pr, px_cy - pr, px_cx + pr, px_cy + pr], fill=(*PIVOT_RING, 255))
    draw.ellipse([px_cx - pr_inner, px_cy - pr_inner, px_cx + pr_inner, px_cy + pr_inner], fill=(*BG_OUTER, 255))
    draw.ellipse([px_cx - pr_core, px_cy - pr_core, px_cx + pr_core, px_cy + pr_core], fill=(*PIVOT_RING, 255))

    # -- REC dot (top-right) --
    rdx, rdy = int(380 * sc), int(150 * sc)
    rd_r = int(22 * sc)
    rd_inner = int(13 * sc)
    # glow
    rec_glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    rgd = ImageDraw.Draw(rec_glow)
    rgd.ellipse([rdx - rd_r * 2, rdy - rd_r * 2, rdx + rd_r * 2, rdy + rd_r * 2], fill=(*REC_RED, 120))
    rec_glow = rec_glow.filter(ImageFilter.GaussianBlur(int(12 * sc)))
    img.alpha_composite(rec_glow)
    draw.ellipse([rdx - rd_r, rdy - rd_r, rdx + rd_r, rdy + rd_r], fill=(*REC_RED, 230))
    draw.ellipse([rdx - rd_inner, rdy - rd_inner, rdx + rd_inner, rdy + rd_inner], fill=(*REC_LIGHT, 240))

    # -- Inner border ring on background --
    border_col = (*PIVOT_RING, int(0.18 * 255))
    draw.rounded_rectangle([3, 3, W - 4, W - 4], radius=rx - 3, outline=border_col, width=max(1, int(3 * sc)))

    # Downsample 2x → target size
    img = img.resize((S, S), Image.LANCZOS)
    return img


def main():
    sizes = [512, 256, 128, 64, 32, 16]
    images: dict[int, Image.Image] = {}

    for s in sizes:
        print(f"  Rendering {s}x{s}...")
        images[s] = render_logo(s)
        if s == 512:
            out = HERE / "logo.png"
        else:
            out = HERE / f"logo_{s}.png"
        images[s].save(out, "PNG")
        print(f"  Saved {out.name}")

    # ICO: embed 256, 128, 64, 32, 16
    ico_sizes = [256, 128, 64, 32, 16]
    ico_path = HERE / "favicon.ico"
    images[256].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=[images[s] for s in ico_sizes[1:]],
    )
    print(f"  Saved {ico_path.name}  ({', '.join(str(s) for s in ico_sizes)}px)")

    print("\nDone.")


if __name__ == "__main__":
    main()
