'use strict';

// "KES 100" or "KES 100.50"
function formatMoney(amount, currency) {
  const rounded = Math.round(amount * 100) / 100;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2);
  return `${currency} ${text}`;
}

// Fee rounded to cents.
function feeFor(amount, percent) {
  return Math.round(amount * percent) / 100;
}

// "10:55"
function formatTime(date, timezone) {
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: timezone,
  }).format(date);
}

// "10 Oct 2026, 10:55"
function formatDateTime(date, timezone) {
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: timezone,
  }).format(date);
}

// Turn what the user typed into +<country code><9 digits>, or null if it isn't
// a phone number. Kenya, Tanzania, Uganda and Rwanda all use 9-digit numbers
// after the country code, e.g. 0712 345 678 -> +254712345678.
function normalizePhone(input, countryCode) {
  let digits = String(input).trim().replace(/[\s\-().]/g, '');
  if (digits.startsWith('+')) digits = digits.slice(1);
  else if (digits.startsWith('00')) digits = digits.slice(2);
  else if (digits.startsWith('0')) digits = countryCode + digits.slice(1);
  else if (digits.length === 9) digits = countryCode + digits;

  if (!/^\d+$/.test(digits) || !digits.startsWith(countryCode)) return null;
  if (digits.length - countryCode.length !== 9) return null;
  return `+${digits}`;
}

module.exports = { formatMoney, feeFor, formatTime, formatDateTime, normalizePhone };
