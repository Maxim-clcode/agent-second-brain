"""Button handlers for reply keyboard."""

from datetime import datetime, timedelta

import httpx
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from d_brain.bot.keyboards import get_plan_day_keyboard
from d_brain.services.workday import next_workday

_NOTION_TOKEN = "ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh"
_PM_BACKLOG_DB = "22876284-e92f-4866-a908-3a3bda425637"


def _count_notion_backlog() -> int | None:
    """Hard Notion API call — returns exact count of non-done tasks in PM Backlog.
    Returns None on error so the prompt still works without the count.
    """
    try:
        r = httpx.post(
            f"https://api.notion.com/v1/databases/{_PM_BACKLOG_DB}/query",
            headers={
                "Authorization": f"Bearer {_NOTION_TOKEN}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={
                "filter": {
                    "and": [
                        {"property": "Статус", "select": {"does_not_equal": "Выполнено"}},
                        {"property": "Статус", "select": {"does_not_equal": "Отменено"}},
                    ]
                },
                "page_size": 1,  # we only need total count via has_more + pagination
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        # Count all pages via pagination
        count = len(data.get("results", []))
        cursor = data.get("next_cursor")
        while cursor:
            r2 = httpx.post(
                f"https://api.notion.com/v1/databases/{_PM_BACKLOG_DB}/query",
                headers={
                    "Authorization": f"Bearer {_NOTION_TOKEN}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json={
                    "filter": {
                        "and": [
                            {"property": "Статус", "select": {"does_not_equal": "Выполнено"}},
                            {"property": "Статус", "select": {"does_not_equal": "Отменено"}},
                        ]
                    },
                    "page_size": 100,
                    "start_cursor": cursor,
                },
                timeout=10,
            )
            if r2.status_code != 200:
                break
            d2 = r2.json()
            count += len(d2.get("results", []))
            cursor = d2.get("next_cursor")
        return count
    except Exception:
        return None

router = Router(name="buttons")


@router.message(F.text == "📊 Статус")
async def btn_status(message: Message) -> None:
    """Handle Status button."""
    from d_brain.bot.handlers.commands import cmd_status

    await cmd_status(message)


@router.message(F.text == "⚙️ Обработать")
async def btn_process(message: Message) -> None:
    """Handle Process button."""
    from d_brain.bot.handlers.process import cmd_process

    await cmd_process(message)


@router.message(F.text == "❓ Помощь")
async def btn_help(message: Message) -> None:
    """Handle Help button."""
    from d_brain.bot.handlers.commands import cmd_help

    await cmd_help(message)


@router.message(F.text == "📅 Собрать план")
async def btn_plan(message: Message) -> None:
    """Ask user which day to plan."""
    await message.answer(
        "На какой день собираем план?",
        reply_markup=get_plan_day_keyboard(),
    )


@router.message(F.text == "📋 Итог недели")
async def btn_weekly(message: Message, bot: Bot) -> None:
    """Start weekly review flow."""
    from d_brain.bot.handlers.chat import _process_and_reply
    from d_brain.config import get_settings
    from d_brain.services.timezone import get_user_tz

    settings = get_settings()
    user_tz = get_user_tz(settings.vault_path)
    now = datetime.now(user_tz)

    week_start = (now.date() - timedelta(days=now.weekday())).isoformat()
    today_iso = now.date().isoformat()
    today_str = now.strftime("%d.%m.%Y")
    days_elapsed = now.weekday() + 1  # 1=пн, 2=вт, 3=ср, 4=чт, 5=пт

    await message.answer("📋 Собираю данные за неделю...")

    if not message.from_user:
        return
    prompt = _build_weekly_prompt(week_start, today_iso, today_str, days_elapsed)
    await _process_and_reply(bot, message.chat.id, message.from_user.id, prompt)


@router.callback_query(F.data == "weekly_start")
async def callback_weekly_start(callback: CallbackQuery, bot: Bot) -> None:
    """Handle weekly review trigger from scheduled reminder."""
    from d_brain.bot.handlers.chat import _process_and_reply
    from d_brain.config import get_settings
    from d_brain.services.timezone import get_user_tz

    settings = get_settings()
    user_tz = get_user_tz(settings.vault_path)
    now = datetime.now(user_tz)

    week_start = (now.date() - timedelta(days=now.weekday())).isoformat()
    today_iso = now.date().isoformat()
    today_str = now.strftime("%d.%m.%Y")
    days_elapsed = now.weekday() + 1

    await callback.answer()
    await callback.message.edit_text("📋 Собираю данные за неделю...")

    if not callback.from_user:
        return
    prompt = _build_weekly_prompt(week_start, today_iso, today_str, days_elapsed)
    await _process_and_reply(bot, callback.message.chat.id, callback.from_user.id, prompt)


def _build_weekly_prompt(week_start: str, today_iso: str, today_str: str, days_elapsed: int) -> str:
    days_total = 5
    week_note = (
        f"Сегодня {days_elapsed}-й рабочий день из {days_total}. "
        f"Анализируем только пройденные {days_elapsed} дн. (пн–{'пт' if days_elapsed==5 else ['пн','вт','ср','чт','пт'][days_elapsed-1]}). "
        "Не оценивай как полную неделю — делай поправку на то, сколько дней реально прошло."
        if days_elapsed < 5 else
        "Анализируем полную рабочую неделю (пн–пт)."
    )
    return (
        f"Максим хочет подвести итог недели. Период: {week_start} — {today_iso}.\n"
        f"{week_note}\n\n"
        "Выполни строго по шагам:\n\n"
        "ШАГ 1. Собери данные из Notion PM Backlog "
        "(Database ID: 22876284-e92f-4866-a908-3a3bda425637).\n"
        "Нужны все задачи за эту неделю. Раздели на группы:\n"
        "— «Выполнено» — закрытые задачи\n"
        "— «Спринт неделя» — планировались, но не закрыты\n"
        "— «Выполняют сотрудники» — делегированные (прочитай тело страницы — там история)\n"
        "— «Бэклог» — не начинались\n"
        "Запомни: приоритеты (P1/P2/P3), сложность, ответственных, дедлайны — они нужны для анализа.\n\n"
        "ШАГ 2. Прочитай дневники за эту неделю из vault/daily/ "
        f"(файлы от {week_start} до {today_iso}). "
        "Ищи: в какое время суток больше записей, какие темы/задачи повторяются, "
        "были ли застревания, жалобы на усталость или перегруз, эмоциональный тон записей.\n\n"
        "ШАГ 3. Задай Максиму три вопроса одним сообщением:\n"
        "1) По каждой делегированной задаче: «[Задача X] у [ответственного] — как продвинулась? Есть блокеры?»\n"
        "2) «Что на этой неделе давалось сложнее всего — задача, ситуация или решение?»\n"
        "3) «Как я могу помочь тебе работать эффективнее? Чего не хватает — напоминаний, анализа, "
        "чего-то ещё?»\n\n"
        "ШАГ 4. Жди ответа Максима.\n\n"
        "ШАГ 5. После ответа:\n"
        "а) Для каждой делегированной задачи — обнови тело страницы в Notion через curl:\n"
        "   curl -s -X PATCH \"https://api.notion.com/v1/blocks/{PAGE_ID}/children\" \\\n"
        "     -H \"Authorization: Bearer ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh\" \\\n"
        "     -H \"Notion-Version: 2022-06-28\" -H \"Content-Type: application/json\" \\\n"
        "     -d '{\"children\":[{\"object\":\"block\",\"type\":\"paragraph\","
        "\"paragraph\":{\"rich_text\":[{\"type\":\"text\",\"text\":"
        "{\"content\":\"📅 {ДАТА}: {СТАТУС ОБНОВЛЕНИЯ}\"}}]}}]}'\n\n"
        "б) Сформируй итог недели строго в таком формате:\n\n"
        f"📊 <b>Итог недели {today_str}</b>\n"
        "✅ Выполнено: X задач\n"
        "🔄 Не закрыто: X задач\n"
        "👥 У сотрудников: X задач\n"
        "📈 Закрытие: X% (от задач в спринте)\n\n"
        "🧠 <b>Ментальные показатели:</b>\n"
        "[На основе дневников и ответа про сложность: когда была пиковая нагрузка, "
        "был ли перегруз или наоборот недозагруз, какие решения давались тяжело, "
        "был ли фокус или постоянные переключения. Конкретно — не «ты много работал», "
        "а «в среду после обеда три задачи подряд без переключения — явный перегруз»]\n\n"
        "📊 <b>Количественные показатели:</b>\n"
        "[Факты с цифрами: сколько P1/P2/P3 закрыто vs не закрыто, "
        "сколько задач делегировано vs выполнено лично, "
        "сколько дней задачи висели до закрытия (среднее), "
        "были ли просроченные дедлайны. "
        "Пример: «2 из 3 P1 закрыты, P2 закрыты на 50%, все P3 в бэклоге»]\n\n"
        "✨ <b>Качественные показатели:</b>\n"
        "[Паттерны в решениях и подходе: правильно ли делегировал (движутся ли задачи у сотрудников), "
        "были ли задачи-пожиратели времени, застревал ли на чём-то одном, "
        "как быстро принимал решения, откладывал ли сложное на потом. "
        "Пример: «Делегировал 3 задачи, но только 1 движется — возможно нет контроля точек»]\n\n"
        "🎯 <b>Как работать эффективнее:</b>\n"
        "[3 конкретных совета, каждый основан на реальном паттерне этой недели. "
        "НЕ шаблонные («планируй заранее») — только то, что видно из данных. "
        "Примеры уровня конкретности: "
        "«Ты дважды переносил встречу с Никитой — поставь hard deadline и не двигай», "
        "«Задача X висит 5 дней без движения — раздели на шаги или делегируй», "
        "«Работаешь после 21:00 три дня подряд — в понедельник поставь hard stop в 19:00»]\n\n"
        "🤖 <b>Как улучшить бота:</b>\n"
        "[На основе ответа Максима на вопрос «как я могу помочь лучше» — "
        "дай 2-3 конкретных предложения по улучшению бота. "
        "Не пересказывай что он сказал — предложи конкретные функции или изменения поведения. "
        "Примеры: «Ты сказал, что не хватает напоминаний о дедлайнах — можно добавить уведомление "
        "за день до дедлайна по задачам P1/P2», "
        "«Ты хочешь больше контроля по делегированным — можно каждый вторник присылать статус по задачам «Выполняют сотрудники»», "
        "«Ты говоришь, что теряешь контекст — можно в начале дня присылать краткий briefing: что запланировано и что висит». "
        "Если Максим сказал «всё ок» или не дал конкретного ответа — напиши одну идею "
        "исходя из паттернов недели, которая реально помогла бы.]\n\n"
        "[Одна финальная фраза — честная, без воды. Мотивирующая если ≥70% закрыто, "
        "жёстко-честная если <70%.]\n\n"
        "ШАГ 6. СРАЗУ после итога (без ожидания) предложи планирование следующего рабочего дня.\n"
        "Notion уже актуален после обновлений на этой неделе. Выполни шаги:\n"
        "6а. Определи следующий рабочий день (пропусти выходные и праздники РФ — обычно это понедельник).\n"
        "6б. Вызови get_tasks_by_date для следующего рабочего дня — проверь занятость.\n"
        "6в. Запроси PM Backlog — задачи со статусом «Бэклог» и «Спринт неделя» "
        "(выполненные за неделю помечены «Выполнено» и не появятся).\n"
        "6г. Предложи план по правилам: не занимать 13:00–14:00 (обед), "
        "задачи друг за другом без пауз, только в свободные окна, приоритет P1→P2→P3.\n"
        "6д. Жди подтверждения — после него добавь задачи в TickTick с startDatetime/endDatetime "
        "и обнови статус в Notion на «Спринт неделя».\n\n"
        "Отвечай по-русски. Никакой воды — только факты из данных и конкретные выводы."
    )


@router.message(F.text == "📊 Итог дня")
async def btn_summary(message: Message, bot: Bot) -> None:
    """Start end-of-day summary flow."""
    from d_brain.bot.handlers.chat import _process_and_reply
    from d_brain.config import get_settings
    from d_brain.services.timezone import get_user_tz

    settings = get_settings()
    user_tz = get_user_tz(settings.vault_path)
    today = datetime.now(user_tz).date()
    today_iso = today.isoformat()
    today_str = today.strftime("%d.%m.%Y")
    next_day = next_workday(today)
    next_day_iso = next_day.isoformat()
    next_day_str = next_day.strftime("%d.%m.%Y")

    await message.answer("📊 Собираю данные о сегодняшнем дне...")

    if not message.from_user:
        return
    prompt = _build_summary_prompt(today_str, today_iso, next_day_str, next_day_iso)
    await _process_and_reply(bot, message.chat.id, message.from_user.id, prompt)


@router.callback_query(F.data == "summary_start")
async def callback_summary_start(callback: CallbackQuery, bot: Bot) -> None:
    """Handle summary trigger from scheduled reminder."""
    from d_brain.bot.handlers.chat import _process_and_reply
    from d_brain.config import get_settings
    from d_brain.services.timezone import get_user_tz

    settings = get_settings()
    user_tz = get_user_tz(settings.vault_path)
    today = datetime.now(user_tz).date()
    today_iso = today.isoformat()
    today_str = today.strftime("%d.%m.%Y")
    next_day = next_workday(today)
    next_day_iso = next_day.isoformat()
    next_day_str = next_day.strftime("%d.%m.%Y")

    await callback.answer()
    await callback.message.edit_text("📊 Собираю данные о сегодняшнем дне...")

    if not callback.from_user:
        return
    backlog_count = _count_notion_backlog()
    prompt = _build_summary_prompt(today_str, today_iso, next_day_str, next_day_iso, backlog_count)
    await _process_and_reply(bot, callback.message.chat.id, callback.from_user.id, prompt)


def _build_summary_prompt(
    today_str: str,
    today_iso: str,
    next_day_str: str,
    next_day_iso: str,
    backlog_count: int | None = None,
) -> str:
    TICKTICK_ALL_DB = "8a215823-eb16-835b-9876-81806599e224"
    PM_BACKLOG_DB = "22876284-e92f-4866-a908-3a3bda425637"

    if backlog_count is not None:
        backlog_hint = (
            f"ВАЖНО: Notion API подтвердил — в бэклоге сейчас ровно {backlog_count} активных задач "
            f"(статус не «Выполнено» и не «Отменено»). "
            "Если MCP вернул меньше — пагинируй через cursor пока не получишь все. "
            "Сообщать «бэклог пуст» можно ТОЛЬКО если получил 0 задач И это совпадает с подтверждённым числом.\n"
        )
    else:
        backlog_hint = (
            "Если PM Backlog вернул 0 задач — это подозрительно. Запроси ещё раз перед тем как сказать «бэклог пуст».\n"
        )

    return (
        f"Максим хочет подвести итог дня — {today_str}.\n\n"
        "Выполни строго по шагам:\n\n"
        f"ШАГ 1. Запроси TickTick:All (Database ID: {TICKTICK_ALL_DB}) — "
        f"все записи где поле Date содержит дату {today_iso}. "
        "Раздели по полю Checkbox:\n"
        "— Checkbox = true → ВЫПОЛНЕНО\n"
        "— Checkbox = false → НЕ ВЫПОЛНЕНО\n\n"
        f"ШАГ 2. Запроси PM Backlog (Database ID: {PM_BACKLOG_DB}) — "
        "все задачи (любой статус, кроме «Выполнено» и «Отменено»). Запомни название и page_id каждой.\n"
        f"{backlog_hint}\n"
        "ШАГ 3. Покажи Максиму срез одним сообщением:\n"
        "«✅ Вижу выполненными: Задача A, Задача B\n\n"
        "❓ Не отмечены выполненными: Задача C, Задача D\n\n"
        "Всё верно? По невыполненным — что с ними?»\n\n"
        "ШАГ 4. Жди ответа Максима.\n\n"
        "ШАГ 5. После ответа обнови PM Backlog — сопоставь задачи по названию:\n"
        "— Выполнена (Checkbox=true или Максим подтвердил) и ЕСТЬ в PM Backlog → статус «Выполнено» через notion-update-page\n"
        "— Выполнена но НЕТ в PM Backlog → создай через notion-create-pages со статусом «Выполнено»\n"
        "— «Выполняют сотрудники» и ЕСТЬ в PM Backlog → статус «Выполняют сотрудники» через notion-update-page\n"
        "— «Выполняют сотрудники» но НЕТ в PM Backlog → создай через notion-create-pages со статусом «Выполняют сотрудники»\n"
        "— Частично выполнена и ЕСТЬ в PM Backlog → запись в тело страницы через bash:\n"
        "  curl -s -X PATCH \"https://api.notion.com/v1/blocks/{PAGE_ID}/children\" \\\n"
        "    -H \"Authorization: Bearer ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh\" \\\n"
        "    -H \"Notion-Version: 2022-06-28\" -H \"Content-Type: application/json\" \\\n"
        f"    -d '{{\"children\":[{{\"object\":\"block\",\"type\":\"paragraph\","
        f"\"paragraph\":{{\"rich_text\":[{{\"type\":\"text\",\"text\":"
        f"{{\"content\":\"📝 {today_str}: Что сделано: [текст]. Что ещё нужно: [текст]\"}}}}]}}}}]}}'\n"
        f"При создании новой задачи в PM Backlog используй Database ID: {PM_BACKLOG_DB}, поле «Задача» = название.\n\n"
        "ШАГ 6. Подведи итог:\n"
        "✅ Выполнено: X\n🔄 В работе: X\n❌ Не сделано: X\n📈 Процент: X%\n\n"
        "Одна фраза: мотивирующая если ≥70%, честно подстёгивающая если <70%.\n\n"
        "ШАГ 6а. Обнови закреплённые дорожные карты (если есть):\n"
        "Выполни команду:\n"
        "TELEGRAM_TOKEN=8945688412:AAFf5U8JtSScWVT_ex7u2T5M9Zvwj2dKZ8Y "
        "TELEGRAM_CHAT_ID=257352741 "
        "/home/brain/projects/agent-second-brain/.venv/bin/python "
        "/home/brain/projects/agent-second-brain/scripts/update_roadmap_pin.py\n"
        "Если файлов дорожных карт нет — пропусти.\n\n"
        f"ШАГ 7. СРАЗУ после итога предложи план на следующий рабочий день.\n"
        f"Следующий рабочий день: {next_day_str} (дата для всех запросов: {next_day_iso}).\n"
        "НЕ вычисляй дату самостоятельно — используй именно эту дату.\n\n"
        f"7а. Вызови get_tasks_by_date с date=\"{next_day_iso}\" — это занятые слоты.\n"
        "Из результата выпиши ВСЕ задачи у которых есть startDate — это уже запланировано, не предлагай их повторно.\n"
        "Запомни названия уже запланированных задач — они НЕ кандидаты для добавления.\n\n"
        f"7б. Запроси PM Backlog — задачи со статусом «Бэклог» и «Спринт неделя».\n"
        f"{backlog_hint}"
        "СТРОГО: кандидатами могут быть ТОЛЬКО задачи из PM Backlog. Не добавляй задачи из контекста разговора, "
        "не придумывай задачи — только то, что реально есть в PM Backlog.\n"
        "Исключи задачи, чьё название совпадает (полностью или частично) с уже запланированными из шага 7а.\n\n"
        "7в. Расставь кандидатов по свободным окнам: 09:00–13:00 и 14:00–18:00, без пауз между задачами.\n"
        "Задачи с тегом 'platrum' в TickTick — жёсткие блоки, их нельзя двигать.\n\n"
        "7г. Покажи план и жди подтверждения. После — добавь задачи в TickTick "
        f"с startDatetime=\"{next_day_iso}THH:MM\" и endDatetime=\"{next_day_iso}THH:MM\", "
        "обнови статус в Notion на «Спринт неделя».\n\n"
        "Отвечай по-русски."
    )


@router.callback_query(F.data == "plan_skip")
async def callback_plan_skip(callback: CallbackQuery) -> None:
    """User chose to plan later — dismiss the message."""
    await callback.answer("Окей, напомню в следующий раз 👌")
    await callback.message.edit_text("⏰ Хорошо, вернёмся к планированию позже.")


def _build_plan_prompt(day_label: str, date_str: str, date_iso: str) -> str:
    return (
        f"Максим хочет собрать план на {day_label} — {date_str}.\n\n"
        "Выполни строго по шагам — не пропускай ни один:\n\n"
        f"ШАГ 1. Вызови get_tasks_by_date с date=\"{date_iso}\".\n"
        "Из результата выпиши все задачи у которых есть startDate/dueDate — это занятые слоты.\n"
        "Построй список занятых интервалов: [(09:00–10:30), (14:00–15:00), ...].\n"
        "Рабочее окно: 09:00–18:00. Вычти занятые слоты → получи список СВОБОДНЫХ окон.\n"
        "ВАЖНО: 13:00–14:00 — обед, всегда заблокирован, никогда не предлагать под задачи.\n"
        "ВАЖНО: задачи с тегом 'platrum' — жёсткие назначения из внешней системы, их нельзя двигать.\n"
        "Запомни эти свободные окна — в них и только в них можно ставить новые задачи.\n\n"
        "ШАГ 2. Получи кандидатов из Notion PM Backlog "
        "(Database ID: 22876284-e92f-4866-a908-3a3bda425637) "
        "со статусом «Бэклог» или «Спринт неделя». Приоритет: P1 → P2 → P3.\n\n"
        "ШАГ 3. Расставь задачи-кандидаты по свободным окнам.\n"
        "ПРАВИЛО: новая задача не может пересекаться ни с одним занятым слотом из шага 1.\n"
        "Задачи идут друг за другом без пауз между ними.\n"
        "Если свободного времени мало — возьми только самые важные задачи.\n\n"
        "ШАГ 4. Покажи итог и жди подтверждения.\n\n"
        f"ШАГ 5. После подтверждения: добавь каждую задачу в TickTick через add_task, "
        f"передавай startDatetime=\"{date_iso}THH:MM\" и endDatetime=\"{date_iso}THH:MM\" "
        "с точным временем начала и конца из плана. Без этих полей задача встанет без длительности. "
        "Затем обнови статус в Notion на «Спринт неделя».\n\n"
        "Отвечай по-русски, кратко. Формат ответа:\n"
        "📌 Уже занято: 09:00–10:00 Встреча, 14:00–15:00 Звонок\n"
        "🕐 Свободно: 10:15–13:45, 15:15–18:00\n"
        "📋 Предлагаю добавить:\n"
        "1. [название] — 10:15–11:30 (P1)\n"
        "2. ..."
    )


@router.callback_query(F.data.startswith("plan_date:"))
async def callback_plan_date(callback: CallbackQuery, bot: Bot) -> None:
    """Handle date-specific plan request (from evening reminder)."""
    from d_brain.bot.handlers.chat import _process_and_reply

    date_str_raw = callback.data.split(":", 1)[1]  # e.g. "2026-06-29"

    DAY_RU = {
        0: "понедельник", 1: "вторник", 2: "среду",
        3: "четверг", 4: "пятницу", 5: "субботу", 6: "воскресенье",
    }
    try:
        from datetime import date as date_type
        target = date_type.fromisoformat(date_str_raw)
        day_label = DAY_RU[target.weekday()]
        date_iso = target.isoformat()
        date_str = target.strftime("%d.%m.%Y")
    except ValueError:
        day_label = date_str_raw
        date_iso = date_str_raw
        date_str = date_str_raw

    await callback.answer()
    await callback.message.edit_text(f"📅 Строю план на {day_label}...")

    if not callback.from_user:
        return
    prompt = _build_plan_prompt(day_label, date_str, date_iso)
    await _process_and_reply(bot, callback.message.chat.id, callback.from_user.id, prompt)


@router.callback_query(F.data.startswith("plan_day:"))
async def callback_plan_day(callback: CallbackQuery, bot: Bot) -> None:
    """Handle today/tomorrow plan request (from main keyboard button)."""
    from d_brain.bot.handlers.chat import _process_and_reply
    from d_brain.config import get_settings
    from d_brain.services.timezone import get_user_tz

    day = callback.data.split(":")[1]
    user_tz = get_user_tz(get_settings().vault_path)
    now = datetime.now(user_tz)

    DAY_RU = {0: "понедельник", 1: "вторник", 2: "среду", 3: "четверг", 4: "пятницу"}

    if day == "today":
        target = now
        day_label = "сегодня"
    else:
        target_date = next_workday(now.date())
        target = now.replace(year=target_date.year, month=target_date.month, day=target_date.day)
        day_label = DAY_RU[target_date.weekday()]

    date_iso = target.date().isoformat()
    date_str = target.strftime("%d.%m.%Y")

    await callback.answer()
    await callback.message.edit_text(f"📅 Строю план на {day_label}...")

    if not callback.from_user:
        return
    prompt = _build_plan_prompt(day_label, date_str, date_iso)
    await _process_and_reply(bot, callback.message.chat.id, callback.from_user.id, prompt)
