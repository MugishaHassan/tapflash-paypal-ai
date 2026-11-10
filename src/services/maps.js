'use strict';

// Google Maps walking directions to a restroom. With no origin, Google Maps
// starts from the phone's current position, so we don't put the user's
// location in the link.
function directionsUrl(destination) {
  const params = new URLSearchParams({
    api: '1',
    destination: `${destination.lat.toFixed(6)},${destination.lng.toFixed(6)}`,
    travelmode: 'walking',
  });
  return `https://www.google.com/maps/dir/?${params}`;
}

module.exports = { directionsUrl };
