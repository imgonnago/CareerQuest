from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")

USAGE_FILE = Path("data/runtime/api_usage.json")


class ApiQuotaExceeded(Exception):
    pass


class ApiQuotaManager:
    """
    CareerQuest 내부 API 호출 제한기.

    실제 제공기관 한도보다 낮은 자체 한도를 두어
    개발 중 실수로 API를 과도하게 호출하지 않도록 한다.
    """

    def __init__(self) -> None:
        self.usage_file = USAGE_FILE

        self.usage_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.min_interval = float(
            os.getenv(
                "API_MIN_INTERVAL_SECONDS",
                "0.3",
            )
        )

        self.last_request_time = 0.0

    def _today(self) -> str:
        return datetime.now(KST).date().isoformat()

    def _load(self) -> dict:
        if not self.usage_file.exists():
            return {
                "date": self._today(),
                "usage": {},
            }

        try:
            with self.usage_file.open(
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)

        except (json.JSONDecodeError, OSError):
            return {
                "date": self._today(),
                "usage": {},
            }

        # 날짜가 바뀌면 자동 초기화
        if data.get("date") != self._today():
            return {
                "date": self._today(),
                "usage": {},
            }

        return data

    def _save(self, data: dict) -> None:
        temp_file = self.usage_file.with_suffix(".tmp")

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

        temp_file.replace(self.usage_file)

    def check_and_consume(
        self,
        api_name: str,
        daily_budget: int,
    ) -> None:

        data = self._load()

        used = int(
            data["usage"].get(
                api_name,
                0,
            )
        )

        if used >= daily_budget:
            raise ApiQuotaExceeded(
                f"{api_name} 일일 자체 한도 도달 "
                f"({used}/{daily_budget})"
            )

        # 너무 빠른 연속 호출 방지
        elapsed = time.monotonic() - self.last_request_time

        if elapsed < self.min_interval:
            time.sleep(
                self.min_interval - elapsed
            )

        # 실패한 요청도 실제 API에서는 호출로 집계될 수 있으므로
        # 요청 직전에 사용량 증가
        data["usage"][api_name] = used + 1

        self._save(data)

        self.last_request_time = time.monotonic()

    def remaining(
        self,
        api_name: str,
        daily_budget: int,
    ) -> int:

        data = self._load()

        used = int(
            data["usage"].get(
                api_name,
                0,
            )
        )

        return max(
            daily_budget - used,
            0,
        )


quota_manager = ApiQuotaManager()