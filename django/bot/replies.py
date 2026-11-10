"""The replies the conversation engine returns, for a channel to send.

    Text(text)
    Image(data, mimetype, filename, caption)
    Menu(text, options, image, input_placeholder)

A menu is a question with choices. Its text is the question without the
choices written out; each channel shows the options its own way (WhatsApp
buttons or a list, cards in the web chat, or numbered lines from menu_text).
"""
from dataclasses import dataclass, field, replace
from typing import ClassVar


@dataclass(frozen=True)
class Text:
    kind: ClassVar[str] = 'text'
    text: str


@dataclass(frozen=True)
class Image:
    kind: ClassVar[str] = 'image'
    data: bytes = field(repr=False)
    mimetype: str
    filename: str
    caption: str = ''

    def with_caption(self, caption):
        return replace(self, caption=caption)


@dataclass(frozen=True)
class Option:
    id: str  # what choosing it sends back, such as '1'
    title: str
    description: str = ''
    icon: Image | None = None  # a small picture: restroom photo, payment logo
    secondary: bool = False  # a quiet choice such as Back or Exit
    # Written in place of the numbered line, for a choice made by typing a word.
    text_hint: str = ''


@dataclass(frozen=True)
class Menu:
    kind: ClassVar[str] = 'menu'
    text: str
    options: list[Option]
    image: Image | None = None  # shown above the question: route map, access card
    # Set when the user may also type an answer, such as a phone number.
    input_placeholder: str = ''


def option_line(option):
    """'*1.* Title', with the description in italics on the next line."""
    if option.text_hint:
        return option.text_hint
    line = f'*{option.id}.* {option.title}'
    return f'{line}\n_{option.description}_' if option.description else line


def menu_text(menu):
    """The whole menu as plain text: the question, then numbered choices.

    Choices with descriptions get room to breathe, with Back and Exit kept
    apart at the end.
    """
    if not any(option.description for option in menu.options):
        parts = [menu.text, '\n'.join(option_line(option) for option in menu.options)]
    else:
        main = [option_line(option) for option in menu.options if not option.secondary]
        quiet = [option_line(option) for option in menu.options if option.secondary]
        parts = [menu.text, *main, '\n'.join(quiet)]
    return '\n\n'.join(part for part in parts if part)
