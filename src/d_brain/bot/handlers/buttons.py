"""Button handlers for reply keyboard."""

import os
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from d_brain.bot.keyboards import get_plan_day_keyboard
from d_brain.services.workday import next_workday

_NOTION_TOKEN = os.environ["NOTION_TOKEN"]
_PM_BACKLOG_DB = "22876284-e92f-4866-a908-3a3bda425637"
_TT_TOKEN = os.environ["TICKTICK_TOKEN"]
_VL_TZ = ZoneInfo("Asia/Vladivostok")


async def _count_notion_backlog() -> int | None:
    """Hard Notion API call — returns exact count of non-done tasks in PM Backlog.
    Returns None on error so the prompt still works without the count.
    """
    _filter = {
        "and": [
            {"property": "Статус", "select": {"does_not_equal": "Выполнено"}},
            {"property": "Статус", "select": {"does_not_equal": "Отменено"}},
        ]
    }
    _headers = {
        "Authorization": f"Bearer {_NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"https://api.notion.com/v1/databases/{_PM_BACKLOG_DB}/query",
                headers=_headers,
                json={"filter": _filter, "page_size": 100},
            )
            if r.status_code != 200:
                print(f"[count_backlog] Notion API error: {r.status_code} {r.text[:200]}")
                return None
            data = r.json()
            count = len(data.get("results", []))
            cursor = data.get("next_cursor")
            while cursor:
                r2 = await client.post(
                    f"https://api.notion.com/v1/databases/{_PM_BACKLOG_DB}/query",
                    headers=_headers,
                    json={"filter": _filter, "page_size": 100, "start_cursor": cursor},
                )
                if r2.status_code != 200:
                    break
                d2 = r2.json()
                count += len(d2.get("results", []))
                cursor = d2.get("next_cursor")
            print(f"[count_backlog] result={count} (has_more={cursor is not None})")
            return count
    except Exception as exc:
        print(f"[count_backlog] Exception: {exc}")
        return None

