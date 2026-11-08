"""Database layer: models, engine, session, startup migration and seeding.

Postgres on Railway (DATABASE_URL from the plugin) or SQLite locally. The
schema is created at start with `create_all`; `schema_version` records which
version is live so a later real migration tool has somewhere to start.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import ROOT, settings

SCHEMA_VERSION = 2  # 2: GeoChronos tables (sites, site_dates, site_sources) added next to the legacy bakery tables


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", order_by="Message.id")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)  # plain text shown in the UI
    has_image: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Run(Base):
    """One model turn: the record behind the cost cap, the digest and the runbook."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    model: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | refusal | error | aborted | capped
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_names: Mapped[str] = mapped_column(String(200), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    item: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(20))
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))


class TripRequest(Base):
    """A saved 'visit' request. Demo only: no booking, no payment. Mismatches are kept for a human to look at."""

    __tablename__ = "trip_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_slug: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    site_name: Mapped[str] = mapped_column(String(200))
    requested_year: Mapped[int] = mapped_column(Integer)  # signed calendar year, BCE negative
    verdict: Mapped[str] = mapped_column(String(40))  # fits | fits_only_alternative_dating | too_early | too_late | ...
    suggested_year: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed | needs_a_look
    traveller: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    logged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Sale(Base):
    """One line of the Lesson 4 bakery dataset (29,115 rows, Sep 2025 to Aug 2026)."""

    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(20), index=True)
    order_datetime: Mapped[datetime] = mapped_column(DateTime)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    branch: Mapped[str] = mapped_column(String(60), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    payment_method: Mapped[str] = mapped_column(String(20))
    customer_type: Mapped[str] = mapped_column(String(20))
    product: Mapped[str] = mapped_column(String(60), index=True)
    category: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(Float)
    line_total: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), index=True)  # completed | refunded


class Site(Base):
    """One curated item from GeoChronos: a site, structure or object. Read-only through the site tools."""

    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    name_he: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(200), nullable=True)
    function: Mapped[str | None] = mapped_column(String(300), nullable=True)
    form: Mapped[str | None] = mapped_column(String(300), nullable=True)
    culture: Mapped[str | None] = mapped_column(String(300), nullable=True)
    polity: Mapped[str | None] = mapped_column(String(300), nullable=True)
    period: Mapped[str | None] = mapped_column(String(300), nullable=True)
    country: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    places: Mapped[str | None] = mapped_column(String(400), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_precision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location_role: Mapped[str | None] = mapped_column(String(30), nullable=True)


class SiteDate(Base):
    """One dating claim about a site. Years are astronomical (1 BCE = 0, 2500 BCE = -2499), as in GeoChronos."""

    __tablename__ = "site_dates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_slug: Mapped[str] = mapped_column(ForeignKey("sites.slug"), index=True)
    role: Mapped[str] = mapped_column(String(30), index=True)  # construction | creation | existence | use | occupation | ...
    start_earliest: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_latest: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_earliest: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_latest: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    qualifier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)  # the dating as the source expressed it
    dating_method: Mapped[str | None] = mapped_column(String(200), nullable=True)
    calendar: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)  # accepted | alternative | disputed | needs_verification
    confidence: Mapped[str] = mapped_column(String(10))  # high | medium | low | unknown
    rank: Mapped[str] = mapped_column(String(12))  # preferred | normal
    dispute_group: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class SiteSource(Base):
    """A citation behind a site's records: source, link, licence. Never the source text."""

    __tablename__ = "site_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_slug: Mapped[str] = mapped_column(ForeignKey("sites.slug"), index=True)
    source: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    locator: Mapped[str | None] = mapped_column(String(200), nullable=True)
    licence: Mapped[str | None] = mapped_column(String(200), nullable=True)


# ---------------------------------------------------------------------------
# Engine and session
# ---------------------------------------------------------------------------

def make_engine(url: str | None = None):
    url = url or settings.database_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Startup: schema, version, seed data
# ---------------------------------------------------------------------------

