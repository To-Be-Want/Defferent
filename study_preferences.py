# -*- coding: utf-8 -*-
"""本地单人部署的学习隐私偏好。

偏好只保存在本机 ``study_library/settings.json``，不会上传或同步。
"""

import json
from pathlib import Path
from threading import Lock


_LOCK = Lock()
_DEFAULTS = {
    "allow_web_search": True,
    "allow_cloud_model": True,
    "external_data_notice_acknowledged": False,
    "default_daily_minutes": 240,
    "study_time_slots": [{"start": "19:00", "end": "23:00"}],
}
_BOOL_KEYS = {"allow_web_search", "allow_cloud_model", "external_data_notice_acknowledged"}


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    hour, minute = int(hour), int(minute)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError
    return hour * 60 + minute


def _validate_slots(value) -> list:
    if not isinstance(value, list) or not value or len(value) > 8:
        raise ValueError("每天至少保留 1 个复习时间段，且不能超过 8 段")
    slots = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("复习时间段格式不正确")
        start, end = str(item.get("start", "")), str(item.get("end", ""))
        try:
            start_min, end_min = _minutes(start), _minutes(end)
        except (ValueError, TypeError):
            raise ValueError("复习时间应使用 HH:MM 格式")
        if end_min <= start_min:
            raise ValueError("每个复习时间段的结束时间必须晚于开始时间")
        slots.append({"start": start, "end": end, "_start": start_min, "_end": end_min})
    slots.sort(key=lambda item: item["_start"])
    for previous, current in zip(slots, slots[1:]):
        if current["_start"] < previous["_end"]:
            raise ValueError("每日复习时间段不能互相重叠")
    return [{"start": item["start"], "end": item["end"]} for item in slots]


def validate_study_time_slots(value) -> list:
    """供接口在开始写数据库前做无副作用校验。"""
    return _validate_slots(value)


def _path() -> Path:
    from user_context import current_user, user_root
    folder = (user_root() if current_user.get() else Path(__file__).resolve().parent) / "study_library"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def get_preferences() -> dict:
    with _LOCK:
        try:
            saved = json.loads(_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            saved = {}
        current = dict(_DEFAULTS)
        from user_context import current_user
        if current_user.get():
            current.update(allow_web_search=False, allow_cloud_model=False)
        for key in _BOOL_KEYS:
            if key in saved:
                current[key] = bool(saved[key])
        try:
            if "study_time_slots" in saved:
                current["study_time_slots"] = _validate_slots(saved["study_time_slots"])
            if "default_daily_minutes" in saved:
                current["default_daily_minutes"] = min(1440, max(15, int(saved["default_daily_minutes"])))
        except (ValueError, TypeError):
            pass
        return current


def update_preferences(changes: dict) -> dict:
    if not isinstance(changes, dict):
        raise ValueError("设置必须是对象")
    unknown = set(changes) - set(_DEFAULTS)
    if unknown:
        raise ValueError("不支持的设置：" + "、".join(sorted(unknown)))
    current = get_preferences()
    for key, value in changes.items():
        if key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise ValueError("隐私开关必须为布尔值")
            current[key] = value
        elif key == "study_time_slots":
            current[key] = _validate_slots(value)
            current["default_daily_minutes"] = sum(
                _minutes(slot["end"]) - _minutes(slot["start"]) for slot in current[key]
            ) or current["default_daily_minutes"]
        elif key == "default_daily_minutes":
            minutes = int(value)
            if not 15 <= minutes <= 1440:
                raise ValueError("每日复习时长必须在 15 到 1440 分钟之间")
            current[key] = minutes
    with _LOCK:
        _path().write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current
