"""The morning digest: yesterday in five lines.

Runs as a Railway cron service (`python -m app.digest`, schedule in README.md)
or through POST /internal/digest. It only reads; the rows it reports were
written by the chat turns during the day.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import Message, Run, SessionLocal, TripRequest, init_db


def _local_bounds(day: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.timezone)
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


def build_digest(db: Session, day: date | None = None) -> str:
    tz = ZoneInfo(settings.timezone)
    day = day or (datetime.now(tz).date() - timedelta(days=1))
    start, end = _local_bounds(day)

    trips = db.scalars(select(TripRequest).where(TripRequest.created_at >= start, TripRequest.created_at < end)
                       .order_by(TripRequest.created_at)).all()
    confirmed = [t for t in trips if t.status == "confirmed"]
    needs = [t for t in trips if t.status == "needs_a_look"]

    messages = db.scalar(select(func.count()).select_from(Message)
                         .where(Message.role == "user", Message.created_at >= start, Message.created_at < end)) or 0
    runs = db.execute(select(func.count(Run.id), func.coalesce(func.sum(Run.cost_usd), 0.0),
                             func.sum(case((Run.status != "ok", 1), else_=0)))
                      .where(Run.created_at >= start, Run.created_at < end)).one()
    run_count, cost, not_ok = int(runs[0] or 0), float(runs[1] or 0.0), int(runs[2] or 0)

    lines = [f"GeoChronos Time-Travel Concierge — {day.strftime('%A %d %b %Y')}",
             f"Trip requests saved: {len(confirmed)} confirmed · {len(needs)} needing a look",
             "Needs a look: " + ("; ".join(f"{t.site_name} {t.requested_year} ({t.verdict}, suggested {t.suggested_year or 'none'})" for t in needs[:5]) if needs else "none"),
             f"Chat: {messages} messages · {run_count} model runs · ${cost:.2f} of ${settings.daily_cap_usd:.2f} cap",
             f"Runs not OK: {not_ok}" + (" — check the runs table and runbook.md" if not_ok else "")]
    return "\n".join(lines)


def send_digest(text: str) -> bool:
    """POST {"text": ...} to DIGEST_WEBHOOK_URL (Slack-compatible). Returns False when no URL is set."""
    if not settings.digest_webhook_url:
        return False
    import httpx

    r = httpx.post(settings.digest_webhook_url, json={"text": text}, timeout=15)
    r.raise_for_status()
    return True


def main() -> None:
    init_db()
    with SessionLocal() as db:
        body = build_digest(db)
    print(body)
    sent = send_digest(body)
    print(f"\n[sent to webhook: {sent}]")


if __name__ == "__main__":
    main()
