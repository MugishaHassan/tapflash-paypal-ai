import logging
from io import BytesIO

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from bot.replies import Image

logger = logging.getLogger(__name__)

SIZE = 512
BORDER = 2  # modules of white around the code


def qr_image(text, filename):
    """`text` as a QR code PNG of about 512 pixels, ready to send, or None
    when the image library can't be loaded on this server (text only)."""
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=BORDER)
    code.add_data(text)
    code.make(fit=True)
    # Whole pixels per module keep the squares sharp for scanners.
    code.box_size = max(1, SIZE // (code.modules_count + 2 * BORDER))
    buffer = BytesIO()
    try:
        code.make_image().save(buffer)
    except ImportError:
        logger.warning('No QR code: the image library could not be loaded')
        return None
    return Image(buffer.getvalue(), 'image/png', filename)
