'use strict';

const { formatMoney, formatTime } = require('../services/format');
const { describeDistance } = require('../services/restrooms');

// Every text the bot sends lives here, so wording can be changed in one place.
// *text* is bold in WhatsApp. Choices are not written here: the engine sends
// them as menu options, and each platform shows them its own way.
function createMessages(config) {
  const money = (amount) => formatMoney(amount, config.currency);
  const time = (date) => formatTime(date, config.timezone);
  const cc = config.countryCode;
  const shareLocationHelp = 'Tap 📎 (or +), then *Location*, then *Send your current location*.';

  return {
    greeting: () =>
      `Hello! I'm *${config.botName}*. I help you find the nearest clean restroom in your neighbourhood.`,

    activePassReminder: (pass) =>
      `You still have an access pass for *${pass.facility.name}* until ${time(pass.expiresAt)}. ` +
      'Reply *pass* to see it.',

    askLocation: () => `Share your location and I'll find restrooms near you.\n\n${shareLocationHelp}`,

    needLocation: () => `I don't have your location yet.\n\n${shareLocationHelp}`,

    badLocation: () => "I couldn't read that location.",

    timedOut: (nothingCharged) =>
      'Our chat timed out, so I have started again.' + (nothingCharged ? ' Nothing was charged.' : ''),

    noneNearby: () =>
      `I couldn't find a restroom within ${config.maxDistanceKm} km of you. ` +
      'Share a different location, or reply *exit* to leave.',

    allClosed: () =>
      'The restrooms near you are closed now, or will be before you get there. ' +
      'Share a different location, or try again later.',

    allPaidFor: () =>
      'You already have an access pass for the open restrooms near you. ' +
      'Reply *pass* to see it, or share a different location.',

    closedOnArrival: (r) =>
      `*${r.name}* will be closed by the time you get there (open ${r.hours}), so nothing was charged. ` +
      'Please choose another restroom.',

    chooseRestroom: () => 'Here are the closest restrooms. Which one would you like?',

    restroomDetails: (r) => `${describeDistance(r.distanceKm)}, ${money(r.price)}`,

    payPrompt: (r) =>
      `*${r.name}*\n` +
      `${describeDistance(r.distanceKm)}\n` +
      `Price: ${money(r.price)}` +
      `${r.hours ? `\nOpen ${r.hours}` : ''}\n\n` +
      'Pay to get the route and your access code.',

    choosePaymentMethod: () => 'How would you like to pay?',

    methodFee: (method) => (method.feePercent ? `${method.feePercent}% fee` : 'No fees'),

    askPhone: (method) => `Enter your ${method.label} number, for example 0712 345 678.`,

    useWhatsAppNumber: (phone) => `Use ${phone}`,

    badPhone: (method) =>
      `That doesn't look like an ${method.label} number. ` +
      `Send it like 0712 345 678 or +${cc} 712 345 678.`,

    paymentSummary: (p, testMode) =>
      '*Payment summary*\n' +
      `Restroom: ${p.facility.name}\n` +
      `Method: ${p.method.label}${p.phone ? ` (${p.phone})` : ''}\n` +
      `Price: ${money(p.price)}\n` +
      `Fee: ${p.fee ? money(p.fee) : 'none'}\n` +
      `*Total: ${money(p.total)}*` +
      (p.closesAt ? `\n\nThe restroom closes at ${time(p.closesAt)}, so your pass ends then.` : '') +
      (testMode ? '\n\nTest mode: no real money is taken.' : ''),

    paymentFailed: (reason) => `Payment failed. ${reason}`,

    paymentReceived: (reference) =>
      `Payment received. Reference *${reference}*.\nScan the QR code to see your payment details.`,

    receiptText: (pass, dateTime) =>
      `${config.botName} receipt\n` +
      `Ref: ${pass.reference}\n` +
      `Restroom: ${pass.facility.name}\n` +
      `Method: ${pass.method.label}${pass.phone ? ` ${pass.phone}` : ''}\n` +
      `Amount: ${money(pass.total)}\n` +
      `Paid: ${dateTime}`,

    searchingRoute: (r) => `Finding the shortest route to *${r.name}*…`,

    startJourney: (url) => `Start walking, I'll guide you. Open the route in Google Maps:\n${url}`,

    arrived: (r) => `You have arrived at *${r.name}*.`,

    accessDetails: (pass) =>
      '*Your access details*\n' +
      `Restroom: ${pass.facility.name}\n` +
      `Amount paid: ${money(pass.total)}\n` +
      `Access code (OTP): *${pass.otp}*\n` +
      `Valid until: *${time(pass.expiresAt)}*\n\n` +
      'Show the QR code at the entrance, or enter the access code.',

    // Under the access card, which already shows the details.
    accessCardCaption: (pass) =>
      `Show this at the entrance of *${pass.facility.name}*, or enter the access code. ` +
      `Valid until ${time(pass.expiresAt)}.`,

    otp: (pass) => `Your access code (OTP) is *${pass.otp}*. It works until ${time(pass.expiresAt)}.`,

    whatNext: () => 'What would you like to do?',

    done: (pass) =>
      `Enjoy! Your pass works until ${time(pass.expiresAt)}. Reply *pass* to see it again, ` +
      'or *hi* to find another restroom.',

    noPass: () => "You don't have an active access pass. Send *hi* to find a restroom.",

    passEnded: (pass) =>
      `Your access pass for *${pass.facility.name}* expired at ${time(pass.expiresAt)}. ` +
      'Send *hi* to find a restroom.',

    // The journey can't go on: the pass ran out before it was used.
    passExpired: (pass) =>
      `Your access pass for *${pass.facility.name}* expired at ${time(pass.expiresAt)}. ` +
      'If you could not use it, report the problem and our team will look into it.',

    askReport: () =>
      "What's wrong? Describe it in one message, for example \"The restroom is closed\" or " +
      '"I can\'t find the entrance".',

    describeInWords: () => 'Please describe the problem in words.',

    reportThanks: () => 'Thank you. Our team has your report and will look into it.',

    goodbye: (pass) =>
      `Thanks for using ${config.botName}. Send *hi* any time to find a restroom.` +
      (pass ? `\n\nYour access pass works until ${time(pass.expiresAt)}. Reply *pass* to see it.` : ''),

    didNotUnderstand: () => "Sorry, I didn't get that. Please pick one of the options.",

    locationNoted: () => 'Got your location.',

    textOnly: () => 'I can only read text and shared locations for now.',

    help: () =>
      `*How to use ${config.botName}*\n` +
      'Send *hi* to start a new search.\n' +
      'Share your location to see restrooms near you.\n' +
      'Reply with the number of an option.\n' +
      'Send *pass* to see your access pass.\n' +
      'Send *exit* to stop at any time.',
  };
}

module.exports = { createMessages };
