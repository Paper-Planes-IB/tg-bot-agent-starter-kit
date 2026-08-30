#!/usr/bin/env python3
"""Polling Telegram agent for dashboard digests, tasks, summaries, and commands.

This script is intentionally local/server-side. Voice transcription is done by a
local command, so the bot does not depend on OpenAI or another paid speech API.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DATA_DIR = ROOT / "data"
BOT_DISPLAY_NAME = os.environ.get("TG_AGENT_DISPLAY_NAME", "TG Bot Agent")
BOT_PROJECT_NAME = os.environ.get("TG_AGENT_PROJECT_NAME", "Project")
DEFAULT_DASHBOARD_DATA_DIR = DATA_DIR / "dashboard"
DASHBOARD_DATA_DIR = Path(
    os.environ.get("TG_DASHBOARD_DATA_DIR")
    or (DEFAULT_DASHBOARD_DATA_DIR if DEFAULT_DASHBOARD_DATA_DIR.exists() else DATA_DIR)
).expanduser()
LOG_DIR = DATA_DIR / "tg_agent_logs"
SNAPSHOT_DIR = DATA_DIR / "tg_agent_snapshots"
STATE_FILE = DATA_DIR / "tg_dashboard_agent_state.json"
LOCK_FILE = DATA_DIR / "tg_dashboard_agent.lock"
INBOX_FILE = DATA_DIR / "tg_agent_inbox.jsonl"
GROUP_MESSAGES_FILE = DATA_DIR / "tg_group_messages.jsonl"
BUSINESS_MESSAGES_FILE = DATA_DIR / "tg_business_messages.jsonl"
BUSINESS_CONNECTIONS_FILE = DATA_DIR / "tg_business_connections.jsonl"
REMINDERS_FILE = DATA_DIR / "tg_agent_reminders.jsonl"
BRAIN_HISTORY_FILE = DATA_DIR / "tg_agent_brain_history.jsonl"
CHART_DIR = DATA_DIR / "tg_agent_charts"
VOICE_REPLY_STATE_FILE = DATA_DIR / "tg_agent_voice_reply_state.json"
VOICE_REPLY_DIR = DATA_DIR / "tg_agent_voice_replies"
DEFAULT_CONFIG = DATA_DIR / "bot_alert_config.example.json"
DEFAULT_LMS_BUG_DATA_DIR = Path(os.environ.get("TG_LMS_BUG_DATA_DIR", str(DATA_DIR / "lms_bug_bot")))
TG_LMS_HOST = os.environ.get("TG_LMS_HOST", "")
TG_LMS_SSH_PORT = os.environ.get("TG_LMS_SSH_PORT", "2222")
TG_LMS_SSH_KEY = os.environ.get("TG_LMS_SSH_KEY", str(Path.home() / ".ssh/paperplanes_frappe_selectel"))
TG_LMS_CONTAINER = os.environ.get("TG_LMS_CONTAINER", "tg-lms-backend-1")
TG_LMS_SITE = os.environ.get("TG_LMS_SITE", "")
TG_LMS_URL = os.environ.get("TG_LMS_URL", "")
TG_LMS_DASHBOARD_METHOD = os.environ.get("TG_LMS_DASHBOARD_METHOD", "lms.tg_rewards.get_dashboard")
CLICKUP_API_BASE = os.environ.get("CLICKUP_API_BASE", "https://api.clickup.com/api/v2").rstrip("/")
CLICKUP_API_TOKEN_ENV = os.environ.get("CLICKUP_API_TOKEN_ENV", "CLICKUP_API_TOKEN")
CLICKUP_TEAM_ID = os.environ.get("CLICKUP_TEAM_ID", "")
CLICKUP_LIST_ID = os.environ.get("CLICKUP_LIST_ID", "")
CLICKUP_LIST_NAME = os.environ.get("CLICKUP_LIST_NAME", "Main")
CLICKUP_TELEGRAM_USER_MAP = os.environ.get("CLICKUP_TELEGRAM_USER_MAP", "")
TG_FLOWERS_WASTE_SPREADSHEET_ID = os.environ.get("TG_FLOWERS_WASTE_SPREADSHEET_ID", "")
TG_FLOWERS_WASTE_JOURNAL_GID = os.environ.get("TG_FLOWERS_WASTE_JOURNAL_GID", "")
TG_FLOWERS_WASTE_SUMMARY_GID = os.environ.get("TG_FLOWERS_WASTE_SUMMARY_GID", "")
TG_FLOWERS_WASTE_URL = os.environ.get(
    "TG_FLOWERS_WASTE_URL",
    (
        f"https://docs.google.com/spreadsheets/d/{TG_FLOWERS_WASTE_SPREADSHEET_ID}/edit?gid={TG_FLOWERS_WASTE_JOURNAL_GID}#gid={TG_FLOWERS_WASTE_JOURNAL_GID}"
        if TG_FLOWERS_WASTE_SPREADSHEET_ID and TG_FLOWERS_WASTE_JOURNAL_GID
        else ""
    ),
)

SCHEDULED_DIGEST_IDS = {
    "AGENDA",
    "FOCUS",
    "NEXT-ACTION",
    "STANDUP",
    "HOT-TASKS",
    "STALE-TASKS",
    "WAITING-FOLLOWUPS",
    "NUDGES",
    "OUTBOX",
    "DUE-SOON-TASKS",
    "TODAY-TASKS",
    "OVERDUE-TASKS",
    "TASKS",
    "TASK-SUMMARY",
    "ALL-TASKS",
    "DOING-TASKS",
    "WAITING-TASKS",
    "REMINDERS",
    "NEXT-REMINDERS",
    "INBOX-SUMMARY",
    "TODAY-LOG",
    "PERIOD-LOG",
    "PERIOD-REVIEW",
    "HANDOFF",
    "SOURCES",
    "HEALTH",
    "ACTIVITY",
    "DEFECTS",
    "MEETINGS",
    "DECISIONS",
    "RISK-LOG",
    "QUESTIONS",
    "OPEN-QUESTIONS",
    "GROUP-IMPORTANT",
    "BUSINESS-SUMMARY",
    "GROUPS",
    "SEND-GROUP",
    "SCHEDULE-GROUP",
    "LMS-SUMMARY",
    "CLICKUP-STATUS",
    "CLICKUP-TASKS",
    "ACCESS-MESSAGE",
    "DASHBOARD-CHART",
    "WASTE-SUMMARY",
    "WASTE-QA",
    "STATS",
}

sys.path.insert(0, str(SCRIPT_DIR))
import build_bot_digests  # noqa: E402


def dashboard_data_path(name: str) -> Path:
    primary = DASHBOARD_DATA_DIR / name
    if primary.exists():
        return primary
    return DATA_DIR / name


COMMANDS: dict[str, str] = {
    "/start": "HELP",
    "/help": "HELP",
    "помощь": "HELP",
    "что умеешь": "HELP",
    "/health": "HEALTH",
    "/status": "HEALTH",
    "статус бота": "HEALTH",
    "/doctor": "DOCTOR",
    "диагностика": "DOCTOR",
    "/voice_status": "VOICE-STATUS",
    "/voice": "VOICE-STATUS",
    "статус голоса": "VOICE-STATUS",
    "проверка голоса": "VOICE-STATUS",
    "/voice_reply": "VOICE-REPLY",
    "ответы голосом": "VOICE-REPLY",
    "голосовые ответы": "VOICE-REPLY",
    "/voice_lang": "VOICE-LANG",
    "язык голоса": "VOICE-LANG",
    "/ocr_status": "OCR-STATUS",
    "/ocr": "OCR-STATUS",
    "статус ocr": "OCR-STATUS",
    "проверка ocr": "OCR-STATUS",
    "статус фото": "OCR-STATUS",
    "/voice_help": "VOICE-HELP",
    "как говорить с ботом": "VOICE-HELP",
    "что говорить боту": "VOICE-HELP",
    "примеры голоса": "VOICE-HELP",
    "/whoami": "WHOAMI",
    "кто я": "WHOAMI",
    "мой id": "WHOAMI",
    "/reload": "RELOAD",
    "перезагрузка": "RELOAD",
    "перечитай данные": "RELOAD",
    "/undo": "UNDO",
    "/undo_last": "UNDO-LAST",
    "отмени последнее": "UNDO-LAST",
    "отмени запись": "UNDO",
    "/self_test": "SELF-TEST",
    "самопроверка": "SELF-TEST",
    "/agenda": "AGENDA",
    "повестка": "AGENDA",
    "что сегодня": "AGENDA",
    "/focus": "FOCUS",
    "фокус": "FOCUS",
    "что в фокусе": "FOCUS",
    "на чем фокус": "FOCUS",
    "/next_action": "NEXT-ACTION",
    "/next_step": "NEXT-ACTION",
    "что дальше": "NEXT-ACTION",
    "следующий шаг": "NEXT-ACTION",
    "что делать дальше": "NEXT-ACTION",
    "/next_for": "NEXT-FOR",
    "/next_about": "NEXT-FOR",
    "что делать по": "NEXT-FOR",
    "следующий шаг по": "NEXT-FOR",
    "/standup": "STANDUP",
    "/daily_standup": "STANDUP",
    "стендап": "STANDUP",
    "статус на созвон": "STANDUP",
    "короткий статус": "STANDUP",
    "/triage": "TRIAGE",
    "/parse_voice": "TRIAGE",
    "/разбор": "TRIAGE",
    "разбери": "TRIAGE",
    "разложи": "TRIAGE",
    "/voice_digest": "VOICE-DIGEST",
    "/voice_recap": "VOICE-DIGEST",
    "голосовой разбор": "VOICE-DIGEST",
    "разбор голосового": "VOICE-DIGEST",
    "сводка голоса": "VOICE-DIGEST",
    "/review": "PERIOD-REVIEW",
    "/weekly_review": "PERIOD-REVIEW",
    "итоги недели": "PERIOD-REVIEW",
    "обзор недели": "PERIOD-REVIEW",
    "что изменилось": "PERIOD-REVIEW",
    "/inbox": "INBOX",
    "последние записи": "INBOX",
    "что записано": "INBOX",
    "/find": "INBOX-FIND",
    "/search": "INBOX-FIND",
    "/context": "CONTEXT",
    "/context_about": "CONTEXT",
    "контекст": "CONTEXT",
    "контекст по": "CONTEXT",
    "что известно про": "CONTEXT",
    "/thread": "THREAD",
    "/origin": "THREAD",
    "цепочка": "THREAD",
    "что вышло из": "THREAD",
    "/inbox_summary": "INBOX-SUMMARY",
    "сводка журнала": "INBOX-SUMMARY",
    "итоги журнала": "INBOX-SUMMARY",
    "/today_log": "TODAY-LOG",
    "/daily_log": "TODAY-LOG",
    "журнал за сегодня": "TODAY-LOG",
    "что записали сегодня": "TODAY-LOG",
    "что сегодня записали": "TODAY-LOG",
    "/period_log": "PERIOD-LOG",
    "/week_log": "PERIOD-LOG",
    "журнал за неделю": "PERIOD-LOG",
    "что записали за неделю": "PERIOD-LOG",
    "/handoff": "HANDOFF",
    "/shift": "HANDOFF",
    "передача контекста": "HANDOFF",
    "сдай смену": "HANDOFF",
    "handoff": "HANDOFF",
    "/tasks": "TASKS",
    "/list": "TASKS",
    "задачи": "TASKS",
    "открытые задачи": "TASKS",
    "/task_summary": "TASK-SUMMARY",
    "статус задач": "TASK-SUMMARY",
    "сводка задач": "TASK-SUMMARY",
    "/hot_tasks": "HOT-TASKS",
    "/hot": "HOT-TASKS",
    "что горит": "HOT-TASKS",
    "где горит": "HOT-TASKS",
    "операционный обзор": "HOT-TASKS",
    "/stale_tasks": "STALE-TASKS",
    "/stale": "STALE-TASKS",
    "зависшие задачи": "STALE-TASKS",
    "давно не обновлялись": "STALE-TASKS",
    "/direction_tasks": "DIRECTION-TASKS",
    "/for": "ASSIGNEE-TASKS",
    "/assignee": "ASSIGNEE-TASKS",
    "/owner_brief": "OWNER-BRIEF",
    "/brief_for": "OWNER-BRIEF",
    "бриф для": "OWNER-BRIEF",
    "статус для": "OWNER-BRIEF",
    "/assign": "TASK-ASSIGN",
    "/due": "TASK-DUE",
    "/deadline": "TASK-DUE",
    "/priority": "TASK-PRIORITY",
    "/prio": "TASK-PRIORITY",
    "/direction": "TASK-DIRECTION",
    "/move_task": "TASK-DIRECTION",
    "/edit": "TASK-EDIT",
    "/update_task": "TASK-EDIT",
    "/comment": "TASK-COMMENT",
    "/note_task": "TASK-COMMENT",
    "/waiting": "TASK-WAITING",
    "/wait": "TASK-WAITING",
    "/waiting_tasks": "WAITING-TASKS",
    "что ждем": "WAITING-TASKS",
    "задачи в ожидании": "WAITING-TASKS",
    "/waiting_followups": "WAITING-FOLLOWUPS",
    "/followups": "WAITING-FOLLOWUPS",
    "кого пинговать": "WAITING-FOLLOWUPS",
    "кого дернуть": "WAITING-FOLLOWUPS",
    "пинги по ожиданиям": "WAITING-FOLLOWUPS",
    "/nudges": "NUDGES",
    "/followup_messages": "NUDGES",
    "готовые пинги": "NUDGES",
    "сообщения для пинга": "NUDGES",
    "что написать": "NUDGES",
    "/outbox": "OUTBOX",
    "/drafts": "OUTBOX",
    "кому написать": "OUTBOX",
    "черновики сообщений": "OUTBOX",
    "/due_soon": "DUE-SOON-TASKS",
    "/soon": "DUE-SOON-TASKS",
    "ближайшие дедлайны": "DUE-SOON-TASKS",
    "ближайшие задачи": "DUE-SOON-TASKS",
    "/all_tasks": "ALL-TASKS",
    "все задачи": "ALL-TASKS",
    "/activity": "ACTIVITY",
    "/audit": "ACTIVITY",
    "что делал бот": "ACTIVITY",
    "последние действия": "ACTIVITY",
    "/snapshot": "SNAPSHOT",
    "сделай снапшот": "SNAPSHOT",
    "сними снапшот": "SNAPSHOT",
    "/snapshots": "SNAPSHOTS",
    "список снапшотов": "SNAPSHOTS",
    "/doing": "DOING-TASKS",
    "/in_progress": "DOING-TASKS",
    "задачи в работе": "DOING-TASKS",
    "/done_tasks": "DONE-TASKS",
    "закрытые задачи": "DONE-TASKS",
    "/snoozed": "SNOOZED-TASKS",
    "отложенные задачи": "SNOOZED-TASKS",
    "/dropped": "DROPPED-TASKS",
    "убранные задачи": "DROPPED-TASKS",
    "/stats": "STATS",
    "статистика": "STATS",
    "/reminders": "REMINDERS",
    "напоминания": "REMINDERS",
    "/next": "NEXT-REMINDERS",
    "/next_reminders": "NEXT-REMINDERS",
    "ближайшие напоминания": "NEXT-REMINDERS",
    "/brain_status": "BRAIN-STATUS",
    "статус brain": "BRAIN-STATUS",
    "статус свободного диалога": "BRAIN-STATUS",
    "/reset": "BRAIN-RESET",
    "/reset_brain": "BRAIN-RESET",
    "/dedup": "DEDUP",
    "/meeting": "MEETING",
    "/meet": "MEETING",
    "/meetings": "MEETINGS",
    "встречи": "MEETINGS",
    "итоги встречи": "MEETING",
    "разбор встречи": "MEETING",
    "встреча": "MEETING",
    "/decisions": "DECISIONS",
    "решения": "DECISIONS",
    "/rule": "RULE-ADD",
    "/remember_rule": "RULE-ADD",
    "правило": "RULE-ADD",
    "запомни правило": "RULE-ADD",
    "/rules": "RULES",
    "правила": "RULES",
    "покажи правила": "RULES",
    "/risk_log": "RISK-LOG",
    "/blockers": "RISK-LOG",
    "журнал рисков": "RISK-LOG",
    "блокеры": "RISK-LOG",
    "/questions": "QUESTIONS",
    "/question_log": "QUESTIONS",
    "вопросы": "QUESTIONS",
    "/open_questions": "OPEN-QUESTIONS",
    "/unanswered": "OPEN-QUESTIONS",
    "открытые вопросы": "OPEN-QUESTIONS",
    "вопросы без ответа": "OPEN-QUESTIONS",
    "/groups": "GROUPS",
    "группы": "GROUPS",
    "известные группы": "GROUPS",
    "чаты": "GROUPS",
    "известные чаты": "GROUPS",
    "какие чаты": "GROUPS",
    "какие группы": "GROUPS",
    "список чатов": "GROUPS",
    "список групп": "GROUPS",
    "/send_group": "SEND-GROUP",
    "/send_to_group": "SEND-GROUP",
    "/schedule_group": "SCHEDULE-GROUP",
    "/regular_group": "SCHEDULE-GROUP",
    "напиши в группу": "SEND-GROUP",
    "отправь в группу": "SEND-GROUP",
    "попроси в группе": "SEND-GROUP",
    "регулярно в группу": "SCHEDULE-GROUP",
    "поставь регулярный пинг": "SCHEDULE-GROUP",
    "/lms_summary": "LMS-SUMMARY",
    "/lms": "LMS-SUMMARY",
    "lms сводка": "LMS-SUMMARY",
    "сводка lms": "LMS-SUMMARY",
    "lms": "LMS-SUMMARY",
    "lms tg": "LMS-SUMMARY",
    "tg lms": "LMS-SUMMARY",
    "что в lms": "LMS-SUMMARY",
    "статус lms": "LMS-SUMMARY",
    "лмс сводка": "LMS-SUMMARY",
    "сводка лмс": "LMS-SUMMARY",
    "лмс": "LMS-SUMMARY",
    "что в лмс": "LMS-SUMMARY",
    "статус лмс": "LMS-SUMMARY",
    "/clickup_status": "CLICKUP-STATUS",
    "/clickup": "CLICKUP-TASKS",
    "/clickup_tasks": "CLICKUP-TASKS",
    "/my_clickup": "CLICKUP-TASKS",
    "/my_tasks": "CLICKUP-TASKS",
    "clickup": "CLICKUP-TASKS",
    "clickup задачи": "CLICKUP-TASKS",
    "кликап": "CLICKUP-TASKS",
    "кликап задачи": "CLICKUP-TASKS",
    "задачи clickup": "CLICKUP-TASKS",
    "задачи кликап": "CLICKUP-TASKS",
    "задачи по мне": "CLICKUP-TASKS",
    "мои задачи": "CLICKUP-TASKS",
    "что у меня": "CLICKUP-TASKS",
    "менинг вазифаларим": "CLICKUP-TASKS",
    "menga vazifalar": "CLICKUP-TASKS",
    "статус clickup": "CLICKUP-STATUS",
    "статус кликап": "CLICKUP-STATUS",
    "/access": "ACCESS-MESSAGE",
    "/access_message": "ACCESS-MESSAGE",
    "/share_access": "ACCESS-MESSAGE",
    "оформи доступ": "ACCESS-MESSAGE",
    "сформулируй доступ": "ACCESS-MESSAGE",
    "сообщение с доступом": "ACCESS-MESSAGE",
    "собери сообщение для коллег": "ACCESS-MESSAGE",
    "собери сообщение для команды": "ACCESS-MESSAGE",
    "напиши сообщение для коллег": "ACCESS-MESSAGE",
    "напиши сообщение для команды": "ACCESS-MESSAGE",
    "доступ для команды": "ACCESS-MESSAGE",
    "/waste_summary": "WASTE-SUMMARY",
    "/waste": "WASTE-SUMMARY",
    "сводка брака": "WASTE-SUMMARY",
    "сводка списаний": "WASTE-SUMMARY",
    "waste сводка": "WASTE-SUMMARY",
    "что по браку": "WASTE-SUMMARY",
    "что по списаниям": "WASTE-SUMMARY",
    "/dashboard_chart": "DASHBOARD-CHART",
    "/chart": "DASHBOARD-CHART",
    "диаграмма дашборда": "DASHBOARD-CHART",
    "график дашборда": "DASHBOARD-CHART",
    "покажи диаграмму": "DASHBOARD-CHART",
    "покажи график": "DASHBOARD-CHART",
    "диаграмма выручки": "DASHBOARD-CHART",
    "график выручки": "DASHBOARD-CHART",
    "/answer": "QUESTION-ANSWER",
    "/answer_question": "QUESTION-ANSWER",
    "ответ на вопрос": "QUESTION-ANSWER",
    "/pin_tasks": "PIN-TASKS",
    "/refresh_pin": "REFRESH-PIN",
    "/unpin_tasks": "UNPIN-TASKS",
    "закрепи задачи": "PIN-TASKS",
    "обнови закреп": "REFRESH-PIN",
    "убери закреп": "UNPIN-TASKS",
    "/today_tasks": "TODAY-TASKS",
    "задачи на сегодня": "TODAY-TASKS",
    "сегодня задачи": "TODAY-TASKS",
    "/overdue": "OVERDUE-TASKS",
    "просроченные задачи": "OVERDUE-TASKS",
    "просрочка": "OVERDUE-TASKS",
    "/defects": "DEFECTS",
    "список брака": "DEFECTS",
    "/export_inbox": "INBOX-EXPORT",
    "выгрузка журнала": "INBOX-EXPORT",
    "экспорт журнала": "INBOX-EXPORT",
    "/export_memory": "MEMORY-EXPORT",
    "/memory_export": "MEMORY-EXPORT",
    "выгрузка памяти": "MEMORY-EXPORT",
    "экспорт памяти": "MEMORY-EXPORT",
    "/sync_inbox": "INBOX-SYNC",
    "синхронизируй журнал": "INBOX-SYNC",
    "синхронизация журнала": "INBOX-SYNC",
    "/today": "DAILY-EXEC",
    "сводка": "DAILY-EXEC",
    "дай сводку": "DAILY-EXEC",
    "сводка за день": "DAILY-EXEC",
    "/morning": "MORNING-STATUS",
    "утренняя сводка": "STEEL-MORNING",
    "утро": "STEEL-MORNING",
    "что на утро": "STEEL-MORNING",
    "статус данных": "MORNING-STATUS",
    "свежесть данных": "MORNING-STATUS",
    "/week": "WEEKLY-BUSINESS",
    "неделя": "WEEKLY-BUSINESS",
    "недельный отчет": "WEEKLY-BUSINESS",
    "недельный обзор": "STEEL-WEEKLY",
    "обзор за неделю": "STEEL-WEEKLY",
    "/clients": "MONTHLY-CLIENTS",
    "клиенты": "MONTHLY-CLIENTS",
    "/risks": "OPERATIONAL-RISKS",
    "риски": "OPERATIONAL-RISKS",
    "/gaps": "DATA-GAP",
    "пробелы": "DATA-GAP",
    "source gaps": "DATA-GAP",
    "/sources": "SOURCES",
    "источники": "SOURCES",
    "источник": "SOURCES",
    "что обновлено": "SOURCES",
    "файлы": "SOURCES",
    "/rfm": "RFM-MONTH",
    "rfm": "RFM-MONTH",
    "/marketing": "MKT-WEEK",
    "маркетинг": "MKT-WEEK",
    "конкуренты": "MKT-WEEK",
    "/defect": "DEFECT-IN",
    "брак": "DEFECT-IN",
    "списание": "DEFECT-IN",
}

TELEGRAM_BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "agenda", "description": "повестка: сводка, задачи, риски"},
    {"command": "focus", "description": "что требует внимания сейчас"},
    {"command": "next_action", "description": "одно рекомендуемое следующее действие"},
    {"command": "next_for", "description": "следующий шаг по теме"},
    {"command": "standup", "description": "короткий статус для созвона"},
    {"command": "triage", "description": "разложить текст на задачи, вопросы, риски"},
    {"command": "voice_digest", "description": "сохранить голос и разложить пункты"},
    {"command": "review", "description": "обзор изменений за период"},
    {"command": "today", "description": "daily executive summary"},
    {"command": "morning", "description": "статус свежести данных"},
    {"command": "steel_morning", "description": "утро: поручения, просрочка, сроки, вопросы"},
    {"command": "steel_weekly", "description": "неделя: выполнено, открыто, блокеры"},
    {"command": "lms_summary", "description": "сводка из TG LMS"},
    {"command": "clickup", "description": "открытые задачи ClickUp"},
    {"command": "clickup_status", "description": "проверка подключения ClickUp"},
    {"command": "access", "description": "оформить доступ в сообщение для команды"},
    {"command": "dashboard_chart", "description": "диаграмма по дашборду"},
    {"command": "waste_summary", "description": "сводка брака и списаний TG Flowers"},
    {"command": "dashboard", "description": "вопрос к дашборду обычным языком"},
    {"command": "sources", "description": "источники и свежесть файлов"},
    {"command": "add", "description": "добавить задачу"},
    {"command": "list", "description": "открытые задачи"},
    {"command": "task_summary", "description": "сводка по задачам"},
    {"command": "hot_tasks", "description": "что горит: просрочка, сроки, ожидания"},
    {"command": "stale_tasks", "description": "задачи без апдейтов N дней"},
    {"command": "direction_tasks", "description": "задачи по направлению"},
    {"command": "for", "description": "задачи по ответственному"},
    {"command": "owner_brief", "description": "бриф по ответственному"},
    {"command": "assign", "description": "назначить ответственного"},
    {"command": "due", "description": "перенести срок задачи"},
    {"command": "priority", "description": "сменить приоритет задачи"},
    {"command": "direction", "description": "сменить направление задачи"},
    {"command": "all_tasks", "description": "все задачи"},
    {"command": "activity", "description": "последние действия бота"},
    {"command": "snapshot", "description": "сохранить снимок состояния"},
    {"command": "snapshots", "description": "список снимков состояния"},
    {"command": "doing", "description": "задачи в работе"},
    {"command": "done_tasks", "description": "закрытые задачи"},
    {"command": "snoozed", "description": "отложенные задачи"},
    {"command": "dropped", "description": "убранные задачи"},
    {"command": "done", "description": "закрыть задачу по id"},
    {"command": "snooze", "description": "отложить задачу"},
    {"command": "drop", "description": "убрать задачу"},
    {"command": "reopen", "description": "вернуть задачу"},
    {"command": "edit", "description": "изменить задачу по id"},
    {"command": "comment", "description": "добавить апдейт к задаче"},
    {"command": "waiting", "description": "список ожиданий или waiting <id>"},
    {"command": "waiting_tasks", "description": "задачи в ожидании"},
    {"command": "waiting_followups", "description": "готовые пинги по ожиданиям"},
    {"command": "nudges", "description": "готовые тексты пингов"},
    {"command": "outbox", "description": "черновики сообщений по людям"},
    {"command": "due_soon", "description": "задачи со сроком в ближайшие N дней"},
    {"command": "voice_help", "description": "примеры голосовых команд"},
    {"command": "voice_reply", "description": "включить или выключить голосовые ответы"},
    {"command": "voice_lang", "description": "язык голосовых ответов: ru, uz, auto"},
    {"command": "remind", "description": "поставить напоминание"},
    {"command": "reminders", "description": "список напоминаний"},
    {"command": "next_reminders", "description": "ближайшие напоминания"},
    {"command": "brain_status", "description": "статус свободного диалога"},
    {"command": "pin_tasks", "description": "закрепить список задач"},
    {"command": "refresh_pin", "description": "обновить закреп задач"},
    {"command": "unpin_tasks", "description": "убрать закреп задач"},
    {"command": "inbox", "description": "последние записи журнала"},
    {"command": "context", "description": "контекст по теме из журнала"},
    {"command": "thread", "description": "исходник и дочерние пункты"},
    {"command": "inbox_summary", "description": "сводка журнала"},
    {"command": "today_log", "description": "что записали сегодня"},
    {"command": "period_log", "description": "журнал за N дней"},
    {"command": "handoff", "description": "передача контекста за период"},
    {"command": "find", "description": "поиск по журналу"},
    {"command": "export_inbox", "description": "CSV-выгрузка журнала"},
    {"command": "export_memory", "description": "Markdown-выгрузка рабочей памяти"},
    {"command": "sync_inbox", "description": "отправить журнал в Sheet"},
    {"command": "dedup", "description": "найти дубли журнала"},
    {"command": "meeting", "description": "разобрать итоги встречи в задачи"},
    {"command": "meetings", "description": "последние встречи"},
    {"command": "decision", "description": "записать решение"},
    {"command": "decisions", "description": "последние решения"},
    {"command": "rule", "description": "записать правило проекта"},
    {"command": "rules", "description": "последние правила проекта"},
    {"command": "risk_log", "description": "последние риски и блокеры"},
    {"command": "questions", "description": "последние вопросы"},
    {"command": "open_questions", "description": "вопросы без ответа"},
    {"command": "answer", "description": "закрыть вопрос ответом"},
    {"command": "groups", "description": "известные группы для отправки"},
    {"command": "send_group", "description": "отправить сообщение в группу"},
    {"command": "schedule_group", "description": "регулярная отправка в группу"},
    {"command": "stats", "description": "статистика журнала"},
    {"command": "whoami", "description": "показать chat_id и user_id"},
    {"command": "undo", "description": "мягко отменить запись по id"},
    {"command": "voice_status", "description": "статус локальной расшифровки"},
    {"command": "ocr_status", "description": "статус чтения фото и документов"},
    {"command": "doctor", "description": "диагностика запуска"},
    {"command": "health", "description": "статус подключений"},
    {"command": "reload", "description": "перечитать файлы и статус"},
    {"command": "help", "description": "справка"},
]

DIRECTIONS = {
    "flowers": "Flowers",
    "флауэрс": "Flowers",
    "цветы": "Flowers",
    "nour": "Nour",
    "нур": "Nour",
    "plants": "Plants",
    "плантс": "Plants",
    "растения": "Plants",
    "wedding": "Wedding",
    "свадьбы": "Wedding",
    "ecom": "E-com",
    "e-com": "E-com",
    "еком": "E-com",
    "яком": "E-com",
    "gourmet": "Gourmet",
    "гурме": "Gourmet",
    "guul": "Guul",
    "гул": "Guul",
    "school": "School",
    "школа": "School",
    "b2b": "B2B Опт",
    "опт": "B2B Опт",
    "corp": "Corp",
    "корп": "Corp",
    "закупка": "Закупка Flowers",
}


@dataclasses.dataclass
class AgentConfig:
    digest_config: Path
    token_env: str
    default_chat_env: str
    allowed_user_ids_env: str
    allowed_chat_ids_env: str
    parse_mode: str
    poll_timeout: int
    sleep_seconds: int
    voice_enabled: bool
    voice_command: str
    voice_language: str
    voice_model: str
    voice_max_seconds: int
    max_download_mb: int
    sheets_webhook_env: str
    sheets_secret_env: str
    sheets_sync_on_save: bool
    media_store_enabled: bool
    media_store_dir: Path
    ocr_enabled: bool
    ocr_command: str
    ocr_max_mb: int
    ocr_timeout_seconds: int
    brain_enabled: bool
    brain_command: str
    brain_timeout: int
    brain_history_turns: int


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_agent_config(path: Path | None = None) -> AgentConfig:
    raw: dict[str, Any] = {}
    config_path = Path(path).expanduser().resolve() if path else None
    if config_path and config_path.exists():
        raw = read_json(config_path)
    def resolve_runtime_path(value: Any, default: Path) -> Path:
        candidate = Path(value or default).expanduser()
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        return candidate
    agent = raw.get("agent") or {}
    voice = raw.get("voice") or {}
    sheets = raw.get("sheets") or {}
    media = raw.get("media") or {}
    ocr = raw.get("ocr") or {}
    brain = raw.get("brain") or {}
    brain_enabled_raw = os.environ.get("TG_AGENT_BRAIN_ENABLED")
    ocr_enabled_raw = os.environ.get("TG_AGENT_OCR_ENABLED")
    return AgentConfig(
        digest_config=resolve_runtime_path(raw.get("digest_config"), DEFAULT_CONFIG),
        token_env=str(agent.get("bot_token_env") or "TG_DASHBOARD_BOT_TOKEN"),
        default_chat_env=str(agent.get("default_chat_id_env") or "TG_DASHBOARD_DRY_RUN_CHAT_ID"),
        allowed_user_ids_env=str(agent.get("allowed_user_ids_env") or "TG_AGENT_ALLOWED_USER_IDS"),
        allowed_chat_ids_env=str(agent.get("allowed_chat_ids_env") or "TG_AGENT_ALLOWED_CHAT_IDS"),
        parse_mode=str(agent.get("parse_mode") or "HTML"),
        poll_timeout=int(agent.get("poll_timeout") or 25),
        sleep_seconds=int(agent.get("sleep_seconds") or 3),
        voice_enabled=bool(voice.get("enabled", True)),
        voice_command=str(voice.get("transcribe_command") or os.environ.get("TG_VOICE_TRANSCRIBE_COMMAND") or ""),
        voice_language=str(voice.get("language") or "ru"),
        voice_model=str(voice.get("model") or "small"),
        voice_max_seconds=int(voice.get("max_seconds") or 300),
        max_download_mb=int(voice.get("max_download_mb") or 25),
        sheets_webhook_env=str(sheets.get("webhook_url_env") or "TG_AGENT_SHEETS_WEBHOOK_URL"),
        sheets_secret_env=str(sheets.get("secret_env") or "TG_AGENT_SHEETS_SECRET"),
        sheets_sync_on_save=bool(sheets.get("sync_on_save", False)),
        media_store_enabled=bool(media.get("store_enabled", True)),
        media_store_dir=resolve_runtime_path(media.get("store_dir"), DATA_DIR / "tg_agent_media"),
        ocr_enabled=(ocr_enabled_raw == "1") if ocr_enabled_raw is not None else bool(ocr.get("enabled", False)),
        ocr_command=str(ocr.get("command") or os.environ.get("TG_AGENT_OCR_COMMAND") or ""),
        ocr_max_mb=int(ocr.get("max_mb") or os.environ.get("TG_AGENT_OCR_MAX_MB") or 10),
        ocr_timeout_seconds=int(ocr.get("timeout_seconds") or os.environ.get("TG_AGENT_OCR_TIMEOUT") or 30),
        brain_enabled=(brain_enabled_raw == "1") if brain_enabled_raw is not None else bool(brain.get("enabled", False)),
        brain_command=str(os.environ.get("TG_AGENT_BRAIN_COMMAND") or brain.get("command") or "codex exec --skip-git-repo-check --output-last-message {output} -"),
        brain_timeout=int(os.environ.get("TG_AGENT_BRAIN_TIMEOUT") or brain.get("timeout_seconds") or 90),
        brain_history_turns=int(os.environ.get("TG_AGENT_BRAIN_HISTORY_TURNS") or brain.get("history_turns") or 8),
    )


def token(config: AgentConfig) -> str:
    value = os.environ.get(config.token_env, "")
    if not value:
        raise RuntimeError(f"Missing Telegram token env: {config.token_env}")
    return value


def parse_id_set(raw: str) -> set[str]:
    return {part.strip() for part in re.split(r"[,;\s]+", raw or "") if part.strip()}


def configured_allowed_user_ids(config: AgentConfig) -> set[str]:
    return parse_id_set(os.environ.get(config.allowed_user_ids_env, ""))


def configured_allowed_chat_ids(config: AgentConfig) -> set[str]:
    return parse_id_set(os.environ.get(config.allowed_chat_ids_env, ""))


def is_authorized(config: AgentConfig, chat_id: str | int = "", user_id: str | int = "") -> bool:
    allowed_users = configured_allowed_user_ids(config)
    allowed_chats = configured_allowed_chat_ids(config)
    if not allowed_users and not allowed_chats:
        return True
    chat_ok = bool(chat_id) and str(chat_id) in allowed_chats
    user_ok = bool(user_id) and str(user_id) in allowed_users
    if chat_ok:
        return True
    if user_ok and (not allowed_chats or str(chat_id) == str(user_id)):
        return True
    return False


def is_public_access_command(text: str) -> bool:
    normalized = strip_bot_command_mention(normalize_text(text))
    return normalized in {
        "/start",
        "/help",
        "помощь",
        "что умеешь",
        "/whoami",
        "кто я",
        "мой id",
    }


def strip_bot_command_mention(text: str) -> str:
    return re.sub(r"^(/[\w_]+)@[a-z0-9_]+(\s|$)", r"\1\2", text, flags=re.IGNORECASE).strip()


def authorization_detail(config: AgentConfig) -> str:
    users = configured_allowed_user_ids(config)
    chats = configured_allowed_chat_ids(config)
    if not users and not chats:
        return "open"
    bits = []
    if users:
        bits.append(f"users:{len(users)}")
    if chats:
        bits.append(f"chats:{len(chats)}")
    return ", ".join(bits)


def telegram_request(config: AgentConfig, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    api_url = f"https://api.telegram.org/bot{token(config)}/{method}"
    encoded = urllib.parse.urlencode(data or {}).encode("utf-8")
    request = urllib.request.Request(api_url, data=encoded, method="POST")
    with urllib.request.urlopen(request, timeout=90) as response:
        body = response.read().decode("utf-8")
    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(result.get("description") or f"Telegram {method} failed")
    return result


def telegram_plain_text(value: str) -> str:
    text = re.sub(r"</?(?:b|strong|i|em|u|s|strike|del|code|pre|a)(?:\s+[^>]*)?>", "", value or "")
    return html.unescape(text)


def telegram_send(
    config: AgentConfig,
    chat_id: str | int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    if config.parse_mode:
        payload["parse_mode"] = config.parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    try:
        return telegram_request(config, "sendMessage", payload)
    except Exception as exc:
        if not config.parse_mode or "400" not in str(exc):
            raise
        fallback = dict(payload)
        fallback.pop("parse_mode", None)
        fallback["text"] = telegram_plain_text(text)[:3900]
        append_log("send_message_plain_fallback", {"chat_id": chat_id, "error": str(exc)})
        return telegram_request(config, "sendMessage", fallback)


def telegram_edit_message(
    config: AgentConfig,
    chat_id: str | int,
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:3900],
        "disable_web_page_preview": True,
    }
    if config.parse_mode:
        payload["parse_mode"] = config.parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    return telegram_request(config, "editMessageText", payload)


def telegram_pin_message(config: AgentConfig, chat_id: str | int, message_id: int) -> dict[str, Any]:
    return telegram_request(config, "pinChatMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
        "disable_notification": True,
    })


def telegram_unpin_message(config: AgentConfig, chat_id: str | int, message_id: int) -> dict[str, Any]:
    return telegram_request(config, "unpinChatMessage", {"chat_id": chat_id, "message_id": message_id})


def telegram_set_commands(config: AgentConfig) -> dict[str, Any]:
    return telegram_request(config, "setMyCommands", {
        "commands": json.dumps(TELEGRAM_BOT_COMMANDS, ensure_ascii=False),
        "scope": json.dumps({"type": "default"}, ensure_ascii=False),
        "language_code": "ru",
    })


def telegram_check(config: AgentConfig, send_health: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "send_health": False}
    me = telegram_request(config, "getMe", {})
    user = me.get("result") or {}
    result.update({
        "ok": True,
        "bot_id": user.get("id"),
        "username": user.get("username"),
        "first_name": user.get("first_name"),
    })
    if send_health:
        chat_id = os.environ.get(config.default_chat_env, "")
        if not chat_id:
            result.update({"ok": False, "error": f"Missing {config.default_chat_env}"})
            return result
        response = telegram_send(config, chat_id, build_health_message(config))
        result.update({
            "send_health": True,
            "chat_id": redact_value(chat_id),
            "message_id": response.get("result", {}).get("message_id"),
        })
    return result


def telegram_answer_callback(config: AgentConfig, callback_query_id: str, text: str = "") -> None:
    if not callback_query_id:
        return
    try:
        telegram_request(config, "answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text[:180]})
    except Exception as exc:
        append_log("callback_answer_error", {"error": str(exc), "callback_query_id": callback_query_id})


def redact_value(value: str) -> str:
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def telegram_send_document(config: AgentConfig, chat_id: str | int, path: Path, caption: str = "") -> dict[str, Any]:
    boundary = f"----tg-bot-agent-{int(time.time() * 1000)}"
    fields = {"chat_id": str(chat_id), "caption": caption[:1024]}
    if config.parse_mode:
        fields["parse_mode"] = config.parse_mode
    body = multipart_body(
        boundary,
        fields=fields,
        files={"document": path},
    )
    api_url = f"https://api.telegram.org/bot{token(config)}/sendDocument"
    request = urllib.request.Request(api_url, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Content-Length", str(len(body)))
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description") or "Telegram sendDocument failed")
    return result


def telegram_send_photo(config: AgentConfig, chat_id: str | int, path: Path, caption: str = "") -> dict[str, Any]:
    boundary = f"----tg-bot-agent-photo-{int(time.time() * 1000)}"
    body = multipart_body(
        boundary,
        fields={"chat_id": str(chat_id), "caption": caption[:1024], "parse_mode": config.parse_mode},
        files={"photo": path},
    )
    api_url = f"https://api.telegram.org/bot{token(config)}/sendPhoto"
    request = urllib.request.Request(api_url, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Content-Length", str(len(body)))
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description") or "Telegram sendPhoto failed")
    return result


def telegram_send_voice(config: AgentConfig, chat_id: str | int, path: Path, caption: str = "") -> dict[str, Any]:
    boundary = f"----tg-bot-agent-voice-{int(time.time() * 1000)}"
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:1024]
    body = multipart_body(
        boundary,
        fields=fields,
        files={"voice": path},
    )
    api_url = f"https://api.telegram.org/bot{token(config)}/sendVoice"
    request = urllib.request.Request(api_url, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Content-Length", str(len(body)))
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description") or "Telegram sendVoice failed")
    return result


def multipart_body(boundary: str, fields: dict[str, str], files: dict[str, Path]) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        if value == "":
            continue
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
            value.encode("utf-8"),
            b"\r\n",
        ])
    for name, path in files.items():
        suffix = path.suffix.lower()
        content_type = (
            "image/png" if suffix == ".png"
            else "audio/ogg" if suffix in {".ogg", ".oga", ".opus"}
            else "text/csv" if suffix == ".csv"
            else "application/octet-stream"
        )
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            path.read_bytes(),
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)


def telegram_action(config: AgentConfig, chat_id: str | int, action: str) -> None:
    try:
        telegram_request(config, "sendChatAction", {"chat_id": chat_id, "action": action})
    except Exception as exc:
        append_log("chat_action_error", {"chat_id": chat_id, "action": action, "error": str(exc)})


def telegram_react(config: AgentConfig, chat_id: str | int, message_id: int | None, emoji: str) -> None:
    if not message_id:
        return
    enabled = os.environ.get("TG_AGENT_REACTIONS_ENABLED", "0").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    try:
        telegram_request(config, "setMessageReaction", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": json.dumps([{"type": "emoji", "emoji": emoji}], ensure_ascii=False),
        })
    except Exception as exc:
        append_log("reaction_error", {"chat_id": chat_id, "message_id": message_id, "error": str(exc)})


def append_log(kind: str, payload: dict[str, Any]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{dt.date.today().isoformat()}.jsonl"
    row = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "kind": kind, "payload": payload}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def safe_slug(value: str, fallback: str = "snapshot") -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-Я._-]+", "-", value.strip()).strip("-._")
    return slug[:60] or fallback


def state_snapshot_sources() -> dict[str, Path]:
    return {
        "inbox": INBOX_FILE,
        "reminders": REMINDERS_FILE,
        "brain_history": BRAIN_HISTORY_FILE,
        "state": STATE_FILE,
    }


def create_state_snapshot(label: str = "") -> dict[str, Any]:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = safe_slug(label, "manual")
    target = SNAPSHOT_DIR / f"{stamp}-{slug}"
    target.mkdir(parents=True, exist_ok=False)
    copied: list[dict[str, Any]] = []
    missing: list[str] = []
    for name, source in state_snapshot_sources().items():
        if source.exists():
            dest = target / source.name
            shutil.copy2(source, dest)
            copied.append({"name": name, "file": dest.name, "bytes": dest.stat().st_size})
        else:
            missing.append(name)
    manifest = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "snapshot": target.name,
        "root": str(ROOT),
        "copied": copied,
        "missing": missing,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    append_log("state_snapshot_created", {"snapshot": target.name, "copied": [item["name"] for item in copied], "missing": missing})
    return {"ok": True, "path": target, "manifest": manifest}


def list_state_snapshots(limit: int = 10) -> list[dict[str, Any]]:
    if not SNAPSHOT_DIR.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted([item for item in SNAPSHOT_DIR.iterdir() if item.is_dir()], reverse=True)[:limit]:
        manifest_path = path / "manifest.json"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
        rows.append({
            "name": path.name,
            "created_at": manifest.get("created_at") or "",
            "label": manifest.get("label") or "",
            "files": len(manifest.get("copied") or []),
        })
    return rows


def restore_state_snapshot(name: str) -> dict[str, Any]:
    snapshot_name = safe_slug(name, "")
    if not snapshot_name:
        return {"ok": False, "error": "Нужно имя снапшота"}
    source_dir = SNAPSHOT_DIR / snapshot_name
    manifest_path = source_dir / "manifest.json"
    if not source_dir.exists() or not manifest_path.exists():
        return {"ok": False, "error": f"Не нашла снапшот {snapshot_name}"}
    safety = create_state_snapshot(f"before-restore-{snapshot_name}")
    restored: list[str] = []
    for name_key, target_path in state_snapshot_sources().items():
        source_path = source_dir / target_path.name
        if source_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            restored.append(name_key)
    append_log("state_snapshot_restored", {"snapshot": snapshot_name, "restored": restored, "safety_snapshot": (safety.get("path") or Path("")).name})
    return {"ok": True, "snapshot": snapshot_name, "restored": restored, "safety_snapshot": (safety.get("path") or Path("")).name}


def build_snapshot_message(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return escape_html(str(result.get("error") or "Не смогла создать снапшот"))
    path = result.get("path")
    manifest = result.get("manifest") or {}
    copied = ", ".join(item["name"] for item in manifest.get("copied") or []) or "пусто"
    missing = ", ".join(manifest.get("missing") or []) or "нет"
    return "\n".join([
        "<b>Снапшот состояния создан</b>",
        f"name: <code>{escape_html(path.name if isinstance(path, Path) else str(path))}</code>",
        f"files: {escape_html(copied)}",
        f"missing: {escape_html(missing)}",
        "Восстановление только локально: <code>restore-snapshot &lt;name&gt;</code>",
    ])


def build_snapshots_message(limit: int = 10) -> str:
    rows = list_state_snapshots(limit=limit)
    if not rows:
        return "Снапшотов пока нет."
    lines = ["<b>Снапшоты состояния</b>"]
    for row in rows:
        label = f" / {row['label']}" if row.get("label") else ""
        lines.append(f"- <code>{escape_html(str(row['name']))}</code>{escape_html(label)} / files: {row.get('files') or 0}")
    return "\n".join(lines)


def read_recent_logs(limit: int = 10) -> list[dict[str, Any]]:
    if not LOG_DIR.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(LOG_DIR.glob("*.jsonl"))[-7:]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"offset": 0}
    try:
        return read_json(STATE_FILE)
    except Exception:
        return {"offset": 0}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def allowed_updates_payload() -> str:
    return json.dumps([
        "message",
        "callback_query",
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
    ], ensure_ascii=False)


def poll_updates(config: AgentConfig, state: dict[str, Any]) -> list[dict[str, Any]]:
    result = telegram_request(config, "getUpdates", {
        "offset": int(state.get("offset", 0)),
        "timeout": config.poll_timeout,
        "allowed_updates": allowed_updates_payload(),
    })
    updates = result.get("result") or []
    if updates:
        state["offset"] = max(int(update["update_id"]) for update in updates) + 1
    return updates


def drop_pending_updates(config: AgentConfig) -> dict[str, Any]:
    state = load_state()
    before = int(state.get("offset", 0) or 0)
    result = telegram_request(config, "getUpdates", {
        "offset": -1,
        "timeout": 0,
        "limit": 1,
        "allowed_updates": allowed_updates_payload(),
    })
    updates = result.get("result") or []
    if updates:
        last_update_id = max(int(update.get("update_id") or 0) for update in updates)
        state["offset"] = last_update_id + 1
    else:
        last_update_id = before - 1 if before else 0
        state["offset"] = before
    save_state(state)
    dropped_estimate = max(0, int(state.get("offset", 0)) - before)
    append_log("drop_pending_updates", {
        "before_offset": before,
        "after_offset": state.get("offset", 0),
        "last_update_id": last_update_id,
        "dropped_estimate": dropped_estimate,
    })
    return {
        "ok": True,
        "before_offset": before,
        "after_offset": state.get("offset", 0),
        "last_update_id": last_update_id,
        "dropped_estimate": dropped_estimate,
    }


def discover_chats(config: AgentConfig) -> list[dict[str, Any]]:
    result = telegram_request(config, "getUpdates", {
        "timeout": 5,
        "allowed_updates": allowed_updates_payload(),
    })
    chats: dict[str, dict[str, Any]] = {}
    for update in result.get("result") or []:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        chats[str(chat_id)] = {
            "chat_id": chat_id,
            "type": chat.get("type"),
            "title": chat.get("title") or " ".join(part for part in [chat.get("first_name"), chat.get("last_name")] if part),
            "username": chat.get("username"),
            "last_text": message.get("text") or message.get("caption"),
        }
    return list(chats.values())


def help_text() -> str:
    return "\n".join([
        f"<b>{BOT_DISPLAY_NAME}</b>",
        "Понимаю текстовые команды и голосовые сообщения.",
        "Чтобы продолжить диалог с Лили, ответь reply на сообщение бота.",
        "",
        "/health - статус подключения и локальных файлов",
        "/doctor - диагностика готовности запуска",
        "/voice_status - статус локальной расшифровки",
        "/voice_help - примеры голосовых сценариев",
        "/voice_reply on/off - дублировать ответы голосовым сообщением",
        "/voice_lang ru/uz/auto - язык голосовых ответов",
        "/whoami - показать chat_id и user_id",
        "/reload - перечитать файлы и показать статус",
        "/undo <id> - мягко отменить ошибочную запись",
        "/self_test - короткая самопроверка",
        "/agenda - повестка: сводка, задачи, риски запуска",
        "/focus - что требует внимания сейчас",
        "/next_action - одно рекомендуемое следующее действие",
        "/next_for <тема> - следующий шаг по теме",
        "/standup - короткий статус для созвона",
        "/triage <текст> - разложить пачку пунктов в журнал",
        "/voice_digest <текст> - сохранить голосовой разбор и связанные пункты",
        "/review [дней] - обзор изменений за период",
        "/inbox - последние сохраненные записи",
        "/context <тема> - собрать контекст по теме из журнала",
        "/inbox_summary - сводка по журналу",
        "/find <запрос> - поиск по журналу",
        "/add <текст> - добавить задачу",
        "/list - открытые задачи",
        "/tasks - открытые задачи",
        "/task_summary - сводка по задачам",
        "/hot_tasks - что горит по задачам",
        "/stale_tasks [дней] - открытые задачи без апдейтов",
        "/tasks <направление> - открытые задачи по направлению",
        "/for <имя> - открытые задачи по ответственному",
        "/owner_brief <имя> - бриф по ответственному",
        "/all_tasks - все задачи",
        "/doing - задачи в работе",
        "/done_tasks - закрытые задачи",
        "/snoozed - отложенные задачи",
        "/dropped - убранные задачи",
        "/task <id> - карточка задачи",
        "/comment <id> <текст> - добавить апдейт к задаче",
        "/waiting - задачи в ожидании",
        "/waiting_followups - кого пинговать по ожиданиям",
        "/nudges - готовые тексты пингов по ожиданиям и вопросам",
        "/due_soon [дней] - ближайшие дедлайны",
        "/today_tasks - задачи на сегодня",
        "/overdue - просроченные задачи",
        "/defects - последние записи брака",
        "/done <id> - закрыть задачу",
        "/close <id> - закрыть задачу, алиас /done",
        "/edit <id> <текст/поля> - изменить задачу",
        "/assign <id> <имя> - назначить ответственного",
        "/due <id> <дата> - перенести срок задачи",
        "/priority <id> <high|medium|low> - сменить приоритет",
        "/direction <id> <направление> - сменить направление",
        "/waiting <id> <причина> - пометить задачу как ожидание",
        "/snooze <id> - отложить задачу",
        "/drop <id> - убрать задачу из открытых",
        "/reopen <id> - вернуть задачу в открытые",
        "/start_task <id> - взять задачу в работу",
        "/pending <id> - вернуть задачу из работы в открытые",
        "/stats - статистика журнала и задач",
        "/remind <когда> <что> - поставить напоминание",
        "/reminders - список напоминаний",
        "/next_reminders - ближайшие напоминания",
        "/cancel_reminder <id> - отменить напоминание",
        "/reset - сбросить историю свободного диалога",
        "/reset_brain - то же самое",
        "/dedup - найти дубли журнала",
        "/dedup apply - пометить дубли в журнале",
        "/meeting <итоги> - сохранить встречу и разложить поручения в задачи",
        "/meetings - последние встречи",
        "/decision <текст> - записать решение",
        "/decisions - последние решения",
        "/rule <текст> - записать правило проекта",
        "/rules - последние правила проекта",
        "/risk_log - последние риски и блокеры",
        "/questions - последние вопросы",
        "/open_questions - вопросы без ответа",
        "/answer <id> <текст> - закрыть вопрос ответом",
        "/groups - известные группы для отправки",
        "/send_group <группа> | <текст> - отправить сообщение в группу",
        "/schedule_group <группа> | <расписание> | <текст> - регулярная отправка в группу",
        "/access <ссылка, логин, пароль> - оформить доступ в сообщение для команды",
        "/pin_tasks - закрепить список открытых задач",
        "/refresh_pin - обновить закрепленный список задач",
        "/unpin_tasks - убрать закрепленный список задач",
        "/export_inbox - CSV-выгрузка журнала",
        "/sync_inbox - отправить журнал в Google Sheet",
        "/today - daily executive summary",
        "/morning - статус данных",
        "/week - недельный отчет",
        "/clients - клиентский отчет",
        "/risks - операционные риски",
        "/gaps - source gaps",
        "/sources - свежесть исходников",
        "/rfm - RFM",
        "/marketing - маркетинг/конкуренты",
        "/defect - режим учета брака",
        "",
        "Голосом можно сказать: дай сводку, риски, маркетинг, клиенты, источники.",
        "Для записи в журнал: задача ..., заметка ..., брак ..., итоги встречи ...",
        "Для голосовых нужен локальный транскрибатор: TG_VOICE_TRANSCRIBE_COMMAND или whisper CLI.",
    ])


def normalize_text(value: str) -> str:
    text = value.strip().lower().replace("ё", "е")
    text = text.replace("’", "'").replace("`", "'").replace("ʻ", "'").replace("‘", "'")
    replacements = {
        "выручке": "выручка",
        "выручки": "выручка",
        "выручку": "выручка",
        "виручка": "выручка",
        "виручке": "выручка",
        "вуручка": "выручка",
        "продажам": "продажи",
        "продажах": "продажи",
        "продаж": "продажи",
        "обороту": "оборот",
        "оборота": "оборот",
        "обороты": "оборот",
        "сводку": "сводка",
        "сводке": "сводка",
        "диограм": "диаграм",
        "диограмма": "диаграмма",
        "дашборду": "дашборд",
        "дашборда": "дашборд",
        "дашборде": "дашборд",
        "кликапу": "кликап",
        "кликапа": "кликап",
        "кликапе": "кликап",
        "чувсвиель": "чувствитель",
        "brak": "брак",
        "otkan": "утган",
        "o'tgan": "утган",
    }
    for source, target in replacements.items():
        text = re.sub(rf"\b{re.escape(source)}\w*\b", target, text)
    return " ".join(text.split())


def detect_text_language(value: str) -> str:
    normalized = normalize_text(value)
    uz_markers = {
        "nima",
        "nimalar",
        "qancha",
        "qaysi",
        "qayer",
        "kecha",
        "bugun",
        "ertaga",
        "hafta",
        "oy",
        "yil",
        "menga",
        "mening",
        "vazifa",
        "vazifalar",
        "savdo",
        "savdosi",
        "tekshirish",
        "kerak",
        "mas'ul",
        "muddat",
        "boldi",
        "bo'ldi",
        "buldi",
        "utgan",
        "утган",
        "brak",
    }
    if any(marker in normalized for marker in uz_markers):
        return "uz"
    if re.search(r"[ўқғҳ]", normalized):
        return "uz"
    return "ru"


def localize_answer_for_text(answer: str, source_text: str) -> str:
    if detect_text_language(source_text) != "uz":
        return answer
    replacements = [
        ("<b>ClickUp подключен</b>", "<b>ClickUp ulangan</b>"),
        ("<b>ClickUp задачи по мне</b>", "<b>ClickUp: mening vazifalarim</b>"),
        ("<b>ClickUp задачи</b>", "<b>ClickUp vazifalari</b>"),
        ("<b>Ближайшие задачи</b>", "<b>Yaqin vazifalar</b>"),
        ("<b>Ближайшие / первые в списке</b>", "<b>Yaqin / ro'yxatdagi birinchi vazifalar</b>"),
        ("<b>TG Flowers: брак и списания</b>", "<b>TG Flowers: brak va hisobdan chiqarishlar</b>"),
        ("<b>Ответ по таблице брака TG Flowers</b>", "<b>TG Flowers brak jadvali bo'yicha javob</b>"),
        ("<b>Топ филиалов</b>", "<b>Filiallar bo'yicha top</b>"),
        ("<b>Топ товаров</b>", "<b>Tovarlar bo'yicha top</b>"),
        ("<b>Топ причин</b>", "<b>Sabablar bo'yicha top</b>"),
        ("Источник:", "Manba:"),
        ("список ОГ", "ОГ ro'yxati"),
        ("Основной список:", "Asosiy ro'yxat:"),
        ("Привязок Telegram к ClickUp:", "Telegram-ClickUp bog'lanishlari:"),
        ("Показано открытых:", "Ochiq vazifalar ko'rsatildi:"),
        ("Статусы:", "Statuslar:"),
        ("Запрос:", "So'rov:"),
        ("Записей в выборке:", "Tanlovdagi yozuvlar:"),
        ("Записей:", "Yozuvlar:"),
        ("Количество:", "Miqdor:"),
        ("Требуют проверки:", "Tekshiruv talab qiladi:"),
        ("Средняя уверенность:", "O'rtacha ishonchlilik:"),
        ("Последняя дата:", "Oxirgi sana:"),
        ("Таблица:", "Jadval:"),
        ("Открытых задач не нашла.", "Ochiq vazifalar topilmadi."),
        ("Не смогла получить задачи.", "Vazifalarni olib bo'lmadi."),
        ("Не смогла прочитать таблицу брака:", "Brak jadvalini o'qib bo'lmadi:"),
        ("Ошибка:", "Xato:"),
        ("  задачи / до ", "  vazifalar / muddat "),
        ("  not started / до ", "  not started / muddat "),
        ("задачи", "vazifalar"),
        ("без статуса", "statussiz"),
        ("без ответственного", "mas'ulsiz"),
        ("записей", "yozuv"),
        ("шт", "dona"),
    ]
    localized = answer
    for source, target in replacements:
        localized = localized.replace(source, target)
    return localized


def normalize_transcribed_command(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"^напомним\b", "напомни", cleaned, flags=re.IGNORECASE)
    return cleaned


def resolve_digest_id(text: str) -> str | None:
    normalized = strip_bot_command_mention(normalize_text(text))
    if normalized.startswith(("/business_summary", "/personal_summary", "/личные_чаты", "сводка по личным чатам", "личные чаты")):
        return "BUSINESS-SUMMARY"
    if normalized.startswith((
        "/access ",
        "/access_message ",
        "/share_access ",
        "оформи доступ ",
        "сформулируй доступ ",
        "сообщение с доступом ",
        "собери сообщение для коллег ",
        "собери сообщение для команды ",
        "напиши сообщение для коллег ",
        "напиши сообщение для команды ",
        "доступ для команды ",
    )):
        return "ACCESS-MESSAGE"
    if normalized.startswith(("/send_group ", "/send_to_group ", "напиши в группу ", "отправь в группу ", "попроси в группе ")):
        return "SEND-GROUP"
    if normalized.startswith(("/schedule_group ", "/regular_group ", "регулярно в группу ", "поставь регулярный пинг ")):
        return "SCHEDULE-GROUP"
    if is_waste_question(normalized):
        return "WASTE-QA"
    if is_dashboard_chart_question(normalized):
        return "DASHBOARD-CHART"
    if is_dashboard_question(normalized):
        return "DASHBOARD-QA"
    if (
        "важн" in normalized
        and ("чатах" in normalized or "групп" in normalized or "внешн" in normalized)
    ) or normalized.startswith(("/group_important", "/chats_important", "что было важного в чатах")):
        return "GROUP-IMPORTANT"
    if normalized.startswith(("/steel_morning", "утренняя сводка", "что на утро", "утро")):
        return "STEEL-MORNING"
    if normalized.startswith(("/steel_weekly", "недельный обзор", "обзор за неделю")):
        return "STEEL-WEEKLY"
    if normalized.startswith(("/dashboard ", "дашборд ", "спроси дашборд ")):
        return "DASHBOARD-QA"
    if normalized.startswith(("/clickup ", "/clickup_tasks ", "clickup ", "кликап ", "задачи clickup ", "задачи кликап ")) or clickup_is_my_tasks_query(text):
        return "CLICKUP-TASKS"
    if normalized.startswith(("/task ", "task ", "покажи задачу ", "карточка задачи ")):
        return "TASK-DETAIL"
    if normalized.startswith(("/add ", "add ", "добавить задачу ", "добавь задачу ")):
        return "TASK-ADD"
    if normalized.startswith(("/edit ", "/update_task ", "edit ", "редактировать задачу ", "обновить задачу ")):
        return "TASK-EDIT"
    if normalized.startswith(("/comment ", "/note_task ", "комментарий к задаче ", "комментарий по задаче ", "апдейт по задаче ", "по задаче ")):
        return "TASK-COMMENT"
    if normalized.startswith(("/answer ", "/answer_question ", "ответ на вопрос ")):
        return "QUESTION-ANSWER"
    if normalized.startswith(("/waiting ", "/wait ", "жду по задаче ", "ждем по задаче ", "ожидание по задаче ")):
        return "TASK-WAITING"
    if normalized.startswith(("/waiting_followups ", "/followups ", "кого пинговать ", "кого дернуть ", "пинги по ожиданиям ")):
        return "WAITING-FOLLOWUPS"
    if normalized.startswith(("/nudges ", "/followup_messages ", "готовые пинги ", "сообщения для пинга ", "что написать ")):
        return "NUDGES"
    if normalized.startswith(("/outbox ", "/drafts ", "кому написать ", "черновики сообщений ")):
        return "OUTBOX"
    if normalized.startswith(("/hot_tasks ", "/hot ", "что горит ", "где горит ", "операционный обзор ")):
        return "HOT-TASKS"
    if normalized.startswith(("/focus ", "фокус ", "что в фокусе ", "на чем фокус ")):
        return "FOCUS"
    if normalized.startswith(("/stale_tasks ", "/stale ", "зависшие задачи ", "давно не обновлялись ")):
        return "STALE-TASKS"
    if normalized.startswith(("/due_soon ", "/soon ", "ближайшие дедлайны ", "ближайшие задачи ")):
        return "DUE-SOON-TASKS"
    if normalized.startswith(("/next_action ", "/next_step ", "что дальше ", "следующий шаг ", "что делать дальше ")):
        return "NEXT-ACTION"
    if normalized.startswith(("/next_for ", "/next_about ", "что делать по ", "следующий шаг по ")):
        return "NEXT-FOR"
    if normalized.startswith(("/standup ", "/daily_standup ", "стендап ", "статус на созвон ", "короткий статус ")):
        return "STANDUP"
    if normalized.startswith(("/triage ", "/parse_voice ", "/разбор ", "разбери ", "разложи ")):
        return "TRIAGE"
    if normalized.startswith(("/voice_digest ", "/voice_recap ", "голосовой разбор ", "разбор голосового ", "сводка голоса ")):
        return "VOICE-DIGEST"
    if normalized.startswith(("/voice_reply ", "ответы голосом ", "голосовые ответы ")):
        return "VOICE-REPLY"
    if normalized.startswith(("/voice_lang ", "язык голоса ", "язык голосовых ответов ")):
        return "VOICE-LANG"
    if normalized.startswith(("/review ", "/weekly_review ", "итоги недели ", "обзор недели ", "что изменилось ")):
        return "PERIOD-REVIEW"
    if normalized.startswith(("/for ", "/assignee ", "задачи для ", "что у ", "что по ")):
        return "ASSIGNEE-TASKS"
    if normalized.startswith(("/owner_brief ", "/brief_for ", "бриф для ", "статус для ")):
        return "OWNER-BRIEF"
    if normalized.startswith(("/tasks ", "/list ", "/direction_tasks ", "задачи ")):
        return "DIRECTION-TASKS"
    if normalized.startswith(("/assign ", "назначь ", "назначить ", "передай задачу ", "передать задачу ")):
        return "TASK-ASSIGN"
    if normalized.startswith(("/direction ", "/move_task ", "направление задачи ")) or (normalized.startswith(("перенеси задачу ", "перенести задачу ")) and " в " in normalized):
        return "TASK-DIRECTION"
    if normalized.startswith(("/due ", "/deadline ", "перенеси задачу ", "перенести задачу ", "срок задачи ")):
        return "TASK-DUE"
    if normalized.startswith(("/priority ", "/prio ", "приоритет задачи ", "сделай задачу ")):
        return "TASK-PRIORITY"
    if normalized.startswith(("/undo_last", "отмени последнее", "отменить последнее")):
        return "UNDO-LAST"
    if normalized.startswith(("/undo ", "/undo_last ", "отмени запись ", "отменить запись ", "отмени ")):
        return "UNDO"
    if normalized.startswith(("/context ", "/context_about ", "контекст ", "контекст по ", "что известно про ")):
        return "CONTEXT"
    if normalized.startswith(("/thread ", "/origin ", "цепочка ", "что вышло из ")):
        return "THREAD"
    if normalized.startswith((
        "/find ",
        "/search ",
        "найди ",
        "найди по ",
        "поиск ",
        "вспомни ",
        "что я говорила про ",
        "что я говорил про ",
        "что говорили про ",
        "что было про ",
        "покажи записи про ",
        "покажи журнал про ",
    )):
        return "INBOX-FIND"
    if normalized.startswith(("/period_log ", "/week_log ", "журнал за неделю ", "что записали за неделю ")):
        return "PERIOD-LOG"
    if normalized.startswith(("/export_memory ", "/memory_export ", "выгрузка памяти ", "экспорт памяти ")):
        return "MEMORY-EXPORT"
    if normalized.startswith(("/handoff ", "/shift ", "передача контекста ", "сдай смену ")):
        return "HANDOFF"
    if normalized.startswith(("/snapshot ", "сделай снапшот ", "сними снапшот ")):
        return "SNAPSHOT"
    if normalized.startswith(("/snapshots ", "список снапшотов ")):
        return "SNAPSHOTS"
    if normalized.startswith(("/meeting ", "/meet ", "итоги встречи ", "разбор встречи ", "встреча ")):
        return "MEETING"
    if normalized.startswith(("/rule ", "/remember_rule ", "запомни правило ", "правило: ", "правило ")):
        return "RULE-ADD"
    if normalized in COMMANDS:
        return COMMANDS[normalized]
    if normalized.startswith(("/done", "/close", "done ", "close ", "готово ", "закрыть ", "закрой ")):
        return "TASK-DONE"
    if normalized.startswith(("/start_task", "/doing", "в работу ", "начать ", "начала ", "начал ")):
        return "TASK-IN-PROGRESS"
    if normalized.startswith(("/pending", "вернуть в открытые ", "верни в открытые ", "вернуть в очередь ", "верни в очередь ")):
        return "TASK-PENDING"
    if normalized.startswith(("/snooze", "отложить ", "отложи ")):
        return "TASK-SNOOZE"
    if normalized.startswith(("/drop", "выбросить ", "выброси ", "убрать ", "убери ")):
        return "TASK-DROP"
    if normalized.startswith(("/reopen", "вернуть ", "верни ")):
        return "TASK-REOPEN"
    if normalized.startswith(("/remind", "напомни ", "напомнить ")):
        return "REMINDER-ADD"
    if normalized.startswith(("/cancel_reminder", "отмени напоминание ", "отменить напоминание ")):
        return "REMINDER-CANCEL"
    if normalized.startswith("/dedup "):
        return "DEDUP"
    for phrase, digest_id in COMMANDS.items():
        if phrase.startswith("/"):
            continue
        if phrase in normalized:
            return digest_id
    return None


def build_digest_message(config: AgentConfig, digest_id: str, date_arg: str = "latest") -> str:
    if digest_id == "HELP":
        return help_text()
    if digest_id == "HEALTH":
        return build_health_message(config)
    if digest_id == "DOCTOR":
        return build_doctor_message(config)
    if digest_id == "VOICE-STATUS":
        return build_voice_status_message(config)
    if digest_id == "OCR-STATUS":
        return build_ocr_status_message(config)
    if digest_id == "VOICE-HELP":
        return build_voice_help_message()
    if digest_id == "RELOAD":
        return build_reload_message(config)
    if digest_id == "SELF-TEST":
        return build_self_test_message(config)
    if digest_id == "AGENDA":
        return build_agenda_message(config)
    if digest_id == "FOCUS":
        return build_focus_message()
    if digest_id == "NEXT-ACTION":
        return build_next_action_message()
    if digest_id == "NEXT-FOR":
        return build_next_for_message("")
    if digest_id == "STANDUP":
        return build_standup_message()
    if digest_id == "TRIAGE":
        return build_triage_preview_message(text="")
    if digest_id == "VOICE-DIGEST":
        return build_voice_digest_preview_message(text="")
    if digest_id == "PERIOD-REVIEW":
        return build_period_review_message()
    if digest_id == "SOURCES":
        return build_sources_message(config)
    if digest_id == "RULES":
        return build_rules_message()
    if digest_id == "INBOX":
        return build_inbox_message()
    if digest_id == "INBOX-FIND":
        return build_inbox_search_message("")
    if digest_id == "CONTEXT":
        return build_context_message("")
    if digest_id == "THREAD":
        return build_thread_message("")
    if digest_id == "INBOX-SUMMARY":
        return build_inbox_summary_message()
    if digest_id == "TODAY-LOG":
        return build_today_log_message()
    if digest_id == "PERIOD-LOG":
        return build_period_log_message(days=7)
    if digest_id == "GROUP-IMPORTANT":
        return build_group_important_message(config, days=1)
    if digest_id == "BUSINESS-SUMMARY":
        return build_business_summary_message(config, days=1)
    if digest_id == "GROUPS":
        return build_groups_message()
    if digest_id == "SEND-GROUP":
        return build_send_group_message(config, "", allow_send=False)
    if digest_id == "SCHEDULE-GROUP":
        return build_schedule_group_message(config, "", source_chat_id="")
    if digest_id == "LMS-SUMMARY":
        return build_tg_lms_summary_message()
    if digest_id == "CLICKUP-STATUS":
        return build_clickup_status_message()
    if digest_id == "CLICKUP-TASKS":
        return build_clickup_tasks_message()
    if digest_id == "DASHBOARD-CHART":
        return build_dashboard_chart_message("диаграмма дашборда")["answer"]
    if digest_id == "WASTE-SUMMARY":
        return build_waste_summary_message()
    if digest_id == "WASTE-QA":
        return build_waste_answer_message("")
    if digest_id == "HANDOFF":
        return build_handoff_message(days=7)
    if digest_id == "TASKS":
        return build_filtered_inbox_message("task", only_open=True)
    if digest_id == "TASK-SUMMARY":
        return build_task_summary_message()
    if digest_id == "HOT-TASKS":
        return build_hot_tasks_message()
    if digest_id == "STALE-TASKS":
        return build_stale_tasks_message()
    if digest_id == "DIRECTION-TASKS":
        return build_direction_tasks_message("")
    if digest_id == "ASSIGNEE-TASKS":
        return build_assignee_tasks_message("")
    if digest_id == "ALL-TASKS":
        return build_task_list_message(limit=None)
    if digest_id == "ACTIVITY":
        return build_activity_message()
    if digest_id == "SNAPSHOTS":
        return build_snapshots_message()
    if digest_id == "DOING-TASKS":
        return build_task_list_message(statuses={"in_progress"}, title="Задачи в работе")
    if digest_id == "WAITING-TASKS":
        return build_task_list_message(statuses={"waiting"}, title="Задачи в ожидании")
    if digest_id == "WAITING-FOLLOWUPS":
        return build_waiting_followups_message()
    if digest_id == "NUDGES":
        return build_nudges_message()
    if digest_id == "OUTBOX":
        return build_outbox_message()
    if digest_id == "DUE-SOON-TASKS":
        return build_due_tasks_message("soon")
    if digest_id == "DONE-TASKS":
        return build_task_list_message(statuses={"done"}, title="Закрытые задачи")
    if digest_id == "SNOOZED-TASKS":
        return build_task_list_message(statuses={"snoozed"}, title="Отложенные задачи")
    if digest_id == "DROPPED-TASKS":
        return build_task_list_message(statuses={"dropped"}, title="Убранные задачи")
    if digest_id == "TODAY-TASKS":
        return build_due_tasks_message("today")
    if digest_id == "OVERDUE-TASKS":
        return build_due_tasks_message("overdue")
    if digest_id == "DEFECTS":
        return build_filtered_inbox_message("defect", only_open=False)
    if digest_id == "MEETINGS":
        return build_filtered_inbox_message("meeting", only_open=False)
    if digest_id == "DECISIONS":
        return build_filtered_inbox_message("decision", only_open=False)
    if digest_id == "RISK-LOG":
        return build_filtered_inbox_message("risk", only_open=False)
    if digest_id == "QUESTIONS":
        return build_filtered_inbox_message("question", only_open=False)
    if digest_id == "OPEN-QUESTIONS":
        return build_filtered_inbox_message("question", only_open=True)
    if digest_id == "STATS":
        return build_stats_message()
    if digest_id == "REMINDERS":
        return build_reminders_message()
    if digest_id == "NEXT-REMINDERS":
        return build_reminders_message(limit=5, title="Ближайшие напоминания")
    if digest_id == "BRAIN-STATUS":
        return build_brain_status_message(config)
    digest_config = build_bot_digests.read_json(config.digest_config)
    management = build_bot_digests.read_json(dashboard_data_path("management.json"))
    widget_map = build_bot_digests.read_json(dashboard_data_path("widget_source_map.json"))
    digest = next((item for item in digest_config.get("digests", []) if item.get("id") == digest_id), None)
    if digest is None:
        digest = {
            "id": digest_id,
            "enabled": True,
            "page": "all",
            "template": digest_id,
            "source_status_allowed": digest_config.get("source_status_allowed"),
        }
    payload = build_bot_digests.build_digest_payload(digest, digest_config, management, widget_map, date_arg)
    return str(payload.get("message") or "").strip() or f"Нет текста для {digest_id}"


def build_sources_message(config: AgentConfig) -> str:
    digest_config: dict[str, Any] = {}
    if config.digest_config.exists():
        digest_config = build_bot_digests.read_json(config.digest_config)
    lines = [
        f"<b>Источники {BOT_DISPLAY_NAME}</b>",
        f"Конфиг: {escape_html(str(config.digest_config.relative_to(ROOT) if config.digest_config.is_absolute() and config.digest_config.is_relative_to(ROOT) else config.digest_config))}",
        f"Версия: {escape_html(str(digest_config.get('version') or 'n/a'))}",
    ]
    dashboard_link = digest_config.get("default_dashboard_link")
    if dashboard_link:
        lines.append(f"Дашборд: {escape_html(str(dashboard_link))}")
    lines.extend(["", "<b>Свежие файлы data/</b>"])
    for path in newest_data_files(limit=10):
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        size_mb = path.stat().st_size / 1024 / 1024
        lines.append(f"- {escape_html(path.name)}: {stamp}, {size_mb:.1f} MB")
    lines.extend([
        "",
        f"Источник для ответов бота: {escape_html(str(DASHBOARD_DATA_DIR))}",
        "Если цифра в дашборде не сходится, начинаем с этих файлов: management.json, dashboard-payloads.js, файлы направления и ecom/amo/OX-слои.",
    ])
    return "\n".join(lines)


def is_dashboard_question(normalized: str) -> bool:
    metric_terms = {
        "выруч", "вируч", "оборот", "продаж", "сумм", "деньг", "доход",
        "результат", "план", "факт", "отста", "динамик", "направлен",
        "e-commerce", "ecom", "e-com", "еком", "дашборд", "dashboard",
    }
    ask_terms = {
        "какая", "какой", "каков", "сколько", "что", "где", "покажи", "дай",
        "сводка", "итог", "обзор", "статус", "разбор",
    }
    period_terms = {
        "вчера", "сегодня", "день", "недел", "месяц", "период", "дат",
        "2025", "2026",
    }
    has_metric = any(term in normalized for term in metric_terms)
    has_ask = any(term in normalized for term in ask_terms)
    has_period_or_direction = any(term in normalized for term in period_terms) or bool(dashboard_query_direction(normalized))
    return has_metric and (has_ask or has_period_or_direction)


def is_dashboard_chart_question(normalized: str) -> bool:
    chart_terms = {"диаграм", "график", "chart", "картинк", "визуал", "рисунок"}
    dashboard_terms = {"дашборд", "dashboard", "выруч", "вируч", "оборот", "продаж", "сумм", "направлен", "ecom", "e-com", "еком"}
    return normalized.startswith(("/dashboard_chart", "/chart")) or (
        any(term in normalized for term in chart_terms) and any(term in normalized for term in dashboard_terms)
    )


def dashboard_rows() -> list[dict[str, Any]]:
    try:
        management = build_bot_digests.read_json(dashboard_data_path("management.json"))
    except Exception:
        return []
    rows = management.get("daily_direction") or []
    return rows if isinstance(rows, list) else []


def dashboard_latest_date(rows: list[dict[str, Any]]) -> dt.date | None:
    dates: list[dt.date] = []
    for row in rows:
        raw = str(row.get("date") or "")
        try:
            dates.append(dt.datetime.strptime(raw, "%Y-%m-%d").date())
        except ValueError:
            continue
    return max(dates) if dates else None


def dashboard_query_period(normalized: str, latest: dt.date) -> tuple[dt.date, dt.date, str]:
    if "вчера" in normalized:
        yesterday = dt.date.today() - dt.timedelta(days=1)
        return yesterday, yesterday, "вчера"
    if "месяц" in normalized:
        return latest.replace(day=1), latest, "месяц к последней дате источника"
    if "сегодня" in normalized or "день" in normalized:
        return latest, latest, "последний день источника"
    return latest - dt.timedelta(days=6), latest, "последние 7 дней источника"


def dashboard_query_direction(normalized: str) -> str:
    for key, direction in DIRECTIONS.items():
        if key in normalized:
            return direction
    if "e-commerce" in normalized:
        return "E-com"
    return ""


def dashboard_row_is_rollup(row: dict[str, Any]) -> bool:
    direction = str(row.get("direction") or "").strip()
    source_id = str(row.get("source_id") or "").strip()
    return direction == "E-com" and source_id == "OX-GEN-ECOM-DAYS"


def dashboard_rows_for_total(rows: list[dict[str, Any]], direction: str) -> list[dict[str, Any]]:
    if direction:
        return rows
    return [row for row in rows if not dashboard_row_is_rollup(row)]


def dashboard_url_for_period(start: dt.date, end: dt.date, direction: str = "") -> str:
    page_by_direction = {
        "E-com": "marketing.html",
        "Flowers": "flowers.html",
        "Nour": "nour.html",
        "Plants": "plants.html",
        "Wedding": "wedding.html",
        "Gourmet": "gourmet.html",
        "Guul": "guul.html",
        "School": "school.html",
        "B2B Опт": "b2b-opt.html",
        "Corp": "corp.html",
        "Закупка Flowers": "procurement-flowers.html",
    }
    page = page_by_direction.get(direction, "index.html")
    query = urllib.parse.urlencode({
        "start": start.isoformat(),
        "end": end.isoformat(),
        "manualPeriod": "1",
        "periodMode": "week",
        "metricMode": "revenue",
    })
    return f"https://tg-bot-agent-generator-qa.vercel.app/{page}?{query}"


def build_dashboard_answer_message(text: str) -> str:
    normalized = normalize_text(text)
    rows = dashboard_rows()
    if not rows:
        return "Не нашла слой дашборда management.json. Сначала нужно обновить данные дашборда."
    latest = dashboard_latest_date(rows)
    if latest is None:
        return "В management.json нет дат. Не могу честно ответить по периоду."
    start, end, period_label = dashboard_query_period(normalized, latest)
    direction = dashboard_query_direction(normalized)
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not direction and dashboard_row_is_rollup(row):
            continue
        try:
            row_date = dt.datetime.strptime(str(row.get("date") or ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= row_date <= end):
            continue
        if direction and str(row.get("direction") or "") != direction:
            continue
        selected.append(row)
    if not selected and start == end and start > latest:
        return "\n".join([
            "<b>Ответ из управленческого дашборда</b>",
            f"Запрос: {escape_html(compact_text(text, 180))}",
            f"Запрошенная дата: {start.isoformat()} ({escape_html(period_label)})",
            f"Данных за эту дату в источнике пока нет. Последняя доступная дата: {latest.isoformat()}.",
            "Источник: management.daily_direction",
            f"Дашборд: {escape_html(dashboard_url_for_period(latest, latest, direction))}",
        ])
    total = sum(float(row.get("revenue") or row.get("amount") or 0) for row in selected)
    by_direction: dict[str, float] = {}
    for row in selected:
        row_direction = str(row.get("direction") or "Unknown")
        by_direction[row_direction] = by_direction.get(row_direction, 0.0) + float(row.get("revenue") or row.get("amount") or 0)
    source_ids = sorted({str(row.get("source_id") or "") for row in selected if row.get("source_id")})
    stale_days = (dt.date.today() - latest).days
    stale_line = (
        f"Внимание: источник отстаёт на {stale_days} дн., последняя дата {latest.isoformat()}."
        if stale_days > 1
        else f"Последняя дата источника: {latest.isoformat()}."
    )
    lines = [
        "<b>Ответ из управленческого дашборда</b>",
        f"Запрос: {escape_html(compact_text(text, 180))}",
        f"Период: {start.isoformat()} - {end.isoformat()} ({escape_html(period_label)})",
        f"Разрез: {escape_html(direction or 'все направления')}",
        f"Выручка: <b>{escape_html(build_bot_digests.money(total))} UZS</b>",
        stale_line,
        f"Источник: {escape_html(', '.join(source_ids[:5]) or 'management.daily_direction')}",
        f"Дашборд: {escape_html(dashboard_url_for_period(start, end, direction))}",
    ]
    if not direction and by_direction:
        weak = sorted(by_direction.items(), key=lambda item: item[1])[:5]
        strong = sorted(by_direction.items(), key=lambda item: item[1], reverse=True)[:5]
        lines.extend(["", "<b>Топ направлений</b>"])
        for name, value in strong:
            lines.append(f"- {escape_html(name)}: {escape_html(build_bot_digests.money(value))} UZS")
        lines.extend(["", "<b>Нижняя часть списка</b>"])
        for name, value in weak:
            lines.append(f"- {escape_html(name)}: {escape_html(build_bot_digests.money(value))} UZS")
    if direction and not selected:
        lines.append("")
        lines.append("По выбранному направлению нет строк в этом периоде. Проверь период или источник.")
    return "\n".join(lines)


def dashboard_selected_rows(text: str) -> tuple[list[dict[str, Any]], dt.date, dt.date, str, str, dt.date]:
    normalized = normalize_text(text)
    rows = dashboard_rows()
    if not rows:
        raise RuntimeError("Не нашла management.json")
    latest = dashboard_latest_date(rows)
    if latest is None:
        raise RuntimeError("В management.json нет дат")
    start, end, period_label = dashboard_query_period(normalized, latest)
    direction = dashboard_query_direction(normalized)
    selected: list[dict[str, Any]] = []
    for row in rows:
        if not direction and dashboard_row_is_rollup(row):
            continue
        try:
            row_date = dt.datetime.strptime(str(row.get("date") or ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= row_date <= end):
            continue
        if direction and str(row.get("direction") or "") != direction:
            continue
        selected.append(row)
    return selected, start, end, period_label, direction, latest


def money_short(value: float) -> str:
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount >= 1_000_000_000:
        return f"{sign}{amount / 1_000_000_000:.1f} млрд"
    if amount >= 1_000_000:
        return f"{sign}{amount / 1_000_000:.1f} млн"
    if amount >= 1_000:
        return f"{sign}{amount / 1_000:.1f} тыс"
    return f"{sign}{amount:.0f}"


def convert_svg_to_png(svg_path: Path) -> Path | None:
    qlmanage = shutil.which("qlmanage") or "/usr/bin/qlmanage"
    if not Path(qlmanage).exists():
        return None
    before = set(CHART_DIR.glob(svg_path.name + ".png"))
    result = subprocess.run(
        [qlmanage, "-t", "-s", "1280", "-o", str(CHART_DIR), str(svg_path)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        append_log("chart_png_convert_error", {"path": str(svg_path), "error": (result.stderr or result.stdout)[:500]})
        return None
    candidates = [path for path in CHART_DIR.glob(svg_path.name + ".png") if path not in before]
    if not candidates:
        fallback = CHART_DIR / f"{svg_path.name}.png"
        candidates = [fallback] if fallback.exists() else []
    return candidates[0] if candidates else None


def svg_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def truncate_label(value: Any, max_len: int) -> str:
    text = str(value)
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def render_dashboard_chart(text: str) -> tuple[Path, str]:
    selected, start, end, period_label, direction, latest = dashboard_selected_rows(text)
    if not selected:
        raise RuntimeError("По выбранному периоду нет данных для диаграммы")
    total = sum(float(row.get("revenue") or row.get("amount") or 0) for row in selected)
    if direction:
        grouped: dict[str, float] = {}
        for row in selected:
            grouped[str(row.get("date") or "")] = grouped.get(str(row.get("date") or ""), 0.0) + float(row.get("revenue") or row.get("amount") or 0)
        title = f"{direction}: выручка по дням"
        items = sorted(grouped.items())[-14:]
    else:
        grouped = {}
        for row in selected:
            key = str(row.get("direction") or "Unknown")
            grouped[key] = grouped.get(key, 0.0) + float(row.get("revenue") or row.get("amount") or 0)
        title = "Выручка по направлениям"
        items = sorted(grouped.items(), key=lambda item: item[1], reverse=True)[:10]

    width, height = 820, 820
    ink = "#181D27"
    muted = "#717680"
    accent = "#FF5850"
    grid = "#D5D7DA"
    subtitle = f"{start.isoformat()} - {end.isoformat()} · {period_label} · всего {money_short(total)} UZS"
    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#F7F8FA"/>',
        f'<text x="42" y="72" font-family="Arial, Helvetica, sans-serif" font-size="34" font-weight="700" fill="{ink}">{svg_escape(truncate_label(title, 34))}</text>',
        f'<text x="42" y="106" font-family="Arial, Helvetica, sans-serif" font-size="18" fill="{muted}">{svg_escape(truncate_label(subtitle, 60))}</text>',
    ]
    stale_days = (dt.date.today() - latest).days
    if stale_days > 1:
        svg.append(f'<text x="42" y="134" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#C43D38">Источник отстает на {stale_days} дн.; последняя дата {latest.isoformat()}</text>')

    chart_left, chart_top, chart_right, chart_bottom = 230, 165, 760, 725
    max_value = max((value for _, value in items), default=1.0) or 1.0
    bar_gap = 12
    bar_h = max(24, min(46, int((chart_bottom - chart_top - bar_gap * (len(items) - 1)) / max(len(items), 1))))
    svg.append(f'<line x1="{chart_left}" y1="{chart_top}" x2="{chart_left}" y2="{chart_bottom}" stroke="{grid}" stroke-width="2"/>')
    svg.append(f'<line x1="{chart_left}" y1="{chart_bottom}" x2="{chart_right}" y2="{chart_bottom}" stroke="{grid}" stroke-width="2"/>')
    for idx, (name, value) in enumerate(items):
        y = chart_top + idx * (bar_h + bar_gap)
        label = name[5:] if direction and re.match(r"^\d{4}-\d{2}-\d{2}$", name) else name
        bar_w = int((chart_right - chart_left - 110) * max(value, 0) / max_value)
        svg.append(f'<text x="42" y="{y + 28}" font-family="Arial, Helvetica, sans-serif" font-size="18" fill="{ink}">{svg_escape(truncate_label(label, 17))}</text>')
        svg.append(f'<rect x="{chart_left}" y="{y}" width="{max(bar_w, 2)}" height="{bar_h}" rx="6" fill="{accent}"/>')
        svg.append(f'<text x="{chart_left + bar_w + 10}" y="{y + 28}" font-family="Arial, Helvetica, sans-serif" font-size="18" fill="{ink}">{svg_escape(money_short(value))}</text>')
    svg.append(f'<text x="42" y="776" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="{muted}">Источник: management.daily_direction</text>')
    svg.append(f'<text x="455" y="776" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="{muted}">{svg_escape(truncate_label(dashboard_url_for_period(start, end, direction), 36))}</text>')
    svg.append("</svg>")

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    path = CHART_DIR / f"dashboard_chart_{dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.svg"
    path.write_text("\n".join(svg), encoding="utf-8")
    caption = "\n".join([
        "<b>Диаграмма по дашборду</b>",
        f"Период: {start.isoformat()} - {end.isoformat()}",
        f"Разрез: {escape_html(direction or 'все направления')}",
        f"Выручка: <b>{escape_html(build_bot_digests.money(total))} UZS</b>",
    ])
    return path, caption


def build_dashboard_chart_message(text: str) -> dict[str, Any]:
    try:
        svg_path, caption = render_dashboard_chart(text)
    except Exception as exc:
        return {"digest_id": "DASHBOARD-CHART", "answer": f"Не смогла построить диаграмму: {escape_html(str(exc))}"}
    png_path = convert_svg_to_png(svg_path)
    if png_path:
        return {
            "digest_id": "DASHBOARD-CHART",
            "answer": caption + f"\nФайл: {escape_html(str(png_path.relative_to(ROOT)))}",
            "photo_path": str(png_path),
            "photo_caption": caption,
            "send_answer": False,
        }
    return {
        "digest_id": "DASHBOARD-CHART",
        "answer": caption + f"\nФайл: {escape_html(str(svg_path.relative_to(ROOT)))}",
        "document_path": str(svg_path),
        "document_caption": caption,
    }


def build_steel_morning_message(config: AgentConfig) -> str:
    lines = ["<b>Утренняя сводка Фатхулло</b>"]
    lines.extend(["", trim_message_block(build_due_tasks_message("overdue"), 700)])
    lines.extend(["", trim_message_block(build_due_tasks_message("today"), 700)])
    lines.extend(["", trim_message_block(build_due_tasks_message("soon", days=3), 700)])
    lines.extend(["", trim_message_block(build_waiting_followups_message(limit=5), 700)])
    lines.extend(["", trim_message_block(build_filtered_inbox_message("question", only_open=True, limit=5), 700)])
    try:
        lines.extend(["", trim_message_block(build_dashboard_answer_message("какая выручка по e-commerce за неделю"), 800)])
    except Exception as exc:
        lines.extend(["", f"Дашборд: не смогла собрать блок, причина: {escape_html(str(exc))}"])
    lines.extend(["", "Команды для продолжения: /focus, /outbox, /review, /sources."])
    return "\n".join(lines)


def build_steel_weekly_message(config: AgentConfig) -> str:
    lines = ["<b>Недельный обзор Фатхулло</b>"]
    lines.extend(["", trim_message_block(build_period_review_message(days=7), 1200)])
    lines.extend(["", trim_message_block(build_task_list_message(statuses={"open", "pending", "in_progress", "waiting"}, title="Открытые и заблокированные задачи", limit=8), 900)])
    lines.extend(["", trim_message_block(build_task_list_message(statuses={"done"}, title="Выполненные задачи", limit=8), 900)])
    lines.extend(["", trim_message_block(build_filtered_inbox_message("risk", only_open=False, limit=6), 800)])
    lines.extend(["", trim_message_block(build_filtered_inbox_message("decision", only_open=False, limit=6), 800)])
    lines.extend(["", "Команды для продолжения: /handoff, /nudges, /sources."])
    return "\n".join(lines)


def build_agenda_message(config: AgentConfig) -> str:
    parts = [f"<b>Повестка {BOT_DISPLAY_NAME}</b>"]
    warnings = [row for row in health_checks(config) if not row["ok"]]
    if warnings:
        parts.append("<b>Запуск / подключения</b>")
        for row in warnings[:6]:
            parts.append(f"- WARN {escape_html(row['name'])}: {escape_html(row['detail'])}")
    try:
        daily = build_digest_message(config, "DAILY-EXEC")
        parts.extend(["", "<b>Дашборд</b>", trim_message_block(daily, 900)])
    except Exception as exc:
        parts.extend(["", "<b>Дашборд</b>", f"Не смогла собрать daily summary: {escape_html(str(exc))}"])
    parts.extend(["", trim_message_block(build_due_tasks_message("overdue"), 900)])
    parts.extend(["", trim_message_block(build_due_tasks_message("today"), 900)])
    parts.extend(["", trim_message_block(build_waiting_agenda_block(), 900)])
    parts.extend(["", trim_message_block(build_reminders_message(limit=5, title="Ближайшие напоминания"), 900)])
    parts.extend(["", trim_message_block(build_meeting_agenda_block(), 900)])
    parts.extend(["", trim_message_block(build_inbox_summary_message(), 900)])
    return "\n".join(part for part in parts if part is not None)


def build_waiting_agenda_block(limit: int = 8) -> str:
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if row.get("type") == "task" and str(row.get("status") or "open") == "waiting"
    ]
    rows = sort_task_rows(rows)[:limit]
    if not rows:
        return "Ожидания: пусто."
    lines = ["<b>Ожидания</b>"]
    for row in rows:
        row_id = str(row.get("id") or "")[:8]
        meta = format_task_meta(row)
        reason = str(row.get("waiting_reason") or "").strip()
        text = compact_text(str(row.get("text") or ""), 95)
        suffix = f" - {reason}" if reason else ""
        lines.append(f"- {escape_html(row_id)} / {escape_html(meta)}: {escape_html(text + suffix)}")
    return "\n".join(lines)


def build_meeting_agenda_block(limit_meetings: int = 3, limit_decisions: int = 5) -> str:
    rows = read_inbox(limit=1_000_000)
    meetings = [row for row in rows if row.get("type") == "meeting"][-limit_meetings:]
    decisions = [row for row in rows if row.get("type") == "decision"][-limit_decisions:]
    if not meetings and not decisions:
        return "Встречи и решения: пока пусто."
    lines = ["<b>Встречи и решения</b>"]
    if meetings:
        lines.append("Встречи:")
        for row in reversed(meetings):
            row_id = str(row.get("id") or "")[:8]
            meta = format_task_meta(row)
            text = compact_text(str(row.get("text") or ""), 100)
            lines.append(f"- {escape_html(row_id)} / {escape_html(meta)}: {escape_html(text)}")
    if decisions:
        lines.append("Решения:")
        for row in reversed(decisions):
            row_id = str(row.get("id") or "")[:8]
            parent = f" / parent {str(row.get('parent_id'))[:8]}" if row.get("parent_id") else ""
            direction = str(row.get("direction") or "без направления")
            text = compact_text(str(row.get("text") or ""), 110)
            lines.append(f"- {escape_html(row_id)} / {escape_html(direction)}{escape_html(parent)}: {escape_html(text)}")
    return "\n".join(lines)


def trim_message_block(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def build_health_message(config: AgentConfig) -> str:
    rows = health_checks(config)
    lines = [f"<b>Статус {BOT_DISPLAY_NAME}</b>"]
    for row in rows:
        marker = "OK" if row["ok"] else "WARN"
        lines.append(f"- {marker} {escape_html(row['name'])}: {escape_html(row['detail'])}")
    return "\n".join(lines)


def build_voice_help_message() -> str:
    lines = [
        f"<b>Как говорить с {BOT_DISPLAY_NAME}</b>",
        "",
        "<b>Короткие записи</b>",
        "- задача ecom до завтра для Мухассар проверить чеки",
        "- брак nour 12 стеблей на 450 000 сум",
        "- решение не выкатываем amo без истории переходов",
        "- вопрос кто даст доступ к amo",
        "",
        "<b>Длинный голос</b>",
        "Надиктуй несколько строк: задача, жду, напомни, риск, вопрос, решение. Бот сам включит голосовой разбор и свяжет пункты через parent_id.",
        "",
        "<b>Ожидания и напоминания</b>",
        "- жду выгрузку OX от Мухассар",
        "- напомни завтра 09:00 проверить источники",
        "- кого пинговать",
        "",
        "<b>Память и контекст</b>",
        "- что я говорила про amo",
        "- что известно про Мухассар чеки",
        "- что делать по ecom",
        "- что вышло из abc123",
        "",
        "<b>Диагностика</b>",
        "- статус голоса",
        "- проверка голоса",
        "- статус ocr",
    ]
    return "\n".join(lines)


def build_voice_status_message(config: AgentConfig) -> str:
    command = config.voice_command.strip()
    command_available = voice_command_available(config)
    prompt_available = voice_prompt_available(config)
    whisper_path = shutil.which("whisper") or ""
    helper_command = command or "whisper CLI fallback"
    prompt_detail = "не требуется"
    if "--prompt-file" in command:
        parts = shlex.split(command)
        try:
            prompt_path = Path(parts[parts.index("--prompt-file") + 1])
        except (ValueError, IndexError):
            prompt_detail = "путь не задан"
        else:
            if not prompt_path.is_absolute():
                prompt_path = ROOT / prompt_path
            prompt_detail = display_path(prompt_path)

    lines = [
        f"<b>Voice status {BOT_DISPLAY_NAME}</b>",
        f"Голос включен: {'да' if config.voice_enabled else 'нет'}",
        f"Лимит длины: {config.voice_max_seconds} сек.",
        f"Язык: {escape_html(config.voice_language)}",
        f"Модель: {escape_html(config.voice_model)}",
        f"TTS provider: {escape_html(tts_provider())}",
        f"TTS model: {escape_html(os.environ.get('TG_AGENT_TTS_MODEL', 'gpt-4o-mini-tts'))}",
        f"TTS voice: {escape_html(os.environ.get('TG_AGENT_TTS_VOICE', 'nova'))}",
        f"OpenAI API key: {'да' if os.environ.get('OPENAI_API_KEY', '').strip() else 'нет'}",
        f"Fallback на macOS voice: {'да' if tts_fallback_enabled() else 'нет'}",
        f"Команда: <code>{escape_html(helper_command)}</code>",
        f"Команда доступна: {'да' if command_available else 'нет'}",
        f"whisper CLI: {escape_html(whisper_path or 'не найден')}",
        f"Prompt: {'OK' if prompt_available else 'WARN'} ({escape_html(prompt_detail)})",
        f"Хранилище медиа: {'да' if config.media_store_enabled else 'нет'} ({escape_html(display_path(config.media_store_dir))})",
        "",
        "<b>Что делать, если голос не читается</b>",
    ]
    if not config.voice_enabled:
        lines.append("- включить TG_VOICE_ENABLED=1 или voice.enabled в конфиге")
    elif not command_available:
        lines.append("- проверить TG_VOICE_TRANSCRIBE_COMMAND или установить whisper CLI")
    elif not prompt_available:
        lines.append("- проверить --prompt-file в TG_VOICE_TRANSCRIBE_COMMAND")
    else:
        lines.append("- контур готов; дальше проверять конкретный файл через simulate-voice")
    return "\n".join(lines)


def build_ocr_status_message(config: AgentConfig) -> str:
    command = config.ocr_command.strip()
    command_available = ocr_command_available(config)
    helper_command = command or "не задана"
    lines = [
        f"<b>OCR status {BOT_DISPLAY_NAME}</b>",
        f"OCR включен: {'да' if config.ocr_enabled else 'нет'}",
        f"Лимит файла: {config.ocr_max_mb} MB",
        f"Таймаут: {config.ocr_timeout_seconds} сек.",
        f"Команда: <code>{escape_html(helper_command)}</code>",
        f"Команда доступна: {'да' if command_available else 'нет'}",
        f"Хранилище медиа: {'да' if config.media_store_enabled else 'нет'} ({escape_html(display_path(config.media_store_dir))})",
        "",
        "<b>Что делать, если фото не читается</b>",
    ]
    if not config.ocr_enabled:
        lines.append("- включить TG_AGENT_OCR_ENABLED=1 или ocr.enabled в конфиге")
    elif not command:
        lines.append("- задать TG_AGENT_OCR_COMMAND или ocr.command")
    elif not command_available:
        lines.append("- проверить первую команду OCR: tesseract/python/helper script")
    else:
        lines.append("- контур готов; дальше отправить фото или документ без подписи")
    return "\n".join(lines)


def build_doctor_message(config: AgentConfig) -> str:
    health = health_checks(config)
    self_test = run_self_tests(config)
    by_name = {str(item["name"]): item for item in health}
    critical_names = [
        "bot token",
        "digest config",
        "management data",
        "widget source map",
        "voice enabled",
        "voice command path",
    ]
    if config.brain_enabled:
        critical_names.append("brain cli")
    blockers = [by_name[name] for name in critical_names if name in by_name and not by_name[name]["ok"]]
    if not self_test.get("ok"):
        blockers.append({"name": "self-test", "ok": False, "detail": "локальная регрессия не проходит"})
    recommended_names = [
        "dry-run chat",
        "access guard",
        "media dir",
        "sheets webhook",
        "sheets secret",
    ]
    warnings = [by_name[name] for name in recommended_names if name in by_name and not by_name[name]["ok"]]
    status = "READY" if not blockers else "NOT READY"
    lines = [
        f"<b>Doctor {BOT_DISPLAY_NAME}: {status}</b>",
        f"Конфиг: {escape_html(display_path(config.digest_config))}",
        f"Журнал: {len(read_inbox(limit=1_000_000))} строк",
        f"Напоминания: {sum(1 for row in read_reminders(limit=1_000_000) if row.get('status') == 'pending')} активных",
        "",
        "<b>Критично для запуска</b>",
    ]
    if blockers:
        for item in blockers:
            lines.append(f"- FAIL {escape_html(str(item['name']))}: {escape_html(str(item['detail']))}")
    else:
        lines.append("- OK критичных блокеров нет")
    lines.extend(["", "<b>Рекомендуется проверить</b>"])
    if warnings:
        for item in warnings:
            lines.append(f"- WARN {escape_html(str(item['name']))}: {escape_html(str(item['detail']))}")
    else:
        lines.append("- OK дополнительных предупреждений нет")
    lines.extend(["", "<b>Self-test</b>"])
    for item in self_test["checks"]:
        marker = "OK" if item["ok"] else "FAIL"
        lines.append(f"- {marker} {escape_html(item['name'])}: {escape_html(item['detail'])}")
    lines.extend(["", "<b>Следующий live-шаг</b>"])
    if blockers:
        lines.append("Сначала закрыть FAIL выше, затем: telegram-check, set-commands, drop-pending-updates, run.")
    else:
        lines.append("Можно проверять Telegram: telegram-check, set-commands, drop-pending-updates, run.")
    return "\n".join(lines)


def masked_env_status(name: str) -> str:
    return "set" if os.environ.get(name) else "missing"


def build_live_status_message(config: AgentConfig) -> str:
    env_path = Path(os.environ.get("TG_DASHBOARD_AGENT_ENV") or ROOT / "deployment-access" / "tg-bot-agent.env")
    launchd_label = os.environ.get("TG_DASHBOARD_AGENT_LAUNCHD_LABEL") or "tg-bot-agent"
    plist_path = Path(
        os.environ.get("TG_DASHBOARD_AGENT_PLIST")
        or Path.home() / "Library" / "LaunchAgents" / f"{launchd_label}.plist"
    )
    allowed_users = configured_allowed_user_ids(config)
    allowed_chats = configured_allowed_chat_ids(config)
    lock_pid = read_lock_pid(LOCK_FILE) if LOCK_FILE.exists() else 0
    lock_status = "absent"
    if lock_pid:
        lock_status = f"pid {lock_pid} / {'running' if process_is_running(lock_pid) else 'stale'}"
    elif LOCK_FILE.exists():
        lock_status = "present / unreadable"
    try:
        launchctl = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{launchd_label}"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        launchd_status = "loaded" if launchctl.returncode == 0 else "not loaded"
    except Exception as exc:
        launchd_status = f"unknown: {exc}"
    rows = [
        f"<b>Live status {BOT_DISPLAY_NAME}</b>",
        f"env file: {'OK' if env_path.exists() else 'MISS'} {escape_html(display_path(env_path))}",
        f"bot token: {masked_env_status(config.token_env)}",
        f"dry-run chat: {masked_env_status('TG_DASHBOARD_DRY_RUN_CHAT_ID')}",
        f"allowed users: {len(allowed_users)}",
        f"allowed chats: {len(allowed_chats)}",
        f"lock: {escape_html(lock_status)}",
        f"launchd plist: {'OK' if plist_path.exists() else 'MISS'} {escape_html(str(plist_path))}",
        f"launchd service: {escape_html(launchd_label)} / {escape_html(launchd_status)}",
        f"commands registered locally: {len(TELEGRAM_BOT_COMMANDS)}",
        "",
        "<b>Next</b>",
    ]
    if not env_path.exists():
        rows.append("Создать deployment-access/tg-bot-agent.env из example и заполнить токен/chat id.")
    elif not os.environ.get(config.token_env):
        rows.append("source env-файл и повторить: live-status, doctor, telegram-check.")
    elif not os.environ.get("TG_DASHBOARD_DRY_RUN_CHAT_ID"):
        rows.append("Добавить TG_DASHBOARD_DRY_RUN_CHAT_ID для telegram-check --send-health.")
    elif lock_pid and process_is_running(lock_pid):
        rows.append("Polling уже запущен; перед новым run остановить текущий процесс или launchd.")
    else:
        rows.append("Можно выполнить: telegram-check, set-commands, drop-pending-updates, once, run.")
    return "\n".join(rows)


def build_whoami_message(chat_id: str | int = "", meta: dict[str, Any] | None = None) -> str:
    meta = meta or {}
    lines = [
        "<b>TG ids для настройки доступа</b>",
        f"chat_id: <code>{escape_html(str(chat_id or ''))}</code>",
        f"user_id: <code>{escape_html(str(meta.get('user_id') or ''))}</code>",
    ]
    if meta.get("username"):
        lines.append(f"username: @{escape_html(str(meta['username']))}")
    full_name = " ".join(part for part in [str(meta.get("first_name") or ""), str(meta.get("last_name") or "")] if part).strip()
    if full_name:
        lines.append(f"name: {escape_html(full_name)}")
    lines.extend([
        "",
        "Для guard:",
        f"TG_AGENT_ALLOWED_USER_IDS={escape_html(str(meta.get('user_id') or ''))}",
        f"TG_AGENT_ALLOWED_CHAT_IDS={escape_html(str(chat_id or ''))}",
    ])
    return "\n".join(lines)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def read_voice_reply_state() -> dict[str, Any]:
    if not VOICE_REPLY_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(VOICE_REPLY_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_voice_reply_state(data: dict[str, Any]) -> None:
    VOICE_REPLY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    VOICE_REPLY_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def voice_reply_enabled(chat_id: str | int) -> bool:
    state = read_voice_reply_state()
    row = state.get(str(chat_id or "")) or {}
    return bool(row.get("enabled"))


def voice_reply_language(chat_id: str | int) -> str:
    state = read_voice_reply_state()
    row = state.get(str(chat_id or "")) or {}
    language = str(row.get("language") or "auto").strip().lower()
    return language if language in {"auto", "ru", "uz"} else "auto"


def set_voice_reply_enabled(chat_id: str | int, enabled: bool) -> None:
    state = read_voice_reply_state()
    previous = state.get(str(chat_id or "")) or {}
    state[str(chat_id or "")] = {
        "enabled": bool(enabled),
        "language": str(previous.get("language") or "auto"),
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    write_voice_reply_state(state)


def set_voice_reply_language(chat_id: str | int, language: str) -> None:
    normalized = language.strip().lower()
    if normalized not in {"auto", "ru", "uz"}:
        normalized = "auto"
    state = read_voice_reply_state()
    previous = state.get(str(chat_id or "")) or {}
    state[str(chat_id or "")] = {
        "enabled": bool(previous.get("enabled")),
        "language": normalized,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    write_voice_reply_state(state)


def wants_one_time_voice_reply(text: str) -> bool:
    normalized = normalize_text(text)
    return any(phrase in normalized for phrase in [
        "ответь голосом",
        "ответь голосовым",
        "скажи голосом",
        "голосом ответь",
        "пришли голосом",
    ])


def detect_voice_language(text: str, chat_id: str | int) -> str:
    configured = voice_reply_language(chat_id)
    if configured in {"ru", "uz"}:
        return configured
    lowered = normalize_text(text)
    uz_markers = [
        "nima", "qanday", "qancha", "bugun", "kecha", "ertaga", "hisobot",
        "sotuv", "tushum", "daromad", "xulosa", "muhim", "bo'yicha",
        "bo‘yicha", "o'zbek", "o‘zbek", "uzbek", "uzbekcha",
    ]
    if any(marker in lowered for marker in uz_markers) or re.search(r"[ўқғҳ]", lowered):
        return "uz"
    return "ru"


def build_voice_reply_message(text: str, chat_id: str | int) -> str:
    normalized = normalize_text(text)
    if re.search(r"\b(?:off|выкл|выключи|отключи|нет)\b", normalized):
        set_voice_reply_enabled(chat_id, False)
        return "Голосовые ответы выключены. Текстовые ответы остаются как раньше."
    if re.search(r"\b(?:on|вкл|включи|да)\b", normalized):
        set_voice_reply_enabled(chat_id, True)
        return "Голосовые ответы включены. Теперь буду дублировать ответы voice-сообщением."
    status = "включены" if voice_reply_enabled(chat_id) else "выключены"
    return "\n".join([
        f"Голосовые ответы сейчас {status}.",
        "/voice_reply on - включить",
        "/voice_reply off - выключить",
        "Разово: напиши в запросе «ответь голосом».",
    ])


def build_voice_lang_message(text: str, chat_id: str | int) -> str:
    normalized = normalize_text(text)
    if re.search(r"\b(?:ru|rus|russian|рус|русский|по русски|по-русски)\b", normalized):
        set_voice_reply_language(chat_id, "ru")
        return "Язык голосовых ответов: русский."
    if re.search(r"\b(?:uz|uzb|uzbek|uzbekcha|уз|узбек|узбекский|по узбекски|по-узбекски)\b", normalized):
        set_voice_reply_language(chat_id, "uz")
        return "Язык голосовых ответов: узбекский."
    if re.search(r"\b(?:auto|авто|автоматически)\b", normalized):
        set_voice_reply_language(chat_id, "auto")
        return "Язык голосовых ответов: auto. Буду выбирать русский или узбекский по тексту."
    current = voice_reply_language(chat_id)
    return "\n".join([
        f"Язык голосовых ответов сейчас: {current}.",
        "/voice_lang ru - русский",
        "/voice_lang uz - узбекский",
        "/voice_lang auto - автоопределение",
    ])


def macos_tts_voice(say_bin: str) -> str:
    preferred = ["Milena", "Yuri", "Katya", "Alyona"]
    try:
        result = subprocess.run([say_bin, "-v", "?"], check=False, capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    available = result.stdout or ""
    for name in preferred:
        if re.search(rf"^{re.escape(name)}\s", available, flags=re.MULTILINE):
            return name
    for line in available.splitlines():
        if "# ru_" in line or "# Russian" in line:
            return line.split()[0]
    return ""


def voice_tts_instructions(language: str) -> str:
    if language == "uz":
        return "Speak naturally in Uzbek. Use a calm, clear assistant voice. Keep business terms understandable."
    return "Speak naturally in Russian. Use a calm, clear assistant voice. Keep numbers and business terms easy to understand."


def render_openai_voice_reply(plain: str, target_path: Path, language: str) -> Path:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    payload = {
        "model": os.environ.get("TG_AGENT_TTS_MODEL", "gpt-4o-mini-tts"),
        "voice": os.environ.get("TG_AGENT_TTS_VOICE", "nova"),
        "input": plain,
        "instructions": voice_tts_instructions(language),
        "response_format": "mp3",
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        target_path.write_bytes(response.read())
    return target_path


def tts_provider() -> str:
    return os.environ.get("TG_AGENT_TTS_PROVIDER", "openai").strip().lower() or "openai"


def tts_fallback_enabled() -> bool:
    raw = os.environ.get("TG_AGENT_TTS_FALLBACK", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def convert_audio_to_telegram_voice(source_path: Path, target_path: Path) -> Path:
    ffmpeg_bin = shutil.which("ffmpeg") or os.environ.get("FFMPEG_BIN", "ffmpeg")
    if not Path(ffmpeg_bin).exists() and not shutil.which(ffmpeg_bin):
        raise RuntimeError("ffmpeg command not found")
    subprocess.run(
        [ffmpeg_bin, "-y", "-i", str(source_path), "-ac", "1", "-c:a", "libopus", "-b:a", "32k", str(target_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return target_path


def render_voice_reply(text: str, message_id: int | None = None, language: str = "ru") -> Path:
    plain = telegram_plain_text(text)
    plain = re.sub(r"https?://\S+", " ссылка ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        raise RuntimeError("empty voice reply text")
    if len(plain) > 1200:
        plain = plain[:1197].rstrip() + "..."
    VOICE_REPLY_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"voice_reply_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{message_id or int(time.time())}"
    ogg_path = VOICE_REPLY_DIR / f"{stem}.ogg"
    mp3_path = VOICE_REPLY_DIR / f"{stem}.mp3"
    provider = tts_provider()
    if provider in {"openai", "auto"}:
        try:
            render_openai_voice_reply(plain, mp3_path, language)
            convert_audio_to_telegram_voice(mp3_path, ogg_path)
            cleanup_temp_path(mp3_path)
            return ogg_path
        except Exception as exc:
            append_log("openai_voice_reply_error", {"message_id": message_id, "language": language, "error": str(exc)})
            cleanup_temp_path(mp3_path)
            if provider == "openai" or not tts_fallback_enabled():
                raise RuntimeError(f"OpenAI TTS недоступен: {exc}")
    if provider not in {"auto", "macos", "say"}:
        raise RuntimeError(f"Неизвестный TTS provider: {provider}")
    say_bin = shutil.which("say") or "/usr/bin/say"
    if not Path(say_bin).exists() and not shutil.which(say_bin):
        raise RuntimeError("say command not found")
    aiff_path = VOICE_REPLY_DIR / f"{stem}.aiff"
    say_args = [say_bin]
    voice = macos_tts_voice(say_bin)
    if voice:
        say_args.extend(["-v", voice])
    say_args.extend(["-o", str(aiff_path), plain])
    subprocess.run(say_args, check=True, capture_output=True, text=True, timeout=90)
    convert_audio_to_telegram_voice(aiff_path, ogg_path)
    cleanup_temp_path(aiff_path)
    return ogg_path


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def build_reload_message(config: AgentConfig) -> str:
    health = health_checks(config)
    self_test = run_self_tests(config)
    rows = read_inbox(limit=1_000_000)
    reminders = read_reminders(limit=1_000_000)
    brain_count = count_jsonl(BRAIN_HISTORY_FILE)
    status = "OK" if self_test["ok"] and all(item["ok"] for item in health if item["name"] not in {"bot token", "dry-run chat", "sheets webhook", "sheets secret"}) else "WARN"
    lines = [
        f"<b>Reload {BOT_DISPLAY_NAME}: {status}</b>",
        f"Конфиг: {escape_html(display_path(config.digest_config))}",
        f"Журнал: {len(rows)} строк",
        f"Напоминания: {sum(1 for row in reminders if row.get('status') == 'pending')} активных / {len(reminders)} всего",
        f"Brain history: {brain_count} записей",
        "",
        "<b>Self-test</b>",
    ]
    for item in self_test["checks"]:
        marker = "OK" if item["ok"] else "FAIL"
        lines.append(f"- {marker} {escape_html(item['name'])}: {escape_html(item['detail'])}")
    lines.extend(["", "<b>Подключения</b>"])
    for item in health:
        marker = "OK" if item["ok"] else "WARN"
        lines.append(f"- {marker} {escape_html(item['name'])}: {escape_html(item['detail'])}")
    lines.extend(["", "<b>Свежие data-файлы</b>"])
    latest = newest_data_files(limit=8)
    if latest:
        for path in latest:
            stamp = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            size_mb = path.stat().st_size / 1024 / 1024
            lines.append(f"- {escape_html(path.name)}: {stamp}, {size_mb:.1f} MB")
    else:
        lines.append("Файлы data не найдены.")
    return "\n".join(lines)


def build_brain_status_message(config: AgentConfig) -> str:
    command = config.brain_command.strip()
    first = ""
    if command:
        try:
            first = shlex.split(command)[0]
        except ValueError:
            first = ""
    if first.startswith("$HOME/"):
        first = str(Path.home() / first.removeprefix("$HOME/"))
    command_exists = bool(first and (Path(first).exists() or shutil.which(first)))
    history_count = count_jsonl(BRAIN_HISTORY_FILE)
    context_snapshot = build_brain_context_snapshot(task_limit=3, reminder_limit=2, inbox_limit=2)
    lines = [
        f"<b>Brain status {BOT_DISPLAY_NAME}</b>",
        f"Включен: {'да' if config.brain_enabled else 'нет'}",
        f"Команда: <code>{escape_html(command or 'не задана')}</code>",
        f"Команда доступна: {'да' if command_exists else 'нет'}",
        f"Файловый ответ: {'да' if '{output}' in command else 'нет'}",
        f"Timeout: {config.brain_timeout}s",
        f"История: {history_count} записей",
        "",
        "<b>Next</b>",
    ]
    if not config.brain_enabled:
        lines.append("Включить TG_AGENT_BRAIN_ENABLED=1 или brain.enabled в конфиге.")
    elif not command_exists:
        lines.append("Проверить TG_AGENT_BRAIN_COMMAND. Для Codex: codex exec --skip-git-repo-check --output-last-message {output} -")
    elif "{output}" not in command and "codex" in command:
        lines.append("Для Codex лучше добавить --output-last-message {output}, чтобы бот читал финальный ответ без event-stream.")
    else:
        lines.append("Можно проверять свободный диалог через simulate/preview или live-чат.")
    if context_snapshot:
        lines.extend(["", "<b>Контекст, который увидит brain</b>", escape_html(context_snapshot)])
    return "\n".join(lines)


def health_checks(config: AgentConfig) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append(check_item("bot token", bool(os.environ.get(config.token_env)), f"env {config.token_env}"))
    checks.append(check_item("dry-run chat", bool(os.environ.get(config.default_chat_env)), f"env {config.default_chat_env}"))
    checks.append(check_item("access guard", True, authorization_detail(config)))
    checks.append(check_item("digest config", config.digest_config.exists(), str(config.digest_config)))
    checks.append(check_item("dashboard data dir", DASHBOARD_DATA_DIR.exists(), str(DASHBOARD_DATA_DIR)))
    checks.append(check_item("management data", dashboard_data_path("management.json").exists(), str(dashboard_data_path("management.json"))))
    checks.append(check_item("widget source map", dashboard_data_path("widget_source_map.json").exists(), str(dashboard_data_path("widget_source_map.json"))))
    checks.append(check_item("inbox", INBOX_FILE.exists(), f"{len(read_inbox(limit=1_000_000))} rows"))
    media_dir = config.media_store_dir
    checks.append(check_item("media dir", media_dir.exists(), str(media_dir)))
    voice_command = config.voice_command or "whisper CLI fallback"
    checks.append(check_item("voice enabled", config.voice_enabled, voice_command))
    checks.append(check_item("voice command path", voice_command_available(config), voice_command))
    checks.append(check_item("brain cli", (not config.brain_enabled) or brain_cli_available(config), "enabled" if config.brain_enabled else "disabled"))
    checks.append(check_item("sheets webhook", bool(os.environ.get(config.sheets_webhook_env)), f"env {config.sheets_webhook_env}"))
    checks.append(check_item("sheets secret", bool(os.environ.get(config.sheets_secret_env)), f"env {config.sheets_secret_env}"))
    return checks


def check_item(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def voice_command_available(config: AgentConfig) -> bool:
    if config.voice_command:
        first = shlex.split(config.voice_command)[0] if config.voice_command.strip() else ""
        if first.startswith("$HOME/"):
            first = str(Path.home() / first.removeprefix("$HOME/"))
        return bool(first and Path(first).exists())
    return shutil.which("whisper") is not None


def configured_command_available(command: str) -> bool:
    if not command.strip():
        return False
    try:
        first = shlex.split(os.path.expandvars(os.path.expanduser(command)))[0]
    except ValueError:
        return False
    if not first:
        return False
    if Path(first).exists():
        return True
    return shutil.which(first) is not None


def ocr_command_available(config: AgentConfig) -> bool:
    return configured_command_available(config.ocr_command)


def voice_prompt_available(config: AgentConfig) -> bool:
    if "--prompt-file" not in config.voice_command:
        return True
    parts = shlex.split(config.voice_command)
    try:
        prompt_path = Path(parts[parts.index("--prompt-file") + 1])
    except (ValueError, IndexError):
        return False
    if not prompt_path.is_absolute():
        prompt_path = ROOT / prompt_path
    return prompt_path.exists() and bool(prompt_path.read_text(encoding="utf-8").strip())


def build_self_test_message(config: AgentConfig) -> str:
    result = run_self_tests(config)
    status = "OK" if result["ok"] else "WARN"
    lines = [f"<b>Self-test: {status}</b>"]
    for item in result["checks"]:
        marker = "OK" if item["ok"] else "FAIL"
        lines.append(f"- {marker} {escape_html(item['name'])}: {escape_html(item['detail'])}")
    return "\n".join(lines)


def run_self_tests(config: AgentConfig) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(test_check("help route", lambda: BOT_DISPLAY_NAME in build_digest_message(config, "HELP")))
    checks.append(test_check("agenda route", lambda: f"Повестка {BOT_DISPLAY_NAME}" in build_digest_message(config, "AGENDA")))
    checks.append(test_check("sources route", lambda: f"Источники {BOT_DISPLAY_NAME}" in build_digest_message(config, "SOURCES")))
    checks.append(test_check("stats route", lambda: "Статистика TG Agent" in build_digest_message(config, "STATS")))
    checks.append(test_check("task parse", lambda: classify_inbox_text("задача ecom срочно для Мухассар до завтра проверить чеки", {"kind": "text"}).get("priority") == "high"))
    checks.append(test_check("defect parse", lambda: classify_inbox_text("брак nour 12 стеблей на 450 000 сум", {"kind": "text"}).get("amount") == 450000))
    checks.append(test_check("uzbek waste route", lambda: resolve_digest_id("otkan hafta tg2 da nimalar brak boldi?") == "WASTE-QA"))
    checks.append(test_check("access message route", lambda: resolve_digest_id("/access ссылка: https://example.com логин: user пароль: pass") == "ACCESS-MESSAGE"))
    checks.append(test_check("access redaction", lambda: "secret" not in redact_sensitive_text("пароль: secret")))
    checks.append(test_check("groups route", lambda: resolve_digest_id("Какие чаты есть у меня") == "GROUPS"))
    checks.append(test_check("send group route", lambda: resolve_digest_id("/send_group -1001 | Дайте статус") == "SEND-GROUP"))
    checks.append(test_check("schedule group route", lambda: resolve_digest_id("/schedule_group -1001 | каждый день в 10:00 | Дайте статус") == "SCHEDULE-GROUP"))
    checks.append(test_check("decision parse", lambda: classify_inbox_text("/decision не меняем схему до сверки", {"kind": "text"}).get("type") == "decision"))
    checks.append(test_check("rule parse", lambda: classify_inbox_text("/rule обновляем все подвкладки вместе", {"kind": "text"}).get("type") == "rule"))
    checks.append(test_check("risk parse", lambda: classify_inbox_text("блокер: нет доступа к amo", {"kind": "text"}).get("type") == "risk"))
    checks.append(test_check("question parse", lambda: classify_inbox_text("вопрос: кто даст доступ к amo", {"kind": "text"}).get("type") == "question"))
    checks.append(test_check("waiting parse", lambda: classify_inbox_text("жду выгрузку OX от Мухассар", {"kind": "voice"}).get("status") == "waiting"))
    checks.append(test_check("ocr status route", lambda: f"OCR status {BOT_DISPLAY_NAME}" in build_digest_message(config, "OCR-STATUS")))
    checks.append(test_check("natural search query", lambda: extract_find_query("что я говорила про amo") == "amo"))
    checks.append(test_check("context query", lambda: extract_context_query("что известно про amo") == "amo"))
    checks.append(test_check("next for query", lambda: extract_next_for_query("что делать по amo") == "amo"))
    checks.append(test_check("thread id extraction", lambda: extract_thread_id("что вышло из abc123") == "abc123"))
    checks.append(test_check("reminder parse", lambda: parse_reminder_request("/remind через 10 минут проверить чеки").get("text") == "проверить чеки"))
    checks.append(test_check("recurring reminder parse", lambda: parse_reminder_request("/remind каждый день в 09:00 проверить источники").get("recurrence") == "daily@09:00"))
    checks.append(test_check("scheduled digest parse", lambda: scheduled_digest_id("/agenda") == "AGENDA" and scheduled_digest_id("/export_inbox") == ""))
    checks.append(test_check("meeting task extraction", lambda: len(extract_meeting_tasks("итоги встречи\n- нужно сверить чеки\n- договорились добавить amo\n- решили не менять дашборд\n- обсудили статус")) == 2))
    checks.append(test_check("meeting decision extraction", lambda: len(extract_meeting_decisions("итоги встречи\n- нужно сверить чеки\n- решили не менять дашборд\n- обсудили статус")) == 1))
    checks.append(test_check("triage extraction", lambda: len(triage_segments("задача проверить чеки\nвопрос кто даст доступ\nриск нет amo", {"kind": "text"})) == 3))
    checks.append(test_check("triage reminder extraction", lambda: triage_segments("напомни завтра 09:00 проверить источники", {"kind": "voice"})[0][1].get("type") == "reminder"))
    checks.append(test_check("voice digest preview", lambda: "Предпросмотр голосового разбора" in build_voice_digest_preview_message("голосовой разбор задача проверить чеки", {"kind": "voice"})))
    checks.append(test_check("auto voice digest heuristic", lambda: should_auto_voice_digest("нужно проверить чеки\nриск нет amo", {"kind": "voice"})))
    checks.append(test_check("dedup key", lambda: dedup_key({"type": "note", "text": " Проверить  чеки "}) == dedup_key({"type": "note", "text": "проверить чеки"})))
    checks.append(test_check("sheet dry-run", lambda: sync_inbox_to_sheet(config, rows=[], dry_run=True).get("ok") is True))
    checks.append(test_check("voice command available", lambda: voice_command_available(config)))
    checks.append(test_check("voice prompt available", lambda: voice_prompt_available(config)))
    checks.append(test_check("voice status message", lambda: "Voice status" in build_digest_message(config, "VOICE-STATUS")))
    checks.append(test_check("voice help message", lambda: "Как говорить" in build_digest_message(config, "VOICE-HELP")))
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def test_check(name: str, fn: Any) -> dict[str, Any]:
    try:
        ok = bool(fn())
        return {"name": name, "ok": ok, "detail": "ok" if ok else "failed"}
    except Exception as exc:
        return {"name": name, "ok": False, "detail": str(exc)}


def newest_data_files(limit: int = 10) -> list[Path]:
    names = {
        "management.json",
        "dashboard-payloads.js",
        "flowers.json",
        "nour.json",
        "plants.json",
        "wedding.json",
        "marketplace.json",
        "ecom-amocrm-leads.json",
        "ecom-ox-checks.json",
        "widget_source_map.json",
        "source_registry.json",
    }
    paths = [dashboard_data_path(name) for name in names if dashboard_data_path(name).exists()]
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def append_inbox(item: dict[str, Any]) -> Path:
    item.setdefault("id", build_inbox_id(item))
    item.setdefault("status", "open")
    INBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with INBOX_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    return INBOX_FILE


def append_group_message(message: dict[str, Any]) -> Path:
    GROUP_MESSAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with GROUP_MESSAGES_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n")
    return GROUP_MESSAGES_FILE


def append_business_connection(connection: dict[str, Any]) -> Path:
    BUSINESS_CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with BUSINESS_CONNECTIONS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(connection, ensure_ascii=False, sort_keys=True) + "\n")
    return BUSINESS_CONNECTIONS_FILE


def read_business_connections(limit: int = 1_000_000) -> list[dict[str, Any]]:
    if not BUSINESS_CONNECTIONS_FILE.exists():
        return []
    rows: list[dict[str, Any]] = []
    with BUSINESS_CONNECTIONS_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def active_business_connection_owners() -> dict[str, str]:
    owners: dict[str, str] = {}
    for row in read_business_connections():
        connection_id = str(row.get("business_connection_id") or "")
        owner_id = str(row.get("owner_user_id") or "")
        is_enabled = bool(row.get("is_enabled"))
        if not connection_id:
            continue
        if is_enabled and owner_id:
            owners[connection_id] = owner_id
        else:
            owners.pop(connection_id, None)
    return owners


def configured_allowed_business_connection_ids() -> set[str]:
    raw = os.environ.get("TG_AGENT_ALLOWED_BUSINESS_CONNECTION_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def business_connection_allowed(config: AgentConfig, connection_id: str) -> bool:
    connection_id = str(connection_id) or ""
    if connection_id in configured_allowed_business_connection_ids():
        return True
    owner_id = active_business_connection_owners().get(connection_id)
    if not owner_id:
        return False
    allowed_users = configured_allowed_user_ids(config)
    return not allowed_users or owner_id in allowed_users


def append_business_message(message: dict[str, Any]) -> Path:
    BUSINESS_MESSAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with BUSINESS_MESSAGES_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(message, ensure_ascii=False, sort_keys=True) + "\n")
    return BUSINESS_MESSAGES_FILE


def save_inbox_item(
    config: AgentConfig,
    item: dict[str, Any],
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
) -> dict[str, Any]:
    if item.get("type") == "task":
        text = strip_priority_flags(text)
    elif item.get("type") == "decision":
        text = strip_decision_prefix(text)
    elif item.get("type") == "rule":
        text = strip_rule_prefix(text)
    elif item.get("type") == "risk":
        text = strip_risk_prefix(text)
    elif item.get("type") == "question":
        text = strip_question_prefix(text)
    item.update({
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "text": text,
        "source_kind": meta.get("kind"),
        "chat_id": chat_id,
        "message_id": message_id,
        "media_file_id": meta.get("media_file_id") or "",
        "media_local_path": meta.get("media_local_path") or "",
        "media_note": meta.get("note") or "",
        "ocr_status": meta.get("ocr_status") or "",
        "source_chat_id": meta.get("source_chat_id") or chat_id,
        "source_chat_title": meta.get("source_chat_title") or "",
        "source_username": meta.get("username") or "",
        "source_first_name": meta.get("first_name") or "",
        "group_capture_reason": meta.get("group_capture_reason") or "",
    })
    append_inbox(item)
    sync_result = sync_inbox_to_sheet(config, [item]) if config.sheets_sync_on_save else None
    answer = build_saved_inbox_message(item)
    if sync_result:
        answer = f"{answer}\n\n{build_sheet_sync_message(sync_result)}"
    return {"digest_id": "INBOX-SAVE", "answer": answer, "inbox_item": item}


def read_inbox(limit: int = 10) -> list[dict[str, Any]]:
    if not INBOX_FILE.exists():
        return []
    rows: list[dict[str, Any]] = []
    with INBOX_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def read_group_messages(limit: int = 1_000_000) -> list[dict[str, Any]]:
    if not GROUP_MESSAGES_FILE.exists():
        return []
    rows: list[dict[str, Any]] = []
    with GROUP_MESSAGES_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def read_business_messages(limit: int = 1_000_000) -> list[dict[str, Any]]:
    if not BUSINESS_MESSAGES_FILE.exists():
        return []
    rows: list[dict[str, Any]] = []
    with BUSINESS_MESSAGES_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def business_message_date(row: dict[str, Any]) -> dt.date | None:
    raw = str(row.get("ts") or "")
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def write_inbox(rows: list[dict[str, Any]]) -> None:
    INBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with INBOX_FILE.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def find_inbox_row_by_prefix(rows: list[dict[str, Any]], id_prefix: str) -> dict[str, Any]:
    id_prefix = id_prefix.strip()
    if not id_prefix:
        return {"ok": False, "error": "Нужен id записи"}
    matches = [row for row in rows if str(row.get("id") or "").startswith(id_prefix)]
    if not matches:
        return {"ok": False, "error": f"Не нашла запись по id {id_prefix}"}
    if len(matches) > 1:
        return {"ok": False, "error": f"По id {id_prefix} найдено несколько записей. Возьми больше символов id."}
    return {"ok": True, "row": matches[0]}


def extract_undo_payload(text: str) -> tuple[str, str]:
    cleaned = re.sub(r"^\s*(?:/undo_last|/undo|отмени\s+запись|отменить\s+запись|отмени)\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    if not cleaned:
        return "", ""
    parts = cleaned.split(maxsplit=1)
    row_id = parts[0].strip(" :#")
    reason = parts[1].strip(" :-") if len(parts) > 1 else ""
    return row_id, reason


def undo_inbox_row_by_prefix(id_prefix: str, actor: str = "", reason: str = "") -> dict[str, Any]:
    rows = read_inbox(limit=1_000_000)
    found = find_inbox_row_by_prefix(rows, id_prefix)
    if not found.get("ok"):
        return found
    target = found["row"]
    if target.get("status") == "undone":
        return {"ok": False, "error": f"Запись {id_prefix} уже отменена"}
    target_id = target.get("id")
    now = dt.datetime.now().isoformat(timespec="seconds")
    updated_row: dict[str, Any] | None = None
    for row in rows:
        if row.get("id") == target_id:
            row["status"] = "undone"
            row["undone_at"] = now
            row["undone_by"] = actor
            row["undo_reason"] = reason
            row["updated_at"] = now
            if actor:
                row["updated_by"] = actor
            updated_row = row
            break
    write_inbox(rows)
    return {"ok": True, "id": target_id, "row": updated_row, "type": target.get("type") or "", "text": target.get("text") or ""}


def undo_last_inbox_row(chat_id: str | int = "", actor: str = "", reason: str = "undo_last") -> dict[str, Any]:
    rows = read_inbox(limit=1_000_000)
    chat_key = str(chat_id or "")
    candidates = [
        row for row in rows
        if row.get("status") != "undone" and (not chat_key or str(row.get("chat_id") or "") == chat_key)
    ]
    if not candidates and chat_key:
        candidates = [row for row in rows if row.get("status") != "undone"]
    if not candidates:
        return {"ok": False, "error": "Нет записей, которые можно отменить"}
    target = candidates[-1]
    return undo_inbox_row_by_prefix(str(target.get("id") or ""), actor=actor, reason=reason)


def build_undo_message(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return escape_html(str(result.get("error") or "Не смогла отменить запись"))
    text = compact_text(str(result.get("text") or ""), 140)
    return "\n".join([
        "<b>Запись отменена</b>",
        f"id: {escape_html(str(result.get('id') or '')[:8])}",
        f"type: {escape_html(str(result.get('type') or ''))}",
        escape_html(text),
    ])


def read_reminders(limit: int = 100) -> list[dict[str, Any]]:
    if not REMINDERS_FILE.exists():
        return []
    rows: list[dict[str, Any]] = []
    with REMINDERS_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def write_reminders(rows: list[dict[str, Any]]) -> None:
    REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with REMINDERS_FILE.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_reminder_id(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("created_at") or ""),
        str(row.get("chat_id") or ""),
        str(row.get("fire_at") or ""),
        str(row.get("text") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def read_brain_history(chat_id: str | int = "", limit_turns: int = 8) -> list[dict[str, Any]]:
    if not BRAIN_HISTORY_FILE.exists():
        return []
    chat_key = str(chat_id or "")
    rows: list[dict[str, Any]] = []
    with BRAIN_HISTORY_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("chat_id") or "") == chat_key:
                rows.append(row)
    return rows[-(limit_turns * 2):]


def append_brain_history(chat_id: str | int, role: str, text: str) -> None:
    BRAIN_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "chat_id": str(chat_id or ""),
        "role": role,
        "text": text,
    }
    with BRAIN_HISTORY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def reset_brain_history(chat_id: str | int = "") -> int:
    if not BRAIN_HISTORY_FILE.exists():
        return 0
    chat_key = str(chat_id or "")
    rows: list[dict[str, Any]] = []
    removed = 0
    with BRAIN_HISTORY_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("chat_id") or "") == chat_key:
                removed += 1
            else:
                rows.append(row)
    with BRAIN_HISTORY_FILE.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return removed


def export_inbox_csv(target: Path) -> Path:
    rows = read_inbox(limit=1_000_000)
    fieldnames = [
        "id",
        "ts",
        "type",
        "status",
        "closed_at",
        "closed_by",
        "due_date",
        "priority",
        "assignee",
        "direction",
        "date",
        "amount",
        "qty",
        "unit",
        "text",
        "source_kind",
        "chat_id",
        "message_id",
        "media_file_id",
        "media_local_path",
        "media_note",
        "ocr_status",
        "parent_id",
        "meeting_order",
        "decision_order",
        "comment_by",
        "undone_at",
        "undone_by",
        "undo_reason",
        "duplicate_of",
        "duplicate_at",
        "updated_at",
        "updated_by",
        "snoozed_at",
        "snoozed_by",
        "snoozed_count",
        "waiting_at",
        "waiting_by",
        "waiting_reason",
        "dropped_at",
        "dropped_by",
        "reopened_at",
        "reopened_by",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return target


def markdown_escape_text(value: str) -> str:
    return (value or "").replace("\r", " ").strip()


def format_memory_task_line(row: dict[str, Any]) -> str:
    row_id = str(row.get("id") or "")[:8]
    meta = [
        str(row.get("status") or "open"),
        str(row.get("priority") or "medium"),
        str(row.get("direction") or "без направления"),
    ]
    if row.get("assignee"):
        meta.append("@" + str(row["assignee"]))
    if row.get("due_date"):
        meta.append("due " + str(row["due_date"]))
    if row.get("waiting_reason"):
        meta.append("ждет: " + str(row["waiting_reason"]))
    text = compact_text(markdown_escape_text(str(row.get("text") or "")), 180)
    return f"- `{row_id}` / {' / '.join(meta)}: {text}"


def format_memory_inbox_line(row: dict[str, Any]) -> str:
    row_id = str(row.get("id") or "")[:8]
    ts = str(row.get("ts") or "")[:16]
    item_type = str(row.get("type") or "note")
    direction = str(row.get("direction") or "без направления")
    text = compact_text(markdown_escape_text(str(row.get("text") or "")), 180)
    return f"- `{row_id}` / {ts} / {item_type} / {direction}: {text}"


def append_markdown_section(lines: list[str], title: str, rows: list[dict[str, Any]], formatter: Any, empty: str = "пусто") -> None:
    lines.extend(["", f"## {title}"])
    if not rows:
        lines.append(empty)
        return
    for row in rows:
        lines.append(formatter(row))


def export_memory_markdown(target: Path, days: int = 7, limit: int = 30) -> Path:
    days = max(1, min(90, int(days)))
    limit = max(1, min(200, int(limit)))
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    rows = read_inbox(limit=1_000_000)
    period_rows = [
        row for row in rows
        if (row_date := inbox_row_date(row)) and start <= row_date <= today
    ]
    open_tasks = sort_task_rows([
        row for row in rows
        if row.get("type") == "task" and is_open_task_status(row.get("status"))
    ])[:limit]
    waiting = waiting_task_rows(limit=limit)
    risks = [row for row in period_rows if row.get("type") == "risk"][-limit:]
    questions = [row for row in period_rows if row.get("type") == "question" and is_open_inbox_status("question", row.get("status"))][-limit:]
    decisions = [row for row in period_rows if row.get("type") == "decision"][-limit:]
    rules = rule_rows(limit=limit)
    recent = period_rows[-limit:]
    reminders = [row for row in read_reminders(limit=1_000_000) if row.get("status") == "pending"][:limit]
    lines = [
        f"# {BOT_DISPLAY_NAME} memory export",
        f"period: `{start.isoformat()} - {today.isoformat()}`",
        f"generated_at: `{dt.datetime.now().isoformat(timespec='seconds')}`",
        f"journal_rows_in_period: `{len(period_rows)}`",
        f"open_tasks_total: `{len([row for row in rows if row.get('type') == 'task' and is_open_task_status(row.get('status'))])}`",
        f"waiting_total: `{len(waiting_task_rows(limit=1_000_000))}`",
        f"rules_total: `{len(rule_rows(limit=1_000_000))}`",
    ]
    append_markdown_section(lines, "Open Tasks", open_tasks, format_memory_task_line)
    append_markdown_section(lines, "Waiting", waiting, format_memory_task_line)
    append_markdown_section(lines, "Project Rules", list(reversed(rules)), format_memory_inbox_line)
    append_markdown_section(lines, "Risks", list(reversed(risks)), format_memory_inbox_line)
    append_markdown_section(lines, "Open Questions", list(reversed(questions)), format_memory_inbox_line)
    append_markdown_section(lines, "Decisions", list(reversed(decisions)), format_memory_inbox_line)
    append_markdown_section(lines, "Pending Reminders", reminders, lambda row: f"- `{str(row.get('id') or '')[:8]}` / {row.get('fire_at') or ''}: {markdown_escape_text(str(row.get('text') or ''))}")
    append_markdown_section(lines, "Recent Journal Rows", list(reversed(recent)), format_memory_inbox_line)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def build_inbox_id(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("ts") or ""),
        str(row.get("chat_id") or ""),
        str(row.get("message_id") or ""),
        str(row.get("text") or ""),
        str(row.get("media_file_id") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def normalize_inbox_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "id": row.get("id") or build_inbox_id(row),
        "ts": row.get("ts") or "",
        "type": row.get("type") or "",
        "status": row.get("status") or "",
        "closed_at": row.get("closed_at") or "",
        "closed_by": row.get("closed_by") or "",
        "due_date": row.get("due_date") or "",
        "priority": row.get("priority") or "",
        "assignee": row.get("assignee") or "",
        "direction": row.get("direction") or "",
        "date": row.get("date") or "",
        "amount": row.get("amount") or "",
        "qty": row.get("qty") or "",
        "unit": row.get("unit") or "",
        "text": row.get("text") or "",
        "source_kind": row.get("source_kind") or "",
        "chat_id": row.get("chat_id") or "",
        "message_id": row.get("message_id") or "",
        "media_file_id": row.get("media_file_id") or "",
        "media_local_path": row.get("media_local_path") or "",
        "media_note": row.get("media_note") or "",
        "ocr_status": row.get("ocr_status") or "",
        "parent_id": row.get("parent_id") or "",
        "meeting_order": row.get("meeting_order") or "",
        "decision_order": row.get("decision_order") or "",
        "comment_by": row.get("comment_by") or "",
        "undone_at": row.get("undone_at") or "",
        "undone_by": row.get("undone_by") or "",
        "undo_reason": row.get("undo_reason") or "",
        "duplicate_of": row.get("duplicate_of") or "",
        "duplicate_at": row.get("duplicate_at") or "",
        "updated_at": row.get("updated_at") or "",
        "updated_by": row.get("updated_by") or "",
        "snoozed_at": row.get("snoozed_at") or "",
        "snoozed_by": row.get("snoozed_by") or "",
        "snoozed_count": row.get("snoozed_count") or "",
        "waiting_at": row.get("waiting_at") or "",
        "waiting_by": row.get("waiting_by") or "",
        "waiting_reason": row.get("waiting_reason") or "",
        "dropped_at": row.get("dropped_at") or "",
        "dropped_by": row.get("dropped_by") or "",
        "reopened_at": row.get("reopened_at") or "",
        "reopened_by": row.get("reopened_by") or "",
    }
    return normalized


def build_sheet_payload(config: AgentConfig, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source": "tg_dashboard_agent",
        "sent_at": dt.datetime.now().isoformat(timespec="seconds"),
        "secret": os.environ.get(config.sheets_secret_env, ""),
        "rows": [normalize_inbox_row(row) for row in rows],
    }


def sync_inbox_to_sheet(config: AgentConfig, rows: list[dict[str, Any]] | None = None, dry_run: bool = False) -> dict[str, Any]:
    selected_rows = rows if rows is not None else read_inbox(limit=1_000_000)
    payload = build_sheet_payload(config, selected_rows)
    if dry_run:
        return {"ok": True, "dry_run": True, "rows": len(payload["rows"]), "payload": payload}
    webhook_url = os.environ.get(config.sheets_webhook_env, "")
    if not webhook_url:
        return {
            "ok": False,
            "skipped": True,
            "rows": len(payload["rows"]),
            "error": f"Missing {config.sheets_webhook_env}",
        }
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        body = response.read().decode("utf-8")
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        result = {"ok": False, "raw": body}
    result.setdefault("rows", len(payload["rows"]))
    append_log("sheet_sync", {"result": result, "rows": len(payload["rows"])})
    return result


def build_sheet_sync_message(result: dict[str, Any]) -> str:
    if result.get("dry_run"):
        return f"Sheet sync dry-run: {result.get('rows', 0)} строк готовы к отправке."
    if result.get("ok"):
        appended = result.get("appended", result.get("rows", 0))
        updated = result.get("updated", 0)
        skipped = result.get("skipped", 0)
        return f"Синхронизировала журнал в Google Sheet: добавлено {appended}, обновлено {updated}, пропущено {skipped}."
    if result.get("skipped"):
        return f"Google Sheet sync не настроен: {escape_html(str(result.get('error') or 'нет webhook'))}."
    return f"Google Sheet sync вернул ошибку: {escape_html(str(result.get('error') or result))}"


def build_inbox_message(limit: int = 8) -> str:
    rows = read_inbox(limit=limit)
    if not rows:
        return "Журнал пока пуст. Можно написать или надиктовать: задача ..., заметка ..., брак ..."
    lines = ["<b>Последние записи журнала</b>"]
    for row in reversed(rows):
        parts = [
            str(row.get("type") or "note"),
            str(row.get("direction") or "без направления"),
        ]
        if row.get("amount"):
            parts.append(f"{row['amount']} UZS")
        if row.get("qty"):
            parts.append(f"{row['qty']} {row.get('unit') or 'шт'}")
        text = str(row.get("text") or "")
        if len(text) > 120:
            text = text[:117] + "..."
        lines.append(f"- {escape_html(' / '.join(parts))}: {escape_html(text)}")
    return "\n".join(lines)


def extract_find_query(text: str) -> str:
    return re.sub(
        r"^\s*(?:/find|/search|найди\s+по|найди|поиск|вспомни|что\s+я\s+говорил[аи]?\s+про|что\s+говорили\s+про|что\s+было\s+про|покажи\s+записи\s+про|покажи\s+журнал\s+про)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()


def extract_context_query(text: str) -> str:
    return re.sub(
        r"^\s*(?:/context_about|/context|контекст\s+по|контекст|что\s+известно\s+про)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()


def extract_next_for_query(text: str) -> str:
    return re.sub(
        r"^\s*(?:/next_for|/next_about|что\s+делать\s+по|следующий\s+шаг\s+по)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()


def build_inbox_search_message(query: str, limit: int = 12) -> str:
    query = query.strip()
    if not query:
        return "Нужен запрос. Пример: /find ecom лиды или /find defect nour."
    normalized = normalize_text(query)
    tokens = [token for token in normalized.split() if token]
    item_type = ""
    type_aliases = {
        "task": "task",
        "задача": "task",
        "задачи": "task",
        "update": "task_update",
        "updates": "task_update",
        "апдейт": "task_update",
        "апдейты": "task_update",
        "комментарий": "task_update",
        "note": "note",
        "заметка": "note",
        "meeting": "meeting",
        "meetings": "meeting",
        "встреча": "meeting",
        "встречи": "meeting",
        "decision": "decision",
        "decisions": "decision",
        "решение": "decision",
        "решения": "decision",
        "risk": "risk",
        "risks": "risk",
        "риск": "risk",
        "риски": "risk",
        "blocker": "risk",
        "blockers": "risk",
        "блокер": "risk",
        "блокеры": "risk",
        "question": "question",
        "questions": "question",
        "вопрос": "question",
        "вопросы": "question",
        "answer": "question_answer",
        "answers": "question_answer",
        "ответ": "question_answer",
        "ответы": "question_answer",
        "defect": "defect",
        "брак": "defect",
    }
    if tokens and tokens[0] in type_aliases:
        item_type = type_aliases[tokens.pop(0)]
    if not tokens:
        return "Нужен текст после типа. Пример: /find task ecom."
    rows = read_inbox(limit=1_000_000)
    matches = []
    for row in rows:
        if item_type and row.get("type") != item_type:
            continue
        haystack = inbox_search_haystack(row)
        if all(token in haystack for token in tokens):
            matches.append(row)
    if not matches:
        return f"По запросу «{escape_html(query)}» ничего не нашла."
    shown = matches[-limit:]
    lines = [f"<b>Поиск по журналу</b>", f"Запрос: {escape_html(query)}", f"Найдено: {len(matches)}"]
    for row in reversed(shown):
        row_id = str(row.get("id") or "")[:8]
        row_type = str(row.get("type") or "note")
        status = str(row.get("status") or "")
        direction = str(row.get("direction") or "без направления")
        text = str(row.get("text") or "")
        if len(text) > 120:
            text = text[:117] + "..."
        bits = [row_id, row_type, direction]
        if status:
            bits.append(status)
        lines.append(f"- {escape_html(' / '.join(bits))}: {escape_html(text)}")
    if len(matches) > len(shown):
        lines.append(f"Показано {len(shown)} из {len(matches)}. Полная выгрузка: /export_inbox.")
    return "\n".join(lines)


def build_context_message(query: str, limit: int = 5) -> str:
    query = query.strip()
    if not query:
        return "Нужна тема. Пример: /context amo или голосом: что известно про чеки."
    tokens = [token for token in normalize_text(query).split() if token]
    if not tokens:
        return "Нужна тема. Пример: /context amo."
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if all(token in inbox_search_haystack(row) for token in tokens)
    ]
    if not rows:
        return f"По теме «{escape_html(query)}» контекста в журнале не нашла."

    open_tasks = [
        row for row in rows
        if row.get("type") == "task" and is_open_task_status(row.get("status")) and str(row.get("status") or "open") != "waiting"
    ]
    waiting = [
        row for row in rows
        if row.get("type") == "task" and str(row.get("status") or "open") == "waiting"
    ]
    risks = [row for row in rows if row.get("type") == "risk"]
    questions = [row for row in rows if row.get("type") == "question" and str(row.get("status") or "open") != "answered"]
    decisions = [row for row in rows if row.get("type") == "decision"]
    rules = [row for row in rows if row.get("type") == "rule"]
    recent = [row for row in rows if row.get("type") not in {"task", "risk", "question", "decision", "rule"}]

    lines = [
        "<b>Контекст по теме</b>",
        f"Тема: {escape_html(query)}",
        f"Найдено записей: {len(rows)}",
    ]
    append_handoff_section(lines, "Открытые задачи", list(reversed(open_tasks[-limit:])), format_task_list_line)
    append_handoff_section(lines, "Ожидания", list(reversed(waiting[-limit:])), format_task_list_line)
    append_handoff_section(lines, "Риски", list(reversed(risks[-limit:])), format_handoff_inbox_line)
    append_handoff_section(lines, "Вопросы", list(reversed(questions[-limit:])), format_handoff_inbox_line)
    append_handoff_section(lines, "Решения", list(reversed(decisions[-limit:])), format_handoff_inbox_line)
    append_handoff_section(lines, "Правила", list(reversed(rules[-limit:])), format_handoff_inbox_line)
    append_handoff_section(lines, "Последние записи", list(reversed(recent[-limit:])), format_handoff_inbox_line)
    lines.extend(["", "Дальше: /find, /focus, /waiting_followups, /handoff."])
    return "\n".join(lines)


def build_next_for_message(query: str) -> str:
    query = query.strip()
    if not query:
        return "Нужна тема. Пример: /next_for amo или голосом: что делать по чекам."
    tokens = [token for token in normalize_text(query).split() if token]
    if not tokens:
        return "Нужна тема. Пример: /next_for amo."
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if all(token in inbox_search_haystack(row) for token in tokens)
    ]
    if not rows:
        return f"По теме «{escape_html(query)}» записей нет. Сначала можно добавить заметку или задачу голосом."

    waiting = [
        row for row in rows
        if row.get("type") == "task" and str(row.get("status") or "open") == "waiting"
    ]
    questions = [
        row for row in rows
        if row.get("type") == "question" and str(row.get("status") or "open") != "answered"
    ]
    risks = [row for row in rows if row.get("type") == "risk"]
    tasks = [
        row for row in rows
        if row.get("type") == "task" and is_open_task_status(row.get("status")) and str(row.get("status") or "open") != "waiting"
    ]
    decisions = [row for row in rows if row.get("type") == "decision"]

    lines = ["<b>Следующий шаг по теме</b>", f"Тема: {escape_html(query)}"]
    if waiting:
        row = sort_task_rows(waiting)[0]
        reason = str(row.get("waiting_reason") or "нужен апдейт").strip()
        lines.extend([
            "1. Пингануть ожидание.",
            format_task_list_line(row),
            f"Текст пинга: {escape_html(reason)}",
            "После ответа: /comment <id> <что ответили> или /start_task <id>.",
        ])
    elif questions:
        row = questions[-1]
        row_id = str(row.get("id") or "")[:8]
        lines.extend([
            "1. Закрыть открытый вопрос.",
            format_handoff_inbox_line(row),
            f"После ответа: /answer {escape_html(row_id)} <ответ>.",
        ])
    elif risks:
        row = risks[-1]
        lines.extend([
            "1. Разобрать свежий риск.",
            format_handoff_inbox_line(row),
            "Дальше: превратить риск в задачу или решение голосом.",
        ])
    elif tasks:
        row = sort_task_rows(tasks)[0]
        lines.extend([
            "1. Продвинуть открытую задачу.",
            format_task_list_line(row),
            "Дальше: /start_task <id>, /comment <id> <апдейт> или /done <id>.",
        ])
    elif decisions:
        row = decisions[-1]
        lines.extend([
            "1. Проверить, не требует ли решение следующего действия.",
            format_handoff_inbox_line(row),
            "Если требует: надиктовать задачу со ссылкой на это решение.",
        ])
    else:
        lines.extend([
            "1. Явного следующего шага не вижу.",
            "Можно надиктовать: задача ..., вопрос ..., риск ...",
        ])
    lines.extend(["", "Контекст: /context " + escape_html(query)])
    return "\n".join(lines)


def extract_thread_id(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:/thread|/origin|цепочка|что\s+вышло\s+из)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    return cleaned.split()[0].strip(" :#") if cleaned else ""


def format_thread_reminder_line(row: dict[str, Any]) -> str:
    row_id = str(row.get("id") or "")[:8]
    status = str(row.get("status") or "")
    fire_at = str(row.get("fire_at") or "")
    text = compact_text(str(row.get("text") or ""), 120)
    bits = [row_id, status or "pending"]
    if fire_at:
        bits.append(fire_at.replace("T", " ")[:16])
    return f"- {escape_html(' / '.join(bits))}: {escape_html(text)}"


def build_thread_message(id_prefix: str, limit: int = 20) -> str:
    id_prefix = id_prefix.strip()
    if not id_prefix:
        return "Нужен id записи. Пример: /thread abc123."
    rows = read_inbox(limit=1_000_000)
    found = find_inbox_row_by_prefix(rows, id_prefix)
    if not found.get("ok"):
        return escape_html(str(found.get("error") or "Не нашла запись"))
    selected = found["row"]
    parent_id = str(selected.get("parent_id") or selected.get("id") or "")
    parent = next((row for row in rows if str(row.get("id") or "") == parent_id), selected)
    children = [
        row for row in rows
        if str(row.get("parent_id") or "") == parent_id and str(row.get("id") or "") != str(parent.get("id") or "")
    ]
    reminders = [
        row for row in read_reminders(limit=1_000_000)
        if str(row.get("parent_id") or "") == parent_id
    ]
    lines = [
        "<b>Цепочка записи</b>",
        f"parent: {escape_html(str(parent.get('id') or '')[:8])}",
        f"тип: {escape_html(str(parent.get('type') or 'note'))}",
        f"источник: {escape_html(str(parent.get('source_kind') or ''))}",
        "",
        "<b>Исходник</b>",
        escape_html(compact_text(str(parent.get("text") or ""), 700)),
    ]
    grouped: list[tuple[str, list[dict[str, Any]], Any]] = [
        ("Задачи", [row for row in children if row.get("type") == "task" and str(row.get("status") or "open") != "waiting"], format_task_list_line),
        ("Ожидания", [row for row in children if row.get("type") == "task" and str(row.get("status") or "open") == "waiting"], format_task_list_line),
        ("Риски", [row for row in children if row.get("type") == "risk"], format_handoff_inbox_line),
        ("Вопросы", [row for row in children if row.get("type") == "question"], format_handoff_inbox_line),
        ("Решения", [row for row in children if row.get("type") == "decision"], format_handoff_inbox_line),
        ("Прочие записи", [row for row in children if row.get("type") not in {"task", "risk", "question", "decision"}], format_handoff_inbox_line),
    ]
    for title, group_rows, formatter in grouped:
        append_handoff_section(lines, title, group_rows[-limit:], formatter)
    append_handoff_section(lines, "Напоминания", reminders[-limit:], format_thread_reminder_line)
    return "\n".join(lines)


def inbox_search_haystack(row: dict[str, Any]) -> str:
    fields = [
        "id",
        "type",
        "status",
        "direction",
        "due_date",
        "priority",
        "assignee",
        "date",
        "amount",
        "qty",
        "unit",
        "text",
        "source_kind",
        "chat_id",
        "message_id",
        "media_local_path",
        "parent_id",
        "meeting_order",
        "decision_order",
        "duplicate_of",
    ]
    return normalize_text(" ".join(str(row.get(field) or "") for field in fields))


def build_filtered_inbox_message(item_type: str, only_open: bool = False, limit: int = 12) -> str:
    rows = [
        row
        for row in read_inbox(limit=1_000_000)
        if row.get("type") == item_type and (not only_open or is_open_inbox_status(item_type, row.get("status")))
    ]
    rows = rows[-limit:]
    titles = {
        "task": "Открытые задачи" if only_open else "Последние задачи",
        "defect": "Последние записи брака",
        "meeting": "Последние встречи",
        "decision": "Последние решения",
        "risk": "Последние риски и блокеры",
        "question": "Открытые вопросы" if only_open else "Последние вопросы",
        "question_answer": "Последние ответы на вопросы",
        "task_update": "Последние апдейты задач",
        "note": "Последние заметки",
    }
    title = titles.get(item_type, f"Последние записи: {item_type}")
    if not rows:
        return f"{title}: пусто."
    lines = [f"<b>{escape_html(title)}</b>"]
    for row in reversed(rows):
        row_id = str(row.get("id") or "")[:8]
        text = str(row.get("text") or "")
        if len(text) > 120:
            text = text[:117] + "..."
        bits = [row_id]
        if item_type in {"task", "question"}:
            bits.append(str(row.get("status") or "open"))
        bits.append(format_task_meta(row))
        if row.get("parent_id"):
            bits.append(f"parent {str(row.get('parent_id'))[:8]}")
        lines.append(f"- {escape_html(' / '.join(bit for bit in bits if bit))}: {escape_html(text)}")
    return "\n".join(lines)


def extract_assignee_query(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:/for|/assignee|задачи\s+для|что\s+у|что\s+по)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,:;!?")


def extract_owner_brief_query(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:/owner_brief|/brief_for|бриф\s+для|статус\s+для)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,:;!?")


def extract_direction_query(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:/tasks|/list|/direction_tasks|задачи)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,:;!?")


def extract_assign_payload(text: str) -> tuple[str, str]:
    cleaned = re.sub(
        r"^\s*(?:/assign|назначь|назначить|передай\s+задачу|передать\s+задачу)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    parts = cleaned.split(maxsplit=1)
    if not parts:
        return "", ""
    task_id = parts[0].strip(" .,:;!?")
    assignee = parts[1].strip(" .,:;!?") if len(parts) > 1 else ""
    assignee = re.sub(r"^(?:на|для|ответственный|ответственная)\s+", "", assignee, flags=re.IGNORECASE).strip(" .,:;!?")
    return task_id, assignee


def extract_due_payload(text: str) -> tuple[str, str]:
    cleaned = re.sub(
        r"^\s*(?:/due|/deadline|перенеси\s+задачу|перенести\s+задачу|срок\s+задачи)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    parts = cleaned.split(maxsplit=1)
    if not parts:
        return "", ""
    task_id = parts[0].strip(" .,:;!?")
    rest = parts[1].strip(" .,:;!?") if len(parts) > 1 else ""
    rest = re.sub(r"^(?:на|до|к)\s+", "", rest, flags=re.IGNORECASE).strip(" .,:;!?")
    due_date = detect_due_date(normalize_text("до " + rest)) or detect_due_date(normalize_text(rest))
    if not due_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", rest):
        due_date = rest
    return task_id, due_date


def extract_priority_payload(text: str) -> tuple[str, str]:
    cleaned = re.sub(
        r"^\s*(?:/priority|/prio|приоритет\s+задачи|сделай\s+задачу)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    parts = cleaned.split(maxsplit=1)
    if not parts:
        return "", ""
    task_id = parts[0].strip(" .,:;!?")
    rest = parts[1].strip(" .,:;!?") if len(parts) > 1 else ""
    rest = re.sub(r"^(?:приоритет|priority|как|в)\s+", "", rest, flags=re.IGNORECASE).strip(" .,:;!?")
    priority = normalize_priority_value(rest) or detect_priority(normalize_text(rest))
    return task_id, priority


def extract_direction_payload(text: str) -> tuple[str, str]:
    cleaned = re.sub(
        r"^\s*(?:/direction|/move_task|направление\s+задачи|перенеси\s+задачу|перенести\s+задачу)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    parts = cleaned.split(maxsplit=1)
    if not parts:
        return "", ""
    task_id = parts[0].strip(" .,:;!?")
    rest = parts[1].strip(" .,:;!?") if len(parts) > 1 else ""
    rest = re.sub(r"^(?:в|на|направление|direction)\s+", "", rest, flags=re.IGNORECASE).strip(" .,:;!?")
    return task_id, detect_direction(normalize_text(rest))


def assignee_task_rows(query: str, statuses: set[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    needle = query.casefold().strip()
    if not needle:
        return []
    rows = [row for row in read_inbox(limit=1_000_000) if row.get("type") == "task"]
    if statuses is not None:
        rows = [row for row in rows if str(row.get("status") or "open") in statuses]
    rows = [
        row for row in rows
        if needle in str(row.get("assignee") or "").casefold()
    ]
    return sort_task_rows(rows)[:limit]


def direction_task_rows(query: str, statuses: set[str] | None = None, limit: int = 20) -> tuple[str, list[dict[str, Any]]]:
    direction = detect_direction(normalize_text(query))
    if not direction:
        return "", []
    rows = [row for row in read_inbox(limit=1_000_000) if row.get("type") == "task"]
    if statuses is not None:
        rows = [row for row in rows if str(row.get("status") or "open") in statuses]
    rows = [row for row in rows if str(row.get("direction") or "") == direction]
    return direction, sort_task_rows(rows)[:limit]


def build_assignee_tasks_message(query: str, statuses: set[str] | None = None, limit: int = 20) -> str:
    query = query.strip()
    if not query:
        return "Нужен ответственный. Пример: /for Мухассар"
    rows = assignee_task_rows(query, statuses=statuses or {"open", "pending", "in_progress", "waiting"}, limit=limit)
    title = f"Задачи для {query}"
    if not rows:
        return f"{title}: пусто."
    lines = [f"<b>{escape_html(title)}</b>"]
    for row in rows:
        lines.append(format_task_list_line(row))
    return "\n".join(lines)


def build_owner_brief_message(query: str, days: int = 7, limit: int = 8) -> str:
    query = query.strip()
    if not query:
        return "Нужен ответственный. Пример: /owner_brief Мухассар"
    open_rows = assignee_task_rows(query, statuses={"open", "pending", "in_progress", "waiting"}, limit=limit)
    waiting_rows = [row for row in open_rows if str(row.get("status") or "open") == "waiting"]
    soon_ids = {str(row.get("id") or "") for row in due_task_rows("soon", days=days, limit=1_000_000)}
    soon_rows = [row for row in open_rows if str(row.get("id") or "") in soon_ids]
    lines = [
        f"<b>Бриф для {escape_html(query)}</b>",
        f"Открытые задачи: {len(open_rows)}",
        f"Ожидания: {len(waiting_rows)}",
        f"Срок в ближайшие {days} дн.: {len(soon_rows)}",
    ]
    lines.append("")
    lines.append("<b>Открытые задачи</b>")
    if open_rows:
        for row in open_rows[:limit]:
            lines.append(format_task_list_line(row))
    else:
        lines.append("пусто")
    lines.append("")
    lines.append("<b>Ожидания</b>")
    if waiting_rows:
        for row in waiting_rows[:5]:
            lines.append(format_task_list_line(row))
    else:
        lines.append("пусто")
    lines.append("")
    lines.append(f"<b>Ближайшие сроки на {days} дн.</b>")
    if soon_rows:
        for row in soon_rows[:5]:
            lines.append(format_task_list_line(row))
    else:
        lines.append("пусто")
    if waiting_rows:
        lines.append("")
        lines.append("<b>Что написать</b>")
        for row in waiting_rows[:3]:
            row_id = str(row.get("id") or "")[:8]
            reason = str(row.get("waiting_reason") or "нужен апдейт по внешнему шагу").strip()
            task_text = compact_text(str(row.get("text") or ""), 110)
            message = f"{query}, добрый день. Подскажите, пожалуйста, есть ли апдейт: {reason}? Задача: {task_text}."
            lines.append(f"- <code>{escape_html(row_id)}</code>: {escape_html(message)}")
    lines.extend(["", f"Дальше: /for {escape_html(query)}, /nudges, /next_action."])
    return "\n".join(lines)


def build_direction_tasks_message(query: str, statuses: set[str] | None = None, limit: int = 20) -> str:
    query = query.strip()
    if not query:
        return "Нужно направление. Пример: /tasks ecom"
    direction, rows = direction_task_rows(query, statuses=statuses or {"open", "pending", "in_progress", "waiting"}, limit=limit)
    if not direction:
        return "Не поняла направление. Пример: /tasks ecom, /tasks nour, /tasks plants"
    title = f"Задачи {direction}"
    if not rows:
        return f"{title}: пусто."
    lines = [f"<b>{escape_html(title)}</b>"]
    for row in rows:
        lines.append(format_task_list_line(row))
    return "\n".join(lines)


def build_task_assign_message(result: dict[str, Any], assignee: str) -> str:
    if not result.get("ok"):
        return "Не смогла назначить: " + escape_html(str(result.get("error") or "unknown error"))
    row_id = str(result.get("id") or "")[:8]
    text = compact_text(str(result.get("text") or ""), 120)
    return "\n".join([
        "<b>Назначила ответственного</b>",
        f"id: <code>{escape_html(row_id)}</code>",
        f"ответственный: {escape_html(assignee)}",
        f"задача: {escape_html(text)}",
    ])


def build_task_due_message(result: dict[str, Any], due_date: str) -> str:
    if not result.get("ok"):
        return "Не смогла перенести срок: " + escape_html(str(result.get("error") or "unknown error"))
    row_id = str(result.get("id") or "")[:8]
    text = compact_text(str(result.get("text") or ""), 120)
    return "\n".join([
        "<b>Перенесла срок задачи</b>",
        f"id: <code>{escape_html(row_id)}</code>",
        f"срок: {escape_html(due_date)}",
        f"задача: {escape_html(text)}",
    ])


def build_task_priority_message(result: dict[str, Any], priority: str) -> str:
    if not result.get("ok"):
        return "Не смогла сменить приоритет: " + escape_html(str(result.get("error") or "unknown error"))
    row_id = str(result.get("id") or "")[:8]
    text = compact_text(str(result.get("text") or ""), 120)
    return "\n".join([
        "<b>Сменила приоритет задачи</b>",
        f"id: <code>{escape_html(row_id)}</code>",
        f"приоритет: {escape_html(priority)}",
        f"задача: {escape_html(text)}",
    ])


def build_task_direction_message(result: dict[str, Any], direction: str) -> str:
    if not result.get("ok"):
        return "Не смогла сменить направление: " + escape_html(str(result.get("error") or "unknown error"))
    row_id = str(result.get("id") or "")[:8]
    text = compact_text(str(result.get("text") or ""), 120)
    return "\n".join([
        "<b>Сменила направление задачи</b>",
        f"id: <code>{escape_html(row_id)}</code>",
        f"направление: {escape_html(direction)}",
        f"задача: {escape_html(text)}",
    ])


def parse_stale_days(text: str, default: int = 3) -> int:
    match = re.search(r"(?:^|\s)(\d{1,3})(?:\s|$)", text)
    if not match:
        return default
    return max(1, min(90, int(match.group(1))))


def parse_task_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed_date = dt.date.fromisoformat(raw[:10])
            parsed = dt.datetime.combine(parsed_date, dt.time.min)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def latest_task_activity(row: dict[str, Any], updates_by_parent: dict[str, list[dict[str, Any]]]) -> dt.datetime | None:
    candidates = [
        parse_task_datetime(row.get("updated_at")),
        parse_task_datetime(row.get("waiting_at")),
        parse_task_datetime(row.get("ts")),
    ]
    for update in updates_by_parent.get(str(row.get("id") or ""), []):
        candidates.extend([
            parse_task_datetime(update.get("updated_at")),
            parse_task_datetime(update.get("ts")),
        ])
    candidates = [item for item in candidates if item is not None]
    return max(candidates) if candidates else None


def stale_task_rows(days: int = 3, limit: int = 20) -> list[dict[str, Any]]:
    threshold = dt.datetime.now() - dt.timedelta(days=days)
    rows = read_inbox(limit=1_000_000)
    updates_by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("type") == "task_update" and row.get("status") != "undone":
            parent_id = str(row.get("parent_id") or "")
            if parent_id:
                updates_by_parent.setdefault(parent_id, []).append(row)

    stale_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("type") != "task" or not is_open_task_status(row.get("status")):
            continue
        last_at = latest_task_activity(row, updates_by_parent)
        if last_at and last_at > threshold:
            continue
        enriched = dict(row)
        enriched["_last_activity_at"] = last_at.isoformat(timespec="seconds") if last_at else ""
        enriched["_stale_days"] = (dt.datetime.now() - last_at).days if last_at else days
        stale_rows.append(enriched)

    stale_rows.sort(key=lambda item: (item.get("_last_activity_at") or "", str(item.get("ts") or "")))
    return stale_rows[:limit]


def build_stale_tasks_message(days: int = 3, limit: int = 20) -> str:
    rows = stale_task_rows(days=days, limit=limit)
    if not rows:
        return f"Зависших задач старше {days} дн. нет."
    lines = [f"<b>Зависшие задачи старше {days} дн.</b>"]
    for row in rows:
        line = format_task_list_line(row)
        last_at = str(row.get("_last_activity_at") or "нет даты")
        stale_days = str(row.get("_stale_days") or "")
        suffix = f"последнее событие: {last_at}"
        if stale_days:
            suffix += f", {stale_days} дн. назад"
        lines.append(f"{line}\n  {escape_html(suffix)}")
    return "\n".join(lines)


def build_task_list_message(
    statuses: set[str] | None = None,
    title: str = "Все задачи",
    limit: int | None = 20,
    max_chars: int = 3600,
) -> str:
    chunks = build_task_list_chunks(statuses=statuses, title=title, limit=limit, max_chars=max_chars)
    return chunks[0] if chunks else f"{title}: пусто."


def build_task_list_chunks(
    statuses: set[str] | None = None,
    title: str = "Все задачи",
    limit: int | None = 20,
    max_chars: int = 3600,
) -> list[str]:
    rows = [row for row in read_inbox(limit=1_000_000) if row.get("type") == "task"]
    if statuses is not None:
        rows = [row for row in rows if str(row.get("status") or "open") in statuses]
    total_rows = len(rows)
    if limit is not None:
        rows = rows[-limit:]
    if not rows:
        return [f"{title}: пусто."]
    lines = [f"<b>{escape_html(title)}</b>"]
    chunks: list[str] = []
    for row in reversed(rows):
        line = format_task_list_line(row)
        next_text = "\n".join(lines + [line])
        if len(next_text) > max_chars and len(lines) > 1:
            chunks.append("\n".join(lines))
            lines = [f"<b>{escape_html(title)}</b> продолжение", line]
            continue
        lines.append(line)
    if lines:
        chunks.append("\n".join(lines))
    if limit is None and len(chunks) > 1:
        chunks = [
            f"{chunk}\n\nЧасть {idx} из {len(chunks)}. Полная CSV-выгрузка: /export_inbox."
            for idx, chunk in enumerate(chunks, start=1)
        ]
    elif limit is None and total_rows:
        chunks[0] = f"{chunks[0]}\n\nПоказано {total_rows} из {total_rows}. Полная CSV-выгрузка: /export_inbox."
    return chunks


def format_task_list_line(row: dict[str, Any]) -> str:
    row_id = str(row.get("id") or "")[:8]
    status = str(row.get("status") or "open")
    text = str(row.get("text") or "")
    if len(text) > 110:
        text = text[:107] + "..."
    return f"- {escape_html(row_id)} / {escape_html(status)} / {escape_html(format_task_meta(row))}: {escape_html(text)}"


def task_priority_rank(row: dict[str, Any]) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(str(row.get("priority") or "medium"), 3)


def task_status_rank(row: dict[str, Any]) -> int:
    return {"in_progress": 0, "waiting": 1, "open": 2, "pending": 2, "snoozed": 3, "done": 4, "dropped": 5, "undone": 6}.get(
        str(row.get("status") or "open"),
        5,
    )


def sort_task_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            task_status_rank(row),
            task_priority_rank(row),
            str(row.get("due_date") or "9999-12-31"),
            str(row.get("ts") or ""),
        ),
    )


def task_rows_for_keyboard(statuses: set[str] | None = None, limit: int = 5) -> list[dict[str, Any]]:
    rows = [row for row in read_inbox(limit=1_000_000) if row.get("type") == "task"]
    if statuses is not None:
        rows = [row for row in rows if str(row.get("status") or "open") in statuses]
    return sort_task_rows(rows)[:limit]


def build_task_keyboard(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    keyboard: list[list[dict[str, str]]] = []
    for row in rows:
        row_id = str(row.get("id") or "")[:8]
        if not row_id:
            continue
        status = str(row.get("status") or "open")
        if status in {"done", "snoozed", "dropped"}:
            keyboard.append([
                {"text": f"вернуть {row_id}", "callback_data": f"task:open:{row_id}"},
                {"text": f"в работу {row_id}", "callback_data": f"task:in_progress:{row_id}"},
            ])
        else:
            keyboard.append([
                {"text": f"готово {row_id}", "callback_data": f"task:done:{row_id}"},
                {"text": f"в работу {row_id}", "callback_data": f"task:in_progress:{row_id}"},
                {"text": f"отложить {row_id}", "callback_data": f"task:snoozed:{row_id}"},
                {"text": f"убрать {row_id}", "callback_data": f"task:dropped:{row_id}"},
            ])
    return {"inline_keyboard": keyboard} if keyboard else None


def task_keyboard_for_digest(digest_id: str) -> dict[str, Any] | None:
    if digest_id == "TASKS":
        return build_task_keyboard(task_rows_for_keyboard(statuses={"open", "pending", "in_progress", "waiting"}))
    if digest_id == "DOING-TASKS":
        return build_task_keyboard(task_rows_for_keyboard(statuses={"in_progress"}))
    if digest_id == "WAITING-TASKS":
        return build_task_keyboard(task_rows_for_keyboard(statuses={"waiting"}))
    if digest_id == "DUE-SOON-TASKS":
        return build_task_keyboard(due_task_rows("soon", days=7, limit=12))
    if digest_id == "DONE-TASKS":
        return build_task_keyboard(task_rows_for_keyboard(statuses={"done"}))
    if digest_id == "SNOOZED-TASKS":
        return build_task_keyboard(task_rows_for_keyboard(statuses={"snoozed"}))
    if digest_id == "DROPPED-TASKS":
        return build_task_keyboard(task_rows_for_keyboard(statuses={"dropped"}))
    if digest_id == "ALL-TASKS":
        return build_task_keyboard(task_rows_for_keyboard())
    return None


def pinned_tasks_message() -> str:
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if row.get("type") == "task" and is_open_task_status(row.get("status"))
    ]
    status_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "open")
        status_counts[status] = status_counts.get(status, 0) + 1
        priority = str(row.get("priority") or "")
        if priority:
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
    updated = dt.datetime.now().strftime("%d.%m %H:%M")
    if not rows:
        return "\n".join([
            f"<b>Задачи {BOT_DISPLAY_NAME}</b>",
            "Чисто: открытых задач нет.",
            f"<i>обновлено {escape_html(updated)}</i>",
        ])
    bits = [f"<b>{len(rows)} задач</b>"]
    if status_counts.get("in_progress"):
        bits.append(f"в работе: {status_counts['in_progress']}")
    if status_counts.get("waiting"):
        bits.append(f"ждут: {status_counts['waiting']}")
    if priority_counts.get("high"):
        bits.append(f"срочно: {priority_counts['high']}")
    if priority_counts.get("low"):
        bits.append(f"когда-нибудь: {priority_counts['low']}")
    return "\n".join([
        f"<b>Задачи {BOT_DISPLAY_NAME}</b>",
        " · ".join(bits),
        "",
        "Кнопки ниже отсортированы: в работе, срочные, обычные, когда-нибудь.",
        "Полный список: /tasks или /all_tasks.",
        f"<i>обновлено {escape_html(updated)}</i>",
    ])


def pinned_tasks_keyboard() -> dict[str, Any] | None:
    return build_task_keyboard(task_rows_for_keyboard(statuses={"open", "pending", "in_progress", "waiting"}, limit=20))


def get_pinned_task_message_id(chat_id: str | int) -> int | None:
    state = load_state()
    raw = (state.get("pinned_tasks") or {}).get(str(chat_id))
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def set_pinned_task_message_id(chat_id: str | int, message_id: int | None) -> None:
    state = load_state()
    pinned = dict(state.get("pinned_tasks") or {})
    if message_id:
        pinned[str(chat_id)] = int(message_id)
    else:
        pinned.pop(str(chat_id), None)
    if pinned:
        state["pinned_tasks"] = pinned
    else:
        state.pop("pinned_tasks", None)
    save_state(state)


def refresh_pinned_tasks(config: AgentConfig, chat_id: str | int, force_create: bool = False) -> dict[str, Any]:
    if not chat_id:
        return {"ok": False, "error": "Нет chat_id"}
    text = pinned_tasks_message()
    keyboard = pinned_tasks_keyboard()
    message_id = get_pinned_task_message_id(chat_id)
    if message_id and not force_create:
        try:
            telegram_edit_message(config, chat_id, message_id, text, reply_markup=keyboard)
            return {"ok": True, "action": "edited", "message_id": message_id}
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return {"ok": True, "action": "unchanged", "message_id": message_id}
            append_log("pinned_task_edit_error", {"chat_id": chat_id, "message_id": message_id, "error": str(exc)})
    response = telegram_send(config, chat_id, text, reply_markup=keyboard)
    new_message_id = int((response.get("result") or {}).get("message_id") or 0)
    if not new_message_id:
        return {"ok": False, "error": "Telegram не вернул message_id"}
    try:
        telegram_pin_message(config, chat_id, new_message_id)
        action = "created_pinned"
    except Exception as exc:
        append_log("pinned_task_pin_error", {"chat_id": chat_id, "message_id": new_message_id, "error": str(exc)})
        action = "created_not_pinned"
    set_pinned_task_message_id(chat_id, new_message_id)
    return {"ok": True, "action": action, "message_id": new_message_id}


def unpin_pinned_tasks(config: AgentConfig, chat_id: str | int) -> dict[str, Any]:
    message_id = get_pinned_task_message_id(chat_id)
    if not message_id:
        return {"ok": True, "action": "missing"}
    try:
        telegram_unpin_message(config, chat_id, message_id)
        action = "unpinned"
    except Exception as exc:
        append_log("pinned_task_unpin_error", {"chat_id": chat_id, "message_id": message_id, "error": str(exc)})
        action = "forgotten_after_unpin_error"
    set_pinned_task_message_id(chat_id, None)
    return {"ok": True, "action": action, "message_id": message_id}


def build_pinned_tasks_result_message(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "Не смогла обновить закреп задач: " + escape_html(str(result.get("error") or "unknown error"))
    action = str(result.get("action") or "updated")
    labels = {
        "edited": "Закреп задач обновлен.",
        "unchanged": "Закреп задач уже актуален.",
        "created_pinned": "Создала и закрепила список задач.",
        "created_not_pinned": "Создала список задач, но закрепить не удалось. Проверь права бота в чате.",
        "unpinned": "Закреп задач убран.",
        "missing": "Закрепленных задач для этого чата не было.",
        "forgotten_after_unpin_error": "Не смогла снять закреп через Telegram, но забыла старый message_id.",
    }
    line = labels.get(action, f"Закреп задач: {action}.")
    if result.get("message_id"):
        line += f" message_id={escape_html(str(result['message_id']))}"
    return line


def maybe_refresh_pinned_tasks(config: AgentConfig, chat_id: str | int, allow_side_effects: bool) -> None:
    if not allow_side_effects or not chat_id or not get_pinned_task_message_id(chat_id):
        return
    try:
        refresh_pinned_tasks(config, chat_id)
    except Exception as exc:
        append_log("pinned_task_refresh_error", {"chat_id": chat_id, "error": str(exc)})


def format_task_meta(row: dict[str, Any]) -> str:
    parts = [str(row.get("direction") or "без направления")]
    if row.get("due_date"):
        parts.append(f"до {row['due_date']}")
    if row.get("priority"):
        parts.append(str(row["priority"]))
    if row.get("assignee"):
        parts.append(f"отв. {row['assignee']}")
    return " / ".join(parts)


def is_open_task_status(status: Any) -> bool:
    return str(status or "open") in {"open", "pending", "in_progress", "waiting"}


def is_open_inbox_status(item_type: str, status: Any) -> bool:
    if item_type == "task":
        return is_open_task_status(status)
    if item_type == "question":
        return str(status or "open") in {"open", "pending", "waiting"}
    return True


def parse_days_arg(text: str, default: int = 7, min_days: int = 1, max_days: int = 90) -> int:
    match = re.search(r"(?:^|\s)(\d{1,3})(?:\s|$)", text)
    if not match:
        return default
    return max(min_days, min(max_days, int(match.group(1))))


def due_task_rows(mode: str, days: int = 7, limit: int = 12) -> list[dict[str, Any]]:
    today_date = dt.date.today()
    today = today_date.isoformat()
    horizon = (today_date + dt.timedelta(days=days)).isoformat()
    rows = []
    for row in read_inbox(limit=1_000_000):
        if row.get("type") != "task" or not is_open_task_status(row.get("status")):
            continue
        due_date = str(row.get("due_date") or "")
        if mode == "today" and due_date == today:
            rows.append(row)
        elif mode == "overdue" and due_date and due_date < today:
            rows.append(row)
        elif mode == "soon" and today <= due_date <= horizon:
            rows.append(row)
    rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("due_date") or "9999-12-31"),
            task_priority_rank(row),
            task_status_rank(row),
            str(row.get("ts") or ""),
        ),
    )
    return rows[:limit]


def build_due_tasks_message(mode: str, days: int = 7) -> str:
    rows = due_task_rows(mode, days=days, limit=12)
    if mode == "today":
        title = "Задачи на сегодня"
    elif mode == "overdue":
        title = "Просроченные задачи"
    else:
        title = f"Ближайшие дедлайны на {days} дн."
    if not rows:
        return f"{title}: пусто."
    lines = [f"<b>{escape_html(title)}</b>"]
    for row in rows:
        row_id = str(row.get("id") or "")[:8]
        text = str(row.get("text") or "")
        if len(text) > 120:
            text = text[:117] + "..."
        lines.append(f"- {escape_html(row_id)} / {escape_html(format_task_meta(row))}: {escape_html(text)}")
    return "\n".join(lines)


def waiting_task_rows(limit: int = 5) -> list[dict[str, Any]]:
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if row.get("type") == "task" and str(row.get("status") or "open") == "waiting"
    ]
    return sort_task_rows(rows)[:limit]


def build_waiting_followups_message(limit: int = 10) -> str:
    rows = waiting_task_rows(limit=limit)
    if not rows:
        return "Пингов по ожиданиям нет: задач в статусе waiting сейчас нет."
    lines = ["<b>Кого пинговать по ожиданиям</b>"]
    for row in rows:
        row_id = str(row.get("id") or "")[:8]
        assignee = str(row.get("assignee") or "ответственного").strip()
        reason = str(row.get("waiting_reason") or "нужен апдейт по внешнему шагу").strip()
        text = compact_text(str(row.get("text") or ""), 110)
        due = str(row.get("due_date") or "").strip()
        suffix = f" Срок: {due}." if due else ""
        lines.append(
            f"- <code>{escape_html(row_id)}</code> / {escape_html(assignee)}: "
            f"пинг: {escape_html(reason)}. Задача: {escape_html(text)}.{escape_html(suffix)}"
        )
    lines.append("")
    lines.append("После ответа: /comment <id> <что ответили> или /start_task <id>.")
    return "\n".join(lines)


def build_nudges_message(limit: int = 8) -> str:
    waiting_rows = waiting_task_rows(limit=limit)
    question_rows = open_question_rows(limit=max(0, limit - len(waiting_rows)))
    if not waiting_rows and not question_rows:
        return "Готовых пингов нет: нет задач в ожидании и вопросов без ответа."
    lines = ["<b>Готовые пинги</b>"]
    if waiting_rows:
        lines.append("")
        lines.append("<b>По ожиданиям</b>")
        for row in waiting_rows:
            row_id = str(row.get("id") or "")[:8]
            assignee = str(row.get("assignee") or "").strip()
            name = assignee if assignee and assignee.lower() not in {"unassigned", "без ответственного"} else "коллеги"
            reason = str(row.get("waiting_reason") or "нужен апдейт по внешнему шагу").strip()
            task_text = compact_text(str(row.get("text") or ""), 120)
            due = str(row.get("due_date") or "").strip()
            due_text = f" Срок сейчас стоит {due}." if due else ""
            message = f"{name}, добрый день. Подскажите, пожалуйста, есть ли апдейт: {reason}? Задача: {task_text}.{due_text}"
            lines.append(f"- <code>{escape_html(row_id)}</code>: {escape_html(message)}")
    if question_rows:
        lines.append("")
        lines.append("<b>По вопросам</b>")
        for row in reversed(question_rows):
            row_id = str(row.get("id") or "")[:8]
            question = compact_text(str(row.get("text") or ""), 140)
            direction = str(row.get("direction") or "").strip()
            scope = f" по {direction}" if direction and direction != "без направления" else ""
            message = f"Коллеги, подскажите, пожалуйста{scope}: {question}"
            lines.append(f"- <code>{escape_html(row_id)}</code>: {escape_html(message)}")
    lines.append("")
    lines.append("После ответа: /comment <id> <что ответили> или /answer <id> <ответ>.")
    return "\n".join(lines)


def add_outbox_item(groups: dict[str, list[str]], person: str, text: str) -> None:
    person = person.strip() or "Команда"
    groups.setdefault(person, []).append(text)


def build_outbox_message(days: int = 7, limit_per_person: int = 5) -> str:
    days = max(1, min(30, int(days)))
    groups: dict[str, list[str]] = {}
    seen: set[str] = set()
    for row in waiting_task_rows(limit=30):
        row_id = str(row.get("id") or "")[:8]
        if row_id in seen:
            continue
        seen.add(row_id)
        reason = str(row.get("waiting_reason") or "нужен апдейт по внешнему шагу").strip()
        task_text = compact_text(str(row.get("text") or ""), 120)
        add_outbox_item(
            groups,
            str(row.get("assignee") or ""),
            f"по задаче {row_id} ждем: {reason}. Контекст: {task_text}",
        )
    for row in due_task_rows("overdue", limit=20):
        row_id = str(row.get("id") or "")[:8]
        if row_id in seen:
            continue
        seen.add(row_id)
        due_date = str(row.get("due_date") or "")
        task_text = compact_text(str(row.get("text") or ""), 120)
        add_outbox_item(
            groups,
            str(row.get("assignee") or ""),
            f"по задаче {row_id} срок уже прошел ({due_date}). Нужно понять статус. Контекст: {task_text}",
        )
    for row in stale_task_rows(days=days, limit=20):
        row_id = str(row.get("id") or "")[:8]
        if row_id in seen:
            continue
        seen.add(row_id)
        task_text = compact_text(str(row.get("text") or ""), 120)
        add_outbox_item(
            groups,
            str(row.get("assignee") or ""),
            f"по задаче {row_id} давно не было апдейта. Нужно коротко обновить статус. Контекст: {task_text}",
        )
    for row in open_question_rows(limit=10):
        row_id = str(row.get("id") or "")[:8]
        question = compact_text(str(row.get("text") or ""), 140)
        direction = str(row.get("direction") or "").strip()
        scope = f" по {direction}" if direction and direction != "без направления" else ""
        add_outbox_item(groups, "Команда", f"нужен ответ на вопрос {row_id}{scope}: {question}")
    if not groups:
        return "Outbox пуст: нет ожиданий, просрочки, зависших задач и открытых вопросов."
    lines = ["<b>Outbox: кому написать</b>", f"Окно для зависших задач: {days} дн."]
    for person, items in sorted(groups.items(), key=lambda item: (item[0] == "Команда", item[0].lower())):
        shown = items[:limit_per_person]
        greeting = "Коллеги, добрый день." if person == "Команда" else f"{person}, добрый день."
        body = " ".join(shown)
        if len(items) > limit_per_person:
            body += f" Еще {len(items) - limit_per_person} пункт(ов) оставила в задачах."
        message = f"{greeting} Подскажите, пожалуйста: {body}"
        lines.append(f"- <b>{escape_html(person)}</b>: <code>{escape_html(message)}</code>")
    lines.extend(["", "Дальше: /nudges, /waiting_followups, /owner_brief <имя>."])
    return "\n".join(lines)


def unique_task_rows(groups: list[list[dict[str, Any]]], limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for row in group:
            row_id = str(row.get("id") or "")
            if not row_id or row_id in seen:
                continue
            seen.add(row_id)
            rows.append(row)
            if len(rows) >= limit:
                return rows
    return rows


def append_hot_task_section(lines: list[str], title: str, rows: list[dict[str, Any]], empty: str = "пусто") -> None:
    lines.append("")
    lines.append(f"<b>{escape_html(title)}</b>")
    if not rows:
        lines.append(empty)
        return
    for row in rows:
        lines.append(format_task_list_line(row))


def build_hot_tasks_message(days: int = 7, stale_days: int = 3) -> str:
    overdue_rows = due_task_rows("overdue", limit=5)
    soon_rows = due_task_rows("soon", days=days, limit=5)
    waiting_rows = waiting_task_rows(limit=5)
    stale_rows = stale_task_rows(days=stale_days, limit=5)
    lines = [
        "<b>Что горит по задачам</b>",
        f"Просрочено: {len(overdue_rows)}",
        f"Срок в ближайшие {days} дн.: {len(soon_rows)}",
        f"Ждут внешнего шага: {len(waiting_rows)}",
        f"Без апдейтов {stale_days}+ дн.: {len(stale_rows)}",
    ]
    append_hot_task_section(lines, "Просрочка", overdue_rows)
    append_hot_task_section(lines, f"Ближайшие сроки на {days} дн.", soon_rows)
    append_hot_task_section(lines, "Ожидания", waiting_rows)
    append_hot_task_section(lines, f"Зависшие {stale_days}+ дн.", stale_rows)
    return "\n".join(lines)


def open_question_rows(limit: int = 5) -> list[dict[str, Any]]:
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if row.get("type") == "question" and is_open_inbox_status("question", row.get("status"))
    ]
    return rows[-limit:]


def build_focus_message(days: int = 7) -> str:
    days = max(1, min(90, int(days)))
    overdue_rows = due_task_rows("overdue", limit=3)
    soon_rows = due_task_rows("soon", days=days, limit=3)
    waiting_rows = waiting_task_rows(limit=3)
    question_rows = open_question_rows(limit=3)
    risk_rows = rows_by_type_for_period("risk", days=days, limit=3)
    lines = [
        "<b>Фокус на сейчас</b>",
        f"Просрочено: {len(overdue_rows)}",
        f"Срок в ближайшие {days} дн.: {len(soon_rows)}",
        f"Ожидания: {len(waiting_rows)}",
        f"Вопросы без ответа: {len(question_rows)}",
        f"Свежие риски за {days} дн.: {len(risk_rows)}",
    ]
    append_hot_task_section(lines, "Просрочка", overdue_rows)
    append_hot_task_section(lines, f"Ближайшие сроки на {days} дн.", soon_rows)
    append_hot_task_section(lines, "Ожидания", waiting_rows)
    append_handoff_section(lines, "Открытые вопросы", list(reversed(question_rows)), format_handoff_inbox_line)
    append_handoff_section(lines, "Свежие риски", list(reversed(risk_rows)), format_handoff_inbox_line)
    lines.extend(["", "Дальше: /waiting_followups, /open_questions, /hot_tasks, /handoff."])
    return "\n".join(lines)


def build_next_action_message(days: int = 7) -> str:
    days = max(1, min(90, int(days)))
    overdue_rows = due_task_rows("overdue", limit=1)
    waiting_rows = waiting_task_rows(limit=1)
    question_rows = open_question_rows(limit=1)
    soon_rows = due_task_rows("soon", days=days, limit=1)
    risk_rows = rows_by_type_for_period("risk", days=days, limit=1)
    lines = ["<b>Следующее действие</b>"]
    if overdue_rows:
        row = overdue_rows[0]
        row_id = str(row.get("id") or "")[:8]
        lines.extend([
            "1. Разобрать просроченную задачу.",
            format_task_list_line(row),
            f"Команды: /done {escape_html(row_id)}, /comment {escape_html(row_id)} <апдейт>, /due {escape_html(row_id)} <дата>.",
        ])
    elif waiting_rows:
        row = waiting_rows[0]
        row_id = str(row.get("id") or "")[:8]
        reason = str(row.get("waiting_reason") or "нужен апдейт по внешнему шагу").strip()
        lines.extend([
            "1. Пингануть ожидание.",
            format_task_list_line(row),
            f"Что спросить: {escape_html(reason)}",
            f"После ответа: /comment {escape_html(row_id)} <что ответили> или /start_task {escape_html(row_id)}.",
        ])
    elif question_rows:
        row = question_rows[-1]
        row_id = str(row.get("id") or "")[:8]
        lines.extend([
            "1. Закрыть открытый вопрос.",
            format_handoff_inbox_line(row),
            f"Команда: /answer {escape_html(row_id)} <ответ>.",
        ])
    elif soon_rows:
        row = soon_rows[0]
        row_id = str(row.get("id") or "")[:8]
        lines.extend([
            f"1. Проверить задачу со сроком в ближайшие {days} дн.",
            format_task_list_line(row),
            f"Команды: /comment {escape_html(row_id)} <апдейт>, /done {escape_html(row_id)}.",
        ])
    elif risk_rows:
        row = risk_rows[-1]
        lines.extend([
            "1. Разобрать свежий риск.",
            format_handoff_inbox_line(row),
            "Если нужен владелец: /add задача <кто и что делает по риску>.",
        ])
    else:
        lines.extend([
            "Срочного действия по журналу не вижу.",
            "Можно запросить /focus или записать новую задачу через /add.",
        ])
    lines.extend(["", f"Полная картина: /focus {days}."])
    return "\n".join(lines)


def recent_task_activity_rows(days: int = 1, limit: int = 5) -> list[dict[str, Any]]:
    days = max(1, min(30, int(days)))
    today_date = dt.date.today()
    start_date = today_date - dt.timedelta(days=days - 1)
    rows: list[dict[str, Any]] = []
    for row in read_inbox(limit=1_000_000):
        if row.get("type") == "task_update":
            row_date = inbox_row_date(row)
            if row_date and start_date <= row_date <= today_date:
                rows.append(row)
            continue
        if row.get("type") == "task" and str(row.get("status") or "") == "done":
            closed_at = str(row.get("closed_at") or "")[:10]
            try:
                closed_date = dt.date.fromisoformat(closed_at) if closed_at else None
            except ValueError:
                closed_date = None
            if closed_date and start_date <= closed_date <= today_date:
                rows.append(row)
    return rows[-limit:]


def format_standup_activity_line(row: dict[str, Any]) -> str:
    row_id = str(row.get("id") or "")[:8]
    if row.get("parent_id"):
        row_id = str(row.get("parent_id") or "")[:8]
    item_type = str(row.get("type") or "note")
    direction = str(row.get("direction") or "без направления")
    text = compact_text(str(row.get("text") or ""), 120)
    return f"- {escape_html(row_id)} / {escape_html(item_type)} / {escape_html(direction)}: {escape_html(text)}"


def build_standup_message(days: int = 1) -> str:
    days = max(1, min(30, int(days)))
    activity_rows = recent_task_activity_rows(days=days, limit=5)
    today_rows = due_task_rows("today", limit=5)
    waiting_rows = waiting_task_rows(limit=5)
    question_rows = open_question_rows(limit=5)
    risk_rows = rows_by_type_for_period("risk", days=days, limit=5)
    title = "Standup за сегодня" if days == 1 else f"Standup за {days} дн."
    lines = [
        f"<b>{escape_html(title)}</b>",
        f"Сделано/апдейты: {len(activity_rows)}",
        f"Задачи на сегодня: {len(today_rows)}",
        f"Ожидания: {len(waiting_rows)}",
        f"Вопросы без ответа: {len(question_rows)}",
        f"Риски: {len(risk_rows)}",
    ]
    lines.append("")
    lines.append("<b>Сделано / апдейты</b>")
    if activity_rows:
        for row in reversed(activity_rows):
            lines.append(format_standup_activity_line(row))
    else:
        lines.append("пусто")
    append_hot_task_section(lines, "На сегодня", today_rows)
    append_hot_task_section(lines, "Ожидания", waiting_rows)
    append_handoff_section(lines, "Вопросы без ответа", list(reversed(question_rows)), format_handoff_inbox_line)
    append_handoff_section(lines, "Риски", list(reversed(risk_rows)), format_handoff_inbox_line)
    lines.extend(["", "Дальше: /next_action, /nudges, /focus."])
    return "\n".join(lines)


def find_task_by_prefix(rows: list[dict[str, Any]], id_prefix: str) -> dict[str, Any]:
    id_prefix = id_prefix.strip()
    if not id_prefix:
        return {"ok": False, "error": "Нужен id задачи"}
    matches = [
        row for row in rows
        if str(row.get("id") or "").startswith(id_prefix) and row.get("type") == "task"
    ]
    if not matches:
        return {"ok": False, "error": f"Не нашла задачу по id {id_prefix}"}
    if len(matches) > 1:
        return {"ok": False, "error": f"По id {id_prefix} найдено несколько задач. Возьми больше символов id."}
    return {"ok": True, "row": matches[0]}


def find_question_by_prefix(rows: list[dict[str, Any]], id_prefix: str) -> dict[str, Any]:
    id_prefix = id_prefix.strip()
    if not id_prefix:
        return {"ok": False, "error": "Нужен id вопроса"}
    matches = [
        row for row in rows
        if str(row.get("id") or "").startswith(id_prefix) and row.get("type") == "question"
    ]
    if not matches:
        return {"ok": False, "error": f"Не нашла вопрос по id {id_prefix}"}
    if len(matches) > 1:
        return {"ok": False, "error": f"По id {id_prefix} найдено несколько вопросов. Возьми больше символов id."}
    return {"ok": True, "row": matches[0]}


def extract_task_detail_id(text: str) -> str:
    return extract_task_action_id(text, ["/task", "task", "покажи задачу", "карточка задачи"])


def extract_question_answer_payload(text: str) -> tuple[str, str]:
    cleaned = re.sub(
        r"^\s*(?:/answer_question|/answer|ответ\s+на\s+вопрос)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned:
        return "", ""
    parts = cleaned.split(maxsplit=1)
    question_id = parts[0].strip(" :#")
    answer = parts[1].strip(" :-") if len(parts) > 1 else ""
    return question_id, answer


def extract_task_comment_payload(text: str) -> tuple[str, str]:
    cleaned = re.sub(
        r"^\s*(?:/comment|/note_task|комментарий\s+к\s+задаче|комментарий\s+по\s+задаче|апдейт\s+по\s+задаче|по\s+задаче)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned:
        return "", ""
    parts = cleaned.split(maxsplit=1)
    task_id = parts[0].strip(" :#")
    comment = parts[1].strip(" :-") if len(parts) > 1 else ""
    return task_id, comment


def extract_waiting_payload(text: str) -> tuple[str, str]:
    cleaned = re.sub(
        r"^\s*(?:/waiting|/wait|жду\s+по\s+задаче|ждем\s+по\s+задаче|ожидание\s+по\s+задаче)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned:
        return "", ""
    parts = cleaned.split(maxsplit=1)
    task_id = parts[0].strip(" :#")
    reason = parts[1].strip(" :-") if len(parts) > 1 else ""
    return task_id, reason


def is_waiting_segment(normalized: str) -> bool:
    return normalized.startswith((
        "жду ",
        "ждем ",
        "ждём ",
        "ожидаю ",
        "ожидаем ",
        "ожидание ",
        "waiting ",
        "wait ",
    ))


def strip_waiting_segment_prefix(text: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:жду|ждем|ждём|ожидаю|ожидаем|ожидание|waiting|wait)\b[:\s-]*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    return cleaned or text.strip()


def answer_question(
    config: AgentConfig,
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
) -> dict[str, Any]:
    question_id, answer = extract_question_answer_payload(text)
    if not question_id:
        return {"digest_id": "QUESTION-ANSWER", "answer": "Нужен id вопроса. Пример: /answer abc123 доступ даст Мухассар."}
    if not answer:
        return {"digest_id": "QUESTION-ANSWER", "answer": "Нужен текст ответа после id вопроса."}
    rows = read_inbox(limit=1_000_000)
    found = find_question_by_prefix(rows, question_id)
    if not found.get("ok"):
        return {"digest_id": "QUESTION-ANSWER", "answer": escape_html(str(found.get("error") or "Не нашла вопрос"))}
    question = found["row"]
    now = dt.datetime.now().isoformat(timespec="seconds")
    updated_question: dict[str, Any] | None = None
    for row in rows:
        if row.get("id") == question.get("id"):
            row["status"] = "answered"
            row["updated_at"] = now
            row["closed_at"] = now
            row["closed_by"] = str(chat_id or "")
            if chat_id:
                row["updated_by"] = str(chat_id)
            updated_question = row
            break
    if not updated_question:
        return {"digest_id": "QUESTION-ANSWER", "answer": "Не смогла обновить вопрос: строка исчезла из журнала."}
    answer_row = {
        "type": "question_answer",
        "status": "recorded",
        "parent_id": question.get("id") or "",
        "direction": question.get("direction") or detect_direction(normalize_text(answer)),
        "date": detect_date(normalize_text(answer)),
        "ts": now,
        "text": answer,
        "source_kind": meta.get("kind"),
        "chat_id": chat_id,
        "message_id": message_id,
        "media_file_id": meta.get("media_file_id") or "",
        "media_local_path": meta.get("media_local_path") or "",
        "comment_by": str(chat_id or ""),
    }
    answer_row["id"] = build_inbox_id(answer_row)
    rows.append(answer_row)
    write_inbox(rows)
    sync_result = sync_inbox_to_sheet(config, [updated_question, answer_row]) if config.sheets_sync_on_save else None
    lines = [
        "<b>Ответ на вопрос записан</b>",
        f"вопрос: <code>{escape_html(str(question.get('id') or '')[:8])}</code>",
        f"ответ: {escape_html(compact_text(answer, 180))}",
    ]
    if sync_result:
        lines.extend(["", build_sheet_sync_message(sync_result)])
    return {
        "digest_id": "QUESTION-ANSWER",
        "answer": "\n".join(lines),
        "question": updated_question,
        "inbox_item": answer_row,
    }


def add_task_comment(
    config: AgentConfig,
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
) -> dict[str, Any]:
    task_id, comment = extract_task_comment_payload(text)
    if not task_id:
        return {"digest_id": "TASK-COMMENT", "answer": "Нужен id задачи. Пример: /comment abc123 жду выгрузку."}
    if not comment:
        return {"digest_id": "TASK-COMMENT", "answer": "Нужен текст апдейта после id задачи."}
    found = find_task_by_prefix(read_inbox(limit=1_000_000), task_id)
    if not found.get("ok"):
        return {"digest_id": "TASK-COMMENT", "answer": escape_html(str(found.get("error") or "Не нашла задачу"))}
    task = found["row"]
    item = {
        "type": "task_update",
        "status": "open",
        "parent_id": task.get("id") or "",
        "direction": task.get("direction") or "",
        "date": detect_date(normalize_text(comment)),
        "due_date": "",
        "priority": "",
        "assignee": "",
        "amount": None,
        "qty": None,
        "unit": "",
        "comment_by": str(chat_id or ""),
    }
    result = save_inbox_item(config, item, comment, meta, chat_id, message_id)
    update = result.get("inbox_item") or {}
    lines = [
        "<b>Апдейт к задаче записан</b>",
        f"задача: {escape_html(str(task.get('id') or '')[:8])}",
        f"апдейт: {escape_html(str(update.get('id') or '')[:8])}",
        escape_html(comment),
    ]
    sync_text = ""
    if "Sheet:" in result.get("answer", ""):
        sync_text = result["answer"].split("\n\n", 1)[-1]
    if sync_text:
        lines.extend(["", sync_text])
    return {"digest_id": "TASK-COMMENT", "answer": "\n".join(lines), "inbox_item": update}


def build_task_detail_message(id_prefix: str) -> str:
    found = find_task_by_prefix(read_inbox(limit=1_000_000), id_prefix)
    if not found.get("ok"):
        return escape_html(str(found.get("error") or "Не нашла задачу"))
    row = found["row"]
    row_id = str(row.get("id") or "")
    lines = [
        "<b>Карточка задачи</b>",
        f"id: {escape_html(row_id)}",
        f"статус: {escape_html(str(row.get('status') or 'open'))}",
        f"направление: {escape_html(str(row.get('direction') or 'без направления'))}",
    ]
    optional_fields = [
        ("срок", row.get("due_date")),
        ("приоритет", row.get("priority")),
        ("ответственный", row.get("assignee")),
        ("ожидание", row.get("waiting_reason")),
        ("создана", row.get("ts")),
        ("обновлена", row.get("updated_at")),
        ("закрыта", row.get("closed_at")),
        ("источник", row.get("source_kind")),
        ("медиа", row.get("media_local_path")),
    ]
    for label, value in optional_fields:
        if value:
            lines.append(f"{label}: {escape_html(str(value))}")
    lines.append("")
    lines.append(escape_html(str(row.get("text") or "")))
    updates = task_updates_for(row_id, limit=5)
    if updates:
        lines.extend(["", "<b>Апдейты</b>"])
        for update in updates:
            update_id = str(update.get("id") or "")[:8]
            stamp = str(update.get("ts") or "")
            text = compact_text(str(update.get("text") or ""), 130)
            lines.append(f"- {escape_html(update_id)} / {escape_html(stamp)}: {escape_html(text)}")
    return "\n".join(lines)


def task_updates_for(task_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if row.get("type") == "task_update" and str(row.get("parent_id") or "") == task_id and row.get("status") != "undone"
    ]
    return rows[-limit:]


def mark_task_done(id_prefix: str, closed_by: str = "") -> dict[str, Any]:
    id_prefix = id_prefix.strip()
    rows = read_inbox(limit=1_000_000)
    found = find_task_by_prefix(rows, id_prefix)
    if not found.get("ok"):
        return found
    target = found["row"]
    if not is_open_task_status(target.get("status")):
        return {"ok": False, "error": f"Задача {id_prefix} уже закрыта"}
    target_id = target.get("id")
    closed_at = dt.datetime.now().isoformat(timespec="seconds")
    updated_row: dict[str, Any] | None = None
    for row in rows:
        if row.get("id") == target_id:
            row["status"] = "done"
            row["closed_at"] = closed_at
            row["closed_by"] = closed_by
            updated_row = row
            break
    write_inbox(rows)
    return {"ok": True, "id": target_id, "closed_at": closed_at, "text": target.get("text") or "", "status": "done", "row": updated_row}


def set_task_status_by_prefix(
    id_prefix: str,
    status: str,
    *,
    actor: str = "",
    until: str = "",
    reason: str = "",
) -> dict[str, Any]:
    rows = read_inbox(limit=1_000_000)
    found = find_task_by_prefix(rows, id_prefix)
    if not found.get("ok"):
        return found
    target = found["row"]
    target_id = target.get("id")
    now = dt.datetime.now().isoformat(timespec="seconds")
    updated_row: dict[str, Any] | None = None
    for row in rows:
        if row.get("id") == target_id:
            row["status"] = status
            row["updated_at"] = now
            if actor:
                row["updated_by"] = actor
            if status != "waiting":
                row.pop("waiting_at", None)
                row.pop("waiting_by", None)
                row.pop("waiting_reason", None)
            if status == "snoozed":
                row["snoozed_at"] = now
                row["snoozed_by"] = actor
                if until:
                    row["due_date"] = until
                row["snoozed_count"] = int(row.get("snoozed_count") or 0) + 1
            elif status == "waiting":
                row["waiting_at"] = now
                row["waiting_by"] = actor
                row["waiting_reason"] = reason
            if status == "dropped":
                row["dropped_at"] = now
                row["dropped_by"] = actor
            elif status == "open":
                row["reopened_at"] = now
                row["reopened_by"] = actor
                row.pop("closed_at", None)
                row.pop("closed_by", None)
            updated_row = row
            break
    write_inbox(rows)
    return {"ok": True, "id": target_id, "text": target.get("text") or "", "status": status, "until": until, "reason": reason, "row": updated_row}


def update_task_by_prefix(id_prefix: str, fields: dict[str, str], actor: str = "") -> dict[str, Any]:
    rows = read_inbox(limit=1_000_000)
    found = find_task_by_prefix(rows, id_prefix)
    if not found.get("ok"):
        return found
    if not fields:
        return {"ok": False, "error": "Нечего обновлять. Пример: /edit <id> !must до завтра для Мухассар новый текст"}
    target = found["row"]
    target_id = target.get("id")
    now = dt.datetime.now().isoformat(timespec="seconds")
    updated_row: dict[str, Any] | None = None
    allowed_fields = {"text", "direction", "due_date", "priority", "assignee"}
    applied: dict[str, str] = {}
    for row in rows:
        if row.get("id") != target_id:
            continue
        for key, value in fields.items():
            if key not in allowed_fields:
                continue
            clean_value = str(value or "").strip()
            if clean_value:
                row[key] = clean_value
                applied[key] = clean_value
        row["updated_at"] = now
        if actor:
            row["updated_by"] = actor
        updated_row = row
        break
    if not updated_row:
        return {"ok": False, "error": f"Не нашла задачу по id {id_prefix}"}
    write_inbox(rows)
    return {"ok": True, "id": target_id, "fields": applied, "row": updated_row, "text": updated_row.get("text") or ""}


def build_task_edit_message(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "Не смогла обновить задачу: " + escape_html(str(result.get("error") or "unknown error"))
    fields = result.get("fields") or {}
    if not fields:
        return "Задача найдена, но поля не изменились."
    labels = {
        "text": "текст",
        "direction": "направление",
        "due_date": "срок",
        "priority": "приоритет",
        "assignee": "ответственный",
    }
    changed = ", ".join(f"{labels.get(key, key)}: {value}" for key, value in fields.items())
    return f"Обновила задачу {escape_html(str(result.get('id') or '')[:8])}: {escape_html(changed)}"


def extract_done_id(text: str) -> str:
    normalized = normalize_text(text)
    for prefix in ["/done", "/close", "done", "close", "готово", "закрыть", "закрой"]:
        if normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return ""


def extract_add_text(text: str) -> str:
    return re.sub(
        r"^\s*(?:/add|add|добавить\s+задачу|добавь\s+задачу)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()


def extract_edit_payload(text: str) -> tuple[str, str]:
    payload = re.sub(
        r"^\s*(?:/edit|/update_task|edit|редактировать\s+задачу|обновить\s+задачу)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    if not payload:
        return "", ""
    parts = payload.split(maxsplit=1)
    return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""


def parse_task_edit_fields(rest: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    normalized = normalize_text(rest)
    priority = detect_priority(normalized)
    if priority:
        fields["priority"] = priority
    due_date = detect_due_date(normalized)
    if due_date:
        fields["due_date"] = due_date
    assignee = detect_assignee(rest)
    if assignee:
        fields["assignee"] = assignee
    direction = detect_direction(normalized)
    if direction:
        fields["direction"] = direction

    key_patterns = {
        "priority": r"\b(?:priority|приоритет)\s*=\s*(high|medium|low|высокий|средний|низкий)\b",
        "due_date": r"\b(?:due|deadline|срок)\s*=\s*([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}[./-][0-9]{1,2}(?:[./-][0-9]{2,4})?)",
        "assignee": r"\b(?:assignee|ответственный|ответственная)\s*=\s*([A-Za-zА-Яа-яЁё._-]{2,40})",
        "direction": r"\b(?:direction|направление)\s*=\s*([A-Za-zА-Яа-яЁё0-9._ -]{2,40})",
    }
    for key, pattern in key_patterns.items():
        match = re.search(pattern, rest, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip()
        if key == "priority":
            fields[key] = normalize_priority_value(value)
        elif key == "due_date":
            parsed = value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else ""
            parsed = parsed or detect_due_date(f"до {normalize_text(value)}") or detect_date(normalize_text(value))
            fields[key] = parsed or value
        elif key == "direction":
            fields[key] = detect_direction(normalize_text(value)) or value
        else:
            fields[key] = value

    text_match = re.search(
        r"(?:^|\s)(?:text|текст)\s*[:=]\s*(.+?)(?=\s+(?:direction|направление|due|deadline|срок|assignee|ответственный|ответственная|priority|приоритет)\s*=|$)",
        rest,
        flags=re.IGNORECASE,
    )
    if text_match:
        fields["text"] = strip_priority_flags(text_match.group(1).strip())
    else:
        text_candidate = clean_edit_text_candidate(rest)
        if text_candidate:
            fields["text"] = text_candidate
    return {key: value for key, value in fields.items() if str(value or "").strip()}


def normalize_priority_value(value: str) -> str:
    normalized = normalize_text(value)
    if normalized in {"high", "высокий", "высокая", "высоким", "срочно", "срочная", "срочной", "срочным", "must"}:
        return "high"
    if normalized in {"medium", "средний", "средняя", "средним", "важно", "важная", "важной", "важным", "want"}:
        return "medium"
    if normalized in {"low", "низкий", "низкая", "низким", "когда-нибудь", "когда нибудь", "wish"}:
        return "low"
    return ""


def clean_edit_text_candidate(rest: str) -> str:
    candidate = strip_priority_flags(rest)
    candidate = re.sub(r"\b(?:priority|приоритет|due|deadline|срок|assignee|ответственный|ответственная|direction|направление)\s*=\s*\S+(?:\s+\S+)?", " ", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\b(?:до|к|на)\s+(?:сегодня|завтра|послезавтра|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)", " ", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\b(?:для|ответственный|ответственная|на)\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё._-]{1,40}", " ", candidate)
    normalized_candidate = normalize_text(candidate)
    if normalized_candidate in set(DIRECTIONS.keys()):
        return ""
    direction = detect_direction(normalized_candidate)
    if direction and normalized_candidate.replace("-", "") in {key.replace("-", "") for key in DIRECTIONS.keys()}:
        return ""
    return " ".join(candidate.split())


def detect_priority_flag(text: str) -> str:
    normalized = normalize_text(text)
    if "!must" in normalized:
        return "high"
    if "!want" in normalized:
        return "medium"
    if "!wish" in normalized:
        return "low"
    return ""


def strip_priority_flags(text: str) -> str:
    return " ".join(re.sub(r"!(?:must|want|wish)\b", "", text, flags=re.IGNORECASE).split())


def strip_decision_prefix(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^/(?:decision|decide)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^(?:decision|решение|решили|договорились|зафиксировали)\b[:\s-]*(?:что\s+)?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def strip_rule_prefix(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^/(?:rule|remember_rule)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^(?:rule|правило|запомни\s+правило)\b[:\s-]*(?:что\s+)?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def strip_risk_prefix(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^/(?:risk|risks|blocker|blockers)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^(?:risk|risks|риск|риски|blocker|blockers|блокер|блокеры)\b[:\s-]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def strip_question_prefix(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^/(?:question|questions)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"^(?:question|questions|вопрос|вопросы)\b[:\s-]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def add_task_from_command(
    config: AgentConfig,
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
) -> dict[str, Any]:
    task_text = extract_add_text(text)
    if not task_text:
        return {"digest_id": "TASK-ADD", "answer": "Нужен текст задачи после /add."}
    priority_flag = detect_priority_flag(task_text)
    task_text = strip_priority_flags(task_text)
    item = classify_inbox_text(f"задача {task_text}", meta) or {"type": "task"}
    item["type"] = "task"
    if priority_flag:
        item["priority"] = priority_flag
    return save_inbox_item(config, item, task_text, meta, chat_id, message_id)


def strip_meeting_prefix(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^/(?:meeting|meet)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:итоги\s+встречи|разбор\s+встречи|встреча)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def strip_triage_prefix(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^/(?:triage|parse_voice)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^/разбор\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:разбери|разложи)\b[:\s-]*(?:это\s+)?", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def strip_voice_digest_prefix(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^/(?:voice_digest|voice_recap)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:голосовой\s+разбор|разбор\s+голосового|сводка\s+голоса)\b[:\s-]*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def split_meeting_segments(text: str) -> list[str]:
    pieces: list[str] = []
    for line in text.replace("\r", "\n").split("\n"):
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if not line:
            continue
        subparts = re.split(r"\s*(?:;|•)\s*", line)
        for part in subparts:
            part = " ".join(part.strip(" -–—").split())
            if part:
                pieces.append(part)
    if len(pieces) <= 1 and len(text) > 180:
        pieces = [" ".join(part.strip().split()) for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    return pieces


def triage_segments(text: str, meta: dict[str, Any], limit: int = 20) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for segment in split_meeting_segments(text):
        item = classify_triage_segment(segment, meta)
        if not item:
            continue
        item_type = str(item.get("type") or "")
        if item_type not in {"task", "question", "risk", "decision", "rule", "defect", "note", "reminder"}:
            continue
        key = f"{item_type}:{normalize_text(segment)}"
        if key in seen:
            continue
        seen.add(key)
        rows.append((segment, item))
        if len(rows) >= limit:
            break
    return rows


def classify_triage_segment(segment: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    normalized = normalize_text(segment)
    if normalized.startswith(("/remind", "напомни ", "напомнить ")):
        parsed = parse_reminder_request(segment)
        if parsed.get("ok"):
            return {
                "type": "reminder",
                "fire_at": parsed.get("fire_at") or "",
                "reminder_text": parsed.get("text") or "",
                "recurrence": parsed.get("recurrence") or "",
            }
    return classify_inbox_text(segment, meta)


def save_triage_item(
    config: AgentConfig,
    segment: str,
    item: dict[str, Any],
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
) -> dict[str, Any]:
    if item.get("type") == "reminder":
        result = add_reminder(
            segment,
            chat_id=chat_id,
            extra={
                "source_kind": meta.get("kind") or "",
                "message_id": message_id or "",
                "parent_id": item.get("parent_id") or "",
                "triage_order": item.get("triage_order") or "",
            },
        )
        if not result.get("ok"):
            return {"type": "reminder", "status": "error", "text": segment, "error": result.get("error") or ""}
        return {"type": "reminder", **result}
    return save_inbox_item(config, item, segment, meta, chat_id, message_id).get("inbox_item") or {}


def build_triage_preview_message(text: str, meta: dict[str, Any] | None = None) -> str:
    triage_text = strip_triage_prefix(text)
    if not triage_text:
        return "Нужен текст после /triage. Можно надиктовать: задача..., вопрос..., риск..., решение..."
    rows = triage_segments(triage_text, meta or {"kind": "text"})
    if not rows:
        return "Не нашла пунктов для записи. Добавь явные маркеры: задача, вопрос, риск, решение, брак."
    counts: dict[str, int] = {}
    for _, item in rows:
        item_type = str(item.get("type") or "note")
        counts[item_type] = counts.get(item_type, 0) + 1
    counts_text = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
    lines = [
        "<b>Разбор пачки</b>",
        f"распознано: {len(rows)}",
        f"по типам: {escape_html(counts_text)}",
        "",
        "<b>Пункты</b>",
    ]
    for segment, item in rows[:12]:
        item_type = str(item.get("type") or "note")
        direction = str(item.get("direction") or "без направления")
        lines.append(f"- {escape_html(item_type)} / {escape_html(direction)}: {escape_html(compact_text(segment, 120))}")
    return "\n".join(lines)


def save_triage_batch(
    config: AgentConfig,
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
) -> dict[str, Any]:
    triage_text = strip_triage_prefix(text)
    if not triage_text:
        return {"digest_id": "TRIAGE", "answer": "Нужен текст после /triage. Можно надиктовать: задача..., вопрос..., риск..., решение..."}
    rows = triage_segments(triage_text, meta)
    if not rows:
        return {"digest_id": "TRIAGE", "answer": "Не нашла пунктов для записи. Добавь явные маркеры: задача, вопрос, риск, решение, брак."}
    created: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for index, (segment, item) in enumerate(rows, start=1):
        item["triage_order"] = index
        row = save_triage_item(config, segment, item, meta, chat_id, message_id)
        created.append(row)
        item_type = str(row.get("type") or item.get("type") or "note")
        counts[item_type] = counts.get(item_type, 0) + 1
    counts_text = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
    lines = [
        "<b>Пачка разобрана</b>",
        f"записей создано: {len(created)}",
        f"по типам: {escape_html(counts_text)}",
    ]
    for row in created[:10]:
        row_id = str(row.get("id") or "")[:8]
        item_type = str(row.get("type") or "note")
        direction = str(row.get("direction") or "без направления")
        text_value = str(row.get("text") or row.get("reminder_text") or "")
        lines.append(f"- {escape_html(row_id)} / {escape_html(item_type)} / {escape_html(direction)}: {escape_html(compact_text(text_value, 110))}")
    return {"digest_id": "TRIAGE", "answer": "\n".join(lines), "items": created, "counts": counts}


def build_voice_digest_preview_message(text: str, meta: dict[str, Any] | None = None) -> str:
    digest_text = strip_voice_digest_prefix(text)
    if not digest_text:
        return "Нужен текст после /voice_digest. Можно переслать расшифровку или надиктовать: голосовой разбор ..."
    rows = triage_segments(digest_text, meta or {"kind": "voice"})
    direction = detect_direction(normalize_text(digest_text)) or "без направления"
    lines = [
        "<b>Предпросмотр голосового разбора</b>",
        f"направление: {escape_html(direction)}",
        f"символов в исходнике: {len(digest_text)}",
        f"распознанных пунктов: {len(rows)}",
        "",
        "<b>Коротко</b>",
    ]
    for segment in split_meeting_segments(digest_text)[:3]:
        lines.append(f"- {escape_html(compact_text(segment, 140))}")
    if rows:
        counts: dict[str, int] = {}
        for _, item in rows:
            item_type = str(item.get("type") or "note")
            counts[item_type] = counts.get(item_type, 0) + 1
        counts_text = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        lines.extend(["", f"по типам: {escape_html(counts_text)}"])
    else:
        lines.extend(["", "Пока вижу только исходный текст. Для дочерних пунктов нужны явные маркеры: задача, риск, вопрос, решение, брак."])
    return "\n".join(lines)


def save_voice_digest(
    config: AgentConfig,
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
) -> dict[str, Any]:
    digest_text = strip_voice_digest_prefix(text)
    if not digest_text:
        return {"digest_id": "VOICE-DIGEST", "answer": "Нужен текст после /voice_digest. Можно надиктовать: голосовой разбор ..."}
    normalized = normalize_text(digest_text)
    parent_item = {
        "type": "note",
        "status": "",
        "direction": detect_direction(normalized),
        "date": detect_date(normalized),
        "priority": "",
        "due_date": "",
        "assignee": "",
        "amount": detect_amount(normalized),
        "qty": detect_qty(normalized),
        "unit": detect_unit(normalized),
    }
    parent_result = save_inbox_item(config, parent_item, digest_text, meta, chat_id, message_id)
    parent = parent_result.get("inbox_item") or {}
    parent_id = str(parent.get("id") or "")
    created: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for index, (segment, item) in enumerate(triage_segments(digest_text, meta), start=1):
        item["parent_id"] = parent_id
        item["triage_order"] = index
        if parent_item.get("direction") and not item.get("direction"):
            item["direction"] = parent_item["direction"]
        row = save_triage_item(config, segment, item, meta, chat_id, message_id)
        created.append(row)
        item_type = str(row.get("type") or item.get("type") or "note")
        counts[item_type] = counts.get(item_type, 0) + 1
    counts_text = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items())) or "нет дочерних пунктов"
    lines = [
        "<b>Голосовой разбор сохранен</b>",
        f"id: {escape_html(parent_id[:8])}",
        f"дочерних пунктов: {len(created)}",
        f"по типам: {escape_html(counts_text)}",
    ]
    if created:
        lines.extend(["", "<b>Пункты</b>"])
        for row in created[:10]:
            row_id = str(row.get("id") or "")[:8]
            item_type = str(row.get("type") or "note")
            direction = str(row.get("direction") or "без направления")
            text_value = str(row.get("text") or row.get("reminder_text") or "")
            lines.append(f"- {escape_html(row_id)} / {escape_html(item_type)} / {escape_html(direction)}: {escape_html(compact_text(text_value, 110))}")
    else:
        lines.extend(["", "Исходник сохранен как заметка. Для автоматического разложения нужны явные маркеры: задача, риск, вопрос, решение, брак."])
    return {
        "digest_id": "VOICE-DIGEST",
        "answer": "\n".join(lines),
        "parent": parent,
        "items": created,
        "counts": counts,
    }


def should_auto_voice_digest(text: str, meta: dict[str, Any]) -> bool:
    if meta.get("kind") != "voice":
        return False
    normalized = normalize_text(text)
    if not normalized or normalized.startswith("/"):
        return False
    if normalized.startswith((
        "итоги встречи",
        "разбор встречи",
        "встреча ",
        "разбери ",
        "разложи ",
        "голосовой разбор",
        "разбор голосового",
        "сводка голоса",
        "напомни ",
        "напомнить ",
    )):
        return False
    segments = split_meeting_segments(text)
    rows = triage_segments(text, meta, limit=20)
    typed_rows = [item for _, item in rows if str(item.get("type") or "") in {"task", "question", "risk", "decision", "defect", "reminder"}]
    if len(typed_rows) < 2:
        return False
    if len(segments) >= 2:
        return True
    return len(text) >= 220


def is_meeting_task_segment(segment: str) -> bool:
    normalized = normalize_text(segment)
    if not normalized:
        return False
    task_verbs = [
        "нужно",
        "надо",
        "сделать",
        "проверить",
        "проверь",
        "добавить",
        "добавь",
        "поправить",
        "поправь",
        "собрать",
        "сверить",
        "выгрузить",
    ]
    if normalized.startswith(tuple(f"{verb} " for verb in task_verbs)):
        return True
    if normalized.startswith(("решили ", "договорились ")):
        return any(re.search(rf"\b{re.escape(verb)}\b", normalized) for verb in task_verbs)
    if any(marker in normalized for marker in [" до завтра", " до сегодня"]):
        return True
    return any(f" {verb} " in f" {normalized} " for verb in task_verbs)


def is_meeting_decision_segment(segment: str) -> bool:
    normalized = normalize_text(segment)
    if not normalized:
        return False
    if is_meeting_task_segment(segment):
        return False
    return normalized.startswith((
        "решили ",
        "решение ",
        "решение:",
        "договорились ",
        "зафиксировали ",
        "оставляем ",
        "не меняем ",
        "не трогаем ",
    ))


def extract_meeting_tasks(text: str, limit: int = 12) -> list[str]:
    tasks: list[str] = []
    seen: set[str] = set()
    for segment in split_meeting_segments(text):
        if not is_meeting_task_segment(segment):
            continue
        cleaned = re.sub(r"^(?:решили|договорились)\s+(?:что\s+)?", "", segment, flags=re.IGNORECASE).strip()
        key = normalize_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        tasks.append(cleaned)
        if len(tasks) >= limit:
            break
    return tasks


def extract_meeting_decisions(text: str, limit: int = 8) -> list[str]:
    decisions: list[str] = []
    seen: set[str] = set()
    for segment in split_meeting_segments(text):
        if not is_meeting_decision_segment(segment):
            continue
        cleaned = re.sub(r"^(?:решили|решение:?|договорились|зафиксировали)\s+(?:что\s+)?", "", segment, flags=re.IGNORECASE).strip()
        key = normalize_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        decisions.append(cleaned)
        if len(decisions) >= limit:
            break
    return decisions


def save_meeting_summary(
    config: AgentConfig,
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
) -> dict[str, Any]:
    meeting_text = strip_meeting_prefix(text)
    if not meeting_text:
        return {"digest_id": "MEETING", "answer": "Нужны итоги встречи после /meeting."}
    normalized = normalize_text(meeting_text)
    meeting_item = {
        "type": "meeting",
        "direction": detect_direction(normalized),
        "date": detect_date(normalized),
        "priority": "",
        "due_date": "",
        "assignee": "",
        "amount": None,
        "qty": None,
        "unit": "",
    }
    meeting_result = save_inbox_item(config, meeting_item, meeting_text, meta, chat_id, message_id)
    meeting_id = str((meeting_result.get("inbox_item") or {}).get("id") or "")
    meeting_direction = str(meeting_item.get("direction") or "")
    created_tasks: list[dict[str, Any]] = []
    created_decisions: list[dict[str, Any]] = []
    for index, task_text in enumerate(extract_meeting_tasks(meeting_text), start=1):
        task_item = classify_inbox_text(f"задача {task_text}", meta) or {"type": "task"}
        task_item["type"] = "task"
        if meeting_direction and not task_item.get("direction"):
            task_item["direction"] = meeting_direction
        task_item["parent_id"] = meeting_id
        task_item["meeting_order"] = index
        task_result = save_inbox_item(config, task_item, task_text, meta, chat_id, message_id)
        created_tasks.append(task_result.get("inbox_item") or {})
    for index, decision_text in enumerate(extract_meeting_decisions(meeting_text), start=1):
        decision_item = {
            "type": "decision",
            "direction": meeting_direction or detect_direction(normalize_text(decision_text)),
            "date": detect_date(normalize_text(decision_text)),
            "priority": "",
            "due_date": "",
            "assignee": "",
            "amount": None,
            "qty": None,
            "unit": "",
            "parent_id": meeting_id,
            "decision_order": index,
        }
        decision_result = save_inbox_item(config, decision_item, decision_text, meta, chat_id, message_id)
        created_decisions.append(decision_result.get("inbox_item") or {})
    lines = [
        "<b>Встреча записана</b>",
        f"id: {escape_html(meeting_id[:8])}",
        f"задач создано: {len(created_tasks)}",
        f"решений сохранено: {len(created_decisions)}",
    ]
    if created_tasks:
        lines.append("")
        lines.append("<b>Задачи</b>")
        for task in created_tasks[:8]:
            row_id = str(task.get("id") or "")[:8]
            meta_text = format_task_meta(task)
            lines.append(f"- {escape_html(row_id)} / {escape_html(meta_text)}: {escape_html(compact_text(str(task.get('text') or ''), 120))}")
    if created_decisions:
        lines.append("")
        lines.append("<b>Решения</b>")
        for decision in created_decisions[:6]:
            row_id = str(decision.get("id") or "")[:8]
            direction = str(decision.get("direction") or "без направления")
            lines.append(f"- {escape_html(row_id)} / {escape_html(direction)}: {escape_html(compact_text(str(decision.get('text') or ''), 120))}")
    if not created_tasks and not created_decisions:
        lines.extend(["", "Поручения не нашла. Можно прислать строки со словами: надо, нужно, проверить, добавить, поправить."])
    return {
        "digest_id": "MEETING",
        "answer": "\n".join(lines),
        "meeting_id": meeting_id,
        "tasks": created_tasks,
        "decisions": created_decisions,
    }


def extract_task_action_id(text: str, prefixes: list[str]) -> str:
    normalized = normalize_text(text)
    for prefix in prefixes:
        if normalized.startswith(prefix):
            rest = normalized[len(prefix):].strip()
            return rest.split()[0] if rest else ""
    return ""


def extract_snooze_until(text: str) -> str:
    return detect_due_date(normalize_text(text))


def build_task_done_message(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return escape_html(str(result.get("error") or "Не смогла закрыть задачу"))
    row_id = str(result.get("id") or "")[:8]
    text = str(result.get("text") or "")
    if len(text) > 160:
        text = text[:157] + "..."
    return "\n".join([
        "<b>Задача закрыта</b>",
        escape_html(row_id),
        escape_html(text),
    ])


def build_task_status_message(result: dict[str, Any], title: str) -> str:
    if not result.get("ok"):
        return escape_html(str(result.get("error") or "Не смогла изменить задачу"))
    row_id = str(result.get("id") or "")[:8]
    text = str(result.get("text") or "")
    if len(text) > 160:
        text = text[:157] + "..."
    lines = [f"<b>{escape_html(title)}</b>", escape_html(row_id)]
    if result.get("until"):
        lines.append(f"до {escape_html(str(result['until']))}")
    lines.append(escape_html(text))
    return "\n".join(lines)


def append_task_sheet_sync(config: AgentConfig, result: dict[str, Any], answer: str) -> str:
    row = result.get("row")
    if not config.sheets_sync_on_save or not result.get("ok") or not isinstance(row, dict):
        return answer
    sync_result = sync_inbox_to_sheet(config, [row])
    return f"{answer}\n\n{build_sheet_sync_message(sync_result)}"


def process_task_callback(config: AgentConfig, callback_data: str, actor: str = "") -> dict[str, Any]:
    match = re.fullmatch(r"task:(done|in_progress|snoozed|open|dropped):([0-9a-fA-F]{4,24})", callback_data or "")
    if not match:
        return {"digest_id": "CALLBACK-UNKNOWN", "answer": "Не поняла кнопку."}
    action, task_id = match.group(1), match.group(2)
    if action == "done":
        result = mark_task_done(task_id, closed_by=actor)
        answer = build_task_done_message(result)
        digest_id = "TASK-DONE"
    elif action == "in_progress":
        result = set_task_status_by_prefix(task_id, "in_progress", actor=actor)
        answer = build_task_status_message(result, "Задача в работе")
        digest_id = "TASK-IN-PROGRESS"
    elif action == "snoozed":
        result = set_task_status_by_prefix(task_id, "snoozed", actor=actor)
        answer = build_task_status_message(result, "Задача отложена")
        digest_id = "TASK-SNOOZE"
    elif action == "dropped":
        result = set_task_status_by_prefix(task_id, "dropped", actor=actor)
        answer = build_task_status_message(result, "Задача убрана")
        digest_id = "TASK-DROP"
    else:
        result = set_task_status_by_prefix(task_id, "open", actor=actor)
        answer = build_task_status_message(result, "Задача возвращена")
        digest_id = "TASK-REOPEN"
    return {"digest_id": digest_id, "answer": append_task_sheet_sync(config, result, answer), "result": result}


def build_stats_message() -> str:
    rows = read_inbox(limit=1_000_000)
    by_type: dict[str, int] = {}
    task_status: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    defect_amount = 0
    for row in rows:
        item_type = str(row.get("type") or "note")
        by_type[item_type] = by_type.get(item_type, 0) + 1
        direction = str(row.get("direction") or "без направления")
        by_direction[direction] = by_direction.get(direction, 0) + 1
        if item_type == "task":
            status = str(row.get("status") or "open")
            task_status[status] = task_status.get(status, 0) + 1
        if item_type == "defect":
            defect_amount += int(row.get("amount") or 0)
    type_line = ", ".join(f"{key}: {value}" for key, value in sorted(by_type.items())) or "пусто"
    task_line = ", ".join(f"{key}: {value}" for key, value in sorted(task_status.items())) or "пусто"
    top_directions = sorted(by_direction.items(), key=lambda item: item[1], reverse=True)[:6]
    direction_line = ", ".join(f"{key}: {value}" for key, value in top_directions) or "пусто"
    return "\n".join([
        "<b>Статистика TG Agent</b>",
        f"Всего записей: {len(rows)}",
        f"Типы: {escape_html(type_line)}",
        f"Задачи по статусам: {escape_html(task_line)}",
        f"Направления: {escape_html(direction_line)}",
        f"Брак на сумму: {defect_amount:,} UZS".replace(",", " "),
    ])


def build_task_summary_message() -> str:
    rows = [row for row in read_inbox(limit=1_000_000) if row.get("type") == "task"]
    if not rows:
        return "Задач пока нет."
    today = dt.date.today().isoformat()
    open_rows = [row for row in rows if is_open_task_status(row.get("status"))]
    status_counts: dict[str, int] = {}
    assignee_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    overdue = 0
    due_today = 0
    for row in open_rows:
        status = str(row.get("status") or "open")
        status_counts[status] = status_counts.get(status, 0) + 1
        assignee = str(row.get("assignee") or "без ответственного")
        assignee_counts[assignee] = assignee_counts.get(assignee, 0) + 1
        direction = str(row.get("direction") or "без направления")
        direction_counts[direction] = direction_counts.get(direction, 0) + 1
        due = str(row.get("due_date") or "")
        if due and due < today:
            overdue += 1
        elif due == today:
            due_today += 1
    status_line = ", ".join(f"{key}: {value}" for key, value in sorted(status_counts.items())) or "пусто"
    top_assignees = ", ".join(f"{key}: {value}" for key, value in sorted(assignee_counts.items(), key=lambda item: item[1], reverse=True)[:5]) or "пусто"
    top_directions = ", ".join(f"{key}: {value}" for key, value in sorted(direction_counts.items(), key=lambda item: item[1], reverse=True)[:5]) or "пусто"
    waiting_count = status_counts.get("waiting", 0)
    in_progress_count = status_counts.get("in_progress", 0)
    lines = [
        "<b>Сводка задач</b>",
        f"Всего задач: {len(rows)}",
        f"Открытый поток: {len(open_rows)}",
        f"В работе: {in_progress_count}",
        f"Ждут внешнего шага: {waiting_count}",
        f"Просрочено: {overdue}",
        f"На сегодня: {due_today}",
        f"Статусы: {escape_html(status_line)}",
        f"Ответственные: {escape_html(top_assignees)}",
        f"Направления: {escape_html(top_directions)}",
    ]
    if open_rows:
        first_rows = sort_task_rows(open_rows)[:5]
        lines.extend(["", "<b>Первые в очереди</b>"])
        for row in first_rows:
            lines.append(format_task_list_line(row))
    return "\n".join(lines)


def dedup_key(row: dict[str, Any]) -> str:
    text = normalize_text(str(row.get("text") or ""))
    text = re.sub(r"\s+", " ", text)
    return "|".join([
        str(row.get("type") or ""),
        str(row.get("direction") or ""),
        str(row.get("amount") or ""),
        str(row.get("qty") or ""),
        text,
    ])


def find_inbox_duplicates() -> list[dict[str, Any]]:
    rows = read_inbox(limit=1_000_000)
    first_by_key: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") == "duplicate":
            continue
        key = dedup_key(row)
        if not key.strip("|"):
            continue
        if key in first_by_key:
            duplicates.append({"row": row, "original": first_by_key[key], "key": key})
        else:
            first_by_key[key] = row
    return duplicates


def mark_inbox_duplicates() -> dict[str, Any]:
    rows = read_inbox(limit=1_000_000)
    first_by_key: dict[str, dict[str, Any]] = {}
    duplicate_ids: dict[str, str] = {}
    now = dt.datetime.now().isoformat(timespec="seconds")
    for row in rows:
        if row.get("status") == "duplicate":
            continue
        key = dedup_key(row)
        if not key.strip("|"):
            continue
        if key in first_by_key:
            duplicate_ids[str(row.get("id") or "")] = str(first_by_key[key].get("id") or "")
        else:
            first_by_key[key] = row
    if duplicate_ids:
        for row in rows:
            row_id = str(row.get("id") or "")
            if row_id in duplicate_ids:
                row["status"] = "duplicate"
                row["duplicate_of"] = duplicate_ids[row_id]
                row["duplicate_at"] = now
        write_inbox(rows)
    return {"ok": True, "marked": len(duplicate_ids), "duplicates": duplicate_ids}


def build_dedup_message(apply: bool = False) -> str:
    if apply:
        result = mark_inbox_duplicates()
        return f"Dedup: пометила дублей {result['marked']}."
    duplicates = find_inbox_duplicates()
    if not duplicates:
        return "Dedup: дублей не нашла."
    lines = [f"<b>Dedup preview</b>: найдено {len(duplicates)}"]
    for pair in duplicates[:12]:
        row = pair["row"]
        original = pair["original"]
        text = str(row.get("text") or "")
        if len(text) > 90:
            text = text[:87] + "..."
        lines.append(
            f"- {escape_html(str(row.get('id') or '')[:8])} дубль {escape_html(str(original.get('id') or '')[:8])}: {escape_html(text)}"
        )
    lines.append("Чтобы пометить: /dedup apply")
    return "\n".join(lines)


def dedup_should_apply(text: str) -> bool:
    normalized = normalize_text(text)
    tokens = set(normalized.split())
    return bool(tokens & {"apply", "yes", "да", "ок", "ok", "force", "-y"})


def parse_reminder_request(text: str) -> dict[str, Any]:
    raw = text.strip()
    normalized = normalize_text(raw)
    for prefix in ["/remind", "напомни", "напомнить"]:
        if normalized.startswith(prefix):
            raw = raw[len(prefix):].strip()
            normalized = normalize_text(raw)
            break
    recurrence = parse_recurrence(normalized)
    fire_at = parse_reminder_fire_at(normalized)
    if recurrence and not fire_at:
        fire_at = next_recurrence_fire_at(recurrence, from_time=dt.datetime.now())
    reminder_text = strip_reminder_time_phrase(raw)
    if not fire_at:
        return {"ok": False, "error": "Не поняла время. Примеры: /remind через 10 минут проверить чеки; /remind завтра 09:00 сверить E-com"}
    if not reminder_text:
        return {"ok": False, "error": "Не поняла текст напоминания"}
    return {"ok": True, "fire_at": fire_at, "text": reminder_text, "recurrence": recurrence}


def parse_recurrence(normalized: str) -> str:
    explicit = re.search(r"\b(every:\d{1,4}[mhd]|daily@\d{1,2}:\d{2})\b", normalized)
    if explicit:
        value = explicit.group(1)
        if value.startswith("daily@"):
            hh, mm = [int(part) for part in value.removeprefix("daily@").split(":", 1)]
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"daily@{hh:02d}:{mm:02d}"
            return ""
        return value

    daily = re.search(r"\b(?:кажд\w*\s+день|ежедневн\w*).*?(\d{1,2})[:.](\d{2})\b", normalized)
    if daily:
        hh, mm = int(daily.group(1)), int(daily.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"daily@{hh:02d}:{mm:02d}"

    if re.search(r"\b(?:кажд\w*\s+день|ежедневн\w*)\b", normalized):
        return "every:1d"

    repeated = re.search(r"\bкажд\w*\s+(?:(\d{1,4})\s*)?(минут|мин|час|часа|часов|дн|дня|дней|день|сут)\w*\b", normalized)
    if repeated:
        amount = int(repeated.group(1) or 1)
        unit_text = repeated.group(2)
        if amount <= 0:
            return ""
        if unit_text.startswith("мин"):
            return f"every:{amount}m"
        if unit_text.startswith("час"):
            return f"every:{amount}h"
        return f"every:{amount}d"
    return ""


def next_recurrence_fire_at(recurrence: str, from_time: dt.datetime | None = None) -> str:
    base = from_time or dt.datetime.now()
    if recurrence.startswith("every:"):
        spec = recurrence.removeprefix("every:")
        match = re.fullmatch(r"(\d{1,4})([mhd])", spec)
        if not match:
            return ""
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "m":
            target = base + dt.timedelta(minutes=amount)
        elif unit == "h":
            target = base + dt.timedelta(hours=amount)
        else:
            target = base + dt.timedelta(days=amount)
        return target.isoformat(timespec="seconds")
    if recurrence.startswith("daily@"):
        match = re.fullmatch(r"daily@(\d{2}):(\d{2})", recurrence)
        if not match:
            return ""
        hh, mm = int(match.group(1)), int(match.group(2))
        try:
            target = dt.datetime.combine(base.date(), dt.time(hh, mm))
        except ValueError:
            return ""
        if target <= base:
            target += dt.timedelta(days=1)
        return target.isoformat(timespec="seconds")
    return ""


def parse_reminder_fire_at(normalized: str) -> str:
    now = dt.datetime.now()
    match = re.search(r"\bчерез\s+(\d{1,4})\s*(минут|мин|час|часа|часов|дн|дня|дней)\b", normalized)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("мин"):
            target = now + dt.timedelta(minutes=amount)
        elif unit.startswith("час"):
            target = now + dt.timedelta(hours=amount)
        else:
            target = now + dt.timedelta(days=amount)
        return target.isoformat(timespec="seconds")

    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})(?:[ t](\d{1,2}):(\d{2}))?\b", normalized)
    if iso_match:
        date_part = iso_match.group(1)
        hh = int(iso_match.group(2) or 9)
        mm = int(iso_match.group(3) or 0)
        try:
            return dt.datetime.fromisoformat(f"{date_part}T{hh:02d}:{mm:02d}:00").isoformat(timespec="seconds")
        except ValueError:
            return ""

    day_offset: int | None = None
    if "послезавтра" in normalized:
        day_offset = 2
    elif "завтра" in normalized:
        day_offset = 1
    elif "сегодня" in normalized:
        day_offset = 0
    if day_offset is not None:
        time_match = re.search(r"\b(\d{1,2})[:.](\d{2})\b", normalized)
        hh = int(time_match.group(1)) if time_match else 9
        mm = int(time_match.group(2)) if time_match else 0
        try:
            target_date = (now + dt.timedelta(days=day_offset)).date()
            return dt.datetime.combine(target_date, dt.time(hh, mm)).isoformat(timespec="seconds")
        except ValueError:
            return ""
    return ""


def strip_reminder_time_phrase(text: str) -> str:
    cleaned = re.sub(r"\b(?:every:\d{1,4}[mhd]|daily@\d{1,2}:\d{2})\b", "", text, flags=re.I)
    cleaned = re.sub(r"\b(?:кажд\w*\s+день|ежедневн\w*)(?:\s+в\s+\d{1,2}[:.]\d{2})?\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bкажд\w*\s+(?:\d{1,4}\s*)?(?:минут|мин|час|часа|часов|дн|дня|дней|день|сут)\w*\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bчерез\s+\d{1,4}\s*(?:минут|мин|час|часа|часов|дн|дня|дней)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b20\d{2}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2})?\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(?:сегодня|завтра|послезавтра)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d{1,2}[:.]\d{2}\b", "", cleaned)
    return " ".join(cleaned.strip(" ,.-").split())


def add_reminder(text: str, chat_id: str | int = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    parsed = parse_reminder_request(text)
    if not parsed.get("ok"):
        return parsed
    row = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "chat_id": str(chat_id or ""),
        "fire_at": parsed["fire_at"],
        "text": parsed["text"],
        "status": "pending",
        "recurrence": parsed.get("recurrence") or "",
    }
    if extra:
        row.update({key: value for key, value in extra.items() if value not in (None, "")})
    row["id"] = build_reminder_id(row)
    rows = read_reminders(limit=1_000_000)
    rows.append(row)
    write_reminders(rows)
    return {"ok": True, **row}


def cancel_reminder(id_prefix: str, actor: str = "") -> dict[str, Any]:
    id_prefix = id_prefix.strip()
    if not id_prefix:
        return {"ok": False, "error": "Нужен id напоминания"}
    rows = read_reminders(limit=1_000_000)
    matches = [row for row in rows if str(row.get("id") or "").startswith(id_prefix)]
    if not matches:
        return {"ok": False, "error": f"Не нашла напоминание по id {id_prefix}"}
    if len(matches) > 1:
        return {"ok": False, "error": f"По id {id_prefix} найдено несколько напоминаний. Возьми больше символов id."}
    target_id = matches[0].get("id")
    for row in rows:
        if row.get("id") == target_id:
            row["status"] = "cancelled"
            row["cancelled_at"] = dt.datetime.now().isoformat(timespec="seconds")
            row["cancelled_by"] = actor
            break
    write_reminders(rows)
    return {"ok": True, "id": target_id, "text": matches[0].get("text") or ""}


def build_reminder_added_message(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return escape_html(str(result.get("error") or "Не смогла поставить напоминание"))
    lines = [
        "<b>Напоминание поставлено</b>",
        escape_html(str(result.get("id") or "")[:8]),
        f"когда: {escape_html(str(result.get('fire_at') or ''))}",
    ]
    if result.get("recurrence"):
        lines.append(f"повтор: {escape_html(str(result['recurrence']))}")
    lines.append(escape_html(str(result.get("text") or "")))
    return "\n".join(lines)


def build_cancel_reminder_message(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return escape_html(str(result.get("error") or "Не смогла отменить напоминание"))
    return "\n".join([
        "<b>Напоминание отменено</b>",
        escape_html(str(result.get("id") or "")[:8]),
        escape_html(str(result.get("text") or "")),
    ])


def build_reminders_message(limit: int = 12, title: str = "Напоминания") -> str:
    rows = [row for row in read_reminders(limit=1_000_000) if row.get("status") == "pending"]
    rows = sorted(rows, key=lambda row: str(row.get("fire_at") or ""))[:limit]
    if not rows:
        return "Напоминаний пока нет."
    lines = [f"<b>{escape_html(title)}</b>"]
    for row in rows:
        row_id = str(row.get("id") or "")[:8]
        recurrence = f" / {row['recurrence']}" if row.get("recurrence") else ""
        lines.append(f"- {escape_html(row_id)} / {escape_html(str(row.get('fire_at') or ''))}{escape_html(recurrence)}: {escape_html(str(row.get('text') or ''))}")
    return "\n".join(lines)


def scheduled_digest_id(text: str) -> str:
    text = (text or "").strip()
    if not text.startswith("/"):
        return ""
    digest_id = resolve_digest_id(text) or ""
    return digest_id if digest_id in SCHEDULED_DIGEST_IDS else ""


def render_due_reminder_text(config: AgentConfig, row: dict[str, Any]) -> str:
    text = str(row.get("text") or "").strip()
    if row.get("kind") == "group_send":
        return escape_html(text)
    digest_id = scheduled_digest_id(text)
    if not digest_id:
        return "<b>Напоминание</b>\n" + escape_html(text)
    result = process_text_command(
        config,
        text,
        {"kind": "scheduled_reminder", "reminder_id": row.get("id") or ""},
        chat_id=row.get("chat_id") or "",
        message_id=None,
        allow_telegram_side_effects=False,
    )
    return "\n".join([
        f"<b>Scheduled report: {escape_html(text)}</b>",
        str(result.get("answer") or build_digest_message(config, digest_id)),
    ])


def process_due_reminders(now: dt.datetime, send_fn: Any) -> int:
    rows = read_reminders(limit=1_000_000)
    if not rows:
        return 0
    now_iso = now.isoformat(timespec="seconds")
    sent = 0
    changed = False
    for row in rows:
        if row.get("status") != "pending":
            continue
        if str(row.get("fire_at") or "") > now_iso:
            continue
        chat_id = row.get("chat_id") or ""
        if not chat_id:
            continue
        send_fn(chat_id, row)
        fired_at = now
        row["last_fired_at"] = fired_at.isoformat(timespec="seconds")
        row["fired_count"] = int(row.get("fired_count") or 0) + 1
        recurrence = str(row.get("recurrence") or "")
        next_fire = next_recurrence_fire_at(recurrence, from_time=fired_at) if recurrence else ""
        if next_fire:
            row["fire_at"] = next_fire
        else:
            row["status"] = "fired"
            row["fired_at"] = row["last_fired_at"]
        sent += 1
        changed = True
    if changed:
        write_reminders(rows)
    return sent


def check_due_reminders(config: AgentConfig) -> int:
    if not os.environ.get(config.token_env):
        return 0

    def send(chat_id: str | int, row: dict[str, Any]) -> None:
        target_chat = chat_id or os.environ.get(config.default_chat_env, "")
        if target_chat:
            telegram_send(config, target_chat, render_due_reminder_text(config, row))

    return process_due_reminders(dt.datetime.now(), send)


def brain_cli_available(config: AgentConfig) -> bool:
    if not config.brain_enabled:
        return False
    try:
        first = shlex.split(config.brain_command)[0]
    except ValueError:
        return False
    if first.startswith("$HOME/"):
        first = str(Path.home() / first.removeprefix("$HOME/"))
    if Path(first).exists():
        return True
    return shutil.which(first) is not None


def brain_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ]:
        env.pop(key, None)
    token_file = Path.home() / ".config" / "claude-headless" / "token"
    if token_file.exists() and not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    extra_path = os.environ.get("TG_AGENT_EXTRA_PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    env["PATH"] = f"{env.get('PATH', '')}:{extra_path}" if env.get("PATH") else extra_path
    return env


def build_brain_prompt(config: AgentConfig, text: str, chat_id: str | int = "") -> str:
    history = read_brain_history(chat_id, limit_turns=config.brain_history_turns)
    context_snapshot = build_brain_context_snapshot()
    language = detect_text_language(text)
    language_instruction = (
        "Foydalanuvchi o'zbekcha yozsa, o'zbekcha javob ber. Qisqa va aniq yoz."
        if language == "uz"
        else "Отвечай по-русски, коротко и по делу."
    )
    lines = [
        f"Ты {BOT_DISPLAY_NAME} для проекта {BOT_PROJECT_NAME}.",
        language_instruction,
        "Ты помогаешь Наталье разбирать дашборды, задачи, брак, источники и проверки.",
        "Если нужно выполнить действие, верни JSON: {\"answer\":\"короткий ответ\", \"actions\":[...]}",
        "Доступные actions: add_task {text, priority?, due_date?, assignee?, direction?}; update_task {id, text?, priority?, due_date?, assignee?, direction?}; set_task_status {id, status}; add_reminder {text, fire_at?, recurrence?}; add_note {text, direction?}.",
        "status: open, in_progress, waiting, done, snoozed, dropped. priority: low, medium, high.",
        "Если action не нужен, можно вернуть обычный текст или JSON с answer без actions.",
        "Не обещай действий вне чата, если не вернул соответствующий action.",
        "",
        f"Текущее время: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    if context_snapshot:
        lines.extend(["", "Текущий рабочий контекст:", context_snapshot])
    if history:
        lines.extend(["", "Недавний разговор:"])
        for row in history:
            role = "Пользователь" if row.get("role") == "user" else "Бот"
            lines.append(f"{role}: {row.get('text') or ''}")
    lines.extend(["", "Сообщение пользователя:", text])
    return "\n".join(lines)


def build_brain_context_snapshot(task_limit: int = 12, reminder_limit: int = 5, inbox_limit: int = 6) -> str:
    lines: list[str] = []
    rows = read_inbox(limit=1_000_000)
    open_tasks = sort_task_rows([
        row for row in rows
        if row.get("type") == "task" and is_open_task_status(row.get("status"))
    ])[:task_limit]
    if open_tasks:
        lines.append("Открытые задачи:")
        for row in open_tasks:
            row_id = str(row.get("id") or "")[:8]
            text = compact_text(str(row.get("text") or ""), 120)
            lines.append(f"- {row_id} / {row.get('status') or 'open'} / {format_task_meta(row)}: {text}")
    reminders = [row for row in read_reminders(limit=1_000_000) if row.get("status") == "pending"]
    reminders = sorted(reminders, key=lambda row: str(row.get("fire_at") or ""))[:reminder_limit]
    if reminders:
        lines.append("Ближайшие напоминания:")
        for row in reminders:
            row_id = str(row.get("id") or "")[:8]
            recurrence = f" / {row['recurrence']}" if row.get("recurrence") else ""
            lines.append(f"- {row_id} / {row.get('fire_at') or ''}{recurrence}: {compact_text(str(row.get('text') or ''), 120)}")
    recent_rows = [row for row in rows if row.get("type") != "task"][-inbox_limit:]
    if recent_rows:
        lines.append("Последние записи журнала:")
        for row in reversed(recent_rows):
            row_id = str(row.get("id") or "")[:8]
            bits = [row_id, str(row.get("type") or "note"), str(row.get("direction") or "без направления")]
            lines.append(f"- {' / '.join(bits)}: {compact_text(str(row.get('text') or ''), 120)}")
    return "\n".join(lines)


def compact_text(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def redact_sensitive_text(text: str) -> str:
    redacted = str(text or "")
    patterns = [
        r"(?im)^(\s*(?:пароль|password|pass|pwd)\s*[:=]\s*)(.+)$",
        r"(?im)^(\s*(?:логин|login|username|user)\s*[:=]\s*)(.+)$",
    ]
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1[redacted]", redacted)
    return redacted


def strip_access_command(text: str) -> str:
    return re.sub(
        r"^\s*(?:/access_message|/share_access|/access|оформи\s+доступ|сформулируй\s+доступ|сообщение\s+с\s+доступом|собери\s+сообщение\s+для\s+коллег|собери\s+сообщение\s+для\s+команды|напиши\s+сообщение\s+для\s+коллег|напиши\s+сообщение\s+для\s+команды|доступ\s+для\s+команды)\s*[:\-]?\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()


def extract_labeled_value(text: str, labels: tuple[str, ...]) -> str:
    joined = "|".join(re.escape(label) for label in labels)
    pattern = rf"(?im)^\s*(?:{joined})\s*[:=]\s*(.+?)\s*$"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def infer_access_resource(text: str, url: str) -> str:
    explicit = extract_labeled_value(text, ("ресурс", "resource", "сервис", "дашборд", "dashboard", "ссылка на"))
    if explicit and explicit != url:
        return explicit
    lowered = normalize_text(text)
    if "дашборд" in lowered or "dashboard" in lowered:
        return "дашборд"
    if url:
        host = re.sub(r"^https?://", "", url).split("/", 1)[0]
        return host or "ресурс"
    return "ресурс"


def infer_access_purpose(text: str, resource: str) -> str:
    explicit = extract_labeled_value(text, ("для чего", "зачем", "назначение", "что делает", "позволяет"))
    if explicit:
        return explicit
    normalized = normalize_text(text)
    if "дашборд" in normalized or "dashboard" in normalized or resource == "дашборд":
        return "смотреть показатели, проверять динамику и сверять данные перед управленческими решениями"
    return "работать с материалами и данными по проекту"


def build_access_message(text: str) -> str:
    payload = strip_access_command(text)
    if not payload:
        return "\n".join([
            "<b>Нужны данные доступа</b>",
            "Формат:",
            "<code>/access",
            "ресурс: управленческий дашборд",
            "ссылка: https://...",
            "логин: ...",
            "пароль: ...",
            "для чего: смотреть продажи и план-факт</code>",
        ])
    url_match = re.search(r"https?://\S+", payload)
    url = url_match.group(0).rstrip(").,;") if url_match else extract_labeled_value(payload, ("ссылка", "url", "link"))
    login = extract_labeled_value(payload, ("логин", "login", "username", "user"))
    password = extract_labeled_value(payload, ("пароль", "password", "pass", "pwd"))
    resource = infer_access_resource(payload, url)
    purpose = infer_access_purpose(payload, resource)
    lines = [
        "Коллеги, добрый день!",
        "",
        f"Передаю доступ к ресурсу: {escape_html(resource)}.",
        f"Он нужен, чтобы {escape_html(purpose)}.",
    ]
    if url:
        lines.extend(["", f"Ссылка: {escape_html(url)}"])
    if login or password:
        lines.append("")
        lines.append("Данные для входа:")
        if login:
            lines.append(f"Логин: <code>{escape_html(login)}</code>")
        if password:
            lines.append(f"Пароль: <code>{escape_html(password)}</code>")
    lines.extend([
        "",
        "Пожалуйста, проверьте вход и напишите, если доступ не открывается.",
    ])
    return "\n".join(lines)


def parse_brain_response(stdout: str) -> dict[str, Any]:
    stdout = (stdout or "").strip()
    if not stdout:
        return {"answer": "", "actions": []}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if 0 <= start < end:
            try:
                data = json.loads(stdout[start : end + 1])
            except json.JSONDecodeError:
                return {"answer": stdout, "actions": []}
            if isinstance(data, dict):
                answer = str(data.get("answer") or data.get("result") or data.get("text") or "").strip()
                prefix = stdout[:start].strip()
                actions = data.get("actions") if isinstance(data.get("actions"), list) else []
                return {"answer": prefix or answer, "actions": actions}
        return {"answer": stdout, "actions": []}
    if isinstance(data, dict):
        for key in ["answer", "result", "text", "message", "content"]:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                actions = data.get("actions") if isinstance(data.get("actions"), list) else []
                return {"answer": value.strip(), "actions": actions}
        if isinstance(data.get("content"), list):
            parts = []
            for item in data["content"]:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                actions = data.get("actions") if isinstance(data.get("actions"), list) else []
                return {"answer": "\n".join(parts).strip(), "actions": actions}
        actions = data.get("actions") if isinstance(data.get("actions"), list) else []
        return {"answer": "", "actions": actions}
    return {"answer": stdout, "actions": []}


def parse_brain_output(stdout: str) -> str:
    return str(parse_brain_response(stdout).get("answer") or "")


def cleanup_temp_path(path: Path | None) -> None:
    if not path:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def execute_brain_action(config: AgentConfig, action: dict[str, Any], chat_id: str | int = "") -> dict[str, Any]:
    action_type = str(action.get("type") or action.get("name") or "").strip()
    if action_type == "add_task":
        text = str(action.get("text") or "").strip()
        if not text:
            return {"ok": False, "type": action_type, "error": "empty task text"}
        item = {
            "type": "task",
            "direction": str(action.get("direction") or detect_direction(text) or "не определено"),
            "priority": str(action.get("priority") or detect_priority(text) or "medium"),
            "status": "open",
        }
        for key in ["due_date", "assignee"]:
            if action.get(key):
                item[key] = str(action[key])
        result = save_inbox_item(config, item, text, {"kind": "brain_action"}, chat_id=chat_id)
        return {"ok": True, "type": action_type, "id": (result.get("inbox_item") or {}).get("id", "")}
    if action_type == "update_task":
        task_id = str(action.get("id") or action.get("task_id") or "").strip()
        fields = {
            key: str(action[key]).strip()
            for key in ["text", "direction", "due_date", "priority", "assignee"]
            if action.get(key)
        }
        result = update_task_by_prefix(task_id, fields, actor=str(chat_id))
        sync_result = None
        if config.sheets_sync_on_save and result.get("ok") and isinstance(result.get("row"), dict):
            sync_result = sync_inbox_to_sheet(config, [result["row"]])
        response = {"ok": bool(result.get("ok")), "type": action_type, "id": result.get("id", ""), "error": result.get("error", "")}
        if sync_result is not None:
            response["sync"] = sync_result
        return response
    if action_type == "add_note":
        text = str(action.get("text") or "").strip()
        if not text:
            return {"ok": False, "type": action_type, "error": "empty note text"}
        item = {
            "type": "note",
            "direction": str(action.get("direction") or detect_direction(text) or "не определено"),
            "status": "open",
        }
        result = save_inbox_item(config, item, text, {"kind": "brain_action"}, chat_id=chat_id)
        return {"ok": True, "type": action_type, "id": (result.get("inbox_item") or {}).get("id", "")}
    if action_type == "set_task_status":
        task_id = str(action.get("id") or action.get("task_id") or "").strip()
        status = str(action.get("status") or "").strip()
        if status == "pending":
            status = "open"
        if status == "done":
            result = mark_task_done(task_id, closed_by=str(chat_id))
        elif status in {"open", "in_progress", "waiting", "snoozed", "dropped"}:
            result = set_task_status_by_prefix(
                task_id,
                status,
                actor=str(chat_id),
                reason=str(action.get("reason") or action.get("waiting_reason") or "").strip(),
            )
        else:
            return {"ok": False, "type": action_type, "error": f"bad status {status}"}
        return {"ok": bool(result.get("ok")), "type": action_type, "id": result.get("id", ""), "error": result.get("error", "")}
    if action_type == "add_reminder":
        reminder_text = str(action.get("text") or "").strip()
        if not reminder_text:
            return {"ok": False, "type": action_type, "error": "empty reminder text"}
        if action.get("fire_at"):
            row = {
                "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                "chat_id": str(chat_id or ""),
                "fire_at": str(action.get("fire_at")),
                "text": reminder_text,
                "status": "pending",
                "recurrence": str(action.get("recurrence") or ""),
            }
            row["id"] = build_reminder_id(row)
            rows = read_reminders(limit=1_000_000)
            rows.append(row)
            write_reminders(rows)
            return {"ok": True, "type": action_type, "id": row["id"]}
        result = add_reminder(f"/remind {reminder_text}", chat_id=chat_id)
        return {"ok": bool(result.get("ok")), "type": action_type, "id": result.get("id", ""), "error": result.get("error", "")}
    return {"ok": False, "type": action_type or "unknown", "error": "unknown action"}


def execute_brain_actions(config: AgentConfig, actions: list[Any], chat_id: str | int = "") -> list[dict[str, Any]]:
    results = []
    for action in actions[:8]:
        if isinstance(action, dict):
            results.append(execute_brain_action(config, action, chat_id=chat_id))
    return results


def ask_brain_cli(config: AgentConfig, text: str, chat_id: str | int = "", allow_actions: bool = True) -> dict[str, Any]:
    if not config.brain_enabled:
        return {"ok": False, "error": "Brain CLI выключен"}
    if not brain_cli_available(config):
        return {"ok": False, "error": f"Brain CLI недоступен: {config.brain_command}"}
    prompt_text = build_brain_prompt(config, text, chat_id=chat_id)
    output_path: Path | None = None
    command = config.brain_command
    if "{output}" in command:
        fd, output_name = tempfile.mkstemp(prefix="tg-agent-brain-", suffix=".txt")
        os.close(fd)
        output_path = Path(output_name)
        command = command.replace("{output}", str(output_path))
    try:
        proc = subprocess.run(
            shlex.split(command),
            input=prompt_text,
            capture_output=True,
            text=True,
            timeout=config.brain_timeout,
            env=brain_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        cleanup_temp_path(output_path)
        return {"ok": False, "error": f"Brain CLI timeout: {config.brain_timeout}s"}
    if proc.returncode != 0:
        cleanup_temp_path(output_path)
        return {"ok": False, "error": f"Brain CLI exit={proc.returncode}: {(proc.stderr or '')[:500]}"}
    stdout = proc.stdout
    if output_path and output_path.exists():
        output_text = output_path.read_text(encoding="utf-8").strip()
        if output_text:
            stdout = output_text
        cleanup_temp_path(output_path)
    parsed = parse_brain_response(stdout)
    answer = str(parsed.get("answer") or "")
    action_results = execute_brain_actions(config, parsed.get("actions") or [], chat_id=chat_id) if allow_actions else []
    if not answer:
        if action_results:
            answer = "Готово."
        else:
            return {"ok": False, "error": "Brain CLI вернул пустой ответ"}
    if action_results:
        ok_count = sum(1 for item in action_results if item.get("ok"))
        fail_count = len(action_results) - ok_count
        if detect_text_language(text) == "uz":
            suffix = f"\n\nAmallar: bajarildi {ok_count}"
        else:
            suffix = f"\n\nДействия: выполнено {ok_count}"
        if fail_count:
            suffix += f", xatolar {fail_count}" if detect_text_language(text) == "uz" else f", ошибок {fail_count}"
        answer = answer.rstrip() + suffix + "."
    append_brain_history(chat_id, "user", text)
    append_brain_history(chat_id, "assistant", answer)
    return {"ok": True, "answer": answer, "actions": action_results}


def build_brain_message(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return escape_html(str(result.get("answer") or ""))
    return "Не смогла ответить через brain CLI: " + escape_html(str(result.get("error") or "unknown error"))


def build_reply_brain_text(text: str, meta: dict[str, Any]) -> str:
    reply_text = compact_text(str(meta.get("reply_to_text") or ""), 1200)
    if not reply_text:
        return text
    return "\n".join([
        "Пользователь отвечает reply на мое предыдущее сообщение.",
        "",
        "Мое предыдущее сообщение:",
        reply_text,
        "",
        "Ответ пользователя:",
        text,
    ])


def build_inbox_summary_message() -> str:
    rows = read_inbox(limit=1_000_000)
    if not rows:
        return "Журнал пока пуст. Сводку пока строить не из чего."
    by_type: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    defect_amount = 0
    open_tasks = 0
    for row in rows:
        item_type = str(row.get("type") or "note")
        direction = str(row.get("direction") or "без направления")
        by_type[item_type] = by_type.get(item_type, 0) + 1
        by_direction[direction] = by_direction.get(direction, 0) + 1
        if item_type == "defect":
            defect_amount += int(row.get("amount") or 0)
        if item_type == "task" and is_open_task_status(row.get("status")):
            open_tasks += 1
    type_line = ", ".join(f"{key}: {value}" for key, value in sorted(by_type.items()))
    top_directions = sorted(by_direction.items(), key=lambda item: item[1], reverse=True)[:5]
    direction_line = ", ".join(f"{key}: {value}" for key, value in top_directions)
    lines = [
        "<b>Сводка журнала</b>",
        f"Всего записей: {len(rows)}",
        f"По типам: {escape_html(type_line)}",
        f"По направлениям: {escape_html(direction_line)}",
        f"Открытых задач: {open_tasks}",
        f"Брак на сумму: {defect_amount:,} UZS".replace(",", " "),
        "",
        "Последние записи:",
    ]
    for row in reversed(rows[-5:]):
        text = str(row.get("text") or "")
        if len(text) > 90:
            text = text[:87] + "..."
        lines.append(f"- {escape_html(str(row.get('type') or 'note'))}: {escape_html(text)}")
    return "\n".join(lines)


def build_activity_message(limit: int = 8) -> str:
    rows = read_inbox(limit=1_000_000)
    recent_rows = rows[-limit:]
    undone_rows = [row for row in rows if row.get("status") == "undone"][-limit:]
    logs = read_recent_logs(limit=limit)
    lines = [
        f"<b>Последние действия {BOT_DISPLAY_NAME}</b>",
        f"Журнал: {len(rows)} строк",
        f"Отменено: {len([row for row in rows if row.get('status') == 'undone'])}",
        f"Логи: {count_jsonl(LOG_DIR / (dt.date.today().isoformat() + '.jsonl')) if LOG_DIR.exists() else 0} строк сегодня",
    ]
    lines.extend(["", "<b>Последние записи журнала</b>"])
    if recent_rows:
        for row in reversed(recent_rows):
            row_id = str(row.get("id") or "")[:8]
            status = str(row.get("status") or "open")
            item_type = str(row.get("type") or "note")
            source = str(row.get("source_kind") or "")
            text = compact_text(str(row.get("text") or ""), 100)
            lines.append(f"- {escape_html(row_id)} / {escape_html(item_type)} / {escape_html(status)} / {escape_html(source)}: {escape_html(text)}")
    else:
        lines.append("пусто")
    lines.extend(["", "<b>Последние отмены</b>"])
    if undone_rows:
        for row in reversed(undone_rows):
            row_id = str(row.get("id") or "")[:8]
            reason = str(row.get("undo_reason") or "")
            text = compact_text(str(row.get("text") or ""), 100)
            lines.append(f"- {escape_html(row_id)} / {escape_html(str(row.get('undone_at') or ''))}: {escape_html(text)}")
            if reason:
                lines.append(f"  причина: {escape_html(reason)}")
    else:
        lines.append("пусто")
    lines.extend(["", "<b>Последние системные события</b>"])
    if logs:
        for row in reversed(logs):
            payload = compact_text(json.dumps(row.get("payload") or {}, ensure_ascii=False, sort_keys=True), 140)
            lines.append(f"- {escape_html(str(row.get('ts') or ''))} / {escape_html(str(row.get('kind') or 'log'))}: {escape_html(payload)}")
    else:
        lines.append("пусто")
    lines.extend(["", "Дальше: /undo_last, /export_memory 7, /doctor."])
    return "\n".join(lines)


def inbox_row_date(row: dict[str, Any]) -> dt.date | None:
    stamp = str(row.get("ts") or "")[:10]
    if not stamp:
        return None
    try:
        return dt.date.fromisoformat(stamp)
    except ValueError:
        return None


def build_period_log_message(days: int = 1, limit: int = 12) -> str:
    days = max(1, min(90, int(days)))
    today_date = dt.date.today()
    start_date = today_date - dt.timedelta(days=days - 1)
    today = today_date.isoformat()
    start = start_date.isoformat()
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if (row_date := inbox_row_date(row)) and start_date <= row_date <= today_date
    ]
    if not rows:
        if days == 1:
            return f"За сегодня ({today}) в журнале пока пусто."
        return f"За период {start} - {today} в журнале пока пусто."
    by_type: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    for row in rows:
        item_type = str(row.get("type") or "note")
        direction = str(row.get("direction") or "без направления")
        by_type[item_type] = by_type.get(item_type, 0) + 1
        by_direction[direction] = by_direction.get(direction, 0) + 1
    type_line = ", ".join(f"{key}: {value}" for key, value in sorted(by_type.items()))
    top_directions = sorted(by_direction.items(), key=lambda item: item[1], reverse=True)[:5]
    direction_line = ", ".join(f"{key}: {value}" for key, value in top_directions) or "пусто"
    title = f"Журнал за сегодня ({today})" if days == 1 else f"Журнал за период {start} - {today}"
    lines = [
        f"<b>{escape_html(title)}</b>",
        f"Всего записей: {len(rows)}",
        f"По типам: {escape_html(type_line)}",
        f"По направлениям: {escape_html(direction_line)}",
        "",
        "Последние записи:",
    ]
    for row in reversed(rows[-limit:]):
        row_id = str(row.get("id") or "")[:8]
        item_type = str(row.get("type") or "note")
        direction = str(row.get("direction") or "без направления")
        text = compact_text(str(row.get("text") or ""), 110)
        bits = [row_id, item_type, direction]
        lines.append(f"- {escape_html(' / '.join(bits))}: {escape_html(text)}")
    if len(rows) > limit:
        lines.append(f"Показано {limit} из {len(rows)}. Полная выгрузка: /export_inbox.")
    return "\n".join(lines)


def build_today_log_message(limit: int = 12) -> str:
    return build_period_log_message(days=1, limit=limit)


def group_message_date(row: dict[str, Any]) -> dt.date | None:
    stamp = str(row.get("ts") or "")[:10]
    if not stamp:
        return None
    try:
        return dt.date.fromisoformat(stamp)
    except ValueError:
        return None


def clean_capture_reason(value: str) -> str:
    reason = str(value or "")
    if reason.startswith("trigger:"):
        return "триггер"
    return reason or "сообщение в подключенном чате"


def telegram_message_link(row: dict[str, Any]) -> str:
    message_id = str(row.get("message_id") or "").strip()
    if not message_id:
        return ""
    username = str(row.get("chat_username") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = str(row.get("chat_id") or "").strip()
    if chat_id.startswith("-100") and len(chat_id) > 4:
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return ""


def telegram_chat_link(config: AgentConfig, chat_id: str | int) -> str:
    try:
        result = telegram_request(config, "getChat", {"chat_id": chat_id})
    except Exception as exc:
        append_log("chat_link_lookup_error", {"chat_id": chat_id, "error": str(exc)})
        return ""
    chat = result.get("result") or {}
    username = str(chat.get("username") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}"
    return str(chat.get("invite_link") or "")


def known_group_chats() -> list[dict[str, str]]:
    chats: dict[str, dict[str, str]] = {}
    if GROUP_MESSAGES_FILE.exists():
        with GROUP_MESSAGES_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chat_id = str(row.get("chat_id") or "").strip()
                if not chat_id:
                    continue
                chat_type = "group" if chat_id.startswith("-") else "private"
                chats[chat_id] = {
                    "chat_id": chat_id,
                    "title": str(row.get("chat_title") or row.get("chat_username") or chat_id),
                    "type": chat_type,
                }
    return sorted(chats.values(), key=lambda row: (row.get("title") or "").casefold())


def build_groups_message() -> str:
    groups = [row for row in known_group_chats() if str(row.get("chat_id") or "").startswith("-")]
    if not groups:
        return "\n".join([
            "<b>Известные группы</b>",
            "Пока нет групп.",
            "Добавьте бота в группу и напишите там /whoami, чтобы я увидела chat_id.",
        ])
    lines = ["<b>Известные группы</b>"]
    for row in groups:
        lines.append(f"- {escape_html(row['title'])}: <code>{escape_html(row['chat_id'])}</code>")
    lines.extend([
        "",
        "Отправка: <code>/send_group chat_id | текст</code>",
    ])
    return "\n".join(lines)


def resolve_group_chat(query: str) -> dict[str, str] | None:
    needle = query.strip()
    if not needle:
        return None
    if re.fullmatch(r"-?\d+", needle):
        return {"chat_id": needle, "title": needle, "type": "group" if needle.startswith("-") else "private"}
    normalized = normalize_text(needle)
    groups = known_group_chats()
    for row in groups:
        title = normalize_text(str(row.get("title") or ""))
        if title == normalized:
            return row
    for row in groups:
        title = normalize_text(str(row.get("title") or ""))
        if normalized and normalized in title:
            return row
    return None


def strip_group_send_prefix(text: str) -> str:
    return re.sub(
        r"^\s*(?:/send_to_group|/send_group|напиши\s+в\s+группу|отправь\s+в\s+группу|попроси\s+в\s+группе)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip(" :")


def strip_group_schedule_prefix(text: str) -> str:
    return re.sub(
        r"^\s*(?:/schedule_group|/regular_group|регулярно\s+в\s+группу|поставь\s+регулярный\s+пинг)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip(" :")


def parse_group_send_request(text: str) -> tuple[str, str]:
    payload = strip_group_send_prefix(text)
    if "|" in payload:
        target, message = payload.split("|", 1)
        return target.strip(), message.strip()
    match = re.match(r"(.+?)\s*[:—-]\s*(.+)", payload, flags=re.S)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", payload.strip()


def parse_group_schedule_request(text: str) -> tuple[str, str, str]:
    payload = strip_group_schedule_prefix(text)
    parts = [part.strip() for part in payload.split("|", 2)]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    match = re.match(r"(.+?)\s*[:—-]\s*(кажд.+?|every:\d{1,4}[mhd]|daily@\d{1,2}:\d{2}|20\d{2}-\d{2}-\d{2}.*?)\s*[:—-]\s*(.+)", payload, flags=re.I | re.S)
    if match:
        return match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
    return "", "", payload.strip()


def build_schedule_group_message(config: AgentConfig, text: str, source_chat_id: str | int = "") -> str:
    target_query, schedule, message = parse_group_schedule_request(text)
    if not target_query or not schedule or not message:
        return "\n".join([
            "<b>Нужна группа, расписание и текст</b>",
            "Формат: <code>/schedule_group группа | каждый день в 10:00 | Дайте статус по задаче ...</code>",
            "Можно также: <code>daily@10:00</code> или <code>every:2h</code>.",
            "",
            build_groups_message(),
        ])
    target = resolve_group_chat(target_query)
    if not target:
        return "\n".join([
            "<b>Группу не нашла</b>",
            f"Запрос: {escape_html(target_query)}",
            "",
            build_groups_message(),
        ])
    reminder_text = f"/remind {schedule} {message}"
    result = add_reminder(
        reminder_text,
        chat_id=target["chat_id"],
        extra={
            "kind": "group_send",
            "target_title": target.get("title") or target["chat_id"],
            "created_from_chat_id": str(source_chat_id or ""),
        },
    )
    if not result.get("ok"):
        return "\n".join([
            "<b>Не смогла поставить регулярную отправку</b>",
            escape_html(str(result.get("error") or "")),
        ])
    return "\n".join([
        "<b>Регулярная отправка поставлена</b>",
        f"id: <code>{escape_html(str(result.get('id') or '')[:8])}</code>",
        f"группа: {escape_html(str(target.get('title') or target['chat_id']))} / <code>{escape_html(str(target['chat_id']))}</code>",
        f"когда: {escape_html(str(result.get('fire_at') or ''))}",
        f"повтор: {escape_html(str(result.get('recurrence') or 'один раз'))}",
        "",
        escape_html(message),
        "",
        "Отменить: <code>/cancel_reminder " + escape_html(str(result.get("id") or "")[:8]) + "</code>",
    ])


def build_send_group_message(config: AgentConfig, text: str, allow_send: bool = False) -> str:
    target_query, message = parse_group_send_request(text)
    if not target_query or not message:
        return "\n".join([
            "<b>Нужна группа и текст</b>",
            "Формат: <code>/send_group chat_id | Дайте статус по задаче ...</code>",
            "",
            build_groups_message(),
        ])
    target = resolve_group_chat(target_query)
    if not target:
        return "\n".join([
            "<b>Группу не нашла</b>",
            f"Запрос: {escape_html(target_query)}",
            "",
            build_groups_message(),
        ])
    chat_id = target["chat_id"]
    title = target.get("title") or chat_id
    if not allow_send:
        return "\n".join([
            "<b>Черновик отправки в группу</b>",
            f"Группа: {escape_html(title)} / <code>{escape_html(chat_id)}</code>",
            "",
            escape_html(message),
        ])
    try:
        response = telegram_send(config, chat_id, escape_html(message))
    except Exception as exc:
        return "\n".join([
            "<b>Не смогла отправить в группу</b>",
            f"Группа: {escape_html(title)} / <code>{escape_html(chat_id)}</code>",
            f"Ошибка: {escape_html(str(exc))}",
        ])
    message_id = ((response.get("result") or {}).get("message_id") if isinstance(response, dict) else "") or ""
    append_log("sent_group_message", {"target_chat_id": chat_id, "target_title": title, "message_id": message_id, "text": message})
    return "\n".join([
        "<b>Отправила в группу</b>",
        f"Группа: {escape_html(title)} / <code>{escape_html(chat_id)}</code>",
        f"message_id: <code>{escape_html(str(message_id))}</code>" if message_id else "",
    ]).strip()


def group_message_source_label(config: AgentConfig, row: dict[str, Any], index: int, chat_links: dict[str, str]) -> str:
    ts = str(row.get("ts") or "")[11:16] or str(row.get("ts") or "")[:16]
    chat_title = str(row.get("chat_title") or row.get("chat_id") or "чат")
    author = str(row.get("username") or row.get("first_name") or "участник").lstrip("@")
    label = f"[{index}] {ts} / {chat_title} / @{author}"
    link = telegram_message_link(row)
    if link:
        return f'<a href="{escape_html(link)}">{escape_html(label)}</a>'
    chat_id = str(row.get("chat_id") or "")
    if chat_id and chat_id not in chat_links:
        chat_links[chat_id] = telegram_chat_link(config, chat_id)
    if chat_links.get(chat_id):
        message_id = str(row.get("message_id") or "")
        suffix = f" / message_id {message_id}" if message_id else ""
        return f'<a href="{escape_html(chat_links[chat_id])}">{escape_html(label + suffix)}</a>'
    message_id = str(row.get("message_id") or "")
    suffix = f" / message_id {message_id}" if message_id else ""
    return escape_html(label + suffix)


def run_brain_prompt(config: AgentConfig, prompt_text: str, timeout: int | None = None) -> dict[str, Any]:
    if not config.brain_enabled:
        return {"ok": False, "error": "Brain CLI выключен"}
    if not brain_cli_available(config):
        return {"ok": False, "error": f"Brain CLI недоступен: {config.brain_command}"}
    output_path: Path | None = None
    command = config.brain_command
    if "{output}" in command:
        fd, output_name = tempfile.mkstemp(prefix="tg-agent-brain-", suffix=".txt")
        os.close(fd)
        output_path = Path(output_name)
        command = command.replace("{output}", str(output_path))
    try:
        proc = subprocess.run(
            shlex.split(command),
            input=prompt_text,
            capture_output=True,
            text=True,
            timeout=timeout or config.brain_timeout,
            env=brain_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        cleanup_temp_path(output_path)
        return {"ok": False, "error": f"Brain CLI timeout: {timeout or config.brain_timeout}s"}
    if proc.returncode != 0:
        cleanup_temp_path(output_path)
        return {"ok": False, "error": f"Brain CLI exit={proc.returncode}: {(proc.stderr or '')[:500]}"}
    stdout = proc.stdout
    if output_path and output_path.exists():
        output_text = output_path.read_text(encoding="utf-8").strip()
        if output_text:
            stdout = output_text
        cleanup_temp_path(output_path)
    answer = parse_brain_output(stdout)
    if not answer:
        return {"ok": False, "error": "Brain CLI вернул пустой ответ"}
    return {"ok": True, "answer": answer}


def summarize_group_messages(config: AgentConfig, rows: list[dict[str, Any]]) -> str:
    chunks = []
    for idx, row in enumerate(rows, start=1):
        text = compact_text(str(row.get("text") or ""), 900)
        chat_title = str(row.get("chat_title") or row.get("chat_id") or "чат")
        author = str(row.get("username") or row.get("first_name") or "участник").lstrip("@")
        reason = clean_capture_reason(str(row.get("capture_reason") or ""))
        chunks.append(
            f"[{idx}] {row.get('ts') or ''} / {chat_title} / @{author} / {reason}\n{text}"
        )
    prompt = "\n\n".join([
        "Сделай интерпретированную управленческую сводку по сообщениям из внешних Telegram-чатов.",
        "Цель: Наталья должна за 40 секунд понять, что произошло, что решено, где зависло и кого пинговать.",
        "Не пересказывай сообщения по порядку. Сгруппируй их в темы и назови управленческий смысл каждой темы.",
        "Если в сообщениях есть эмоции, сомнения, тишина после обещания, конфликт, риск или скрытое поручение, явно вытащи это в вывод.",
        "Пиши по-русски, живо и конкретно. Без markdown-таблиц, без канцелярита, без рекламного тона.",
        "Каждый содержательный пункт должен ссылаться на источник в квадратных скобках, например [2].",
        "Не выдумывай факты, ответственных и решения. Если есть только намёк, пиши 'похоже' или 'нужно уточнить'.",
        "",
        "Структура ответа строго такая:",
        "Итог дня:",
        "1 короткий абзац: главный смысл обсуждений, не больше 2 предложений.",
        "",
        "О чём говорили:",
        "- 2-5 тем. Формат: 'Тема — управленческий смысл, что произошло и почему это важно [источник]'.",
        "",
        "Что решили:",
        "- только подтверждённые решения. Если решений нет, напиши: 'Подтверждённых решений не вижу.'",
        "",
        "Что зависло:",
        "- вопросы без ответа, обещания без следующего шага, тишина после договорённости, риски с ответственными. Если ничего нет, напиши: 'Явных зависаний не вижу.'",
        "",
        "Кого пинговать:",
        "- имя/роль — зачем пинговать. Если по сообщениям неясно, напиши: 'Неясно, нужен ручной выбор ответственного.'",
        "",
        "Сообщения:",
        "\n\n".join(chunks),
    ])
    result = run_brain_prompt(config, prompt, timeout=min(config.brain_timeout, 90))
    if result.get("ok"):
        return str(result.get("answer") or "").strip()
    append_log("group_important_brain_error", {"error": str(result.get("error") or "unknown")})
    return ""


def build_group_important_message(config: AgentConfig, days: int = 1, limit: int = 10) -> str:
    days = max(1, min(30, int(days)))
    today_date = dt.date.today()
    start_date = today_date - dt.timedelta(days=days - 1)
    rows = [
        row for row in read_group_messages()
        if (row_date := group_message_date(row)) and start_date <= row_date <= today_date
    ]
    captured = [row for row in rows if str(row.get("capture_reason") or "").strip()]
    title = f"Важное во внешних чатах за {today_date.isoformat()}" if days == 1 else f"Важное во внешних чатах за {start_date.isoformat()} - {today_date.isoformat()}"
    lines = [
        f"<b>{escape_html(title)}</b>",
        f"Сообщений в подключенных чатах: {len(rows)}",
        f"Захвачено как важное: {len(captured)}",
    ]
    if not rows:
        lines.extend(["", "За период нет сохраненных сообщений из внешних чатов."])
        return "\n".join(lines)
    by_chat: dict[str, int] = {}
    for row in rows:
        chat_title = str(row.get("chat_title") or row.get("chat_id") or "без названия")
        by_chat[chat_title] = by_chat.get(chat_title, 0) + 1
    chat_line = ", ".join(f"{name}: {count}" for name, count in sorted(by_chat.items(), key=lambda item: item[1], reverse=True)[:5])
    lines.extend(["", f"Чаты: {escape_html(chat_line)}"])
    source_rows = captured or rows
    selected_rows = list(reversed(source_rows[-limit:]))
    summary = summarize_group_messages(config, selected_rows)
    if summary:
        lines.extend(["", escape_html(summary)])
    else:
        lines.extend(["", "Ключевые идеи:"])
        for idx, row in enumerate(selected_rows, start=1):
            text = compact_text(str(row.get("text") or ""), 220)
            lines.append(f"- [{idx}] {escape_html(text)}")
    lines.extend(["", "<b>Источники</b>"])
    chat_links: dict[str, str] = {}
    for idx, row in enumerate(selected_rows, start=1):
        reason = clean_capture_reason(str(row.get("capture_reason") or ""))
        text = compact_text(str(row.get("text") or ""), 90)
        lines.append(f"- {group_message_source_label(config, row, idx, chat_links)} / {escape_html(reason)}: {escape_html(text)}")
    if len(source_rows) > limit:
        lines.append(f"Показано {limit} из {len(source_rows)}.")
    return "\n".join(lines)



def business_message_source_label(row: dict[str, Any], index: int) -> str:
    chat_title = str(row.get("chat_title") or row.get("chat_id") or "личный чат")
    username = str(row.get("username") or "").strip()
    sender = f"@{username}" if username else "без username"
    message_id = row.get("message_id") or ""
    return f"[{index}] {escape_html(chat_title)} / {escape_html(sender)} / message_id {escape_html(str(message_id))}"


def build_business_summary_message(config: AgentConfig, days: int = 1, limit: int = 15) -> str:
    days = max(1, min(30, int(days)))
    today_date = dt.date.today()
    start_date = today_date - dt.timedelta(days=days - 1)
    rows = [
        row for row in read_business_messages()
        if (row_date := business_message_date(row)) and start_date <= row_date <= today_date
    ]
    title = f"Личные business-чаты за {today_date.isoformat()}" if days == 1 else f"Личные business-чаты за {start_date.isoformat()} - {today_date.isoformat()}"
    lines = [
        f"<b>{escape_html(title)}</b>",
        f"Сообщений: {len(rows)}",
    ]
    if not rows:
        lines.extend([
            "",
            "Новых сообщений из Telegram Business пока нет.",
            "Если Фатхулло уже подключил автоматизацию, проверьте Secretary Mode в BotFather и права чтения сообщений в Telegram Business.",
        ])
        return "\n".join(lines)
    by_chat: dict[str, int] = {}
    for row in rows:
        chat_title = str(row.get("chat_title") or row.get("chat_id") or "личный чат")
        by_chat[chat_title] = by_chat.get(chat_title, 0) + 1
    chat_line = ", ".join(f"{name}: {count}" for name, count in sorted(by_chat.items(), key=lambda item: item[1], reverse=True)[:8])
    lines.extend(["", f"Чаты: {escape_html(chat_line)}"])
    selected_rows = list(reversed(rows[-limit:]))
    summary = summarize_group_messages(config, selected_rows)
    if summary:
        lines.extend(["", escape_html(summary)])
    else:
        lines.extend(["", "Ключевые сообщения:"])
        for idx, row in enumerate(selected_rows, start=1):
            text = compact_text(str(row.get("text") or ""), 220)
            lines.append(f"- [{idx}] {escape_html(text)}")
    lines.extend(["", "<b>Источники</b>"])
    for idx, row in enumerate(selected_rows, start=1):
        event = str(row.get("event") or "business_message")
        text = compact_text(str(row.get("text") or ""), 90)
        lines.append(f"- {business_message_source_label(row, idx)} / {escape_html(event)}: {escape_html(text)}")
    if len(rows) > limit:
        lines.append(f"Показано {limit} из {len(rows)}.")
    return "\n".join(lines)


def lms_bug_data_dir() -> Path:
    return Path(os.environ.get("LMS_BUG_BOT_DATA_DIR") or DEFAULT_LMS_BUG_DATA_DIR).expanduser()


def read_lms_bugs() -> list[dict[str, str]]:
    path = lms_bug_data_dir() / "bugs.csv"
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_lms_ignored(limit: int = 20) -> list[dict[str, Any]]:
    path = lms_bug_data_dir() / "ignored_messages.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def lms_bug_link(row: dict[str, str]) -> str:
    link = str(row.get("telegram_link") or "").strip()
    if link:
        return link
    chat_id = str(row.get("telegram_chat_id") or "").strip()
    message_id = str(row.get("telegram_message_id") or "").strip()
    if chat_id.startswith("-100") and message_id:
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return ""


def extract_json_object(raw: str) -> Any:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON object not found in command output")
    return json.loads(text[start : end + 1])


def fetch_tg_lms_dashboard() -> dict[str, Any]:
    command = [
        "ssh",
        "-i",
        TG_LMS_SSH_KEY,
        "-p",
        TG_LMS_SSH_PORT,
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"root@{TG_LMS_HOST}",
        (
            f"docker exec -u frappe {shlex.quote(TG_LMS_CONTAINER)} bash -lc "
            + shlex.quote(
                "cd /home/frappe/frappe-bench && "
                f"bench --site {shlex.quote(TG_LMS_SITE)} execute {shlex.quote(TG_LMS_DASHBOARD_METHOD)}"
            )
        ),
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=75)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(compact_text(detail, 500) or f"exit {result.returncode}")
    data = extract_json_object(result.stdout)
    if not isinstance(data, dict):
        raise ValueError("TG LMS dashboard returned non-object JSON")
    return data


def top_people_by_number(people: list[dict[str, Any]], key: str, limit: int = 5, reverse: bool = True) -> list[dict[str, Any]]:
    return sorted(
        people,
        key=lambda row: float(row.get(key) or 0),
        reverse=reverse,
    )[:limit]


def is_tg_lms_demo_person(row: dict[str, Any]) -> bool:
    raw = " ".join(str(row.get(key) or "") for key in ("member", "full_name", "department")).lower()
    return any(marker in raw for marker in ("john doe", "jane smith", "ashley ippolito", "toshkent gullari", "demo", "test"))


def build_tg_lms_summary_message(limit: int = 5) -> str:
    fetched_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        data = fetch_tg_lms_dashboard()
    except Exception as exc:
        return "\n".join([
            "<b>TG LMS</b>",
            "Не смогла получить живую сводку из Frappe.",
            f"Ошибка: {escape_html(str(exc))}",
            f"Источник: {escape_html(TG_LMS_URL)}",
        ])

    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    people = data.get("people") if isinstance(data.get("people"), list) else []
    redemptions = data.get("redemptions") if isinstance(data.get("redemptions"), list) else []
    limits = data.get("limits") if isinstance(data.get("limits"), list) else []
    courses = data.get("course_options") if isinstance(data.get("course_options"), list) else []
    programs = data.get("program_options") if isinstance(data.get("program_options"), list) else []

    active_people = [row for row in people if isinstance(row, dict)]
    managers = [row for row in active_people if row.get("can_issue_rewards")]
    pending_redemptions = [
        row for row in redemptions
        if isinstance(row, dict) and str(row.get("status") or "").lower() in {"новая", "new", "pending", "ожидает"}
    ]
    total_balance = sum(float(row.get("balance") or 0) for row in active_people)
    total_earned = sum(float(row.get("earned") or 0) for row in active_people)
    assignments = sum(int(row.get("assignments") or 0) for row in active_people)
    real_people = [row for row in active_people if not is_tg_lms_demo_person(row)]
    richest = top_people_by_number(real_people, "balance", limit=limit, reverse=True)
    zero_or_low = [
        row for row in sorted(real_people, key=lambda item: float(item.get("balance") or 0))
        if float(row.get("balance") or 0) <= 10
    ][:limit]
    limit_rows = sorted(
        [row for row in limits if isinstance(row, dict)],
        key=lambda row: float(row.get("remaining") or 0),
    )[:limit]

    from_date = period.get("from_date") or "?"
    to_date = period.get("to_date") or "?"
    lines = [
        "<b>TG LMS: живая сводка</b>",
        f"Период: {escape_html(str(from_date))} - {escape_html(str(to_date))}",
        f"Сотрудников: {int(summary.get('employees') or len(active_people))}",
        f"Руководителей с правом начислять: {len(managers)}",
        f"Курсов: {len(courses)}; программ: {len(programs)}",
        f"Назначений курсов/программ: {assignments}",
        f"Баллы: начислено {format_qty(total_earned)}, баланс {format_qty(total_balance)}",
        f"Начислений за период: {int(summary.get('issued') or 0)}; штрафов: {int(summary.get('penalties') or 0)}; заявок в ожидании: {len(pending_redemptions) or int(summary.get('pending') or 0)}",
    ]
    if pending_redemptions:
        lines.extend(["", "<b>Заявки на призы</b>"])
        for row in pending_redemptions[:limit]:
            name = row.get("member_name") or row.get("member") or "без имени"
            item = row.get("item_title") or row.get("item") or "без названия"
            points = row.get("points") or 0
            requested = row.get("requested_on") or ""
            lines.append(f"- {escape_html(str(name))}: {escape_html(str(item))}, {format_qty(points)} баллов, {escape_html(str(requested))}")
    if richest:
        lines.extend(["", "<b>Топ балансов</b>"])
        for row in richest:
            lines.append(f"- {escape_html(str(row.get('full_name') or row.get('member') or ''))}: {format_qty(row.get('balance') or 0)}")
    if zero_or_low:
        lines.extend(["", "<b>Низкий баланс</b>"])
        for row in zero_or_low:
            lines.append(f"- {escape_html(str(row.get('full_name') or row.get('member') or ''))}: {format_qty(row.get('balance') or 0)}")
    if limit_rows:
        lines.extend(["", "<b>Лимиты руководителей</b>"])
        for row in limit_rows:
            manager = row.get("manager_name") or row.get("manager") or "без имени"
            used = row.get("used") or 0
            remaining = row.get("remaining") or 0
            monthly = row.get("monthly_limit") or 0
            lines.append(f"- {escape_html(str(manager))}: {format_qty(used)} / {format_qty(monthly)}, осталось {format_qty(remaining)}")
    lines.extend(["", f"Источник: {escape_html(TG_LMS_URL)}", f"Обновлено: {escape_html(fetched_at)} МСК"])
    return "\n".join(lines)


def build_lms_bug_summary_message(limit: int = 8) -> str:
    rows = read_lms_bugs()
    ignored = read_lms_ignored(limit=5)
    base = lms_bug_data_dir()
    if not rows and not ignored:
        return "\n".join([
            "<b>LMS Telegram сводка</b>",
            f"Источник: {escape_html(str(base))}",
            "Пока нет записей от LMS bug bot.",
        ])
    open_rows = [row for row in rows if str(row.get("status") or "new").lower() not in {"done", "closed", "resolved", "ignored"}]
    s1s2 = [row for row in open_rows if str(row.get("severity") or "") in {"S1", "S2"}]
    by_status: dict[str, int] = {}
    by_area: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "new")
        area = str(row.get("area") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_area[area] = by_area.get(area, 0) + 1
    status_line = ", ".join(f"{key}: {value}" for key, value in sorted(by_status.items())) or "пусто"
    area_line = ", ".join(f"{key}: {value}" for key, value in sorted(by_area.items(), key=lambda item: item[1], reverse=True)[:5]) or "пусто"
    lines = [
        "<b>LMS Telegram сводка для Фатхулло</b>",
        f"Всего багов: {len(rows)}",
        f"Открыто: {len(open_rows)}",
        f"S1/S2: {len(s1s2)}",
        f"По статусам: {escape_html(status_line)}",
        f"По зонам: {escape_html(area_line)}",
    ]
    if s1s2:
        lines.extend(["", "<b>Критично</b>"])
        for row in s1s2[:limit]:
            bug_id = str(row.get("id") or "")
            severity = str(row.get("severity") or "")
            area = str(row.get("area") or "")
            title = compact_text(str(row.get("title") or row.get("summary") or ""), 120)
            missing = str(row.get("missing") or "").strip()
            link = lms_bug_link(row)
            label = f"{bug_id} / {severity} / {area}"
            if link:
                lines.append(f'- <a href="{escape_html(link)}">{escape_html(label)}</a>: {escape_html(title)}')
            else:
                msg = str(row.get("telegram_message_id") or "")
                suffix = f" / message_id {msg}" if msg else ""
                lines.append(f"- {escape_html(label + suffix)}: {escape_html(title)}")
            if missing:
                lines.append(f"  не хватает: {escape_html(missing)}")
    latest = open_rows[-limit:]
    if latest:
        lines.extend(["", "<b>Последние открытые</b>"])
        for row in reversed(latest):
            bug_id = str(row.get("id") or "")
            severity = str(row.get("severity") or "")
            status = str(row.get("status") or "")
            title = compact_text(str(row.get("title") or row.get("summary") or ""), 110)
            lines.append(f"- {escape_html(bug_id)} / {escape_html(status)} / {escape_html(severity)}: {escape_html(title)}")
    if ignored:
        lines.extend(["", "<b>Ответы/фиксы Натальи, не заведены как баги</b>"])
        for item in reversed(ignored):
            message = item.get("message") or {}
            text = compact_text(str(message.get("text") or message.get("caption") or ""), 130)
            reason = str(item.get("reason") or "")
            lines.append(f"- {escape_html(reason)}: {escape_html(text)}")
    lines.extend(["", f"Источник: {escape_html(str(base))}"])
    return "\n".join(lines)


def clickup_token() -> str:
    return os.environ.get(CLICKUP_API_TOKEN_ENV, "").strip()


def clickup_request(path: str, params: dict[str, Any] | None = None) -> Any:
    token_value = clickup_token()
    if not token_value:
        raise RuntimeError(f"ClickUp token is missing: {CLICKUP_API_TOKEN_ENV}")
    query = ""
    if params:
        clean_params = {key: value for key, value in params.items() if value not in (None, "")}
        query = "?" + urllib.parse.urlencode(clean_params, doseq=True) if clean_params else ""
    request = urllib.request.Request(
        f"{CLICKUP_API_BASE}{path}{query}",
        headers={
            "Authorization": token_value,
            "Accept": "application/json",
            "User-Agent": "tg-bot-agent/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"ClickUp HTTP {exc.code}: {detail}") from exc


def clickup_teams() -> list[dict[str, Any]]:
    data = clickup_request("/team")
    teams = data.get("teams") if isinstance(data, dict) else []
    return [row for row in teams if isinstance(row, dict)]


def clickup_team_id() -> str:
    if CLICKUP_TEAM_ID:
        return CLICKUP_TEAM_ID
    teams = clickup_teams()
    if not teams:
        raise RuntimeError("ClickUp workspace is not available for this token")
    return str(teams[0].get("id") or "")


def parse_clickup_user_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in re.split(r"[,\s]+", CLICKUP_TELEGRAM_USER_MAP.strip()):
        if not item or ":" not in item:
            continue
        tg_id, clickup_id = item.split(":", 1)
        tg_id = tg_id.strip()
        clickup_id = clickup_id.strip()
        if tg_id and clickup_id:
            mapping[tg_id] = clickup_id
    return mapping


def clickup_user_id_for_telegram(telegram_user_id: str | int | None) -> str:
    if telegram_user_id in (None, ""):
        return ""
    return parse_clickup_user_map().get(str(telegram_user_id), "")


def clickup_task_url(task: dict[str, Any]) -> str:
    return str(task.get("url") or f"https://app.clickup.com/t/{task.get('id') or ''}")


def clickup_ms_to_date(value: Any) -> str:
    if value in (None, "", 0, "0"):
        return ""
    try:
        stamp = int(value) / 1000
    except (TypeError, ValueError):
        return str(value)
    return dt.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")


def clickup_task_status(task: dict[str, Any]) -> str:
    status = task.get("status")
    if isinstance(status, dict):
        return str(status.get("status") or status.get("type") or "")
    return str(status or "")


def clickup_task_assignees(task: dict[str, Any]) -> str:
    assignees = task.get("assignees") if isinstance(task.get("assignees"), list) else []
    names = [
        str(row.get("username") or row.get("email") or row.get("id") or "").strip()
        for row in assignees
        if isinstance(row, dict)
    ]
    return ", ".join([name for name in names if name])


def clickup_task_assignee_ids(task: dict[str, Any]) -> set[str]:
    assignees = task.get("assignees") if isinstance(task.get("assignees"), list) else []
    return {
        str(row.get("id") or "").strip()
        for row in assignees
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }


def clickup_target_list_id() -> str:
    return CLICKUP_LIST_ID.strip()


def clickup_task_source_label() -> str:
    list_id = clickup_target_list_id()
    if list_id:
        return f"список {CLICKUP_LIST_NAME} ({list_id})"
    return f"workspace {clickup_team_id()}"


def fetch_clickup_tasks(
    limit: int = 12,
    include_closed: bool = False,
    assignee_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    team_id = clickup_team_id()
    list_id = clickup_target_list_id()
    path = f"/list/{urllib.parse.quote(list_id)}/task" if list_id else f"/team/{urllib.parse.quote(team_id)}/task"
    params: dict[str, Any] = {
        "include_closed": str(include_closed).lower(),
        "subtasks": "true",
        "page": 0,
        "order_by": "due_date",
        "reverse": "false",
    }
    if assignee_ids:
        params["assignees[]"] = assignee_ids
    data = clickup_request(
        path,
        params,
    )
    tasks = data.get("tasks") if isinstance(data, dict) else []
    rows = [row for row in tasks if isinstance(row, dict)]
    if assignee_ids:
        allowed = {str(value) for value in assignee_ids}
        rows = [row for row in rows if clickup_task_assignee_ids(row) & allowed]
    rows.sort(key=lambda row: (
        int(row.get("due_date") or 9_999_999_999_999),
        int(row.get("date_created") or 9_999_999_999_999),
    ))
    return rows[:limit]


def clickup_is_my_tasks_query(text: str) -> bool:
    normalized = normalize_text(text)
    return any(marker in normalized for marker in (
        "/my_clickup",
        "/my_tasks",
        "мои задачи",
        "задачи по мне",
        "что у меня",
        "мой clickup",
        "мой кликап",
        "mening vazifalarim",
        "menga vazifalar",
    ))


def clickup_assignee_query(text: str) -> str:
    normalized = normalize_text(text)
    cleaned = re.sub(
        r"^\s*(?:/clickup|/clickup_tasks|clickup|кликап|задачи\s+clickup|задачи\s+кликап)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip(" .,:;!?")
    cleaned = re.sub(r"^(?:по|для|у|ответственный|ответственная)\s+", "", cleaned, flags=re.IGNORECASE).strip(" .,:;!?")
    if clickup_is_my_tasks_query(text):
        return ""
    if normalized in {"clickup", "кликап", "clickup задачи", "кликап задачи", "задачи clickup", "задачи кликап", "/clickup", "/clickup_tasks"}:
        return ""
    return cleaned


def clickup_filter_tasks_by_assignee_name(tasks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = query.casefold().strip()
    if not needle:
        return tasks
    return [
        task for task in tasks
        if needle in clickup_task_assignees(task).casefold()
    ]


def clickup_group_tasks_by_assignee(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        assignees = [name.strip() for name in clickup_task_assignees(task).split(",") if name.strip()]
        if not assignees:
            assignees = ["без ответственного"]
        for name in assignees:
            groups.setdefault(name, []).append(task)
    return dict(sorted(groups.items(), key=lambda item: (-len(item[1]), item[0].casefold())))


def build_clickup_status_message() -> str:
    try:
        teams = clickup_teams()
    except Exception as exc:
        return "\n".join([
            "<b>ClickUp</b>",
            "Подключение не прошло.",
            f"Ошибка: {escape_html(str(exc))}",
        ])
    lines = ["<b>ClickUp подключен</b>"]
    for team in teams[:5]:
        name = team.get("name") or "без названия"
        team_id_value = team.get("id") or ""
        members = team.get("members") if isinstance(team.get("members"), list) else []
        lines.append(f"- {escape_html(str(name))}: workspace {escape_html(str(team_id_value))}, участников {len(members)}")
    if not teams:
        lines.append("Workspace не найден.")
    lines.append(f"Основной список: {escape_html(clickup_task_source_label())}")
    mapped = parse_clickup_user_map()
    lines.append(f"Привязок Telegram к ClickUp: {len(mapped)}")
    return "\n".join(lines)


def format_clickup_task_line(task: dict[str, Any]) -> list[str]:
    task_id_value = str(task.get("id") or "")
    name = compact_text(str(task.get("name") or "Без названия"), 110)
    status = clickup_task_status(task) or "без статуса"
    due = clickup_ms_to_date(task.get("due_date"))
    meta = " / ".join([part for part in [status, f"до {due}" if due else ""] if part])
    url = clickup_task_url(task)
    label = f"{task_id_value}: {name}" if task_id_value else name
    lines: list[str] = []
    if url:
        lines.append(f'- <a href="{escape_html(url)}">{escape_html(label)}</a>')
    else:
        lines.append(f"- {escape_html(label)}")
    if meta:
        lines.append(f"  {escape_html(meta)}")
    return lines


def build_clickup_tasks_message(limit: int = 12, text: str = "", telegram_user_id: str | int | None = None) -> str:
    try:
        assignee_ids: list[str] = []
        if clickup_is_my_tasks_query(text):
            clickup_id = clickup_user_id_for_telegram(telegram_user_id)
            if not clickup_id:
                return "\n".join([
                    "<b>ClickUp задачи по мне</b>",
                    "Не знаю, какой ClickUp-пользователь привязан к твоему Telegram.",
                    "Отправь /whoami администратору, чтобы добавить привязку.",
                ])
            assignee_ids = [clickup_id]
        tasks = fetch_clickup_tasks(limit=100, assignee_ids=assignee_ids)
        assignee_query = clickup_assignee_query(text)
        tasks = clickup_filter_tasks_by_assignee_name(tasks, assignee_query)[:limit]
    except Exception as exc:
        return "\n".join([
            "<b>ClickUp задачи</b>",
            "Не смогла получить задачи.",
            f"Ошибка: {escape_html(str(exc))}",
        ])
    if not tasks:
        scope = "по мне" if clickup_is_my_tasks_query(text) else (f"для {clickup_assignee_query(text)}" if clickup_assignee_query(text) else "")
        return f"<b>ClickUp задачи {escape_html(scope)}</b>\nОткрытых задач не нашла.\nИсточник: {escape_html(clickup_task_source_label())}"
    by_status: dict[str, int] = {}
    for task in tasks:
        status = clickup_task_status(task) or "без статуса"
        by_status[status] = by_status.get(status, 0) + 1
    title_suffix = ""
    if clickup_is_my_tasks_query(text):
        title_suffix = " по мне"
    elif clickup_assignee_query(text):
        title_suffix = f" для {clickup_assignee_query(text)}"
    lines = [
        f"<b>ClickUp задачи{escape_html(title_suffix)}</b>",
        f"Источник: {escape_html(clickup_task_source_label())}",
        f"Показано открытых: {len(tasks)}",
        "Статусы: " + escape_html(", ".join(f"{key}: {value}" for key, value in sorted(by_status.items()))),
        "",
    ]
    grouped = clickup_group_tasks_by_assignee(tasks)
    if title_suffix:
        lines.append("<b>Ближайшие задачи</b>")
        for task in tasks[:limit]:
            lines.extend(format_clickup_task_line(task))
    else:
        for assignee, group in grouped.items():
            lines.extend(["", f"<b>{escape_html(assignee)} — {len(group)}</b>"])
            for task in group[:5]:
                lines.extend(format_clickup_task_line(task))
    return "\n".join(lines)


def google_sheet_csv_url(gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{TG_FLOWERS_WASTE_SPREADSHEET_ID}/export?format=csv&gid={gid}"


def fetch_csv_rows(url: str, timeout: int = 30) -> list[dict[str, str]]:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def waste_journal_rows() -> list[dict[str, str]]:
    return fetch_csv_rows(google_sheet_csv_url(TG_FLOWERS_WASTE_JOURNAL_GID))


def parse_float_ru(value: str) -> float:
    cleaned = str(value or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def waste_row_date(row: dict[str, str]) -> dt.date | None:
    raw = str(row.get("source_date") or row.get("created_at") or "")[:10]
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def is_waste_question(normalized: str) -> bool:
    terms = ("брак", "списан", "списани", "списания", "waste", "дефект", "defect")
    if not any(term in normalized for term in terms):
        return False
    return any(marker in normalized for marker in (
        "что",
        "сколько",
        "какой",
        "какая",
        "покажи",
        "дай",
        "сводка",
        "топ",
        "где",
        "по ",
        "nima",
        "nimalar",
        "qancha",
        "qaysi",
        "qayer",
        "kursat",
        "ko'rsat",
        "ber",
        "булди",
        "boldi",
        "утган",
        "hafta",
    ))


def waste_period_days(text: str) -> int | None:
    normalized = normalize_text(text)
    if "сегодня" in normalized or "за день" in normalized or "bugun" in normalized:
        return 1
    if "вчера" in normalized or "kecha" in normalized:
        return 1
    if "недел" in normalized or "hafta" in normalized or "утган" in normalized:
        return 7
    if "месяц" in normalized:
        return 31
    return None


def filter_waste_rows(rows: list[dict[str, str]], text: str = "") -> list[dict[str, str]]:
    normalized = normalize_text(text)
    days = waste_period_days(text)
    if days:
        today = dt.date.today()
        start = today - dt.timedelta(days=days - 1)
        if "вчера" in normalized:
            start = today - dt.timedelta(days=1)
            today = start
        rows = [row for row in rows if (row_date := waste_row_date(row)) and start <= row_date <= today]
    for branch in ("tg1", "tg2", "tg3", "tg4", "nour", "склад", "база"):
        if branch in normalized:
            rows = [row for row in rows if normalize_text(str(row.get("branch_norm") or row.get("branch_raw") or "")) == branch]
            break
    for status in ("требует проверки", "подтверждено", "отклонено"):
        if status in normalized:
            rows = [row for row in rows if normalize_text(str(row.get("status") or "")) == status]
            break
    return rows


def sum_qty(rows: list[dict[str, str]]) -> float:
    return sum(parse_float_ru(str(row.get("qty_norm") or "")) for row in rows)


def top_waste(rows: list[dict[str, str]], key: str, limit: int = 5) -> list[tuple[str, int, float]]:
    agg: dict[str, tuple[int, float]] = {}
    for row in rows:
        name = str(row.get(key) or "").strip() or "не распознано"
        count, qty = agg.get(name, (0, 0.0))
        agg[name] = (count + 1, qty + parse_float_ru(str(row.get("qty_norm") or "")))
    return [(name, count, qty) for name, (count, qty) in sorted(agg.items(), key=lambda item: item[1][1], reverse=True)[:limit]]


def format_qty(value: float) -> str:
    if abs(value - round(value)) < 0.001:
        return str(int(round(value)))
    return f"{value:.1f}".replace(".", ",")


def build_waste_summary_message(text: str = "") -> str:
    try:
        rows = waste_journal_rows()
    except Exception as exc:
        return "Не смогла прочитать таблицу брака: " + escape_html(str(exc))
    filtered = filter_waste_rows(rows, text)
    total = len(filtered)
    qty = sum_qty(filtered)
    needs_review = sum(1 for row in filtered if normalize_text(str(row.get("status") or "")) == "требует проверки")
    avg_conf = 0.0
    confidences = [parse_float_ru(str(row.get("ai_confidence") or "").replace("%", "")) for row in filtered if row.get("ai_confidence")]
    if confidences:
        avg_conf = sum(confidences) / len(confidences)
    title = "TG Flowers: брак и списания"
    lines = [
        f"<b>{escape_html(title)}</b>",
        f"Записей: {total}",
        f"Количество: {escape_html(format_qty(qty))} шт",
        f"Требуют проверки: {needs_review}",
        f"Средняя уверенность: {round(avg_conf)}%",
        "",
        "<b>Топ филиалов</b>",
    ]
    for name, count, item_qty in top_waste(filtered, "branch_norm", limit=5):
        lines.append(f"- {escape_html(name)}: {count} записей, {escape_html(format_qty(item_qty))} шт")
    lines.extend(["", "<b>Топ товаров</b>"])
    for name, count, item_qty in top_waste(filtered, "product_norm", limit=5):
        lines.append(f"- {escape_html(name)}: {count} записей, {escape_html(format_qty(item_qty))} шт")
    lines.extend(["", "<b>Топ причин</b>"])
    for name, count, item_qty in top_waste(filtered, "reason_norm", limit=5):
        lines.append(f"- {escape_html(name)}: {count} записей, {escape_html(format_qty(item_qty))} шт")
    if filtered:
        latest = max((waste_row_date(row) for row in filtered if waste_row_date(row)), default=None)
        if latest:
            lines.append(f"\nПоследняя дата: {latest.isoformat()}")
    lines.append(f"Таблица: {escape_html(TG_FLOWERS_WASTE_URL)}")
    return "\n".join(lines)


def build_waste_answer_message(text: str) -> str:
    normalized = normalize_text(text)
    try:
        rows = waste_journal_rows()
    except Exception as exc:
        return "Не смогла прочитать таблицу брака: " + escape_html(str(exc))
    filtered = filter_waste_rows(rows, text)
    branch_terms = ("tg1", "tg2", "tg3", "tg4", "nour", "склад", "база")
    if any(branch in normalized for branch in branch_terms) and not any(term in normalized for term in ("топ филиал", "по филиалам")):
        return build_waste_summary_message(text)
    if "товар" in normalized or "позици" in normalized or "роза" in normalized:
        section = "Топ товаров"
        top = top_waste(filtered, "product_norm", limit=8)
    elif "причин" in normalized or "почему" in normalized:
        section = "Топ причин"
        top = top_waste(filtered, "reason_norm", limit=8)
    elif "филиал" in normalized or any(branch in normalized for branch in ("tg1", "tg2", "tg3", "tg4", "склад", "база")):
        section = "По филиалам"
        top = top_waste(filtered, "branch_norm", limit=8)
    else:
        return build_waste_summary_message(text)
    lines = [
        "<b>Ответ по таблице брака TG Flowers</b>",
        f"Запрос: {escape_html(compact_text(text, 180))}",
        f"Записей в выборке: {len(filtered)}",
        f"Количество: {escape_html(format_qty(sum_qty(filtered)))} шт",
        "",
        f"<b>{escape_html(section)}</b>",
    ]
    for name, count, item_qty in top:
        lines.append(f"- {escape_html(name)}: {count} записей, {escape_html(format_qty(item_qty))} шт")
    lines.append(f"\nТаблица: {escape_html(TG_FLOWERS_WASTE_URL)}")
    return "\n".join(lines)


def parse_period_log_days(text: str) -> int:
    normalized = normalize_text(text)
    if "недел" in normalized or normalized.startswith("/week_log"):
        default = 7
    else:
        default = 7
    return parse_days_arg(text, default=default, min_days=1, max_days=90)


def parse_group_important_days(text: str) -> int:
    normalized = normalize_text(text)
    default = 7 if "недел" in normalized else 1
    return parse_days_arg(text, default=default, min_days=1, max_days=30)


def rows_by_type_for_period(item_type: str, days: int = 7, limit: int = 5) -> list[dict[str, Any]]:
    days = max(1, min(90, int(days)))
    today_date = dt.date.today()
    start_date = today_date - dt.timedelta(days=days - 1)
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if row.get("type") == item_type
        and (row_date := inbox_row_date(row))
        and start_date <= row_date <= today_date
    ]
    return rows[-limit:]


def rule_rows(limit: int = 10) -> list[dict[str, Any]]:
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if row.get("type") == "rule" and not row.get("undone_at")
    ]
    return rows[-limit:]


def build_rules_message(limit: int = 12) -> str:
    rows = rule_rows(limit=limit)
    lines = ["<b>Правила проекта</b>"]
    if not rows:
        lines.extend([
            "Пока правил нет.",
            "Добавить: /rule если обновляем данные, обновляем все подвкладки.",
        ])
        return "\n".join(lines)
    for row in reversed(rows):
        row_id = str(row.get("id") or "")[:8]
        direction = str(row.get("direction") or "общее")
        text = compact_text(str(row.get("text") or ""), 180)
        lines.append(f"- {escape_html(row_id)} / {escape_html(direction)}: {escape_html(text)}")
    lines.extend(["", "Добавить новое: /rule <текст>. Отменить ошибочное: /undo <id>."])
    return "\n".join(lines)


def task_rows_created_for_period(days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
    days = max(1, min(90, int(days)))
    today_date = dt.date.today()
    start_date = today_date - dt.timedelta(days=days - 1)
    rows = [
        row for row in read_inbox(limit=1_000_000)
        if row.get("type") == "task"
        and (row_date := inbox_row_date(row))
        and start_date <= row_date <= today_date
    ]
    return sort_task_rows(rows)[-limit:]


def task_rows_closed_for_period(days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
    days = max(1, min(90, int(days)))
    today_date = dt.date.today()
    start_date = today_date - dt.timedelta(days=days - 1)
    rows: list[dict[str, Any]] = []
    for row in read_inbox(limit=1_000_000):
        if row.get("type") != "task" or str(row.get("status") or "") != "done":
            continue
        closed_at = str(row.get("closed_at") or "")[:10]
        try:
            closed_date = dt.date.fromisoformat(closed_at) if closed_at else None
        except ValueError:
            closed_date = None
        if closed_date and start_date <= closed_date <= today_date:
            rows.append(row)
    return sort_task_rows(rows)[-limit:]


def build_period_review_message(days: int = 7) -> str:
    days = max(1, min(90, int(days)))
    created_tasks = task_rows_created_for_period(days=days, limit=6)
    closed_tasks = task_rows_closed_for_period(days=days, limit=6)
    decisions = rows_by_type_for_period("decision", days=days, limit=6)
    risks = rows_by_type_for_period("risk", days=days, limit=6)
    questions = [row for row in rows_by_type_for_period("question", days=days, limit=8) if is_open_inbox_status("question", row.get("status"))]
    lines = [
        f"<b>Обзор изменений за {days} дн.</b>",
        f"Новые задачи: {len(task_rows_created_for_period(days=days, limit=1_000_000))}",
        f"Закрытые задачи: {len(task_rows_closed_for_period(days=days, limit=1_000_000))}",
        f"Решения: {len(rows_by_type_for_period('decision', days=days, limit=1_000_000))}",
        f"Риски: {len(rows_by_type_for_period('risk', days=days, limit=1_000_000))}",
        f"Открытые вопросы периода: {len(questions)}",
    ]
    append_hot_task_section(lines, "Новые задачи", created_tasks)
    append_hot_task_section(lines, "Закрытые задачи", closed_tasks)
    append_handoff_section(lines, "Решения", list(reversed(decisions)), format_handoff_inbox_line)
    append_handoff_section(lines, "Риски", list(reversed(risks)), format_handoff_inbox_line)
    append_handoff_section(lines, "Открытые вопросы периода", list(reversed(questions)), format_handoff_inbox_line)
    lines.extend(["", "Дальше: /standup, /focus, /handoff."])
    return "\n".join(lines)


def format_handoff_inbox_line(row: dict[str, Any]) -> str:
    row_id = str(row.get("id") or "")[:8]
    direction = str(row.get("direction") or "без направления")
    text = compact_text(str(row.get("text") or ""), 120)
    return f"- {escape_html(row_id)} / {escape_html(direction)}: {escape_html(text)}"


def append_handoff_section(lines: list[str], title: str, rows: list[dict[str, Any]], formatter: Any, empty: str = "пусто") -> None:
    lines.append("")
    lines.append(f"<b>{escape_html(title)}</b>")
    if not rows:
        lines.append(empty)
        return
    for row in rows:
        lines.append(formatter(row))


def build_handoff_message(days: int = 7) -> str:
    days = max(1, min(90, int(days)))
    today = dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    open_rows = task_rows_for_keyboard(statuses={"open", "pending", "in_progress", "waiting"}, limit=5)
    waiting_rows = waiting_task_rows(limit=5)
    risk_rows = rows_by_type_for_period("risk", days=days, limit=5)
    question_rows = rows_by_type_for_period("question", days=days, limit=5)
    decision_rows = rows_by_type_for_period("decision", days=days, limit=5)
    rules = rule_rows(limit=5)
    lines = [
        f"<b>Передача контекста за {days} дн.</b>",
        f"Период: {escape_html(start.isoformat())} - {escape_html(today.isoformat())}",
        f"Открытые задачи: {len([row for row in read_inbox(limit=1_000_000) if row.get('type') == 'task' and is_open_task_status(row.get('status'))])}",
        f"Ожидания: {len(waiting_task_rows(limit=1_000_000))}",
        f"Правила проекта: {len(rule_rows(limit=1_000_000))}",
        f"Риски за период: {len(rows_by_type_for_period('risk', days=days, limit=1_000_000))}",
        f"Вопросы за период: {len(rows_by_type_for_period('question', days=days, limit=1_000_000))}",
        f"Решения за период: {len(rows_by_type_for_period('decision', days=days, limit=1_000_000))}",
    ]
    append_handoff_section(lines, "Открытые задачи", open_rows, format_task_list_line)
    append_handoff_section(lines, "Ожидания", waiting_rows, format_task_list_line)
    append_handoff_section(lines, "Правила проекта", list(reversed(rules)), format_handoff_inbox_line)
    append_handoff_section(lines, "Риски", list(reversed(risk_rows)), format_handoff_inbox_line)
    append_handoff_section(lines, "Вопросы", list(reversed(question_rows)), format_handoff_inbox_line)
    append_handoff_section(lines, "Решения", list(reversed(decision_rows)), format_handoff_inbox_line)
    lines.extend(["", trim_message_block(build_period_log_message(days=days, limit=5), 1000)])
    return "\n".join(lines)


def classify_inbox_text(text: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    normalized = normalize_text(text)
    if not normalized and meta.get("kind") != "photo":
        return None
    item_type = "note"
    if meta.get("kind") == "photo":
        item_type = "defect"
    elif any(marker in normalized for marker in ["брак", "списание", "сломал", "сломали", "увял", "увяли", "битый", "битая"]):
        item_type = "defect"
    elif normalized.startswith(("/risk", "/blocker", "risk ", "risks ", "риск", "риски ", "blocker ", "blockers ", "блокер", "блокеры ")):
        item_type = "risk"
    elif normalized.startswith(("/question", "question ", "questions ", "вопрос", "вопросы ")):
        item_type = "question"
    elif is_waiting_segment(normalized):
        item_type = "task"
    elif any(marker in normalized for marker in ["задача", "надо", "нужно", "сделать", "проверь", "проверить", "добавь", "поправь"]):
        item_type = "task"
    elif normalized.startswith(("/decision", "/decide", "decision ", "решение", "решили ", "договорились ", "зафиксировали ")):
        item_type = "decision"
    elif normalized.startswith(("/rule", "/remember_rule", "rule ", "правило", "запомни правило ")):
        item_type = "rule"
    elif any(marker in normalized for marker in ["идея", "заметка", "запиши", "фиксирую"]):
        item_type = "note"
    elif meta.get("kind") == "caption" or meta.get("group_capture"):
        item_type = "note"
    else:
        return None

    item = {
        "type": item_type,
        "due_date": detect_due_date(normalized),
        "priority": detect_priority(normalized),
        "assignee": detect_assignee(text),
        "direction": detect_direction(normalized),
        "date": detect_date(normalized),
        "amount": detect_amount(normalized),
        "qty": detect_qty(normalized),
        "unit": detect_unit(normalized),
    }
    if is_waiting_segment(normalized):
        item.update({
            "status": "waiting",
            "waiting_reason": strip_waiting_segment_prefix(text),
        })
    return item


def detect_direction(normalized: str) -> str:
    for marker, direction in DIRECTIONS.items():
        if marker in normalized:
            return direction
    return ""


def detect_date(normalized: str) -> str:
    today = dt.date.today()
    if "сегодня" in normalized:
        return today.isoformat()
    if "вчера" in normalized:
        return (today - dt.timedelta(days=1)).isoformat()
    match = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", normalized)
    if not match:
        return ""
    day = int(match.group(1))
    month = int(match.group(2))
    year_raw = match.group(3)
    year = today.year if not year_raw else int(year_raw)
    if year < 100:
        year += 2000
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def detect_due_date(normalized: str) -> str:
    today = dt.date.today()
    if any(marker in normalized for marker in ["до сегодня", "на сегодня", "сегодня"]):
        return today.isoformat()
    if any(marker in normalized for marker in ["до завтра", "на завтра", "завтра"]):
        return (today + dt.timedelta(days=1)).isoformat()
    if any(marker in normalized for marker in ["до послезавтра", "послезавтра"]):
        return (today + dt.timedelta(days=2)).isoformat()
    match = re.search(r"\b(?:до|к|на)\s+(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", normalized)
    if not match:
        return ""
    day = int(match.group(1))
    month = int(match.group(2))
    year_raw = match.group(3)
    year = today.year if not year_raw else int(year_raw)
    if year < 100:
        year += 2000
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def detect_priority(normalized: str) -> str:
    flag_priority = detect_priority_flag(normalized)
    if flag_priority:
        return flag_priority
    if any(marker in normalized for marker in ["срочно", "критично", "горит", "асап", "asap"]):
        return "high"
    if any(marker in normalized for marker in ["важно", "приоритет"]):
        return "medium"
    return ""


def detect_assignee(text: str) -> str:
    match = re.search(r"\b(?:для|ответственный|ответственная|на)\s+([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё._-]{1,40})", text)
    if match:
        return match.group(1).strip(" .,_-")
    return ""


def detect_amount(normalized: str) -> int | None:
    match = re.search(r"\b(\d[\d\s.,]{2,})\s*(?:uzs|сум|so'?m|сом|млн|тыс)\b", normalized)
    if not match:
        return None
    number_text = match.group(1).replace(" ", "").replace(",", ".")
    try:
        value = float(number_text)
    except ValueError:
        return None
    tail = normalized[match.start(): match.end()]
    if "млн" in tail:
        value *= 1_000_000
    elif "тыс" in tail:
        value *= 1_000
    return int(round(value))


def detect_qty(normalized: str) -> int | None:
    match = re.search(r"\b(\d{1,6})\s*(?:шт|штук|стеб|стебля|стеблей|букет|букета|букетов|позици)\w*", normalized)
    if not match:
        return None
    return int(match.group(1))


def detect_unit(normalized: str) -> str:
    if any(marker in normalized for marker in ["стеб", "стебля", "стеблей"]):
        return "стеб"
    if any(marker in normalized for marker in ["букет", "букета", "букетов"]):
        return "букет"
    if "позици" in normalized:
        return "позиция"
    if any(marker in normalized for marker in ["шт", "штук"]):
        return "шт"
    return ""


def build_saved_inbox_message(item: dict[str, Any]) -> str:
    parts = [str(item.get("type") or "note")]
    if item.get("direction"):
        parts.append(str(item["direction"]))
    if item.get("due_date"):
        parts.append(f"до {item['due_date']}")
    if item.get("priority"):
        parts.append(str(item["priority"]))
    if item.get("assignee"):
        parts.append(f"отв. {item['assignee']}")
    if item.get("date"):
        parts.append(str(item["date"]))
    if item.get("amount"):
        parts.append(f"{item['amount']} UZS")
    if item.get("qty"):
        parts.append(f"{item['qty']} {item.get('unit') or 'шт'}")
    media_line = f"\nФайл: {escape_html(str(item.get('media_local_path')))}" if item.get("media_local_path") else ""
    return "\n".join([
        "<b>Записала в журнал</b>",
        escape_html(" / ".join(parts)),
        "",
        escape_html(str(item.get("text") or "")) + media_line,
    ])


def telegram_file_path(config: AgentConfig, file_id: str) -> str:
    result = telegram_request(config, "getFile", {"file_id": file_id})
    file_path = result.get("result", {}).get("file_path")
    if not file_path:
        raise RuntimeError("Telegram did not return file_path")
    return str(file_path)


def file_download_url(config: AgentConfig, file_id: str) -> str:
    file_path = telegram_file_path(config, file_id)
    return f"https://api.telegram.org/file/bot{token(config)}/{file_path}"


def download_telegram_file(config: AgentConfig, file_id: str, target: Path) -> None:
    url = file_download_url(config, file_id)
    with urllib.request.urlopen(url, timeout=120) as response:
        size = int(response.headers.get("content-length") or 0)
        if size and size > config.max_download_mb * 1024 * 1024:
            raise RuntimeError(f"Telegram file is too large: {size} bytes")
        target.write_bytes(response.read())


def store_telegram_file(config: AgentConfig, file_id: str, prefix: str, message_id: int | None = None) -> str:
    if not config.media_store_enabled or not file_id:
        return ""
    file_path = telegram_file_path(config, file_id)
    suffix = Path(file_path).suffix or ".bin"
    day_dir = config.media_store_dir / dt.date.today().isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", prefix).strip("-") or "media"
    stem = f"{safe_prefix}-{message_id or int(time.time())}-{file_id[:10]}"
    target = day_dir / f"{stem}{suffix}"
    download_telegram_file(config, file_id, target)
    try:
        return str(target.relative_to(ROOT))
    except ValueError:
        return str(target)


def run_transcribe_command(command: str, audio_path: Path, text_path: Path) -> str:
    rendered = command.format(input=shlex.quote(str(audio_path)), output=shlex.quote(str(text_path)))
    completed = subprocess.run(rendered, shell=True, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if completed.returncode != 0:
        raise RuntimeError(f"transcribe command failed: {completed.stderr.strip() or completed.stdout.strip()}")
    if text_path.exists() and text_path.read_text(encoding="utf-8").strip():
        return text_path.read_text(encoding="utf-8").strip()
    return completed.stdout.strip()


def transcription_language(text_path: Path, fallback: str = "") -> str:
    meta_path = text_path.with_suffix(text_path.suffix + ".meta.json")
    if meta_path.exists():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            language = str(payload.get("language") or "").strip().lower()
            if language:
                return language
        except (OSError, ValueError, TypeError):
            pass
    return "unknown" if fallback.lower() in {"", "auto", "detect"} else fallback.lower()


def transcribe_with_whisper(config: AgentConfig, audio_path: Path, work_dir: Path) -> str:
    whisper = shutil.which("whisper")
    if not whisper:
        raise RuntimeError("No local transcription command. Set TG_VOICE_TRANSCRIBE_COMMAND or install whisper CLI.")
    args = [whisper, str(audio_path)]
    if config.voice_language.lower() not in {"", "auto", "detect"}:
        args.extend(["--language", config.voice_language])
    args.extend([
            "--model", config.voice_model,
            "--output_format", "txt",
            "--output_dir", str(work_dir),
        ])
    completed = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1200,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"whisper failed: {completed.stderr.strip() or completed.stdout.strip()}")
    candidates = sorted(work_dir.glob("*.txt"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("whisper did not produce a transcript")
    return candidates[0].read_text(encoding="utf-8").strip()


def transcribe_voice(config: AgentConfig, message: dict[str, Any]) -> tuple[str, str, str]:
    if not config.voice_enabled:
        raise RuntimeError("Voice processing is disabled")
    voice = message.get("voice") or message.get("audio") or {}
    duration = int(voice.get("duration") or 0)
    if duration > config.voice_max_seconds:
        raise RuntimeError(f"Voice is too long: {duration}s > {config.voice_max_seconds}s")
    file_id = voice.get("file_id")
    if not file_id:
        raise RuntimeError("No Telegram file_id in voice/audio message")
    with tempfile.TemporaryDirectory(prefix="tg-bot-agent-voice-") as tmp:
        work_dir = Path(tmp)
        audio_path = work_dir / "voice.oga"
        text_path = work_dir / "voice.txt"
        download_telegram_file(config, str(file_id), audio_path)
        media_local_path = store_local_media_file(config, audio_path, str(file_id), "voice", message.get("message_id"))
        if config.voice_command:
            transcript = run_transcribe_command(config.voice_command, audio_path, text_path)
            return transcript, media_local_path, transcription_language(text_path, config.voice_language)
        return transcribe_with_whisper(config, audio_path, work_dir), media_local_path, config.voice_language


def store_local_media_file(config: AgentConfig, source: Path, file_id: str, prefix: str, message_id: int | None = None) -> str:
    if not config.media_store_enabled or not source.exists():
        return ""
    day_dir = config.media_store_dir / dt.date.today().isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or ".bin"
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", prefix).strip("-") or "media"
    file_token = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(file_id).stem).strip("-")[:24] or "file"
    target = day_dir / f"{safe_prefix}-{message_id or int(time.time())}-{file_token}{suffix}"
    shutil.copy2(source, target)
    try:
        return str(target.relative_to(ROOT))
    except ValueError:
        return str(target)


def local_media_path(media_path: str) -> Path:
    path = Path(media_path)
    if path.is_absolute():
        return path
    return ROOT / path


def run_local_ocr(config: AgentConfig, media_path: str) -> tuple[str, str]:
    if not config.ocr_enabled:
        return "", "disabled"
    if not config.ocr_command.strip():
        return "", "command_missing"
    if not media_path:
        return "", "file_missing"
    path = local_media_path(media_path)
    if not path.exists():
        return "", "file_missing"
    max_bytes = max(config.ocr_max_mb, 1) * 1024 * 1024
    if path.stat().st_size > max_bytes:
        return "", "file_too_large"
    if not ocr_command_available(config):
        return "", "command_unavailable"
    command = os.path.expandvars(os.path.expanduser(config.ocr_command))
    if "{input}" in command:
        command = command.replace("{input}", str(path))
        args = shlex.split(command)
    else:
        args = shlex.split(command) + [str(path)]
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(config.ocr_timeout_seconds, 1),
        )
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except Exception as exc:
        return "", f"error:{exc}"
    text = (result.stdout or "").strip()
    if result.returncode != 0 and not text:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[0] if detail else f"exit_{result.returncode}"
        return "", f"error:{reason[:120]}"
    return text, "ok" if text else "empty"


def simulate_voice_file(
    config: AgentConfig,
    audio_path: Path,
    transcript: str = "",
    chat_id: str | int = "simulate",
    message_id: int = 1,
) -> dict[str, Any]:
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)
    media_local_path = store_local_media_file(config, audio_path, audio_path.name, "voice", message_id)
    detected_language = "provided"
    if not transcript:
        with tempfile.TemporaryDirectory(prefix="tg-bot-agent-sim-voice-") as tmp:
            text_path = Path(tmp) / "voice.txt"
            if config.voice_command:
                transcript = run_transcribe_command(config.voice_command, audio_path, text_path)
                detected_language = transcription_language(text_path, config.voice_language)
            else:
                transcript = transcribe_with_whisper(config, audio_path, Path(tmp))
                detected_language = config.voice_language
    meta = {
        "kind": "voice",
        "transcript": transcript,
        "transcript_language": detected_language,
        "media_file_id": audio_path.name,
        "media_local_path": media_local_path,
    }
    result = process_text_command(config, transcript, meta, chat_id=chat_id, message_id=message_id)
    result["transcript"] = transcript
    result["transcript_language"] = detected_language
    result["media_local_path"] = media_local_path
    return result


def process_text_command_dry_run(
    config: AgentConfig,
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "preview",
    message_id: int | None = None,
) -> dict[str, Any]:
    global INBOX_FILE, REMINDERS_FILE, BRAIN_HISTORY_FILE, LOG_DIR
    old_paths = {
        "inbox": INBOX_FILE,
        "reminders": REMINDERS_FILE,
        "brain_history": BRAIN_HISTORY_FILE,
        "log_dir": LOG_DIR,
    }
    with tempfile.TemporaryDirectory(prefix="tg-bot-agent-command-dry-run-") as tmp:
        tmp_path = Path(tmp)
        INBOX_FILE = tmp_path / "inbox.jsonl"
        REMINDERS_FILE = tmp_path / "reminders.jsonl"
        BRAIN_HISTORY_FILE = tmp_path / "brain_history.jsonl"
        LOG_DIR = tmp_path / "logs"
        dry_config = dataclasses.replace(
            config,
            media_store_dir=tmp_path / "media",
            sheets_sync_on_save=False,
        )
        try:
            result = process_text_command(dry_config, text, meta, chat_id=chat_id, message_id=message_id)
            result["dry_run"] = True
            return result
        finally:
            INBOX_FILE = old_paths["inbox"]
            REMINDERS_FILE = old_paths["reminders"]
            BRAIN_HISTORY_FILE = old_paths["brain_history"]
            LOG_DIR = old_paths["log_dir"]


def simulate_batch_file(
    config: AgentConfig,
    batch_path: Path,
    chat_id: str | int = "simulate",
    start_message_id: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    global INBOX_FILE, REMINDERS_FILE, BRAIN_HISTORY_FILE, LOG_DIR
    if dry_run:
        old_paths = {
            "inbox": INBOX_FILE,
            "reminders": REMINDERS_FILE,
            "brain_history": BRAIN_HISTORY_FILE,
            "log_dir": LOG_DIR,
        }
        with tempfile.TemporaryDirectory(prefix="tg-bot-agent-batch-dry-run-") as tmp:
            tmp_path = Path(tmp)
            INBOX_FILE = tmp_path / "inbox.jsonl"
            REMINDERS_FILE = tmp_path / "reminders.jsonl"
            BRAIN_HISTORY_FILE = tmp_path / "brain_history.jsonl"
            LOG_DIR = tmp_path / "logs"
            dry_config = dataclasses.replace(
                config,
                media_store_dir=tmp_path / "media",
                sheets_sync_on_save=False,
            )
            try:
                result = simulate_batch_file(
                    dry_config,
                    batch_path,
                    chat_id=chat_id,
                    start_message_id=start_message_id,
                    dry_run=False,
                )
                result["dry_run"] = True
                return result
            finally:
                INBOX_FILE = old_paths["inbox"]
                REMINDERS_FILE = old_paths["reminders"]
                BRAIN_HISTORY_FILE = old_paths["brain_history"]
                LOG_DIR = old_paths["log_dir"]

    results: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for line_no, raw_line in enumerate(batch_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        message_id = int(record.get("message_id") or (start_message_id + len(results)))
        if record.get("audio"):
            result = simulate_voice_file(
                config,
                Path(str(record["audio"])).expanduser(),
                transcript=str(record.get("transcript") or ""),
                chat_id=record.get("chat_id") or chat_id,
                message_id=message_id,
            )
        else:
            kind = str(record.get("kind") or "text")
            text = str(record.get("text") or "")
            meta: dict[str, Any] = {"kind": kind}
            if record.get("media"):
                media_path = Path(str(record["media"])).expanduser()
                media_local_path = store_local_media_file(config, media_path, media_path.name, kind, message_id)
                meta.update({"media_file_id": media_path.name, "media_local_path": media_local_path})
            if kind == "voice":
                meta["transcript"] = text
            result = process_text_command(
                config,
                text,
                meta,
                chat_id=record.get("chat_id") or chat_id,
                message_id=message_id,
                allow_telegram_side_effects=False,
            )
        digest_id = str(result.get("digest_id") or "")
        counts[digest_id] = counts.get(digest_id, 0) + 1
        results.append({
            "line": line_no,
            "message_id": message_id,
            "digest_id": digest_id,
            "inbox_id": (result.get("inbox_item") or {}).get("id", ""),
            "answer": result.get("answer", ""),
        })
    return {"ok": True, "processed": len(results), "counts": counts, "results": results}


def strip_html_tags(text: str) -> str:
    text = re.sub(r"</?(?:b|i|code|pre)>", "", text or "")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


def build_batch_markdown_report(result: dict[str, Any], source: Path | None = None) -> str:
    lines = [f"# {BOT_DISPLAY_NAME} batch dry-run"]
    if source:
        lines.append(f"source: `{source}`")
    lines.append(f"dry_run: `{bool(result.get('dry_run'))}`")
    lines.append(f"processed: `{int(result.get('processed') or 0)}`")
    counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
    if counts:
        counts_line = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        lines.append(f"counts: `{counts_line}`")
    lines.extend(["", "## Results"])
    for item in result.get("results") or []:
        line_no = item.get("line")
        message_id = item.get("message_id")
        digest_id = item.get("digest_id") or "UNKNOWN"
        inbox_id = str(item.get("inbox_id") or "")
        title = f"- line {line_no}, message {message_id}: `{digest_id}`"
        if inbox_id:
            title += f" / `{inbox_id[:8]}`"
        lines.append(title)
        answer = strip_html_tags(str(item.get("answer") or "")).strip()
        if answer:
            preview = "\n".join(answer.splitlines()[:4])
            lines.append("  ```text")
            lines.append(preview[:900])
            lines.append("  ```")
    if not result.get("results"):
        lines.append("- no records")
    return "\n".join(lines) + "\n"


def text_from_message(config: AgentConfig, message: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if message.get("text"):
        return str(message.get("text") or ""), {"kind": "text"}
    if message.get("caption"):
        media_file_id = best_media_file_id(message)
        media_local_path = store_telegram_file(config, media_file_id, "caption-media", message.get("message_id")) if media_file_id else ""
        return str(message.get("caption") or ""), {"kind": "caption", "media_file_id": media_file_id, "media_local_path": media_local_path}
    if message.get("voice") or message.get("audio"):
        transcript, media_local_path, detected_language = transcribe_voice(config, message)
        voice = message.get("voice") or message.get("audio") or {}
        return transcript, {"kind": "voice", "transcript": transcript, "transcript_language": detected_language, "media_file_id": voice.get("file_id"), "media_local_path": media_local_path}
    if message.get("photo"):
        media_file_id = best_media_file_id(message)
        media_local_path = store_telegram_file(config, media_file_id, "photo", message.get("message_id")) if media_file_id else ""
        ocr_text, ocr_status = run_local_ocr(config, media_local_path)
        meta = {"kind": "photo", "note": "photo_without_caption", "media_file_id": media_file_id, "media_local_path": media_local_path, "ocr_status": ocr_status}
        if ocr_text:
            meta.update({"note": "photo_ocr", "ocr_text": ocr_text})
            return ocr_text, meta
        return "брак", meta
    if message.get("document"):
        document = message.get("document") or {}
        media_file_id = str(document.get("file_id") or "")
        media_local_path = store_telegram_file(config, media_file_id, "document", message.get("message_id")) if media_file_id else ""
        ocr_text, ocr_status = run_local_ocr(config, media_local_path)
        meta = {"kind": "document", "note": "document_received", "media_file_id": media_file_id, "media_local_path": media_local_path, "ocr_status": ocr_status}
        if ocr_text:
            meta.update({"note": "document_ocr", "ocr_text": ocr_text})
            return ocr_text, meta
        return "источники", meta
    return "", {"kind": "unsupported"}


def best_media_file_id(message: dict[str, Any]) -> str:
    photos = message.get("photo") or []
    if photos:
        return str(photos[-1].get("file_id") or "")
    document = message.get("document") or {}
    return str(document.get("file_id") or "")


GROUP_CAPTURE_TRIGGER_PATTERNS = (
    r"\bважн(?:о|ое|ая|ый|ые|ую|ого|ому|ым|ых)?\b",
    r"\bсрочн(?:о|ое|ая|ый|ые|ую|ого|ому|ым|ых)?\b",
    r"\bзадач(?:а|у|и|ей|е|ами|ах)?\b",
    r"\bрешен(?:ие|ия|ию|ием|ии|ий|иями|иях)?\b",
    r"\bдоговорил(?:ись|ся|ась)?\b",
    r"\bзафиксировал(?:и|а|о)?\b",
    r"\bриск(?:и|а|у|ом|е|ов|ами|ах)?\b",
    r"\bвопрос(?:ы|а|у|ом|е|ов|ами|ах)?\b",
    r"\bнужно\b",
    r"\b(?:проверь|проверить)\b",
    r"\bвсем нужно прочитать\b",
)
GROUP_CAPTURE_MENTIONS = ("@tggfathullo",)
GROUP_CAPTURE_AUTHORS = ("mukhrima4",)


def is_group_chat(chat: dict[str, Any]) -> bool:
    return str(chat.get("type") or "").lower() in {"group", "supergroup", "channel"}


def group_capture_reason(text: str, user: dict[str, Any]) -> str:
    normalized = normalize_text(text)
    if normalized.startswith("/"):
        return "command"
    lowered = str(text or "").casefold()
    for mention in GROUP_CAPTURE_MENTIONS:
        if mention.casefold() in lowered:
            return "mention:tggfathullo"
    username = str(user.get("username") or "").strip().lstrip("@").casefold()
    if username in GROUP_CAPTURE_AUTHORS:
        return "author:mukhrima4"
    for pattern in GROUP_CAPTURE_TRIGGER_PATTERNS:
        if re.search(pattern, normalized):
            return f"trigger:{pattern}"
    return ""


def telegram_reply_context(message: dict[str, Any]) -> dict[str, Any]:
    reply = message.get("reply_to_message") or {}
    if not reply:
        return {}
    sender = reply.get("from") or {}
    reply_text = str(reply.get("text") or reply.get("caption") or "").strip()
    sender_username = str(sender.get("username") or "").strip()
    sender_name = " ".join(
        part for part in [
            str(sender.get("first_name") or "").strip(),
            str(sender.get("last_name") or "").strip(),
        ]
        if part
    )
    return {
        "reply_to_message_id": reply.get("message_id") or "",
        "reply_to_text": reply_text,
        "reply_to_from_is_bot": bool(sender.get("is_bot")),
        "reply_to_from_username": sender_username,
        "reply_to_from_name": sender_name,
        "force_brain_dialog": bool(sender.get("is_bot")),
    }


def handle_update(config: AgentConfig, update: dict[str, Any]) -> None:
    business_connection = update.get("business_connection") or {}
    if business_connection:
        handle_business_connection(config, business_connection)
        return
    business_message = update.get("business_message") or {}
    if business_message:
        handle_business_message(config, business_message, event="business_message")
        return
    edited_business_message = update.get("edited_business_message") or {}
    if edited_business_message:
        handle_business_message(config, edited_business_message, event="edited_business_message")
        return
    deleted_business_messages = update.get("deleted_business_messages") or {}
    if deleted_business_messages:
        handle_deleted_business_messages(config, deleted_business_messages)
        return
    callback = update.get("callback_query") or {}
    if callback:
        handle_callback_query(config, callback)
        return
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if not chat_id:
        return
    user = message.get("from") or {}
    user_id = user.get("id")
    try:
        text, meta = text_from_message(config, message)
        if not is_authorized(config, chat_id=chat_id, user_id=user_id) and not is_public_access_command(text):
            append_log("unauthorized_message", {"chat_id": chat_id, "user_id": user_id, "message_id": message_id, "text": text})
            if is_group_chat(chat):
                return
            telegram_send(config, chat_id, f"Нет доступа к {BOT_DISPLAY_NAME}. Для подключения отправьте /whoami и перешлите ответ администратору.")
            return
        reply_context = telegram_reply_context(message)
        capture_reason = group_capture_reason(text, user) if is_group_chat(chat) else ""
        if is_group_chat(chat) and reply_context.get("force_brain_dialog"):
            capture_reason = "reply_to_bot"
        if is_group_chat(chat):
            append_group_message({
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
                "chat_id": chat_id,
                "chat_title": chat.get("title") or "",
                "chat_username": chat.get("username") or "",
                "message_id": message_id,
                "user_id": user_id,
                "username": user.get("username") or "",
                "first_name": user.get("first_name") or "",
                "last_name": user.get("last_name") or "",
                "text": text,
                "kind": meta.get("kind") or "",
                "capture_reason": capture_reason,
            })
        if is_group_chat(chat) and not capture_reason:
            append_log("ignored_group_message", {
                "chat_id": chat_id,
                "message_id": message_id,
                "user_id": user_id,
                "username": user.get("username") or "",
            })
            return
        if not is_group_chat(chat):
            telegram_react(config, chat_id, message_id, "👀")
            telegram_action(config, chat_id, "typing")
        meta.update({
            "user_id": user_id,
            "username": user.get("username") or "",
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "group_capture": bool(capture_reason and capture_reason != "command"),
            "group_capture_reason": capture_reason,
            "source_chat_id": chat_id,
            "source_chat_title": chat.get("title") or "",
        })
        meta.update(reply_context)
        result = process_text_command(config, text, meta, chat_id=chat_id, message_id=message_id, allow_telegram_side_effects=True)
        answer = result["answer"]
        digest_id = result["digest_id"]
        if meta.get("kind") == "voice":
            answer = f"<b>Расшифровка:</b> {escape_html(str(meta['transcript']))}\n\n{answer}"
        answer = localize_answer_for_text(answer, text)
        if result.get("send_answer", True) and not (is_group_chat(chat) and capture_reason != "command"):
            telegram_send(config, chat_id, answer, reply_markup=result.get("reply_markup"))
        if result.get("photo_path") and not (is_group_chat(chat) and capture_reason != "command"):
            telegram_send_photo(config, chat_id, Path(str(result["photo_path"])), str(result.get("photo_caption") or ""))
        if result.get("document_path") and not (is_group_chat(chat) and capture_reason != "command"):
            telegram_send_document(config, chat_id, Path(str(result["document_path"])), str(result.get("document_caption") or ""))
        for extra_message in result.get("extra_messages") or []:
            telegram_send(config, chat_id, str(extra_message))
        if result.get("send_answer", True) and not (is_group_chat(chat) and capture_reason != "command"):
            if voice_reply_enabled(chat_id) or wants_one_time_voice_reply(text):
                try:
                    voice_language = detect_voice_language(f"{text}\n{answer}", chat_id)
                    voice_path = render_voice_reply(answer, message_id=message_id, language=voice_language)
                    telegram_send_voice(config, chat_id, voice_path)
                except Exception as exc:
                    append_log("voice_reply_error", {"chat_id": chat_id, "message_id": message_id, "error": str(exc)})
                    telegram_send(config, chat_id, "Не смогла отправить голосовой дубль: " + escape_html(str(exc)))
        if not is_group_chat(chat):
            telegram_react(config, chat_id, message_id, "✅")
        append_log("handled_update", {
            "chat_id": chat_id,
            "message_id": message_id,
            "digest_id": digest_id,
            "text": redact_sensitive_text(text) if digest_id == "ACCESS-MESSAGE" else text,
            "meta": meta,
        })
    except Exception as exc:
        if not is_group_chat(chat):
            telegram_react(config, chat_id, message_id, "❌")
            telegram_send(config, chat_id, f"Не смог обработать сообщение: {escape_html(str(exc))}")
        append_log("handle_error", {"chat_id": chat_id, "message_id": message_id, "error": str(exc)})



def handle_business_connection(config: AgentConfig, connection: dict[str, Any]) -> None:
    user = connection.get("user") or {}
    owner_id = str(user.get("id") or "")
    connection_id = str(connection.get("id") or "")
    allowed_users = configured_allowed_user_ids(config)
    is_allowed = bool(connection_id) and (not allowed_users or owner_id in allowed_users)
    rights = connection.get("rights") or {}
    row = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "business_connection_id": connection_id,
        "owner_user_id": owner_id,
        "owner_username": user.get("username") or "",
        "owner_first_name": user.get("first_name") or "",
        "owner_last_name": user.get("last_name") or "",
        "is_enabled": bool(connection.get("is_enabled")),
        "can_reply": bool(rights.get("can_reply")),
        "can_read_messages": bool(rights.get("can_read_messages")),
        "is_allowed": is_allowed,
    }
    append_business_connection(row)
    append_log("business_connection", row)
    if not is_allowed:
        append_log("business_connection_ignored", row)


def handle_business_message(config: AgentConfig, message: dict[str, Any], event: str = "business_message") -> None:
    connection_id = str(message.get("business_connection_id") or "")
    if not business_connection_allowed(config, connection_id):
        append_log("business_message_ignored", {
            "business_connection_id": connection_id,
            "message_id": message.get("message_id") or "",
            "event": event,
        })
        return
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    try:
        text, meta = text_from_message(config, message)
    except Exception as exc:
        append_log("business_message_text_error", {
            "business_connection_id": connection_id,
            "message_id": message.get("message_id") or "",
            "error": str(exc),
        })
        text, meta = "", {"kind": "unknown"}
    row = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "business_connection_id": connection_id,
        "chat_id": chat.get("id") or "",
        "chat_title": chat.get("title") or " ".join(part for part in [chat.get("first_name"), chat.get("last_name")] if part),
        "chat_username": chat.get("username") or "",
        "message_id": message.get("message_id") or "",
        "user_id": user.get("id") or "",
        "username": user.get("username") or "",
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
        "text": text,
        "kind": meta.get("kind") or "",
        "source": "telegram_business",
    }
    append_business_message(row)
    append_log("business_message_saved", {
        "business_connection_id": connection_id,
        "chat_id": row["chat_id"],
        "message_id": row["message_id"],
        "event": event,
        "kind": row["kind"],
    })


def handle_deleted_business_messages(config: AgentConfig, payload: dict[str, Any]) -> None:
    connection_id = str(payload.get("business_connection_id") or "")
    if not business_connection_allowed(config, connection_id):
        append_log("deleted_business_messages_ignored", {"business_connection_id": connection_id})
        return
    chat = payload.get("chat") or {}
    message_ids = payload.get("message_ids") or []
    append_business_message({
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "event": "deleted_business_messages",
        "business_connection_id": connection_id,
        "chat_id": chat.get("id") or "",
        "chat_title": chat.get("title") or " ".join(part for part in [chat.get("first_name"), chat.get("last_name")] if part),
        "message_ids": message_ids,
        "source": "telegram_business",
        "text": "Удалены сообщения: " + ", ".join(str(x) for x in message_ids),
    })


def handle_callback_query(config: AgentConfig, callback: dict[str, Any]) -> None:
    callback_id = str(callback.get("id") or "")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user = callback.get("from") or {}
    actor = str(user.get("id") or chat_id or "")
    data = str(callback.get("data") or "")
    if not chat_id:
        telegram_answer_callback(config, callback_id, "Нет chat_id")
        return
    if not is_authorized(config, chat_id=chat_id, user_id=actor):
        telegram_answer_callback(config, callback_id, "Нет доступа")
        append_log("unauthorized_callback", {"chat_id": chat_id, "user_id": actor, "callback_id": callback_id, "data": data})
        return
    try:
        result = process_task_callback(config, data, actor=actor)
        telegram_answer_callback(config, callback_id, "Готово" if result.get("result", {}).get("ok") else "Не получилось")
        if result.get("result", {}).get("ok"):
            maybe_refresh_pinned_tasks(config, chat_id, True)
        telegram_send(config, chat_id, result["answer"])
        append_log("handled_callback", {"chat_id": chat_id, "callback_id": callback_id, "data": data, "digest_id": result.get("digest_id")})
    except Exception as exc:
        telegram_answer_callback(config, callback_id, "Ошибка")
        telegram_send(config, chat_id, f"Не смогла обработать кнопку: {escape_html(str(exc))}")
        append_log("callback_error", {"chat_id": chat_id, "callback_id": callback_id, "data": data, "error": str(exc)})


def process_text_command(
    config: AgentConfig,
    text: str,
    meta: dict[str, Any],
    chat_id: str | int = "",
    message_id: int | None = None,
    allow_telegram_side_effects: bool = False,
) -> dict[str, Any]:
    if meta.get("kind") == "voice":
        original_text = text
        text = normalize_transcribed_command(text)
        if text != original_text:
            meta = dict(meta)
            meta["raw_transcript"] = original_text
            meta["transcript"] = text
    if meta.get("force_brain_dialog") and config.brain_enabled:
        result = ask_brain_cli(config, build_reply_brain_text(text, meta), chat_id=chat_id, allow_actions=False)
        if any(item.get("ok") and item.get("type") in {"add_task", "update_task", "set_task_status"} for item in result.get("actions") or []):
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": "BRAIN-REPLY", "answer": build_brain_message(result), "brain_result": result}
    if should_auto_voice_digest(text, meta):
        result = save_voice_digest(config, text, meta, chat_id, message_id)
        if any(item.get("type") == "task" for item in result.get("items", [])):
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return result
    digest_id = resolve_digest_id(text)
    if digest_id == "ACCESS-MESSAGE":
        return {"digest_id": digest_id, "answer": build_access_message(text)}
    inbox_item = classify_inbox_text(text, meta)
    if inbox_item and digest_id in (None, "DEFECT-IN"):
        result = save_inbox_item(config, inbox_item, text, meta, chat_id, message_id)
        if inbox_item.get("type") == "task":
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return result

    digest_id = digest_id or "HELP"
    if digest_id == "UNDO":
        row_id, reason = extract_undo_payload(text)
        result = undo_inbox_row_by_prefix(row_id, actor=str(chat_id), reason=reason)
        if result.get("ok") and result.get("row", {}).get("type") in {"task", "task_update"}:
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": build_undo_message(result), "result": result}
    if digest_id == "UNDO-LAST":
        result = undo_last_inbox_row(chat_id=chat_id, actor=str(chat_id), reason="undo_last")
        if result.get("ok") and result.get("row", {}).get("type") in {"task", "task_update"}:
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": build_undo_message(result), "result": result}
    if digest_id == "WHOAMI":
        return {"digest_id": digest_id, "answer": build_whoami_message(chat_id, meta)}
    if digest_id == "MEETING":
        result = save_meeting_summary(config, text, meta, chat_id, message_id)
        if result.get("tasks"):
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return result
    if digest_id == "TRIAGE":
        result = save_triage_batch(config, text, meta, chat_id, message_id)
        if any(item.get("type") == "task" for item in result.get("items", [])):
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return result
    if digest_id == "VOICE-DIGEST":
        result = save_voice_digest(config, text, meta, chat_id, message_id)
        if any(item.get("type") == "task" for item in result.get("items", [])):
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return result
    if digest_id == "VOICE-REPLY":
        return {"digest_id": digest_id, "answer": build_voice_reply_message(text, chat_id)}
    if digest_id == "VOICE-LANG":
        return {"digest_id": digest_id, "answer": build_voice_lang_message(text, chat_id)}
    if digest_id == "RULE-ADD":
        rule_text = strip_rule_prefix(text)
        if not rule_text:
            return {"digest_id": digest_id, "answer": "Нужен текст правила. Пример: /rule если обновляем данные, обновляем все подвкладки."}
        item = {
            "type": "rule",
            "status": "open",
            "due_date": "",
            "priority": "",
            "assignee": "",
            "direction": detect_direction(normalize_text(rule_text)),
            "date": detect_date(normalize_text(rule_text)),
            "amount": "",
            "qty": "",
            "unit": "",
        }
        result = save_inbox_item(config, item, rule_text, meta, chat_id, message_id)
        row = result.get("inbox_item") or {}
        answer = [
            "<b>Правило записано</b>",
            f"id: <code>{escape_html(str(row.get('id') or '')[:8])}</code>",
            escape_html(compact_text(rule_text, 220)),
            "",
            "Список правил: /rules. Ошибку можно отменить: /undo " + escape_html(str(row.get("id") or "")[:8]),
        ]
        return {"digest_id": digest_id, "answer": "\n".join(answer), "inbox_item": row}
    if digest_id == "RULES":
        return {"digest_id": digest_id, "answer": build_rules_message()}
    if digest_id == "INBOX-FIND":
        return {"digest_id": digest_id, "answer": build_inbox_search_message(extract_find_query(text))}
    if digest_id == "CONTEXT":
        return {"digest_id": digest_id, "answer": build_context_message(extract_context_query(text))}
    if digest_id == "THREAD":
        return {"digest_id": digest_id, "answer": build_thread_message(extract_thread_id(text))}
    if digest_id == "SNAPSHOT":
        label = re.sub(r"^\s*(?:/snapshot|сделай\s+снапшот|сними\s+снапшот)\s*", "", text.strip(), flags=re.I).strip()
        return {"digest_id": digest_id, "answer": build_snapshot_message(create_state_snapshot(label or "telegram"))}
    if digest_id == "SNAPSHOTS":
        return {"digest_id": digest_id, "answer": build_snapshots_message()}
    if digest_id == "TODAY-LOG":
        return {"digest_id": digest_id, "answer": build_today_log_message()}
    if digest_id == "PERIOD-LOG":
        days = parse_period_log_days(text)
        return {"digest_id": digest_id, "answer": build_period_log_message(days=days)}
    if digest_id == "GROUP-IMPORTANT":
        days = parse_group_important_days(text)
        return {"digest_id": digest_id, "answer": build_group_important_message(config, days=days)}
    if digest_id == "GROUPS":
        return {"digest_id": digest_id, "answer": build_groups_message()}
    if digest_id == "SEND-GROUP":
        return {"digest_id": digest_id, "answer": build_send_group_message(config, text, allow_send=allow_telegram_side_effects)}
    if digest_id == "SCHEDULE-GROUP":
        return {"digest_id": digest_id, "answer": build_schedule_group_message(config, text, source_chat_id=chat_id)}
    if digest_id == "LMS-SUMMARY":
        return {"digest_id": digest_id, "answer": build_tg_lms_summary_message()}
    if digest_id == "CLICKUP-STATUS":
        return {"digest_id": digest_id, "answer": build_clickup_status_message()}
    if digest_id == "CLICKUP-TASKS":
        return {"digest_id": digest_id, "answer": build_clickup_tasks_message(text=text, telegram_user_id=meta.get("user_id") or chat_id)}
    if digest_id == "WASTE-SUMMARY":
        return {"digest_id": digest_id, "answer": build_waste_summary_message(text)}
    if digest_id == "WASTE-QA":
        return {"digest_id": digest_id, "answer": build_waste_answer_message(text)}
    if digest_id == "PERIOD-REVIEW":
        days = parse_days_arg(text, default=7)
        rows = task_rows_created_for_period(days=days, limit=10)
        return {
            "digest_id": digest_id,
            "answer": build_period_review_message(days=days),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "HANDOFF":
        days = parse_period_log_days(text)
        rows = task_rows_for_keyboard(statuses={"open", "pending", "in_progress", "waiting"}, limit=10)
        return {
            "digest_id": digest_id,
            "answer": build_handoff_message(days=days),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "FOCUS":
        days = parse_days_arg(text, default=7)
        rows = unique_task_rows([
            due_task_rows("overdue", limit=3),
            due_task_rows("soon", days=days, limit=3),
            waiting_task_rows(limit=3),
        ])
        return {
            "digest_id": digest_id,
            "answer": build_focus_message(days=days),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "NEXT-ACTION":
        days = parse_days_arg(text, default=7)
        rows = unique_task_rows([
            due_task_rows("overdue", limit=1),
            waiting_task_rows(limit=1),
            due_task_rows("soon", days=days, limit=1),
        ])
        return {
            "digest_id": digest_id,
            "answer": build_next_action_message(days=days),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "NEXT-FOR":
        return {"digest_id": digest_id, "answer": build_next_for_message(extract_next_for_query(text))}
    if digest_id == "STANDUP":
        days = parse_days_arg(text, default=1, min_days=1, max_days=30)
        rows = unique_task_rows([
            due_task_rows("today", limit=5),
            waiting_task_rows(limit=5),
        ])
        return {
            "digest_id": digest_id,
            "answer": build_standup_message(days=days),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "STEEL-MORNING":
        rows = unique_task_rows([
            due_task_rows("overdue", limit=5),
            due_task_rows("today", limit=5),
            due_task_rows("soon", days=3, limit=5),
            waiting_task_rows(limit=5),
        ])
        return {
            "digest_id": digest_id,
            "answer": build_steel_morning_message(config),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "STEEL-WEEKLY":
        rows = task_rows_for_keyboard(statuses={"open", "pending", "in_progress", "waiting"}, limit=10)
        return {
            "digest_id": digest_id,
            "answer": build_steel_weekly_message(config),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "DASHBOARD-QA":
        return {"digest_id": digest_id, "answer": build_dashboard_answer_message(text)}
    if digest_id == "DASHBOARD-CHART":
        return build_dashboard_chart_message(text)
    if digest_id == "HOT-TASKS":
        days = parse_days_arg(text, default=7)
        rows = unique_task_rows([
            due_task_rows("overdue", limit=5),
            due_task_rows("soon", days=days, limit=5),
            waiting_task_rows(limit=5),
            stale_task_rows(days=3, limit=5),
        ])
        return {
            "digest_id": digest_id,
            "answer": build_hot_tasks_message(days=days),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "WAITING-FOLLOWUPS":
        rows = waiting_task_rows(limit=10)
        return {
            "digest_id": digest_id,
            "answer": build_waiting_followups_message(limit=10),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "NUDGES":
        rows = waiting_task_rows(limit=8)
        return {
            "digest_id": digest_id,
            "answer": build_nudges_message(limit=8),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "OUTBOX":
        days = parse_days_arg(text, default=7, min_days=1, max_days=30)
        rows = unique_task_rows([
            waiting_task_rows(limit=8),
            due_task_rows("overdue", limit=8),
            stale_task_rows(days=days, limit=8),
        ])
        return {
            "digest_id": digest_id,
            "answer": build_outbox_message(days=days),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "ASSIGNEE-TASKS":
        assignee = extract_assignee_query(text)
        rows = assignee_task_rows(assignee, statuses={"open", "pending", "in_progress", "waiting"}, limit=20)
        return {
            "digest_id": digest_id,
            "answer": build_assignee_tasks_message(assignee, limit=20),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "OWNER-BRIEF":
        assignee = extract_owner_brief_query(text)
        days = parse_days_arg(text, default=7)
        rows = assignee_task_rows(assignee, statuses={"open", "pending", "in_progress", "waiting"}, limit=12)
        return {
            "digest_id": digest_id,
            "answer": build_owner_brief_message(assignee, days=days, limit=8),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "STALE-TASKS":
        days = parse_stale_days(text)
        rows = stale_task_rows(days=days, limit=20)
        return {
            "digest_id": digest_id,
            "answer": build_stale_tasks_message(days=days, limit=20),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "DUE-SOON-TASKS":
        days = parse_days_arg(text, default=7)
        rows = due_task_rows("soon", days=days, limit=12)
        return {
            "digest_id": digest_id,
            "answer": build_due_tasks_message("soon", days=days),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "DIRECTION-TASKS":
        direction_query = extract_direction_query(text)
        _, rows = direction_task_rows(direction_query, statuses={"open", "pending", "in_progress", "waiting"}, limit=20)
        return {
            "digest_id": digest_id,
            "answer": build_direction_tasks_message(direction_query, limit=20),
            "reply_markup": build_task_keyboard(rows),
        }
    if digest_id == "TASK-DETAIL":
        return {"digest_id": digest_id, "answer": build_task_detail_message(extract_task_detail_id(text))}
    if digest_id == "QUESTION-ANSWER":
        return answer_question(config, text, meta, chat_id, message_id)
    if digest_id == "TASK-COMMENT":
        result = add_task_comment(config, text, meta, chat_id, message_id)
        if result.get("inbox_item"):
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return result
    if digest_id == "TASK-ADD":
        result = add_task_from_command(config, text, meta, chat_id, message_id)
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return result
    if digest_id == "TASK-EDIT":
        task_id, rest = extract_edit_payload(text)
        result = update_task_by_prefix(task_id, parse_task_edit_fields(rest), actor=str(chat_id))
        answer = append_task_sheet_sync(config, result, build_task_edit_message(result))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-ASSIGN":
        task_id, assignee = extract_assign_payload(text)
        result = update_task_by_prefix(task_id, {"assignee": assignee}, actor=str(chat_id)) if assignee else {"ok": False, "error": "Нужен ответственный. Пример: /assign abc123 Мухассар"}
        answer = append_task_sheet_sync(config, result, build_task_assign_message(result, assignee))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-DUE":
        task_id, due_date = extract_due_payload(text)
        result = update_task_by_prefix(task_id, {"due_date": due_date}, actor=str(chat_id)) if due_date else {"ok": False, "error": "Не поняла дату. Пример: /due abc123 завтра или /due abc123 2026-07-10"}
        answer = append_task_sheet_sync(config, result, build_task_due_message(result, due_date))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-PRIORITY":
        task_id, priority = extract_priority_payload(text)
        result = update_task_by_prefix(task_id, {"priority": priority}, actor=str(chat_id)) if priority else {"ok": False, "error": "Не поняла приоритет. Пример: /priority abc123 high, must, want или wish"}
        answer = append_task_sheet_sync(config, result, build_task_priority_message(result, priority))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-DIRECTION":
        task_id, direction = extract_direction_payload(text)
        result = update_task_by_prefix(task_id, {"direction": direction}, actor=str(chat_id)) if direction else {"ok": False, "error": "Не поняла направление. Пример: /direction abc123 ecom или перенеси задачу abc123 в nour"}
        answer = append_task_sheet_sync(config, result, build_task_direction_message(result, direction))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-DONE":
        result = mark_task_done(extract_done_id(text), closed_by=str(chat_id))
        answer = append_task_sheet_sync(config, result, build_task_done_message(result))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-IN-PROGRESS":
        task_id = extract_task_action_id(text, ["/start_task", "/doing", "в работу", "начать", "начала", "начал"])
        result = set_task_status_by_prefix(task_id, "in_progress", actor=str(chat_id))
        answer = append_task_sheet_sync(config, result, build_task_status_message(result, "Задача в работе"))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-WAITING":
        task_id, reason = extract_waiting_payload(text)
        if not task_id:
            return {
                "digest_id": "WAITING-TASKS",
                "answer": build_task_list_message({"waiting"}, "Задачи в ожидании"),
                "reply_markup": task_keyboard_for_digest("WAITING-TASKS"),
            }
        result = set_task_status_by_prefix(task_id, "waiting", actor=str(chat_id), reason=reason)
        answer = append_task_sheet_sync(config, result, build_task_status_message(result, "Задача ждет"))
        if result.get("ok") and reason:
            comment_result = add_task_comment(config, f"/comment {task_id} {reason}", meta, chat_id, message_id)
            answer = f"{answer}\n\n{comment_result['answer']}"
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-PENDING":
        task_id = extract_task_action_id(text, ["/pending", "вернуть в открытые", "верни в открытые", "вернуть в очередь", "верни в очередь"])
        result = set_task_status_by_prefix(task_id, "open", actor=str(chat_id))
        answer = append_task_sheet_sync(config, result, build_task_status_message(result, "Задача снова открыта"))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-SNOOZE":
        task_id = extract_task_action_id(text, ["/snooze", "отложить", "отложи"])
        result = set_task_status_by_prefix(task_id, "snoozed", actor=str(chat_id), until=extract_snooze_until(text))
        answer = append_task_sheet_sync(config, result, build_task_status_message(result, "Задача отложена"))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-DROP":
        task_id = extract_task_action_id(text, ["/drop", "выбросить", "выброси", "убрать", "убери"])
        result = set_task_status_by_prefix(task_id, "dropped", actor=str(chat_id))
        answer = append_task_sheet_sync(config, result, build_task_status_message(result, "Задача убрана"))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "TASK-REOPEN":
        task_id = extract_task_action_id(text, ["/reopen", "вернуть", "верни"])
        result = set_task_status_by_prefix(task_id, "open", actor=str(chat_id))
        answer = append_task_sheet_sync(config, result, build_task_status_message(result, "Задача снова открыта"))
        maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": digest_id, "answer": answer}
    if digest_id == "REMINDER-ADD":
        return {"digest_id": digest_id, "answer": build_reminder_added_message(add_reminder(text, chat_id=chat_id))}
    if digest_id == "REMINDER-CANCEL":
        reminder_id = extract_task_action_id(text, ["/cancel_reminder", "отмени напоминание", "отменить напоминание"])
        return {"digest_id": digest_id, "answer": build_cancel_reminder_message(cancel_reminder(reminder_id, actor=str(chat_id)))}
    if digest_id == "BRAIN-RESET":
        removed = reset_brain_history(chat_id)
        return {"digest_id": digest_id, "answer": f"История brain очищена: {removed} записей."}
    if digest_id == "DEDUP":
        return {"digest_id": digest_id, "answer": build_dedup_message(apply=dedup_should_apply(text))}
    if digest_id in {"PIN-TASKS", "REFRESH-PIN"}:
        if not allow_telegram_side_effects:
            return {"digest_id": digest_id, "answer": "Закреп задач создается только в live Telegram-чате."}
        result = refresh_pinned_tasks(config, chat_id, force_create=(digest_id == "PIN-TASKS"))
        return {"digest_id": digest_id, "answer": build_pinned_tasks_result_message(result)}
    if digest_id == "UNPIN-TASKS":
        if not allow_telegram_side_effects:
            return {"digest_id": digest_id, "answer": "Закреп задач убирается только в live Telegram-чате."}
        result = unpin_pinned_tasks(config, chat_id)
        return {"digest_id": digest_id, "answer": build_pinned_tasks_result_message(result)}
    if digest_id == "INBOX-EXPORT":
        csv_path = export_inbox_csv(DATA_DIR / "tg_agent_inbox.csv")
        if allow_telegram_side_effects and chat_id:
            telegram_send_document(config, chat_id, csv_path, f"CSV-выгрузка журнала {BOT_DISPLAY_NAME}")
        return {"digest_id": digest_id, "answer": f"Выгрузила журнал: {escape_html(str(csv_path.relative_to(ROOT)))}"}
    if digest_id == "MEMORY-EXPORT":
        days = parse_period_log_days(text)
        md_path = export_memory_markdown(DATA_DIR / f"tg_agent_memory_{days}d.md", days=days)
        if allow_telegram_side_effects and chat_id:
            telegram_send_document(config, chat_id, md_path, f"Markdown-выгрузка памяти {BOT_DISPLAY_NAME} за {days} дн.")
        return {"digest_id": digest_id, "answer": f"Выгрузила память за {days} дн.: {escape_html(str(md_path.relative_to(ROOT)))}"}
    if digest_id == "INBOX-SYNC":
        return {"digest_id": digest_id, "answer": build_sheet_sync_message(sync_inbox_to_sheet(config))}
    if digest_id == "HELP" and config.brain_enabled and normalize_text(text) not in {"", "/help", "/start", "помощь", "что умеешь"}:
        result = ask_brain_cli(config, text, chat_id=chat_id)
        if any(item.get("ok") and item.get("type") in {"add_task", "update_task", "set_task_status"} for item in result.get("actions") or []):
            maybe_refresh_pinned_tasks(config, chat_id, allow_telegram_side_effects)
        return {"digest_id": "BRAIN", "answer": build_brain_message(result), "brain_result": result}
    if digest_id == "ALL-TASKS":
        chunks = build_task_list_chunks(limit=None)
        return {
            "digest_id": digest_id,
            "answer": chunks[0],
            "extra_messages": chunks[1:],
            "reply_markup": task_keyboard_for_digest(digest_id),
        }
    return {
        "digest_id": digest_id,
        "answer": build_digest_message(config, digest_id),
        "reply_markup": task_keyboard_for_digest(digest_id),
    }


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_lock_pid(path: Path = LOCK_FILE) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload.get("pid") or 0)
    except Exception:
        return 0


def acquire_run_lock(path: Path = LOCK_FILE) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
            return {"ok": True, "path": str(path), "pid": payload["pid"]}
        except FileExistsError:
            existing_pid = read_lock_pid(path)
            if existing_pid and process_is_running(existing_pid):
                return {"ok": False, "path": str(path), "pid": existing_pid, "error": "already running"}
            try:
                path.unlink()
                append_log("stale_lock_removed", {"path": str(path), "pid": existing_pid})
            except FileNotFoundError:
                continue


def release_run_lock(lock: dict[str, Any], path: Path = LOCK_FILE) -> None:
    if not lock.get("ok"):
        return
    try:
        if read_lock_pid(path) == os.getpid():
            path.unlink()
    except FileNotFoundError:
        return


def run(config: AgentConfig, once: bool = False) -> None:
    lock: dict[str, Any] = {"ok": False}
    if not once:
        lock = acquire_run_lock()
        if not lock.get("ok"):
            raise RuntimeError(f"{BOT_DISPLAY_NAME} уже запущен: pid={lock.get('pid')} lock={lock.get('path')}")
    state = load_state()
    try:
        while True:
            try:
                for update in poll_updates(config, state):
                    handle_update(config, update)
                save_state(state)
                check_due_reminders(config)
            except Exception as exc:
                append_log("loop_error", {"error": str(exc)})
                print(f"Bot loop error: {exc}", file=sys.stderr)
            if once:
                return
            time.sleep(config.sleep_seconds)
    finally:
        release_run_lock(lock)


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to tg_dashboard_agent config JSON")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Run polling bot")
    sub.add_parser("once", help="Run one polling cycle")
    sub.add_parser("reload", help="Reload local state and print diagnostics")
    sub.add_parser("doctor", help="Show launch readiness diagnostics")
    sub.add_parser("live-status", help="Show safe live launch status without printing secrets")
    sub.add_parser("brain-status", help="Show local brain CLI status")
    sub.add_parser("ocr-status", help="Show local OCR status")
    snapshot = sub.add_parser("snapshot", help="Create local state snapshot")
    snapshot.add_argument("--label", default="manual")
    sub.add_parser("list-snapshots", help="List local state snapshots")
    restore_snapshot = sub.add_parser("restore-snapshot", help="Restore local state snapshot by name")
    restore_snapshot.add_argument("name")
    sub.add_parser("set-commands", help="Register Telegram slash menu via setMyCommands")
    sub.add_parser("drop-pending-updates", help="Advance getUpdates offset to the latest Telegram update")
    sub.add_parser("discover-chat", help="Print recent chats for this bot")
    tg_check = sub.add_parser("telegram-check", help="Check Telegram token/getMe without polling")
    tg_check.add_argument("--send-health", action="store_true", help="Send /health output to TG_DASHBOARD_DRY_RUN_CHAT_ID")
    sub.add_parser("self-test", help="Run local self-tests without Telegram")
    export = sub.add_parser("export-inbox", help="Export saved inbox rows to CSV")
    export.add_argument("--out", type=Path, default=DATA_DIR / "tg_agent_inbox.csv")
    export_memory = sub.add_parser("export-memory", help="Export readable working memory to Markdown")
    export_memory.add_argument("--days", type=int, default=7)
    export_memory.add_argument("--limit", type=int, default=30)
    export_memory.add_argument("--out", type=Path, default=DATA_DIR / "tg_agent_memory_7d.md")
    sync = sub.add_parser("sync-inbox", help="Sync saved inbox rows to Google Sheets webhook")
    sync.add_argument("--dry-run", action="store_true")
    preview = sub.add_parser("preview", help="Preview command routing")
    preview.add_argument("text")
    simulate = sub.add_parser("simulate", help="Process a local fake message and write inbox rows")
    simulate.add_argument("text")
    simulate.add_argument("--kind", choices=["text", "voice", "caption", "photo"], default="text")
    simulate.add_argument("--media", type=Path, help="Optional local media file to copy into media store")
    simulate.add_argument("--chat-id", default="simulate")
    simulate.add_argument("--message-id", type=int, default=1)
    simulate_voice = sub.add_parser("simulate-voice", help="Process a local audio file through voice routing")
    simulate_voice.add_argument("audio", type=Path)
    simulate_voice.add_argument("--transcript", help="Use provided transcript instead of running local speech recognition")
    simulate_voice.add_argument("--chat-id", default="simulate")
    simulate_voice.add_argument("--message-id", type=int, default=1)
    simulate_batch = sub.add_parser("simulate-batch", help="Process JSONL messages through local routing")
    simulate_batch.add_argument("batch", type=Path)
    simulate_batch.add_argument("--chat-id", default="simulate")
    simulate_batch.add_argument("--start-message-id", type=int, default=1)
    simulate_batch.add_argument("--dry-run", action="store_true", help="Process batch through temporary inbox/media files")
    simulate_batch.add_argument("--out", type=Path, help="Optional path to write processing report JSON")
    simulate_batch.add_argument("--md-out", type=Path, help="Optional path to write readable Markdown report")
    args = parser.parse_args()

    config = load_agent_config(args.config)
    if args.cmd == "run":
        run(config, once=False)
        return 0
    if args.cmd == "once":
        run(config, once=True)
        return 0
    if args.cmd == "reload":
        print(build_reload_message(config))
        return 0
    if args.cmd == "doctor":
        print(build_doctor_message(config))
        return 0
    if args.cmd == "live-status":
        print(build_live_status_message(config))
        return 0
    if args.cmd == "brain-status":
        print(build_brain_status_message(config))
        return 0
    if args.cmd == "ocr-status":
        print(build_ocr_status_message(config))
        return 0
    if args.cmd == "snapshot":
        result = create_state_snapshot(args.label)
        print(json.dumps({"ok": True, "snapshot": result["path"].name, "path": str(result["path"])}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "list-snapshots":
        print(json.dumps({"ok": True, "snapshots": list_state_snapshots(limit=30)}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "restore-snapshot":
        result = restore_state_snapshot(args.name)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.cmd == "set-commands":
        try:
            result = telegram_set_commands(config)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        payload = {"ok": bool(result.get("ok")), "commands": len(TELEGRAM_BOT_COMMANDS)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    if args.cmd == "drop-pending-updates":
        try:
            result = drop_pending_updates(config)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    if args.cmd == "discover-chat":
        print(json.dumps(discover_chats(config), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "telegram-check":
        try:
            result = telegram_check(config, send_health=args.send_health)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    if args.cmd == "self-test":
        result = run_self_tests(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.cmd == "export-inbox":
        print(export_inbox_csv(args.out))
        return 0
    if args.cmd == "export-memory":
        print(export_memory_markdown(args.out, days=args.days, limit=args.limit))
        return 0
    if args.cmd == "sync-inbox":
        result = sync_inbox_to_sheet(config, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") or result.get("skipped") else 1
    if args.cmd == "preview":
        digest_id = resolve_digest_id(args.text)
        inbox_item = classify_inbox_text(args.text, {"kind": "text"})
        if digest_id == "INBOX-EXPORT":
            path = export_inbox_csv(DATA_DIR / "tg_agent_inbox.csv")
            print(f"CSV-выгрузка журнала: {path}")
        elif digest_id == "MEMORY-EXPORT":
            days = parse_period_log_days(args.text)
            path = export_memory_markdown(DATA_DIR / f"tg_agent_memory_{days}d.md", days=days)
            print(f"Markdown-выгрузка памяти: {path}")
        elif digest_id == "INBOX-SYNC":
            print(build_sheet_sync_message(sync_inbox_to_sheet(config, dry_run=True)))
        elif digest_id in {"MEETING", "TRIAGE", "VOICE-DIGEST", "UNDO", "UNDO-LAST"}:
            print(process_text_command_dry_run(config, args.text, {"kind": "text"}, chat_id="preview")["answer"])
        elif digest_id in {
            "WHOAMI",
            "ASSIGNEE-TASKS",
            "OWNER-BRIEF",
            "DIRECTION-TASKS",
            "PERIOD-LOG",
            "PERIOD-REVIEW",
            "HANDOFF",
            "FOCUS",
            "NEXT-ACTION",
            "STANDUP",
            "HOT-TASKS",
            "STALE-TASKS",
            "DUE-SOON-TASKS",
            "WAITING-FOLLOWUPS",
            "NUDGES",
            "QUESTION-ANSWER",
            "DASHBOARD-QA",
            "DASHBOARD-CHART",
        } or (digest_id and digest_id.startswith("TASK-") and digest_id != "TASK-ADD"):
            print(process_text_command(config, args.text, {"kind": "text"}, chat_id="preview")["answer"])
        elif inbox_item and digest_id in (None, "DEFECT-IN"):
            print(process_text_command_dry_run(config, args.text, {"kind": "text"}, chat_id="preview")["answer"])
        else:
            print(build_digest_message(config, digest_id or "HELP"))
        return 0
    if args.cmd == "simulate":
        meta: dict[str, Any] = {"kind": args.kind}
        if args.media:
            media_path = store_local_media_file(config, args.media, args.media.name, args.kind, args.message_id)
            meta.update({"media_file_id": args.media.name, "media_local_path": media_path})
        if args.kind == "voice":
            meta["transcript"] = args.text
        result = process_text_command(
            config,
            args.text,
            meta,
            chat_id=args.chat_id,
            message_id=args.message_id,
            allow_telegram_side_effects=False,
        )
        print(result["answer"])
        return 0
    if args.cmd == "simulate-voice":
        result = simulate_voice_file(
            config,
            args.audio,
            transcript=args.transcript or "",
            chat_id=args.chat_id,
            message_id=args.message_id,
        )
        print(f"<b>Расшифровка:</b> {escape_html(str(result['transcript']))}\n\n{result['answer']}")
        return 0
    if args.cmd == "simulate-batch":
        result = simulate_batch_file(
            config,
            args.batch,
            chat_id=args.chat_id,
            start_message_id=args.start_message_id,
            dry_run=args.dry_run,
        )
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(payload + "\n", encoding="utf-8")
        if args.md_out:
            args.md_out.parent.mkdir(parents=True, exist_ok=True)
            args.md_out.write_text(build_batch_markdown_report(result, source=args.batch), encoding="utf-8")
        print(payload)
        return 0 if result.get("ok") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(cli())
