"""Generate football.ico (a small American-football icon) for the app shortcut.

Run:  python make_icon.py
Produces football.ico with several embedded sizes.
"""

import os

from PIL import Image, ImageDraw

SS = 4                      # supersample factor for smooth edges
SIZE = 256
W = H = SIZE * SS

BALL = (124, 74, 30, 255)        # leather brown
BALL_DK = (84, 48, 16, 255)      # darker outline / shading
LINE = (245, 240, 230, 255)      # off-white laces / stripes
BG = (0, 0, 0, 0)                # transparent


def draw():
    img = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(img)

    cx, cy = W / 2, H / 2
    rx, ry = W * 0.42, H * 0.27   # football is wider than tall

    bbox = [cx - rx, cy - ry, cx + rx, cy + ry]

    # body + outline
    d.ellipse(bbox, fill=BALL, outline=BALL_DK, width=int(6 * SS))

    lw = int(5 * SS)

    # white end stripes (short vertical bars near each tip)
    for sx in (cx - rx * 0.78, cx + rx * 0.78):
        d.line([(sx, cy - ry * 0.34), (sx, cy + ry * 0.34)],
               fill=LINE, width=lw)

    # central seam (horizontal lace line)
    seam_half = rx * 0.42
    d.line([(cx - seam_half, cy), (cx + seam_half, cy)], fill=LINE, width=lw)

    # perpendicular lace ticks along the seam
    n = 6
    for i in range(n):
        t = i / (n - 1)
        x = cx - seam_half + t * (2 * seam_half)
        d.line([(x, cy - ry * 0.22), (x, cy + ry * 0.22)], fill=LINE, width=lw)

    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    return img


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "football.ico")
    img = draw()
    img.save(out, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                    (64, 64), (128, 128), (256, 256)])
    print("wrote", out)


if __name__ == "__main__":
    main()
