"""Configuration management."""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Application configuration."""

    # Telegram API
    TELEGRAM_API_ID: int = int(os.getenv('TELEGRAM_API_ID', '0'))
    TELEGRAM_API_HASH: str = os.getenv('TELEGRAM_API_HASH', '')

    # Safety settings
    MAX_MESSAGES_PER_DAY: int = int(os.getenv('MAX_MESSAGES_PER_DAY', '50'))
    MIN_DELAY_SECONDS: int = int(os.getenv('MIN_DELAY_SECONDS', '10'))
    MAX_DELAY_SECONDS: int = int(os.getenv('MAX_DELAY_SECONDS', '30'))
    HUMAN_LIKE_DELAYS: bool = os.getenv('HUMAN_LIKE_DELAYS', 'true').lower() == 'true'

    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    SESSIONS_DIR: Path = BASE_DIR / 'sessions'
    DATA_DIR: Path = BASE_DIR / 'data'
    LOGS_DIR: Path = BASE_DIR / 'logs'

    # Database
    DATABASE_PATH: Path = DATA_DIR / 'leads.db'

    # Logging
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE: str = os.getenv('LOG_FILE', 'logs/app.log')

    # Human-like behavior
    MIN_ACTION_DELAY: float = 3.0  # Minimum delay between actions
    MAX_ACTION_DELAY: float = 15.0  # Maximum delay between actions
    TYPING_SPEED: float = 0.05  # Seconds per character when "typing"
    READ_SPEED: float = 2.0  # Seconds to "read" before replying

    # FloodWait protection
    FLOODWAIT_MULTIPLIER: float = 1.2  # Wait 20% longer than required

    # Retry settings
    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 5

    @classmethod
    def validate(cls) -> bool:
        """Validate configuration."""
        if not cls.TELEGRAM_API_ID or not cls.TELEGRAM_API_HASH:
            raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env file")

        # Create necessary directories
        cls.SESSIONS_DIR.mkdir(exist_ok=True)
        cls.DATA_DIR.mkdir(exist_ok=True)
        cls.LOGS_DIR.mkdir(exist_ok=True)

        return True


# Validate configuration on import
Config.validate()
