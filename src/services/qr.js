'use strict';

const QRCode = require('qrcode');

// Encode text as a QR code PNG, ready to send as an image.
async function qrImage(text, filename) {
  const buffer = await QRCode.toBuffer(text, {
    type: 'png',
    width: 512,
    margin: 2,
    errorCorrectionLevel: 'M',
  });
  return { kind: 'image', buffer, mimetype: 'image/png', filename };
}

module.exports = { qrImage };