def init_db(bind=None, data_dir: Path | None = None) -> dict:
    """Create tables, record the schema version, seed the GeoChronos reference data if empty.

    The legacy bakery tables (sales, suppliers, invoices) are still created so an existing database
    keeps working, but they are no longer seeded or used by the agent.
    """
    bind = bind or engine
    data_dir = data_dir or (ROOT / "data")
    Base.metadata.create_all(bind)
    report = {"schema_version": SCHEMA_VERSION, "sites_seeded": 0, "site_dates_seeded": 0, "site_sources_seeded": 0}
    with Session(bind) as db:
        if db.get(SchemaVersion, SCHEMA_VERSION) is None:
            db.add(SchemaVersion(version=SCHEMA_VERSION))
            db.commit()
        if db.scalar(select(func.count()).select_from(Site)) == 0:
            report.update(_seed_geochronos(db, data_dir))
    return report


def _blank(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _float(value: str | None) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def _seed_geochronos(db: Session, data_dir: Path) -> dict:
    """Load the three GeoChronos CSVs in one transaction: all of it or none of it."""
    sites_path = data_dir / "geochronos_sites.csv"
    if not sites_path.exists():
        return {}
    with sites_path.open(encoding="utf-8", newline="") as f:
        sites = [Site(slug=r["site_id"].strip(), name=r["name"].strip(), name_he=_blank(r["name_he"]),
                      description=_blank(r["description"]), kind=_blank(r["kind"]), function=_blank(r["function"]),
                      form=_blank(r["form"]), culture=_blank(r["culture"]), polity=_blank(r["polity"]),
                      period=_blank(r["period"]), country=_blank(r["country"]), places=_blank(r["places"]),
                      lat=_float(r["lat"]), lon=_float(r["lon"]), location_precision=_blank(r["location_precision"]),
                      location_role=_blank(r["location_role"])) for r in csv.DictReader(f)]
    db.add_all(sites)
    db.flush()

    dates = []
    dates_path = data_dir / "geochronos_site_dates.csv"
    if dates_path.exists():
        with dates_path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                dates.append(SiteDate(
                    site_slug=r["site_id"].strip(), role=r["role"].strip(),
                    start_earliest=_float(r["start_earliest"]), start_latest=_float(r["start_latest"]),
                    end_earliest=_float(r["end_earliest"]), end_latest=_float(r["end_latest"]),
                    precision=_blank(r["precision"]), qualifier=_blank(r["qualifier"]), label=_blank(r["label"]),
                    dating_method=_blank(r["dating_method"]), calendar=_blank(r["calendar"]),
                    status=r["status"].strip(), confidence=r["confidence"].strip(), rank=r["rank"].strip(),
                    dispute_group=_blank(r["dispute_group"]), note=_blank(r["note"])))
    db.add_all(dates)

    cites = []
    cites_path = data_dir / "geochronos_site_sources.csv"
    if cites_path.exists():
        with cites_path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                cites.append(SiteSource(site_slug=r["site_id"].strip(), source=r["source"].strip(),
                                        url=_blank(r["url"]), locator=_blank(r["locator"]), licence=_blank(r["licence"])))
    db.add_all(cites)
    db.commit()
    return {"sites_seeded": len(sites), "site_dates_seeded": len(dates), "site_sources_seeded": len(cites)}


def _seed_suppliers(db: Session, path: Path) -> int:
    if not path.exists():
        return 0
    rows = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(Supplier(name=r["supplier"].strip(), item=r["item"].strip(), unit=r["unit"].strip(),
                                 price=float(r["price"]), currency=r["currency"].strip().upper()))
    db.add_all(rows)
    db.commit()
    return len(rows)


def _seed_sales(db: Session, path: Path, chunk: int = 2000) -> int:
    if not path.exists():
        return 0
    total = 0
    batch: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            dt = datetime.strptime(r["order_datetime"], "%Y-%m-%d %H:%M")
            # Clean at the door (Lesson 5): the source file has a "Herzliya " variant with a trailing space.
            batch.append({
                "order_id": r["order_id"].strip(), "order_datetime": dt, "order_date": dt.date(),
                "branch": r["branch"].strip(), "channel": r["channel"].strip(), "payment_method": r["payment_method"].strip(),
                "customer_type": r["customer_type"].strip(), "product": r["product"].strip(), "category": r["category"].strip(),
                "quantity": int(r["quantity"]), "unit_price": float(r["unit_price"]),
                "line_total": float(r["line_total"]), "status": r["status"],
            })
            if len(batch) >= chunk:
                db.execute(Sale.__table__.insert(), batch)
                total += len(batch)
                batch = []
    if batch:
        db.execute(Sale.__table__.insert(), batch)
        total += len(batch)
    db.commit()
    return total
