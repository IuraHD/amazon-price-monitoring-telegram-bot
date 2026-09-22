from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def keyboard(*rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows
        ]
    )


def menu():
    return keyboard(
        [("Add product", "add")],
        [("Products", "list:all:0")],
        [("Folders", "folders:0")],
        [("Refresh my products", "refresh_all")],
        [("Settings / help", "help")],
    )
