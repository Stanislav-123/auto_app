from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "wf:goto:"


class WorkflowStep(str, Enum):
    PARSE = "parse"
    BINOM = "binom"
    CRM = "crm"
    DOMAIN = "domain"
    BACKEND = "backend"
    # Следующие шаги будут добавляться здесь.


STEP_LABELS: dict[WorkflowStep, str] = {
    WorkflowStep.PARSE: "Распознавание данных",
    WorkflowStep.BINOM: "Создание кампании в Binom",
    WorkflowStep.CRM: "Добавление в CRM",
    WorkflowStep.DOMAIN: "Добавление домена на бэк",
    WorkflowStep.BACKEND: "Настройка бэкенда",
}


def goto_callback(step: WorkflowStep) -> str:
    return f"{CALLBACK_PREFIX}{step.value}"


def parse_callback(data: str) -> WorkflowStep | None:
    if not data.startswith(CALLBACK_PREFIX):
        return None
    value = data.removeprefix(CALLBACK_PREFIX)
    try:
        return WorkflowStep(value)
    except ValueError:
        return None


def next_step(current: WorkflowStep) -> WorkflowStep | None:
    order = list(WorkflowStep)
    try:
        index = order.index(current)
    except ValueError:
        return None
    if index + 1 >= len(order):
        return None
    return order[index + 1]
