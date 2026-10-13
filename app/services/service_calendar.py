"""Say why a date has no buses, so an agent fixes the right thing.

Without this, a date the timetable doesn't cover came back as an ordinary
"nothing fits" and the hint told the user to visit fewer places or start
earlier — advice that cannot work. The three feeds also expire on different
dates, so a day can be covered for one operator and silently empty for the
others; plans then quietly get worse instead of failing.
"""

from __future__ import annotations

from datetime import date

from gtfs_multifeed import GTFSEngine

from app.gtfs_feeds import FEED_LABELS


FIXTURE_GTFS_HINT = (
    "サーバーに実際の時刻表が読み込まれておらず、テスト用の最小データで"
    "動いています。那須のスポット間の行程は作れません。サーバー管理者が "
    "scripts/download_gtfs.py で時刻表を取得し、再起動する必要があります。"
)


def with_note(note: str | None, hint: str, *, failed: bool = False) -> str:
    """Attach a coverage note; on a failure it leads, since it's the likelier cause."""

    if note is None:
        return hint
    return f"{note} {hint}" if failed else f"{hint} {note}"


def _label(feed_name: str) -> str:
    return FEED_LABELS.get(feed_name, feed_name)


def _period(period: tuple[date, date]) -> str:
    return f"{period[0].isoformat()}〜{period[1].isoformat()}"


def no_service_hint(engine: GTFSEngine, service_date: str) -> str | None:
    """Explain a date with no buses at all, or None if some bus runs."""

    if engine.active_service_ids(service_date):
        return None

    uncovered = engine.feeds_not_covering(service_date)
    if uncovered and len(uncovered) == len(engine.service_periods()):
        periods = "、".join(
            f"{_label(name)}: {_period(period)}" for name, period in uncovered.items()
        )
        return (
            f"{service_date}は時刻表の有効期間外です（{periods}）。期間内の日付で"
            "計画するようユーザーに伝えてください。新しい時刻表が公開されていれば、"
            "サーバー管理者が scripts/download_gtfs.py で更新できます。"
            "訪問地を減らしたり出発を早めたりしても解決しません。"
        )
    return (
        f"{service_date}はどの路線もバスの運行がない日です。別の日付を選ぶか、"
        "タクシー（max_taxi_legs）での移動を検討するようユーザーに伝えてください。"
    )


def partial_coverage_note(engine: GTFSEngine, service_date: str) -> str | None:
    """Warn when some, but not all, operators' timetables miss the date."""

    uncovered = engine.feeds_not_covering(service_date)
    if not uncovered or len(uncovered) == len(engine.service_periods()):
        return None
    names = "、".join(
        f"{_label(name)}（{_period(period)}）" for name, period in uncovered.items()
    )
    return (
        f"注意: {service_date}は{names}の時刻表の有効期間外のため、"
        "その事業者のバスは検索に含まれていません。実際より選択肢が少なく"
        "見えている可能性があることをユーザーに伝えてください。"
    )