def _fetch_ticktick_occupied(date_iso: str) -> str:
    """Pre-fetch TickTick tasks for date, convert UTC→VL, return sorted formatted list.

    Returns empty string on any error so the prompt falls back to asking Claude.
    """
    day = datetime.fromisoformat(date_iso)
    start_vl = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=_VL_TZ)
    end_vl = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=_VL_TZ)
    start_utc = start_vl.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_utc = end_vl.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.999Z")
    try:
        r = httpx.post(
            "https://api.ticktick.com/open/v1/task/undone",
            headers={"Authorization": f"Bearer {_TT_TOKEN}", "Content-Type": "application/json"},
            json={"projectIds": [], "taskIds": [], "startDate": start_utc, "endDate": end_utc},
            timeout=15,
        )
        if r.status_code != 200:
            return ""
        tasks = r.json()
        if not isinstance(tasks, list):
            return ""
        slots = []
        for t in tasks:
            if t.get("isAllDay"):
                continue
            start_str = t.get("startDate", "")
            end_str = t.get("dueDate", "")
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(_VL_TZ)
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone(_VL_TZ) if end_str else None
                start_t = start_dt.strftime("%H:%M")
                end_t = end_dt.strftime("%H:%M") if end_dt else "?"
                slots.append((start_dt, f"- {start_t}–{end_t} {t.get('title', '')}"))
            except (ValueError, TypeError):
                continue
        slots.sort(key=lambda x: x[0])
        return "\n".join(s[1] for s in slots)
    except Exception:
        return ""


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
        "     -H \"Authorization: Bearer NOTION_TOKEN_REDACTED\" \\\n"
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
    backlog_count = await _count_notion_backlog()
    prompt = _build_summary_prompt(today_str, today_iso, next_day_str, next_day_iso, backlog_count)
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
    backlog_count = await _count_notion_backlog()
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
            "⚠️ ВНИМАНИЕ: предварительный подсчёт бэклога не удался из-за ошибки сети.\n"
            "Ты ОБЯЗАН самостоятельно запросить PM Backlog через MCP и пагинировать cursor до конца.\n"
            "ЗАПРЕЩЕНО говорить «бэклог пуст» без получения данных из Notion.\n"
            "Если MCP вернул 0 — пагинируй ещё раз, это почти наверняка ошибка.\n"
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
        "    -H \"Authorization: Bearer NOTION_TOKEN_REDACTED\" \\\n"
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
        f"TELEGRAM_TOKEN={os.environ.get('TELEGRAM_BOT_TOKEN', '')} "
        f"TELEGRAM_CHAT_ID={os.environ.get('TELEGRAM_CHAT_ID', '257352741')} "
        "/home/brain/projects/agent-second-brain/.venv/bin/python "
        "/home/brain/projects/agent-second-brain/scripts/update_roadmap_pin.py\n"
        "Если файлов дорожных карт нет — пропусти.\n\n"
        f"ШАГ 7. СРАЗУ после итога предложи план на следующий рабочий день.\n"
        f"Следующий рабочий день: {next_day_str} (дата для всех запросов: {next_day_iso}).\n"
        "НЕ вычисляй дату самостоятельно — используй именно эту дату.\n\n"
        f"7а. Вызови get_tasks_by_date с date=\"{next_day_iso}\" — это занятые слоты.\n"
        "Из результата выпиши ВСЕ задачи у которых есть startDate — это уже запланировано, не предлагай их повторно.\n"
        "Запомни названия уже запланированных задач — они НЕ кандидаты для добавления.\n\n"
        f"7б. Найди просроченные задачи через bash:\n"
        "```bash\n"
        f"TT_TOKEN='{os.environ.get('TICKTICK_TOKEN', '')}'\n"
        f"for PID in 6966e0a0c2c61184ed479a01 6966e1ccaa621184ed479b56 6966e2b53fc19184ed479f22 6966e308c2e99184ed479fb3 inbox13059360; do\n"
        f"  curl -s \"https://api.ticktick.com/open/v1/project/${{PID}}/data\" -H \"Authorization: Bearer ${{TT_TOKEN}}\" | python3 -c \"\nimport json,sys\ndata=json.load(sys.stdin)\nfor t in data.get('tasks',[]):\n    due=(t.get('dueDate') or '')[:10]\n    if due and due < '{next_day_iso}' and not t.get('completedTime'):\n        print(due+'|'+t.get('title','')[:50])\n\"\ndone\n```\n"
        "Если есть просроченные — покажи блоком 🔴 **Просроченные задачи** ПЕРЕД планом.\n\n"
        f"7в. Запроси PM Backlog — задачи со статусом «Бэклог» и «Спринт неделя».\n"
        f"{backlog_hint}"
        "СТРОГО: кандидатами могут быть ТОЛЬКО задачи из PM Backlog. Не добавляй задачи из контекста разговора, "
        "не придумывай задачи — только то, что реально есть в PM Backlog.\n"
        "Исключи задачи, чьё название совпадает (полностью или частично) с уже запланированными из шага 7а.\n\n"
        "7в. Расставь кандидатов по свободным окнам: 09:00–13:00 и 14:00–18:00, без пауз между задачами.\n"
        "Задачи с тегом 'platrum' в TickTick — жёсткие блоки, их нельзя двигать.\n\n"
        "7г. Покажи план и жди подтверждения. После — добавь задачи в TickTick "
        f"с startDatetime=\"{next_day_iso}THH:MM\" и endDatetime=\"{next_day_iso}THH:MM\", "
        "обнови статус в Notion на «Спринт неделя».\n\n"
        "Отвечай по-русски.\n\n"
        "ЗАПРЕЩЕНО использовать фразы «Бэклог чист», «беклог пуст», «бэклог пуст» — "
        "они вводят в заблуждение. Если задач на завтра нет — напиши явно сколько задач в бэклоге и почему они не подходят."
    )


@router.callback_query(F.data == "plan_skip")
async def callback_plan_skip(callback: CallbackQuery) -> None:
    """User chose to plan later — dismiss the message."""
    await callback.answer("Окей, напомню в следующий раз 👌")
    await callback.message.edit_text("⏰ Хорошо, вернёмся к планированию позже.")


