#!/usr/bin/env python3
"""Build dry-run Telegram dashboard digest payloads from source-backed JSON."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_DASHBOARD_DATA_DIR = Path.home() / ".local/lib/tg-dashboard/deployed-source/data"
DASHBOARD_DATA_DIR = Path(
    os.environ.get("TG_DASHBOARD_DATA_DIR")
    or (DEFAULT_DASHBOARD_DATA_DIR if DEFAULT_DASHBOARD_DATA_DIR.exists() else DATA_DIR)
).expanduser()
DEFAULT_CONFIG = DATA_DIR / "bot_alert_config.example.json"
DEFAULT_OUT_DIR = DATA_DIR / "bot_digests"
BUSINESS_DIRECTIONS = {
    "B2B Опт",
    "Flowers",
    "Gourmet",
    "Guul",
    "Nour",
    "Plants",
    "School",
    "Wedding",
}
SUPPRESSED_DEFAULT = {"proxy", "external", "insufficient_base"}
PAGE_PAYLOAD_FILES = {
    "management": "management.json",
    "marketplace": "marketplace.json",
    "flowers": "flowers.json",
    "nour": "nour.json",
    "gourmet": "gourmet.json",
    "plants": "plants.json",
    "wedding": "wedding.json",
    "guul": "guul.json",
    "school": "school.json",
    "procurement-flowers": "procurement-flowers.json",
    "b2b": "b2b-opt.json",
    "corp": "corp.json",
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def dashboard_data_path(name: str) -> Path:
    primary = DASHBOARD_DATA_DIR / name
    if primary.exists():
        return primary
    return DATA_DIR / name


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


def money(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{round(float(value)):,.0f}".replace(",", " ")


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def get_latest_date(rows: list[dict[str, Any]]) -> str:
    dates = [str(row.get("date")) for row in rows if row.get("date")]
    if not dates:
        raise ValueError("management.daily_direction has no date values")
    return max(dates)


def rows_for_date(rows: list[dict[str, Any]], date_value: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("date") == date_value]


def sum_revenue(rows: list[dict[str, Any]]) -> float:
    return sum(float(row.get("revenue") or row.get("amount") or 0) for row in rows)


def sum_qty(rows: list[dict[str, Any]]) -> float:
    return sum(float(row.get("qty") or row.get("items") or 0) for row in rows)


def by_direction(rows: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        direction = str(row.get("direction") or "Unknown")
        if direction in BUSINESS_DIRECTIONS:
            totals[direction] += float(row.get("revenue") or row.get("amount") or 0)
    return dict(totals)


def widget_status_counts(widget_map: dict[str, Any], page: str | None = None) -> Counter:
    pages = widget_map.get("pages", {})
    selected = pages.items() if page in (None, "all") else [(page, pages.get(page, {}))]
    counts: Counter = Counter()
    for _, page_payload in selected:
        widgets = page_payload.get("widgets", {}) if isinstance(page_payload, dict) else {}
        if isinstance(widgets, dict):
            iterable = widgets.values()
        elif isinstance(widgets, list):
            iterable = widgets
        else:
            iterable = []
        for widget in iterable:
            if isinstance(widget, dict):
                counts[str(widget.get("sourceStatus") or "unmapped")] += 1
    return counts


def suppressed_widgets(
    widget_map: dict[str, Any],
    allowed: set[str],
    page: str | None = None,
    limit: int = 15,
) -> list[dict[str, str]]:
    pages = widget_map.get("pages", {})
    selected = pages.items() if page in (None, "all") else [(page, pages.get(page, {}))]
    out: list[dict[str, str]] = []
    for page_name, page_payload in selected:
        widgets = page_payload.get("widgets", {}) if isinstance(page_payload, dict) else {}
        items = widgets.items() if isinstance(widgets, dict) else enumerate(widgets if isinstance(widgets, list) else [])
        for widget_id, widget in items:
            if not isinstance(widget, dict):
                continue
            status = str(widget.get("sourceStatus") or "unmapped")
            if status in allowed:
                continue
            out.append(
                {
                    "page": str(page_name),
                    "widget_id": str(widget_id),
                    "title": str(widget.get("title") or widget_id),
                    "source_status": status,
                    "gap_reason": str(widget.get("gapReason") or ""),
                }
            )
    return out[:limit]


def source_status_summary(counts: Counter) -> str:
    order = ["real", "partial", "proxy", "external", "insufficient_base", "unmapped"]
    parts = [f"{key} {counts.get(key, 0)}" for key in order if counts.get(key, 0)]
    return ", ".join(parts) if parts else "no widgets"


def source_gate(
    digest: dict[str, Any],
    config: dict[str, Any],
    widget_map: dict[str, Any],
) -> dict[str, Any]:
    allowed = set(digest.get("source_status_allowed") or config.get("source_status_allowed") or [])
    page = digest.get("page") or "management"
    counts = widget_status_counts(widget_map, page)
    suppressed = suppressed_widgets(widget_map, allowed, page)
    hard_blockers = [item for item in suppressed if item["source_status"] in {"external", "insufficient_base", "unmapped"}]
    return {
        "ok_for_numeric_digest": not hard_blockers,
        "allowed_statuses": sorted(allowed),
        "status_counts": dict(counts),
        "status_summary": source_status_summary(counts),
        "suppressed_count": sum(count for status, count in counts.items() if status not in allowed),
        "suppressed_sample": suppressed,
    }


def latest_month_to_date(rows: list[dict[str, Any]], latest_date: str) -> list[dict[str, Any]]:
    latest = parse_date(latest_date)
    month = latest.strftime("%Y-%m")
    return [
        row
        for row in rows
        if str(row.get("month")) == month and str(row.get("date", "")) <= latest_date
    ]


def same_day_previous_year(rows: list[dict[str, Any]], latest_date: str) -> list[dict[str, Any]]:
    latest = parse_date(latest_date)
    try:
        previous = latest.replace(year=latest.year - 1).strftime("%Y-%m-%d")
    except ValueError:
        previous = latest.replace(year=latest.year - 1, day=28).strftime("%Y-%m-%d")
    return rows_for_date(rows, previous)


def previous_7day_direction_average(rows: list[dict[str, Any]], latest_date: str) -> dict[str, float]:
    latest = parse_date(latest_date)
    by_day_direction: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        raw_date = row.get("date")
        direction = str(row.get("direction") or "")
        if not raw_date or direction not in BUSINESS_DIRECTIONS:
            continue
        day = parse_date(str(raw_date))
        delta_days = (latest - day).days
        if 1 <= delta_days <= 7:
            by_day_direction[str(raw_date)][direction] += float(row.get("revenue") or row.get("amount") or 0)
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for day_values in by_day_direction.values():
        for direction, value in day_values.items():
            totals[direction] += value
            counts[direction] += 1
    return {direction: totals[direction] / counts[direction] for direction in totals if counts[direction]}


def build_management_metrics(management: dict[str, Any], date_arg: str) -> dict[str, Any]:
    daily = management.get("daily_direction") or []
    if not isinstance(daily, list):
        raise ValueError("management.daily_direction must be a list")
    latest_date = get_latest_date(daily) if date_arg == "latest" else date_arg
    day_rows = rows_for_date(daily, latest_date)
    if not day_rows:
        raise ValueError(f"management.daily_direction has no rows for {latest_date}")

    direction_totals = by_direction(day_rows)
    top_direction = max(direction_totals.items(), key=lambda item: item[1]) if direction_totals else None
    prior_avg = previous_7day_direction_average(daily, latest_date)
    risk_direction = None
    risk_delta_pct = None
    for direction, today_value in direction_totals.items():
        base = prior_avg.get(direction)
        if not base:
            continue
        delta = ((today_value - base) / base) * 100
        if risk_delta_pct is None or delta < risk_delta_pct:
            risk_delta_pct = delta
            risk_direction = direction

    month_rows = latest_month_to_date(daily, latest_date)
    latest_dt = parse_date(latest_date)
    days_in_month = calendar.monthrange(latest_dt.year, latest_dt.month)[1]
    elapsed_days = max(latest_dt.day, 1)
    month_to_date_revenue = sum_revenue(month_rows)
    forecast = month_to_date_revenue / elapsed_days * days_in_month if elapsed_days else None
    previous_year_rows = same_day_previous_year(daily, latest_date)
    yoy_base = sum_revenue(previous_year_rows)
    yoy_delta_pct = ((sum_revenue(day_rows) - yoy_base) / yoy_base * 100) if yoy_base else None

    terminal_rows = management.get("monthly_terminal_direction") or []
    latest_month = latest_dt.strftime("%Y-%m")
    terminal_totals: dict[str, float] = defaultdict(float)
    if isinstance(terminal_rows, list):
        for row in terminal_rows:
            if row.get("month") == latest_month:
                terminal_totals[str(row.get("terminal_group") or "Unknown")] += float(row.get("revenue") or 0)
    top_terminals = sorted(terminal_totals.items(), key=lambda item: item[1], reverse=True)[:3]

    return {
        "date": latest_date,
        "dashboard_generated_at": management.get("generated_at"),
        "revenue_today": sum_revenue(day_rows),
        "direction_totals": direction_totals,
        "top_direction": {"name": top_direction[0], "revenue": top_direction[1]} if top_direction else None,
        "risk_direction": {
            "name": risk_direction,
            "delta_pct_vs_7d_avg": risk_delta_pct,
        }
        if risk_direction is not None
        else None,
        "month_to_date_revenue": month_to_date_revenue,
        "month_forecast": forecast,
        "yoy_delta_pct": yoy_delta_pct,
        "top_terminals": [{"name": name, "revenue": value} for name, value in top_terminals],
    }


def build_mgt_day_message(metrics: dict[str, Any], gate: dict[str, Any], dashboard_link: str) -> str:
    if not gate["ok_for_numeric_digest"]:
        return build_gap_message("MGT-DAY-22", gate, dashboard_link)
    top = metrics.get("top_direction") or {}
    risk = metrics.get("risk_direction") or {}
    lines = [
        f"TG Dashboard - итоги дня {metrics['date']}",
        "",
        f"Выручка: {money(metrics['revenue_today'])} UZS",
        f"YoY: {pct(metrics.get('yoy_delta_pct'))}" if metrics.get("yoy_delta_pct") is not None else "YoY: скрыто - нет подтвержденной базы для даты прошлого года",
        f"Forecast месяца: {money(metrics.get('month_forecast'))} UZS",
        "",
        f"Топ: {top.get('name', 'n/a')} - {money(top.get('revenue'))} UZS",
        f"Зона внимания: {risk.get('name', 'n/a')} ({pct(risk.get('delta_pct_vs_7d_avg'))} к среднему за 7 дней)" if risk else "Зона внимания: нет устойчивой 7-дневной базы",
    ]
    if metrics.get("top_terminals"):
        terminals = "; ".join(f"{item['name']} {money(item['revenue'])}" for item in metrics["top_terminals"])
        lines.append(f"Топ терминалы месяца: {terminals}")
    lines.extend(
        [
            "",
            f"Данные: обновлены {metrics.get('dashboard_generated_at')}; source status: {gate['status_summary']}",
            f"Dashboard: {dashboard_link}",
        ]
    )
    return "\n".join(lines)


def build_mgt_am_message(metrics: dict[str, Any], gate: dict[str, Any], dashboard_link: str) -> str:
    status = "OK" if gate["ok_for_numeric_digest"] else "DATA GAP"
    hidden = gate.get("suppressed_count", 0)
    return "\n".join(
        [
            f"TG Dashboard - refresh {metrics['date']}",
            "",
            f"Статус: {status}",
            f"Период данных: до {metrics['date']}",
            f"Dashboard generated_at: {metrics.get('dashboard_generated_at')}",
            f"Widgets: {gate['status_summary']}",
            f"Скрыто из числовых Telegram-сводок: {hidden}",
            "",
            f"Dashboard: {dashboard_link}",
        ]
    )


def build_gap_message(digest_id: str, gate: dict[str, Any], dashboard_link: str) -> str:
    lines = [
        f"TG Dashboard - data gap ({digest_id})",
        "",
        "Часть блоков не отправлена как цифры: источник не подтвержден, proxy или внешний gap.",
        f"Widgets: {gate['status_summary']}",
    ]
    for item in gate.get("suppressed_sample", [])[:8]:
        reason = f" - {item['gap_reason']}" if item.get("gap_reason") else ""
        lines.append(f"- {item['page']}/{item['widget_id']}: {item['source_status']}{reason}")
    lines.extend(["", f"Dashboard: {dashboard_link}"])
    return "\n".join(lines)


def build_blocked_message(
    digest_id: str,
    title: str,
    reason: str,
    next_step: str,
    gate: dict[str, Any],
    dashboard_link: str,
) -> str:
    return "\n".join(
        [
            f"TG Dashboard - {title} ({digest_id})",
            "",
            "Статус: source gap / blocked",
            f"Причина: {reason}",
            f"Следующий шаг: {next_step}",
            f"Widgets: {gate['status_summary']}",
            "",
            f"Dashboard: {dashboard_link}",
        ]
    )


def build_peak_message(metrics: dict[str, Any], gate: dict[str, Any], dashboard_link: str) -> str:
    if not gate["ok_for_numeric_digest"]:
        return build_gap_message("MGT-PEAK-3H", gate, dashboard_link)
    risk = metrics.get("risk_direction") or {}
    return "\n".join(
        [
            f"TG Dashboard - peak check {metrics['date']}",
            "",
            f"Факт с начала дня: {money(metrics['revenue_today'])} UZS",
            f"Риск: {risk.get('name', 'n/a')} ({pct(risk.get('delta_pct_vs_7d_avg'))} к среднему за 7 дней)" if risk else "Риск: нет устойчивой базы",
            f"Действие: проверить направление/точку риска в dashboard",
            f"Данные: {metrics.get('dashboard_generated_at')}",
            "",
            f"Dashboard: {dashboard_link}",
        ]
    )


def build_direction_week_message(management: dict[str, Any], date_arg: str, gate: dict[str, Any], dashboard_link: str) -> tuple[str, dict[str, Any]]:
    daily = management.get("daily_direction") or []
    latest_date = get_latest_date(daily) if date_arg == "latest" else date_arg
    latest = parse_date(latest_date)
    week_rows = []
    for row in daily:
        raw_date = row.get("date")
        if not raw_date:
            continue
        delta_days = (latest - parse_date(str(raw_date))).days
        if 0 <= delta_days <= 6:
            week_rows.append(row)
    totals = by_direction(week_rows)
    top = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    metrics = {
        "date": latest_date,
        "week_revenue": sum(totals.values()),
        "directions": [{"name": name, "revenue": value} for name, value in top],
        "dashboard_generated_at": management.get("generated_at"),
    }
    if not gate["ok_for_numeric_digest"]:
        return build_gap_message("DIR-WEEK", gate, dashboard_link), metrics
    lines = [
        f"TG Dashboard - недельная сводка направлений до {latest_date}",
        "",
        f"Выручка 7 дней: {money(metrics['week_revenue'])} UZS",
    ]
    for item in metrics["directions"][:8]:
        lines.append(f"- {item['name']}: {money(item['revenue'])} UZS")
    lines.extend(["", f"Данные: {metrics['dashboard_generated_at']}", f"Dashboard: {dashboard_link}"])
    return "\n".join(lines), metrics


def build_rfm_message(config: dict[str, Any], dashboard_link: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    rfm = read_json(DATA_DIR / "rfm-sales-id.json")
    dashboards = rfm.get("dashboards") or {}
    rows = []
    for name in ["flowers", "nour", "gourmet", "plants", "wedding", "b2b", "corp", "guul", "school"]:
        summary = (dashboards.get(name) or {}).get("summary") or {}
        if not summary:
            continue
        rows.append(
            {
                "name": name,
                "clients": summary.get("clients", 0),
                "revenue": summary.get("revenue", 0),
                "may_revenue": summary.get("may_revenue", 0),
                "segments": summary.get("segments", {}),
            }
        )
    top = sorted(rows, key=lambda row: float(row.get("revenue") or 0), reverse=True)
    metrics = {
        "date": (rfm.get("period") or {}).get("as_of") or rfm.get("generated_at"),
        "rfm_generated_at": rfm.get("generated_at"),
        "dashboards": top,
        "total_clients": sum(int(row.get("clients") or 0) for row in top),
        "total_revenue": sum(float(row.get("revenue") or 0) for row in top),
    }
    gate = {
        "ok_for_numeric_digest": True,
        "allowed_statuses": config.get("source_status_allowed", []),
        "status_counts": {"partial": len(top)},
        "status_summary": f"partial {len(top)} RFM dashboards",
        "suppressed_count": 0,
        "suppressed_sample": [],
    }
    lines = [
        f"RFM / клиентские сегменты - {metrics['date']}",
        "",
        f"Клиентов в mart: {metrics['total_clients']}",
        f"Оборот клиентов: {money(metrics['total_revenue'])} UZS",
        "",
    ]
    for row in top[:6]:
        segments = row.get("segments") or {}
        lines.append(
            f"- {row['name']}: {row['clients']} клиентов, {money(row['revenue'])} UZS; "
            f"лучшие {segments.get('Лучшие', 0)}, потерянные {segments.get('Потерянные', 0)}"
        )
    lines.extend(
        [
            "",
            "PII: в Telegram отправлен только summary; рабочие списки клиентов должны идти отдельной защищенной ссылкой.",
            f"Dashboard: {dashboard_link}",
        ]
    )
    return "\n".join(lines), metrics, gate


def build_marketing_message(widget_map: dict[str, Any], config: dict[str, Any], dashboard_link: str) -> tuple[str, dict[str, Any]]:
    gate = source_gate({"id": "MKT-WEEK", "page": "all", "source_status_allowed": config.get("source_status_allowed")}, config, widget_map)
    marketing_items = [
        item
        for item in suppressed_widgets(widget_map, set(config.get("source_status_allowed", [])), "all", limit=80)
        if any(key in (item["widget_id"] + " " + item["title"]).lower() for key in ["ads", "telegram", "competitor", "site", "marketing", "livedune"])
    ][:10]
    custom_gate = dict(gate)
    custom_gate["suppressed_sample"] = marketing_items or gate.get("suppressed_sample", [])
    custom_gate["ok_for_numeric_digest"] = False
    message = build_blocked_message(
        "MKT-WEEK",
        "маркетинговая weekly-сводка",
        "маркетинговые источники (TG-stat, LiveDune, рекламные кабинеты, сайт/Метрика) в текущем payload помечены как external gap",
        "подключить маркетинговые выгрузки/API и обновить widget_source_map",
        custom_gate,
        dashboard_link,
    )
    return message, custom_gate


def build_defect_input_message(dashboard_link: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    metrics = {"date": "always_on", "mode": "telegram_input_flow"}
    gate = {
        "ok_for_numeric_digest": True,
        "allowed_statuses": ["input_flow"],
        "status_counts": {"input_flow": 1},
        "status_summary": "input_flow 1",
        "suppressed_count": 0,
        "suppressed_sample": [],
    }
    message = "\n".join(
        [
            "TG Dashboard - бот учета брака (DEFECT-IN)",
            "",
            "Режим: input flow, не outbound alert.",
            "Что принимает: фото брака + текстовое описание.",
            "Что должен записать: дата, точка, сотрудник, цветок/SKU, причина, количество, фото id, статус human review.",
            "Что дальше: после накопления реестра weekly summary уходит в DEFECT-WEEK.",
            "",
            f"Dashboard: {dashboard_link}",
        ]
    )
    return message, metrics, gate


def build_payload_gap_for_digest(digest: dict[str, Any], config: dict[str, Any], widget_map: dict[str, Any], dashboard_link: str) -> tuple[str, dict[str, Any]]:
    reasons = {
        "LASTYEAR-START": (
            "прошлогодние клиенты требуют защищенный client-level export; текущий Telegram digest не должен слать ФИО/телефоны в чат",
            "собрать protected Sheet/CSV и добавить ссылку в digest",
        ),
        "LASTYEAR-END": (
            "список не купивших требует protected export и PII policy; в чат можно только summary",
            "собрать protected Sheet/CSV и добавить ссылку в digest",
        ),
        "GOUR-EXP-09": (
            "в текущем gourmet payload нет source-backed поля срока годности SKU",
            "добавить OX expiry-date field в payload и source contract",
        ),
        "GOUR-STOCK": (
            "в текущем gourmet payload нет source-backed остатков шоколада по точкам и срокам годности",
            "подключить stock snapshot + expiry fields",
        ),
        "PROC-MIN-STOCK": (
            "procurement payload содержит импортные поступления, но не текущий stock threshold + sales velocity",
            "подключить остатки, пороги и скорость продаж SKU",
        ),
        "DEFECT-WEEK": (
            "реестр брака из Telegram пока не подключен как источник",
            "запустить DEFECT-IN registry и/или ручной weekly import Павлова",
        ),
        "CRM-STALL": (
            "CRM/Amo pipeline пока external gap",
            "подключить read-only CRM source и stage age fields",
        ),
        "GUUL-BILLING": (
            "Guul billing/subscription ledger не подключен; OX продажи не заменяют churn/renewals/payment failures",
            "подключить billing/subscription ledger",
        ),
        "SCHOOL-BILLING": (
            "School LMS/billing lifecycle не подключен; OX продажи не заменяют drop-off/payment status",
            "подключить LMS/billing source",
        ),
        "MP-BALANCE": (
            "балансы WB/Ozon/Uzum/Buchet/Leto требуют кабинеты маркетплейсов; текущий payload содержит только e-com sales",
            "подключить marketplace account balances",
        ),
    }
    reason, next_step = reasons.get(digest["id"], ("источник для уведомления не подключен", "подключить источник и обновить source contract"))
    gate = source_gate(digest, config, widget_map)
    gate = dict(gate)
    gate["ok_for_numeric_digest"] = False
    message = build_blocked_message(digest["id"], str(digest.get("template") or digest["id"]), reason, next_step, gate, dashboard_link)
    return message, gate


def build_daily_exec_summary(
    management: dict[str, Any],
    widget_map: dict[str, Any],
    config: dict[str, Any],
    date_arg: str,
    dashboard_link: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    metrics = build_management_metrics(management, date_arg)
    gate = source_gate({"id": "DAILY-EXEC", "page": "management", "source_status_allowed": config.get("source_status_allowed")}, config, widget_map)
    if not gate["ok_for_numeric_digest"]:
        return build_gap_message("DAILY-EXEC", gate, dashboard_link), metrics, gate

    top = metrics.get("top_direction") or {}
    risk = metrics.get("risk_direction") or {}
    operational_gap_gate = source_gate({"id": "OPERATIONAL-RISKS", "page": "all", "source_status_allowed": config.get("source_status_allowed")}, config, widget_map)
    critical_gaps = [
        item
        for item in operational_gap_gate.get("suppressed_sample", [])
        if item["source_status"] in {"external", "insufficient_base"}
    ][:4]

    lines = [
        f"TG Dashboard - daily executive summary {metrics['date']}",
        "",
        "1. Управление",
        f"Выручка: {money(metrics['revenue_today'])} UZS",
        f"YoY: {pct(metrics.get('yoy_delta_pct'))}" if metrics.get("yoy_delta_pct") is not None else "YoY: скрыто - нет подтвержденной базы прошлого года",
        f"Forecast месяца: {money(metrics.get('month_forecast'))} UZS",
        "",
        "2. Направления",
        f"Топ: {top.get('name', 'n/a')} - {money(top.get('revenue'))} UZS",
        f"Зона внимания: {risk.get('name', 'n/a')} ({pct(risk.get('delta_pct_vs_7d_avg'))} к среднему за 7 дней)" if risk else "Зона внимания: нет устойчивой 7-дневной базы",
    ]
    if metrics.get("top_terminals"):
        terminals = "; ".join(f"{item['name']} {money(item['revenue'])}" for item in metrics["top_terminals"])
        lines.append(f"Топ терминалы месяца: {terminals}")

    lines.extend(["", "3. Операционные риски"])
    if critical_gaps:
        for item in critical_gaps:
            lines.append(f"- {item['title']}: source gap ({item['source_status']})")
    else:
        lines.append("- критичных source gaps в sample нет")

    lines.extend(
        [
            "",
            "4. Данные",
            f"Обновлено: {metrics.get('dashboard_generated_at')}",
            f"Source status: {gate['status_summary']}",
            f"Dashboard: {dashboard_link}",
        ]
    )
    return "\n".join(lines), metrics, gate


def build_weekly_business_report(
    management: dict[str, Any],
    widget_map: dict[str, Any],
    config: dict[str, Any],
    date_arg: str,
    dashboard_link: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    gate = source_gate({"id": "WEEKLY-BUSINESS", "page": "management", "source_status_allowed": config.get("source_status_allowed")}, config, widget_map)
    direction_message, direction_metrics = build_direction_week_message(management, date_arg, gate, dashboard_link)
    rfm_message, rfm_metrics, _ = build_rfm_message(config, dashboard_link)
    marketing_message, marketing_gate = build_marketing_message(widget_map, config, dashboard_link)
    metrics = {
        "date": direction_metrics.get("date"),
        "directions": direction_metrics,
        "rfm": rfm_metrics,
        "marketing_gate": marketing_gate,
    }
    lines = [
        f"TG Dashboard - weekly business report до {metrics['date']}",
        "",
        "1. Направления",
    ]
    direction_lines = direction_message.splitlines()
    lines.extend(line for line in direction_lines[2:] if line and not line.startswith("Dashboard:")) 
    lines.extend(["", "2. Клиенты / RFM"])
    rfm_lines = rfm_message.splitlines()
    lines.extend(line for line in rfm_lines[2:10] if line and not line.startswith("Dashboard:"))
    lines.extend(["", "3. Маркетинг"])
    lines.append("Маркетинговый блок пока не source-backed: TG-stat/LiveDune/реклама/сайт не подключены.")
    lines.append(f"Marketing gaps: {marketing_gate['status_summary']}")
    lines.extend(["", f"Dashboard: {dashboard_link}"])
    return "\n".join(lines), metrics, gate


def build_monthly_clients_report(config: dict[str, Any], dashboard_link: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    rfm_message, metrics, gate = build_rfm_message(config, dashboard_link)
    lines = [
        f"TG Dashboard - monthly clients report {metrics['date']}",
        "",
        "1. RFM summary",
    ]
    rfm_lines = rfm_message.splitlines()
    lines.extend(line for line in rfm_lines[2:] if line and not line.startswith("Dashboard:"))
    lines.extend(
        [
            "",
            "2. Прошлогодние клиенты",
            "Статус: blocked до защищенного client-level export.",
            "В Telegram отправляем только summary; ФИО/телефоны должны идти ссылкой на защищенный Sheet/CSV.",
            "",
            f"Dashboard: {dashboard_link}",
        ]
    )
    return "\n".join(lines), metrics, gate


def build_operational_risks_report(
    widget_map: dict[str, Any],
    config: dict[str, Any],
    dashboard_link: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    gap_ids = [
        "GOUR-EXP-09",
        "GOUR-STOCK",
        "PROC-MIN-STOCK",
        "DEFECT-WEEK",
        "CRM-STALL",
        "GUUL-BILLING",
        "SCHOOL-BILLING",
        "MP-BALANCE",
    ]
    reasons = []
    for digest_id in gap_ids:
        fake_digest = {"id": digest_id, "page": "all", "template": digest_id, "source_status_allowed": config.get("source_status_allowed")}
        message, _ = build_payload_gap_for_digest(fake_digest, config, widget_map, dashboard_link)
        reason = ""
        next_step = ""
        for line in message.splitlines():
            if line.startswith("Причина:"):
                reason = line.replace("Причина:", "").strip()
            if line.startswith("Следующий шаг:"):
                next_step = line.replace("Следующий шаг:", "").strip()
        reasons.append({"id": digest_id, "reason": reason, "next_step": next_step})

    gate = source_gate({"id": "OPERATIONAL-RISKS", "page": "all", "source_status_allowed": config.get("source_status_allowed")}, config, widget_map)
    metrics = {"date": datetime.now(timezone.utc).date().isoformat(), "risks": reasons}
    lines = [
        "TG Dashboard - operational risks report",
        "",
        "Это не 8 отдельных пушей. Это один операционный отчет по рискам.",
        "",
    ]
    for item in reasons:
        lines.append(f"- {item['id']}: {item['reason']} -> {item['next_step']}")
    lines.extend(["", f"Source status: {gate['status_summary']}", f"Dashboard: {dashboard_link}"])
    gate = dict(gate)
    gate["ok_for_numeric_digest"] = False
    return "\n".join(lines), metrics, gate


def build_data_gap_message(widget_map: dict[str, Any], config: dict[str, Any], dashboard_link: str) -> tuple[str, dict[str, Any]]:
    digest = {"id": "DATA-GAP", "page": "all", "source_status_allowed": config.get("source_status_allowed")}
    gate = source_gate(digest, config, widget_map)
    return build_gap_message("DATA-GAP", gate, dashboard_link), gate


def digest_hash(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def build_digest_payload(
    digest: dict[str, Any],
    config: dict[str, Any],
    management: dict[str, Any],
    widget_map: dict[str, Any],
    date_arg: str,
) -> dict[str, Any]:
    dashboard_link = config.get("default_dashboard_link", "")
    now = datetime.now(timezone.utc).isoformat()
    if digest["id"] == "DAILY-EXEC":
        message, metrics, gate = build_daily_exec_summary(management, widget_map, config, date_arg, dashboard_link)
    elif digest["id"] == "MORNING-STATUS":
        metrics = build_management_metrics(management, date_arg)
        gate = source_gate(digest, config, widget_map)
        message = build_mgt_am_message(metrics, gate, dashboard_link)
    elif digest["id"] == "WEEKLY-BUSINESS":
        message, metrics, gate = build_weekly_business_report(management, widget_map, config, date_arg, dashboard_link)
    elif digest["id"] == "MONTHLY-CLIENTS":
        message, metrics, gate = build_monthly_clients_report(config, dashboard_link)
    elif digest["id"] == "OPERATIONAL-RISKS":
        message, metrics, gate = build_operational_risks_report(widget_map, config, dashboard_link)
    elif digest["id"] == "DATA-GAP":
        message, gate = build_data_gap_message(widget_map, config, dashboard_link)
        metrics = {"date": get_latest_date(management.get("daily_direction") or [])}
    elif digest["id"] == "RFM-MONTH":
        message, metrics, gate = build_rfm_message(config, dashboard_link)
    elif digest["id"] == "MKT-WEEK":
        message, gate = build_marketing_message(widget_map, config, dashboard_link)
        metrics = {"date": get_latest_date(management.get("daily_direction") or [])}
    elif digest["id"] == "DEFECT-IN":
        message, metrics, gate = build_defect_input_message(dashboard_link)
    elif digest["id"] in {
        "LASTYEAR-START",
        "LASTYEAR-END",
        "GOUR-EXP-09",
        "GOUR-STOCK",
        "PROC-MIN-STOCK",
        "DEFECT-WEEK",
        "CRM-STALL",
        "GUUL-BILLING",
        "SCHOOL-BILLING",
        "MP-BALANCE",
    }:
        message, gate = build_payload_gap_for_digest(digest, config, widget_map, dashboard_link)
        metrics = {"date": get_latest_date(management.get("daily_direction") or []), "blocked": True}
    else:
        metrics = build_management_metrics(management, date_arg)
        gate = source_gate(digest, config, widget_map)
        if digest["id"] == "MGT-AM-09":
            message = build_mgt_am_message(metrics, gate, dashboard_link)
        elif digest["id"] == "MGT-DAY-22":
            message = build_mgt_day_message(metrics, gate, dashboard_link)
        elif digest["id"] == "MGT-PEAK-3H":
            message = build_peak_message(metrics, gate, dashboard_link)
        elif digest["id"] == "DIR-WEEK":
            message, metrics = build_direction_week_message(management, date_arg, gate, dashboard_link)
        else:
            message = build_gap_message(digest["id"], gate, dashboard_link)
    return {
        "digest_id": digest["id"],
        "built_at": now,
        "dry_run": bool(config.get("dry_run", True)),
        "recipient": digest.get("recipient", "dry_run"),
        "date": metrics.get("date"),
        "template": digest.get("template"),
        "message": message,
        "message_sha256": digest_hash(message),
        "metrics": metrics,
        "source_gate": gate,
        "dashboard_link": dashboard_link,
    }


def build_all(
    config_path: Path,
    date_arg: str,
    out_dir: Path,
    digest_filter: str | None = None,
    include_disabled: bool = False,
) -> list[Path]:
    config = read_json(config_path)
    management = read_json(dashboard_data_path("management.json"))
    widget_map = read_json(dashboard_data_path("widget_source_map.json"))
    digests = [
        digest
        for digest in config.get("digests", [])
        if include_disabled or digest.get("enabled")
    ]
    if digest_filter:
        digests = [digest for digest in digests if digest.get("id") == digest_filter]
    if not digests:
        raise ValueError("No enabled digests matched the requested filter")
    written: list[Path] = []
    for digest in digests:
        payload = build_digest_payload(digest, config, management, widget_map, date_arg)
        digest_date = payload.get("date") or "latest"
        out_path = out_dir / f"{digest['id']}-{digest_date}.json"
        write_json(out_path, payload)
        written.append(out_path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--date", default="latest", help="YYYY-MM-DD or latest")
    parser.add_argument("--digest", help="Optional digest id, e.g. MGT-DAY-22")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--include-disabled", action="store_true", help="Build disabled config entries too")
    args = parser.parse_args()
    written = build_all(args.config, args.date, args.out_dir, args.digest, args.include_disabled)
    for path in written:
        try:
            print(path.relative_to(ROOT))
        except ValueError:
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
