"""
Safe access to the `context` object of a request.

Every field may be missing or have the wrong type (PROTOCOL.md: "the server must not fail on a
missing field"), so nothing here raises: a getter returns "", None, [] or {} instead.
"""

from __future__ import annotations

from .text import to_float

LAYERS = ("body", "muscles", "skeleton")
POSE_RUNNING = ("running", "loading")


class Context:
    def __init__(self, raw: object):
        self.raw: dict = raw if isinstance(raw, dict) else {}

    def _section(self, name: str) -> dict:
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    # -- layer, panel -------------------------------------------------------

    @property
    def layer(self) -> str:
        return self._text(self.raw.get("layer")).lower()

    @property
    def panel(self) -> str:
        return self._text(self.raw.get("panel")).lower()

    # -- timeline -----------------------------------------------------------

    @property
    def playing(self) -> bool | None:
        value = self._section("timeline").get("playing")
        return value if isinstance(value, bool) else None

    @property
    def speed(self) -> float | None:
        return to_float(self._section("timeline").get("speed"))

    @property
    def speed_steps(self) -> list[float]:
        steps = self._section("timeline").get("speed_steps")
        if not isinstance(steps, list):
            return []
        return sorted(number for number in (to_float(step) for step in steps[:32]) if number and number > 0)

    @property
    def recording(self) -> bool | None:
        value = self._section("timeline").get("recording")
        return value if isinstance(value, bool) else None

    # -- actions ------------------------------------------------------------

    @property
    def available_actions(self) -> list[str]:
        names = self._section("action").get("available")
        if not isinstance(names, list):
            return []
        return [name.strip() for name in names[:200] if isinstance(name, str) and name.strip()]

    @property
    def action_playing(self) -> str | None:
        value = self._section("action").get("playing")
        return value.strip() if isinstance(value, str) else None

    # -- pose estimation ----------------------------------------------------

    @property
    def has_pose(self) -> bool:
        return bool(self._section("pose"))

    @property
    def pose_status(self) -> str:
        return self._text(self._section("pose").get("status")).lower()

    @property
    def pose_running(self) -> bool:
        return self.pose_status == "running"

    @property
    def pose_panel_open(self) -> bool | None:
        value = self._section("pose").get("panel_open")
        return value if isinstance(value, bool) else None

    @property
    def pose_recording(self) -> bool | None:
        value = self._section("pose").get("recording")
        return value if isinstance(value, bool) else None

    @property
    def pose_angles(self) -> dict[str, float]:
        angles = self._section("pose").get("angles")
        if not isinstance(angles, dict):
            return {}
        clean = {}
        for name, value in list(angles.items())[:64]:
            number = to_float(value)
            if isinstance(name, str) and name.strip() and number is not None:
                clean[name.strip().lower()] = number
        return clean

    # -- the rest -----------------------------------------------------------

    @property
    def selected_part(self) -> dict | None:
        part = self._section("selected_part")
        if not self._text(part.get("name")):
            return None
        return {
            "kind": self._text(part.get("kind")).lower(),
            "name": self._text(part.get("name")),
            "side": self._text(part.get("side")).lower(),
        }

    @property
    def avatar_name(self) -> str:
        return self._text(self._section("avatar").get("name"))

    @property
    def avatar_index(self) -> int | None:
        value = to_float(self._section("avatar").get("index"))
        return int(value) if value is not None else None

    @property
    def avatar_count(self) -> int | None:
        value = to_float(self._section("avatar").get("count"))
        return int(value) if value is not None else None

    @property
    def last_results(self) -> list[dict]:
        results = self.raw.get("last_results")
        if not isinstance(results, list):
            return []
        clean = []
        for result in results[:16]:
            if isinstance(result, dict) and isinstance(result.get("type"), str):
                clean.append({
                    "type": result["type"][:40],
                    "ok": result.get("ok") is True,
                    "message": self._text(result.get("message"))[:200],
                })
        return clean
