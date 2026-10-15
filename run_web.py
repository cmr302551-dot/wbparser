"""
Запуск веб-интерфейса WB Automation.

Использование:
    python run_web.py

Затем открыть в браузере: http://127.0.0.1:8000
"""

import sys
import os
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == '__main__':
    import uvicorn
    print("\n" + "="*50)
    print("  WB Automation Web Interface")
    print("="*50)
    print("  Адрес: http://127.0.0.1:8000")
    print("  Для остановки: Ctrl+C")
    print("="*50 + "\n")
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False,
                timeout_graceful_shutdown=3)
