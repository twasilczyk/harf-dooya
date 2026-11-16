"""Elapsed-time estimates; no motor feedback or automatic RF commands."""

from dataclasses import dataclass

from .const import BUTTON_CLOSE, BUTTON_OPEN, BUTTON_STOP

TRAVEL_OVERRUN = 0.20
POSITION_UPDATE_INTERVAL = 1.0
DEFAULT_TRAVEL_TIME = 30.0
MIN_TRAVEL_TIME = 1.0
MAX_TRAVEL_TIME = 3600.0


@dataclass
class Motion:
    """One channel's estimate, updated explicitly before publishing HA state."""

    position: float | None = None
    direction: int | None = None
    started: float = 0.0
    start_position: float | None = None
    travel_time: float = DEFAULT_TRAVEL_TIME
    travel_end: float = 0.0
    finish: float = 0.0

    @property
    def percentage(self) -> int | None:
        if self.position is None:
            return None
        percentage = round(self.position)
        if self.direction == BUTTON_OPEN:
            return min(99, percentage)
        if self.direction == BUTTON_CLOSE:
            return max(1, percentage)
        return percentage

    def update(self, now: float) -> None:
        if self.direction is None:
            return
        opening = self.direction == BUTTON_OPEN
        if now >= self.finish:
            self.position = 100.0 if opening else 0.0
            self.direction = None
        elif now >= self.travel_end:
            self.position = 99.0 if opening else 1.0
        elif self.start_position is not None:
            distance = max(0.0, now - self.started) / self.travel_time * 100
            self.position = (
                min(99.0, self.start_position + distance)
                if opening
                else max(1.0, self.start_position - distance)
            )

    def observe(self, command: int, now: float, travel_time: float) -> None:
        self.update(now)
        if command == BUTTON_STOP:
            # Freeze the displayed estimate, including unknown and overrun values.
            self.position = self.percentage
            self.direction = None
        elif command in (BUTTON_OPEN, BUTTON_CLOSE) and command != self.direction:
            self.started = now
            self.start_position = self.position
            self.travel_time = travel_time
            target = 100 if command == BUTTON_OPEN else 0
            remaining = (
                1.0 if self.position is None else abs(target - self.position) / 100
            )
            self.travel_end = now + remaining * travel_time
            self.finish = self.travel_end + TRAVEL_OVERRUN * travel_time
            self.direction = command
            self.update(now)
        # Repeated commands in the same direction do not restart uninterrupted travel.
