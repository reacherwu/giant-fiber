"""
GiantFiber Brand Identity & Logo Asset Generator
================================================
Generates high-resolution, vector-crisp brand assets:
  1. docs/assets/logo.png (800x800 Master Icon / Avatar)
  2. docs/assets/logo_header.png (Master Header Logo for GitHub README)
"""

import os
import math
from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs", "assets"))
os.makedirs(ASSETS_DIR, exist_ok=True)


def draw_glow_circle(draw, center, radius, color, width=2, steps=6):
    cx, cy = center
    r, g, b = color[:3]
    for i in range(steps, 0, -1):
        alpha = int(35 / i)
        glow_rad = radius + i * 3
        draw.ellipse(
            [cx - glow_rad, cy - glow_rad, cx + glow_rad, cy + glow_rad],
            outline=(r, g, b),
            width=width + i,
        )
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=(r, g, b), width=width)


def generate_square_logo():
    SIZE = 800
    img = Image.new("RGBA", (SIZE, SIZE), color=(11, 15, 25, 255))
    draw = ImageDraw.ImageDraw(img)

    cx, cy = 400, 390

    # 1. Subtle Background Hexagonal Matrix (Drosophila Ommatidia Facets)
    hex_radius = 52
    h_dist = hex_radius * math.sqrt(3)
    v_dist = hex_radius * 1.5

    for row in range(-6, 7):
        for col in range(-6, 7):
            hx = cx + col * h_dist + (row % 2) * (h_dist / 2)
            hy = cy + row * v_dist
            dist_to_center = math.hypot(hx - cx, hy - cy)
            if dist_to_center < 320:
                # Fade alpha with distance from center
                fade = max(0.0, 1.0 - (dist_to_center / 320.0))
                stroke_col = (26, 38, 57, int(70 * fade))
                # Draw small hexagon
                points = []
                for a in range(6):
                    angle = math.radians(60 * a + 30)
                    px = hx + (hex_radius * 0.88) * math.cos(angle)
                    py = hy + (hex_radius * 0.88) * math.sin(angle)
                    points.append((px, py))
                draw.polygon(points, outline=stroke_col)

    # 2. Glowing Concentric Energy Orbitals (Microsecond Temporal Waves)
    for radius, col, w in [
        (280, (0, 229, 255, 40), 2),
        (240, (0, 245, 212, 60), 3),
        (200, (14, 165, 233, 50), 2),
    ]:
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=col, width=w)

    # 3. Outer Hexagonal Shield Frame (Hardware Guardian Motif)
    shield_pts = []
    outer_rad = 220
    for a in range(6):
        angle = math.radians(60 * a - 30)
        px = cx + outer_rad * math.cos(angle)
        py = cy + outer_rad * math.sin(angle)
        shield_pts.append((px, py))
    
    # Outer shield glow
    draw.polygon(shield_pts, outline=(0, 229, 255, 120), width=6)
    draw.polygon(shield_pts, outline=(0, 245, 212, 220), width=3)

    # 4. Giant Fiber Neural Axon Core (Explosive Bio-Electric Escape Path)
    # Powerful stylized flight delta wing + neural lightning impulse
    # Top apex to bottom escape thrust
    p_apex = (cx, cy - 150)
    p_left_wing = (cx - 140, cy + 90)
    p_right_wing = (cx + 140, cy + 90)
    p_inner_center = (cx, cy + 30)
    p_tail_left = (cx - 45, cy + 130)
    p_tail_right = (cx + 45, cy + 130)
    p_tail_notch = (cx, cy + 90)

    # Outer delta wing glow fill
    wing_pts = [p_apex, p_right_wing, p_tail_right, p_tail_notch, p_tail_left, p_left_wing]
    draw.polygon(wing_pts, fill=(15, 30, 50, 230), outline=(0, 229, 255, 255), width=4)

    # Inner neural circuit channels
    # Giant Fiber Left & Right descending paths
    draw.line([(cx, cy - 130), (cx - 70, cy + 40)], fill=(0, 245, 212, 255), width=6)
    draw.line([(cx, cy - 130), (cx + 70, cy + 40)], fill=(0, 245, 212, 255), width=6)
    draw.line([(cx - 70, cy + 40), (cx - 120, cy + 80)], fill=(0, 245, 212, 255), width=5)
    draw.line([(cx + 70, cy + 40), (cx + 120, cy + 80)], fill=(0, 245, 212, 255), width=5)

    # Central explosive synaptic node (Col4 -> Giant Fiber trigger zone)
    draw.line([(cx, cy - 130), (cx, cy + 70)], fill=(255, 82, 82, 255), width=7)
    draw.ellipse([cx - 18, cy - 15 - 18, cx + 18, cy - 15 + 18], fill=(255, 82, 82, 255), outline=(255, 255, 255, 255), width=3)
    draw.ellipse([cx - 8, cy - 15 - 8, cx + 8, cy - 15 + 8], fill=(255, 255, 255, 255))

    # Synaptic terminal buttons (TTMn / DLMn motor outputs)
    for bx, by in [(cx - 120, cy + 80), (cx + 120, cy + 80), (cx, cy + 70)]:
        draw.ellipse([bx - 10, by - 10, bx + 10, by + 10], fill=(0, 245, 212, 255), outline=(255, 255, 255, 255), width=2)

    # 5. Brand Typography at bottom
    # Sleek geometric mark
    draw.text((cx - 135, 680), "GIANTFIBER", fill=(248, 250, 252, 255))
    draw.text((cx - 148, 715), "BIO-REFLEX COPROCESSOR", fill=(0, 229, 255, 220))

    out_file = os.path.join(ASSETS_DIR, "logo.png")
    img.save(out_file)
    print(f"✓ Generated Master Square Logo: {out_file}")