def _build_plan_prompt(day_label: str, date_str: str, date_iso: str, occupied_slots: str = "", backlog_count: int | None = None) -> str:
    ROADMAPS_DIR = "/home/brain/projects/agent-second-brain/data/roadmaps"
    TT_TOKEN = os.environ.get("TICKTICK_TOKEN", "")
    TT_PROJECTS = "6966e0a0c2c61184ed479a01 6966e1ccaa621184ed479b56 6966e2b53fc19184ed479f22 6966e308c2e99184ed479fb3 inbox13059360"

    if occupied_slots:
        step1 = (
            f"ШАГ 1 — ЗАНЯТЫЕ СЛОТЫ (уже получено из TickTick API, UTC→UTC+10 пересчитано):\n"
            f"{occupied_slots}\n"
            "- 13:00–14:00 Обед\n\n"
            "ПРАВИЛО: каждая строка выше — заблокированное время. Новые задачи туда ставить запрещено.\n"
            "Свободные окна = 09:00–18:00 за вычетом всех перечисленных слотов.\n"
            "Выведи этот список в разделе «📌 Уже занято» без изменений и без сокращений.\n"
            "get_tasks_by_date вызывать НЕ нужно — данные уже есть выше.\n\n"
        )
    else:
        step1 = (
            f"ШАГ 1. Вызови get_tasks_by_date с date=\"{date_iso}\".\n"
            "ПРАВИЛО: КАЖДАЯ задача из результата — это занятый слот. Без исключений.\n"
            "Для каждой задачи: запомни startTime и endTime — это заблокированное время.\n"
            "Добавь в занятые: 13:00–14:00 (обед).\n"
            "Свободные окна = 09:00–18:00 минус все занятые слоты.\n"
            "Выпиши ВСЕ полученные задачи списком (название + время).\n\n"
        )

    return (
        f"Максим хочет собрать план на {day_label} — {date_str}.\n\n"
        "Выполни строго по шагам — не пропускай ни один:\n\n"
        + step1 +
        "ШАГ 2. Найди ПРОСРОЧЕННЫЕ задачи в TickTick (🔴 Приоритет 1 — ставим в план первыми):\n"
        f"```bash\nfor PID in {TT_PROJECTS}; do\n"
        f"  curl -s \"https://api.ticktick.com/open/v1/project/${{PID}}/data\" -H \"Authorization: Bearer {TT_TOKEN}\" | "
        f"python3 -c \"\nimport json,sys\ndata=json.load(sys.stdin)\nfor t in data.get('tasks',[]):\n"
        f"    due=(t.get('dueDate') or '')[:10]\n"
        f"    if due and due < '{date_iso}' and not t.get('completedTime'):\n"
        f"        print(t.get('id','')+'|'+t.get('projectId','')+'|'+due+'|'+t.get('title','')[:60])\n\"\ndone\n```\n"
        "Запомни для каждой просроченной задачи: id и projectId — они понадобятся на шаге 7 чтобы ОБНОВИТЬ дату, а не создать новую.\n\n"

        f"ШАГ 3. Найди задачи из ДОРОЖНЫХ КАРТ проектов (🟡 Приоритет 2 — ставим вторыми):\n"
        f"Прочитай все JSON-файлы из папки {ROADMAPS_DIR}/ командой:\n"
        f"```bash\nls {ROADMAPS_DIR}/*.json 2>/dev/null && "
        f"python3 -c \"\nimport json,os,glob\nfor f in glob.glob('{ROADMAPS_DIR}/*.json'):\n"
        f"    d=json.load(open(f))\n"
        f"    for t in d.get('tasks',[]):\n"
        f"        if t.get('date','') <= '{date_iso}':\n"
        f"            print(t.get('date','')+'|'+d['project']+'|'+t.get('name','')[:50]+'|'+t.get('notion_id',''))\n\"\n```\n"
        "Это задачи из активных проектов с дедлайном сегодня или ранее — их нужно сделать во вторую очередь.\n\n"

        "ШАГ 4. Получи обычные задачи из Notion PM Backlog (⚪ Приоритет 3 — ставим последними):\n"
        "(Database ID: 22876284-e92f-4866-a908-3a3bda425637), статус «Бэклог» или «Спринт неделя», "
        "ответственный = Максим. Сортировка P1 → P2 → P3.\n\n"

        "ШАГ 5. Расставь кандидатов ТОЛЬКО в свободные окна строго в порядке приоритета:\n"
        "  1️⃣ Сначала просроченные из ШАГ 2\n"
        "  2️⃣ Затем задачи из дорожных карт из ШАГ 3\n"
        "  3️⃣ Затем обычные задачи из ШАГ 4\n"
        "Задачи идут друг за другом без пауз. Если свободных окон не хватает — оставляем в бэклоге.\n\n"

        "ШАГ 6. Покажи итог и жди подтверждения. Формат:\n"
        "📌 Уже занято:\n[полный список из ШАГ 1]\n\n"
        "🔴 Просроченные: [список или «нет»]\n\n"
        "📋 Предлагаю добавить:\n"
        "1. [название] — ЧЧ:ММ–ЧЧ:ММ (🔴 просроченная / 🟡 проект X / ⚪ бэклог)\n"
        "2. ...\n\n"

        f"ШАГ 7. После подтверждения действуй по типу задачи:\n"
        f"  🔴 Просроченные (из ШАГ 2) — они уже ЕСТЬ в TickTick, нужно только перенести дату.\n"
        f"    Используй update_task с id и projectId которые запомнил на шаге 2.\n"
        f"    НЕ создавай новую задачу через add_task — иначе будет дубль.\n"
        f"    ВАЖНО: дата и время берётся из того что согласовал пользователь на шаге 6.\n"
        f"    Если пользователь явно назвал дату/время (например «сегодня в 13:00») — используй именно их.\n"
        f"    Правило об обеде 13:00–14:00 действует только при автоматическом подборе слотов.\n"
        f"    Если пользователь сам выбрал 13:00 — ставь в 13:00, это его решение.\n\n"
        f"  🟡 Задачи из дорожных карт (из ШАГ 3) — аналогично, уже есть в TickTick.\n"
        f"    Найди по названию через search_task и используй update_task.\n"
        f"    Только если задача не найдена — создай через add_task.\n\n"
        f"  ⚪ Задачи из Notion бэклога (из ШАГ 4) — их ещё нет в TickTick.\n"
        f"    Создай через add_task с startDatetime=\"{date_iso}THH:MM:SS+10:00\" и endDatetime=\"{date_iso}THH:MM:SS+10:00\".\n"
        "    Обнови статус в Notion на «Спринт неделя».\n\n"
        + (
            f"ℹ️ СПРАВКА: в Notion PM Backlog сейчас {backlog_count} активных задач. "
            "Если ни одна не подходит для плана — объясни почему (просрочены, не тот ответственный и т.п.), "
            "но НЕ говори что бэклог пуст.\n\n"
            if backlog_count is not None else ""
        )
        + "ЗАПРЕЩЕНО использовать фразы «Бэклог чист», «беклог пуст», «бэклог пуст» и любые аналоги — "
        "они вводят в заблуждение. Если задач на сегодня нет — напиши явно сколько задач в бэклоге всего "
        "и почему они не подходят для этого дня (статус, приоритет, уже заполнен день и т.п.).\n\n"
        "Отвечай по-русски, кратко."
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
    occupied_slots = _fetch_ticktick_occupied(date_iso)
    backlog_count = await _count_notion_backlog()
    prompt = _build_plan_prompt(day_label, date_str, date_iso, occupied_slots, backlog_count)
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
    occupied_slots = _fetch_ticktick_occupied(date_iso)
    backlog_count = await _count_notion_backlog()
    prompt = _build_plan_prompt(day_label, date_str, date_iso, occupied_slots, backlog_count)
    await _process_and_reply(bot, callback.message.chat.id, callback.from_user.id, prompt)
