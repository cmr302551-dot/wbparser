"""Logging configuration for the application."""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import coloredlogs


class Logger:
    """Enhanced logger with colored output and file logging."""

    _instances = {}

    def __new__(cls, name: str):
        if name not in cls._instances:
            cls._instances[name] = super().__new__(cls)
        return cls._instances[name]

    def __init__(self, name: str):
        if hasattr(self, '_initialized'):
            return

        self._initialized = True
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)

        # Create logs directory
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True)

        # File handler - detailed logs
        log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)

        # Console handler - colored output
        coloredlogs.install(
            level='INFO',
            logger=self.logger,
            fmt='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S',
            level_styles={
                'debug': {'color': 'white'},
                'info': {'color': 'green'},
                'warning': {'color': 'yellow', 'bold': True},
                'error': {'color': 'red', 'bold': True},
                'critical': {'color': 'red', 'bold': True, 'background': 'white'},
            },
            field_styles={
                'asctime': {'color': 'cyan'},
                'name': {'color': 'blue'},
                'levelname': {'color': 'magenta', 'bold': True},
            }
        )

    def debug(self, message: str, **kwargs):
        """Log debug message."""
        self.logger.debug(message, **kwargs)

    def info(self, message: str, **kwargs):
        """Log info message."""
        self.logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log warning message."""
        self.logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs):
        """Log error message."""
        self.logger.error(message, **kwargs)

    def critical(self, message: str, **kwargs):
        """Log critical message."""
        self.logger.critical(message, **kwargs)

    def success(self, message: str, **kwargs):
        """Log success message (as INFO with special formatting)."""
        self.logger.info(f"✓ {message}", **kwargs)

    def task_start(self, task_name: str, **kwargs):
        """Log task start."""
        self.logger.info(f"▶ Задача запущена: {task_name}", **kwargs)

    def task_complete(self, task_name: str, **kwargs):
        """Log task completion."""
        self.logger.info(f"✓ Задача завершена: {task_name}", **kwargs)

    def account_action(self, account_name: str, action: str, **kwargs):
        """Log account-specific action."""
        self.logger.info(f"👤 Аккаунт {account_name}: {action}", **kwargs)

    def send_success(self, account_name: str, recipient: str, **kwargs):
        """Log successful message send."""
        self.logger.info(f"📤 Аккаунт {account_name} → Сообщение отправлено: {recipient}", **kwargs)

    def send_failure(self, account_name: str, recipient: str, reason: str, **kwargs):
        """Log failed message send."""
        self.logger.warning(f"❌ Аккаунт {account_name} → Не удалось отправить {recipient} (причина: {reason})", **kwargs)


def get_logger(name: str) -> Logger:
    """Get or create a logger instance."""
    return Logger(name)