def generate_header_logo():
    """Generates a horizontal lockup banner for GitHub README header."""
    WIDTH, HEIGHT = 980, 240
    img = Image.new("RGBA", (WIDTH, HEIGHT), color=(11, 15, 25, 255))
    draw = ImageDraw.ImageDraw(img)

    # Left Logo Emblem (scaled version)
    cx, cy = 120, 120

    # Hex shield
    shield_pts = []
    outer_rad = 85
    for a in range(6):
        angle = math.radians(60 * a - 30)
        px = cx + outer_rad * math.cos(angle)
        py = cy + outer_rad * math.sin(angle)
        shield_pts.append((px, py))
    draw.polygon(shield_pts, fill=(15, 23, 42, 255), outline=(0, 229, 255, 240), width=3)

    # Delta wing
    p_apex = (cx, cy - 55)
    p_lw = (cx - 50, cy + 35)
    p_rw = (cx + 50, cy + 35)
    p_tl = (cx - 18, cy + 50)
    p_tr = (cx + 18, cy + 50)
    p_tn = (cx, cy + 35)
    draw.polygon([p_apex, p_rw, p_tr, p_tn, p_tl, p_lw], fill=(13, 27, 46, 255), outline=(0, 245, 212, 255), width=2)

    # Synaptic core
    draw.line([(cx, cy - 45), (cx, cy + 25)], fill=(255, 82, 82, 255), width=4)
    draw.ellipse([cx - 8, cy - 10 - 8, cx + 8, cy - 10 + 8], fill=(255, 82, 82, 255), outline=(255, 255, 255, 255), width=2)
    draw.ellipse([cx - 50, cy + 35 - 5, cx - 50 + 10, cy + 35 + 5], fill=(0, 245, 212, 255))
    draw.ellipse([cx + 50 - 5, cy + 35 - 5, cx + 50 + 5, cy + 35 + 5], fill=(0, 245, 212, 255))

    # Right Typography Lockup
    text_x = 240
    # Main Brand Name
    draw.text((text_x, 50), "GIANTFIBER", fill=(248, 250, 252, 255))
    draw.text((text_x + 360, 50), "GF-1", fill=(0, 229, 255, 255))

    # Subtitle
    draw.text((text_x, 105), "Sub-5ms, Sub-1W Bio-Reflex Coprocessor for Autonomous Machines", fill=(203, 213, 225, 255))
    draw.text((text_x, 138), "Drosophila Connectome Prior (FlyWire / MaleCNS) × Jev System-1 Calibrated Decision", fill=(0, 245, 212, 220))
    draw.text((text_x, 170), "Native PX4-Autopilot MAVLink v2 Preemption | Pure Rust Zero-GC Core", fill=(148, 163, 184, 255))

    out_file = os.path.join(ASSETS_DIR, "logo_header.png")
    img.save(out_file)
    print(f"✓ Generated Header Logo: {out_file}")


if __name__ == "__main__":
    generate_square_logo()
    generate_header_logo()
