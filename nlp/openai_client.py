"""Обёртка над OpenAI SDK для задач NLP проекта."""
from __future__ import annotations

import asyncio
import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from config.settings import config
"""Обёртка над OpenAI SDK для задач NLP проекта."""
