'use strict';

// The replies the conversation engine returns, and how platforms without
// tappable options show them.
//
//   { kind: 'text', text }
//   { kind: 'image', buffer | path, mimetype, filename, caption? }
//   { kind: 'menu', text, options, image?, optionsImage?, input? }
//
// A menu is a question with choices:
//   text          the question, without the choices written out
//   options       [{ id, title, description?, icon?, secondary? }]
//                   id: what choosing it sends back, e.g. '1'
//                   icon: small image (restroom photo, payment logo)
//                   secondary: a quiet choice such as Back or Exit
//   image         picture shown above the question (route map, access card)
//   optionsImage  picture that already shows the main choices, for platforms
//                 that can't show tappable choices (WhatsApp Web)
//   input         { placeholder } when the user may also type an answer,
//                 e.g. a phone number
//
// The web tester shows options as tappable cards. WhatsApp Web can't, so
// menuAsMessages() turns a menu into a picture or text with numbered choices.
// The official Cloud API will map menus to WhatsApp list and button messages.

// WhatsApp cuts image captions at 1024 characters.
const MAX_CAPTION_LENGTH = 1000;

// "*1.* Title", with the description in italics on the next line.
function optionLine(option) {
  if (option.textHint) return option.textHint;
  return `*${option.id}.* ${option.title}${option.description ? `\n_${option.description}_` : ''}`;
}

// The whole menu as plain text: the question, then numbered choices. Choices
// with descriptions get room to breathe, with Back/Exit kept apart at the end.
function menuText(menu) {
  if (!menu.options.some((o) => o.description)) {
    return [menu.text, menu.options.map(optionLine).join('\n')].filter(Boolean).join('\n\n');
  }
  const main = menu.options.filter((o) => !o.secondary);
  const quiet = menu.options.filter((o) => o.secondary);
  return [menu.text, ...main.map(optionLine), quiet.map(optionLine).join('\n')].filter(Boolean).join('\n\n');
}

// Under a picture that already shows the main choices, only the quiet ones
// (Back, Exit) still need writing out.
function captionUnderOptionsImage(menu) {
  const main = menu.options.filter((o) => !o.secondary);
  const quiet = menu.options.filter((o) => o.secondary);
  const ids = main.map((o) => o.id);
  const range = ids.length > 1 ? `${ids[0]}–${ids[ids.length - 1]}` : ids[0];
  return [menu.text, `Reply with a number (${range}).`, quiet.map(optionLine).join('\n')]
    .filter(Boolean)
    .join('\n\n');
}

function withCaption(image, caption) {
  if (caption.length <= MAX_CAPTION_LENGTH) return [{ ...image, caption }];
  return [image, { kind: 'text', text: caption }];
}

// What to send on a platform where the user types the number of a choice.
function menuAsMessages(menu) {
  if (menu.optionsImage) return withCaption(menu.optionsImage, captionUnderOptionsImage(menu));
  if (menu.image) return withCaption(menu.image, menuText(menu));
  return [{ kind: 'text', text: menuText(menu) }];
}

// Expand every menu in a list of replies (text-only platforms).
function flattenMenus(replies) {
  return replies.flatMap((r) => (r.kind === 'menu' ? menuAsMessages(r) : [r]));
}

module.exports = { MAX_CAPTION_LENGTH, menuText, menuAsMessages, flattenMenus, withCaption };
