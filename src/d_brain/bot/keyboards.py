"""Reply keyboards for Telegram bot."""

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Main reply keyboard with common commands."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📅 Собрать план")
    builder.button(text="📊 Итог дня")
    builder.button(text="📋 Итог недели")
    builder.button(text="❓ Помощь")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def get_plan_day_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing which day to plan."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Сегодня", callback_data="plan_day:today")
    builder.button(text="🌅 Завтра", callback_data="plan_day:tomorrow")
    builder.adjust(2)
    return builder.as_markup()
