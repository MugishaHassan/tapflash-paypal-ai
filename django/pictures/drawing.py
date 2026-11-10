"""Small drawing helpers shared by the cards and the maps.

Pictures are drawn at SCALE times their size and shrunk at the end, which
gives smooth edges (Pillow does not antialias shapes). Text is measured with
the bundled Barlow font (SIL Open Font License, see fonts/OFL.txt), because a
Linux server has no fonts of its own.
"""
import functools
import re
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from bot.replies import Image

FONTS = Path(__file__).resolve().parent / 'fonts'
SCALE = 2

# Characters a font can't draw: controls, colour emoji and their joiners.
UNDRAWABLE = re.compile(
    '[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\U0001f000-\U0001faff\U000e0020-\U000e007f️‍⃣]'
)
WHITESPACE = re.compile(r'\s+')


@functools.cache
def font(size, bold=False):
    name = 'Barlow-Bold.ttf' if bold else 'Barlow-Regular.ttf'
    return ImageFont.truetype(str(FONTS / name), size=max(1, round(size)))


def clean(value):
    """Text without characters the font can't draw, on one line."""
    text = UNDRAWABLE.sub('', '' if value is None else str(value))
    return WHITESPACE.sub(' ', text).strip()


def text_width(text, size, bold=False):
    return font(size, bold).getlength(text)


def fit_text(value, max_width, size, bold=False):
    """Text cut with an ellipsis so it is at most max_width pixels wide,
    cutting between words unless that would throw away too much."""
    text = clean(value)
    if text_width(text, size, bold) <= max_width:
        return text
    chars = list(text)
    kept = len(chars)
    while kept > 1 and text_width(''.join(chars[:kept]) + '…', size, bold) > max_width:
        kept -= 1
    result = ''.join(chars[:kept])
    mid_word = chars[kept] != ' '
    last_space = result.rfind(' ')
    if mid_word and last_space > len(result) * 0.5:
        result = result[:last_space]
    return re.sub(r'[\s.,;:·–—&+/(-]+$', '', result) + '…'


class Canvas:
    """A picture drawn in logical pixels at SCALE times the size."""

    def __init__(self, width, height, background='#ffffff', mode='RGB', scale=SCALE):
        self.width, self.height, self.s = width, height, scale
        self.image = PILImage.new(mode, (round(width * scale), round(height * scale)), background)
        self.draw = ImageDraw.Draw(self.image, 'RGBA')  # RGBA: colours with alpha blend in

    def _box(self, x, y, w, h):
        s = self.s
        return [x * s, y * s, (x + w) * s - 1, (y + h) * s - 1]

    def rect(self, x, y, w, h, fill=None, radius=0, outline=None, width=1):
        self.draw.rounded_rectangle(
            self._box(x, y, w, h), radius=radius * self.s, fill=fill, outline=outline,
            width=max(1, round(width * self.s)),
        )

    def circle(self, cx, cy, r, fill=None, outline=None, width=1):
        s = self.s
        self.draw.ellipse(
            [(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=fill, outline=outline,
            width=max(1, round(width * s)),
        )

    def line(self, points, fill, width, round_caps=True):
        s = self.s
        scaled = [(x * s, y * s) for x, y in points]
        self.draw.line(scaled, fill=fill, width=max(1, round(width * s)), joint='curve')
        if round_caps:
            for x, y in (points[0], points[-1]):
                self.circle(x, y, width / 2, fill=fill)

    def polygon(self, points, fill):
        self.draw.polygon([(x * self.s, y * self.s) for x, y in points], fill=fill)

    def text(self, x, y, value, size, bold=False, fill='#111b21', anchor='start', max_width=None, spacing=0):
        """Text with its baseline at y. anchor: start, middle or end."""
        shown = fit_text(value, max_width, size, bold) if max_width else clean(value)
        f = font(size * self.s, bold)
        if spacing:
            width = sum(f.getlength(ch) for ch in shown) + spacing * self.s * len(shown)
            left = x * self.s - {'start': 0, 'middle': width / 2, 'end': width}[anchor]
            for ch in shown:
                self.draw.text((left, y * self.s), ch, font=f, fill=fill, anchor='ls')
                left += f.getlength(ch) + spacing * self.s
            return width / self.s
        code = {'start': 'ls', 'middle': 'ms', 'end': 'rs'}[anchor]
        self.draw.text((x * self.s, y * self.s), shown, font=f, fill=fill, anchor=code)
        return f.getlength(shown) / self.s

    def paste(self, picture, x, y, mask=None):
        self.image.paste(picture, (round(x * self.s), round(y * self.s)), mask)

    def result(self, scale=1):
        """The finished picture at `scale` times the logical size."""
        size = (round(self.width * scale), round(self.height * scale))
        return self.image if self.image.size == size else self.image.resize(size, PILImage.LANCZOS)


def rounded_mask(width, height, radius):
    mask = PILImage.new('L', (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, fill=255)
    return mask


def to_png(picture, filename):
    buffer = BytesIO()
    picture.save(buffer, 'PNG', optimize=False)
    return Image(buffer.getvalue(), 'image/png', filename)


def slug(value):
    """An id safe to use in a file name."""
    return re.sub('[^A-Za-z0-9_-]+', '-', clean(value)).strip('-') or 'x'
