'use strict';

const path = require('path');
const { createCards } = require('./cards');
const { createMapImages } = require('./maps-image');

// Everything the bot draws, in the shape the conversation engine expects.
// A function returning null means "no picture, send the text only".
function createImages(config) {
  if (!config.images) return undefined;
  const cards = createCards({
    currency: config.currency,
    timezone: config.timezone,
    paymentIconsDir: path.join(config.imagesDir, 'payments'),
  });
  // Maps use OpenStreetMap over the internet; MAP_IMAGES=false turns them off.
  const maps = config.mapImages ? createMapImages({ cacheDir: path.join(config.root, '.cache', 'maps') }) : null;

  const small = new Map();
  const once = (key, make) => {
    if (!small.has(key)) small.set(key, make().catch((err) => { small.delete(key); throw err; }));
    return small.get(key);
  };

  return {
    restroomThumb: (r) => once(`thumb:${r.id}:${r.imagePath || ''}`, () => cards.restroomThumb(r)),
    paymentIcon: (m) => once(`icon:${m.id}`, () => cards.paymentIcon(m)),
    restroomList: (options) => cards.restroomList(options),
    restroomPhoto: (restroom) => cards.restroomPhoto(restroom),
    paymentMethods: (methods) => cards.paymentMethods(methods),
    accessCard: (pass, qrBuffer) => cards.accessCard(pass, qrBuffer),
    // Blurred before payment, like the demo video; clear once paid.
    routePreview: async (from, to) => maps && maps.routeMap(from, to, { label: to.name, blur: true }),
    routeMap: async (from, to) => maps && maps.routeMap(from, to, { label: to.name }),
  };
}

module.exports = { createImages };
