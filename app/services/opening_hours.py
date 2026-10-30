"""When a spot can actually be visited.

Without this the planner happily sent people to the safari park on its
closing day, or to the ropeway in January when it doesn't run. The data is
hand-collected from travel sites that often disagree with each other (see
`source`/`note` on each spot), so it is deliberately simple:

- one daily window (`open`/`close`, optional `last_entry`);
- weekdays it is closed;
- seasons, by month-day range, that either change the window or close the
  place outright (winter closures are the big one here);
- `always_open` for outdoor places — a bridge has no hours.

A spot with no `hours` at all is *unknown*, not always open: it is never
ruled out, but answers say its hours weren't checked.

Things it can't express — "closed the second Tuesday of February", "open on
public holidays even if that's a Wednesday" — live in `note`. Where weekday
and weekend hours differ, the data records the narrower weekday window, so
a plan errs toward leaving early rather than arriving to a locked gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from gtfs_multifeed import parse_gtfs_time_to_minutes, parse_service_date

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_LABELS = ("月", "火", "水", "木", "金", "土", "日")


@dataclass(frozen=True)
class Season:
    """A month-day range ("12-01" to "03-15" wraps over New Year)."""

    start: str
    end: str
    closed: bool = False
    open_minutes: int | None = None
    close_minutes: int | None = None

    def covers(self, day: date) -> bool:
        key = day.strftime("%m-%d")
        if self.start <= self.end:
            return self.start <= key <= self.end
        return key >= self.start or key <= self.end


@dataclass(frozen=True)
class OpeningHours:
    always_open: bool = False
    open_minutes: int = 0
    close_minutes: int = 24 * 60
    last_entry_minutes: int | None = None
    closed_weekdays: frozenset[int] = frozenset()
    seasons: tuple[Season, ...] = ()
    source: str | None = None
    checked: str | None = None
    note: str | None = None

    def window(self, day: date) -> tuple[int, int, int] | None:
        """(open, close, last entry) in minutes on that day, or None if closed."""

        if self.always_open:
            return 0, 24 * 60, 24 * 60
        for season in self.seasons:
            if season.covers(day) and season.closed:
                return None
        if day.weekday() in self.closed_weekdays:
            return None

        open_minutes, close_minutes = self.open_minutes, self.close_minutes
        for season in self.seasons:
            if season.covers(day):
                open_minutes = season.open_minutes or open_minutes
                close_minutes = season.close_minutes or close_minutes
                break
        last_entry = self.last_entry_minutes or close_minutes
        return open_minutes, close_minutes, min(last_entry, close_minutes)

    def describe(self, day: date) -> str:
        """A short Japanese summary for that day, for people to double-check."""

        if self.always_open:
            return "屋外・常時見学可"
        window = self.window(day)
        if window is None:
            return f"{day.isoformat()}は休業（概算・要確認）"
        open_minutes, close_minutes, last_entry = window
        text = f"{_hhmm(open_minutes)}〜{_hhmm(close_minutes)}"
        if last_entry < close_minutes:
            text += f"（最終入場{_hhmm(last_entry)}）"
        return text + "・概算、要確認"

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "OpeningHours":
        if raw.get("always_open"):
            return cls(always_open=True, note=raw.get("note"), source=raw.get("source"),
                       checked=raw.get("checked"))
        return cls(
            open_minutes=parse_gtfs_time_to_minutes(raw["open"]),
            close_minutes=parse_gtfs_time_to_minutes(raw["close"]),
            last_entry_minutes=(
                parse_gtfs_time_to_minutes(raw["last_entry"]) if raw.get("last_entry") else None
            ),
            closed_weekdays=frozenset(WEEKDAYS.index(d) for d in raw.get("closed_weekdays", [])),
            seasons=tuple(
                Season(
                    start=season["from"],
                    end=season["to"],
                    closed=bool(season.get("closed")),
                    open_minutes=(
                        parse_gtfs_time_to_minutes(season["open"]) if season.get("open") else None
                    ),
                    close_minutes=(
                        parse_gtfs_time_to_minutes(season["close"]) if season.get("close") else None
                    ),
                )
                for season in raw.get("seasons", [])
            ),
            source=raw.get("source"),
            checked=raw.get("checked"),
            note=raw.get("note"),
        )

    def to_json(self) -> dict[str, Any]:
        meta = {
            key: value
            for key, value in (("source", self.source), ("checked", self.checked), ("note", self.note))
            if value is not None
        }
        if self.always_open:
            return {"always_open": True, **meta}
        raw: dict[str, Any] = {"open": _hhmm(self.open_minutes), "close": _hhmm(self.close_minutes)}
        if self.last_entry_minutes is not None:
            raw["last_entry"] = _hhmm(self.last_entry_minutes)
        if self.closed_weekdays:
            raw["closed_weekdays"] = [WEEKDAYS[d] for d in sorted(self.closed_weekdays)]
        if self.seasons:
            raw["seasons"] = [_season_json(season) for season in self.seasons]
        return {**raw, **meta}


@dataclass(frozen=True)
class Visit:
    start_minutes: int  # may be later than arrival: waiting for opening
    end_minutes: int


class VisitRefused(Exception):
    """Why a visit can't happen — a Japanese sentence fit for an ai_hint."""


def plan_visit(
    name: str, hours: OpeningHours | None, service_date: str | date,
    arrival_minutes: int, dwell_minutes: int,
) -> Visit:
    """Fit a stay into the opening hours, or raise VisitRefused.

    Arriving before opening waits until it opens. Arriving after last
    entry, or too late to stay `dwell_minutes` before closing, is refused
    rather than quietly cut short: the stay length is what the person asked
    for, and a plan that silently halves it is worse than one that says no.
    """

    if hours is None:
        return Visit(arrival_minutes, arrival_minutes + dwell_minutes)

    day = parse_service_date(service_date)
    window = hours.window(day)
    if window is None:
        raise VisitRefused(f"{name}は{day.isoformat()}（{WEEKDAY_LABELS[day.weekday()]}）は休業日です（概算データ・要確認）。")

    open_minutes, close_minutes, last_entry = window
    start = max(arrival_minutes, open_minutes)
    if start > last_entry:
        raise VisitRefused(
            f"{name}に{_hhmm(arrival_minutes)}着では最終入場（{_hhmm(last_entry)}）に間に合いません。"
        )
    if start + dwell_minutes > close_minutes:
        raise VisitRefused(
            f"{name}に{_hhmm(start)}から{dwell_minutes}分滞在すると閉館（{_hhmm(close_minutes)}）を過ぎます。"
        )
    return Visit(start, start + dwell_minutes)


def is_closed_on(hours: OpeningHours | None, service_date: str | date) -> bool:
    return hours is not None and hours.window(parse_service_date(service_date)) is None


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _season_json(season: Season) -> dict[str, Any]:
    raw: dict[str, Any] = {"from": season.start, "to": season.end}
    if season.closed:
        raw["closed"] = True
    if season.open_minutes is not None:
        raw["open"] = _hhmm(season.open_minutes)
    if season.close_minutes is not None:
        raw["close"] = _hhmm(season.close_minutes)
    return raw
