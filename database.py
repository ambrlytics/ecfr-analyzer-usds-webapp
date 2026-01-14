"""Database models and operations."""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

Base = declarative_base()
DB_PATH = Path("data/regulations.db")
DB_PATH.parent.mkdir(exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


class AgencySnapshot(Base):
    """Store agency regulation data snapshots."""

    __tablename__ = "agency_snapshots"

    id = Column(Integer, primary_key=True)
    snapshot_date = Column(DateTime, nullable=False)
    agency_name = Column(String, nullable=False, index=True)
    agency_slug = Column(String, nullable=False)
    parent_agency = Column(String, nullable=True)
    child_agencies = Column(Text, nullable=True)  # JSON string
    word_count = Column(Integer, nullable=False)
    checksum = Column(String, nullable=False)
    complexity_score = Column(Float, nullable=False)
    cfr_references = Column(Text, nullable=False)  # JSON string

    def to_dict(self):
        return {
            'id': self.id,
            'snapshot_date': self.snapshot_date.isoformat(),
            'agency_name': self.agency_name,
            'agency_slug': self.agency_slug,
            'parent_agency': self.parent_agency,
            'child_agencies': json.loads(self.child_agencies) if self.child_agencies else [],
            'word_count': self.word_count,
            'checksum': self.checksum,
            'complexity_score': self.complexity_score,
            'cfr_references': json.loads(self.cfr_references)
        }


class DeregulationCache(Base):
    """Cache deregulation likelihood analysis results."""

    __tablename__ = "deregulation_cache"

    id = Column(Integer, primary_key=True)
    agency_slug = Column(String, nullable=False, unique=True, index=True)
    agency_name = Column(String, nullable=False)
    likelihood = Column(String, nullable=False)  # strong, moderate, low, unlikely, unknown
    label = Column(String, nullable=False)
    recent_revisions = Column(Integer, default=0)
    analysis = Column(Text, nullable=True)
    full_analysis = Column(Text, nullable=True)  # Full AI analysis text
    computed_at = Column(DateTime, nullable=False)

    def to_dict(self):
        return {
            'agency_slug': self.agency_slug,
            'agency_name': self.agency_name,
            'likelihood': self.likelihood,
            'label': self.label,
            'recent_revisions': self.recent_revisions,
            'analysis': self.analysis,
            'full_analysis': self.full_analysis,
            'computed_at': self.computed_at.isoformat()
        }


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(engine)


def get_db() -> Session:
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def save_snapshot(db: Session, agencies: List[dict], snapshot_date: datetime) -> int:
    """Save agency data snapshot to database.

    Handles both current snapshots and historical snapshots.
    Historical snapshots have a 'fetched_at' field with the historical date.
    """
    count = 0
    for agency in agencies:
        # Use fetched_at for historical snapshots, otherwise use snapshot_date
        if 'fetched_at' in agency:
            fetched_at = agency['fetched_at']
            if isinstance(fetched_at, str):
                try:
                    snapshot_dt = datetime.strptime(fetched_at, "%Y-%m-%d")
                except:
                    snapshot_dt = snapshot_date
            else:
                snapshot_dt = fetched_at
        else:
            snapshot_dt = snapshot_date

        snapshot = AgencySnapshot(
            snapshot_date=snapshot_dt,
            agency_name=agency['name'],
            agency_slug=agency['slug'],
            parent_agency=agency.get('parent_agency'),
            child_agencies=json.dumps(agency.get('child_agencies', [])),
            word_count=agency['word_count'],
            checksum=agency['checksum'],
            complexity_score=agency['complexity_score'],
            cfr_references=json.dumps(agency['cfr_references'])
        )
        db.add(snapshot)
        count += 1

    db.commit()
    return count


def get_latest_snapshot(db: Session) -> Optional[datetime]:
    """Get most recent snapshot date."""
    result = db.query(AgencySnapshot.snapshot_date).order_by(
        AgencySnapshot.snapshot_date.desc()
    ).first()
    return result[0] if result else None


def get_agencies_by_snapshot(db: Session, snapshot_date: datetime) -> List[AgencySnapshot]:
    """Get all agencies for a specific snapshot."""
    return db.query(AgencySnapshot).filter_by(snapshot_date=snapshot_date).all()


def get_agency_history(db: Session, agency_name: str) -> List[AgencySnapshot]:
    """Get historical snapshots for an agency."""
    return db.query(AgencySnapshot).filter_by(
        agency_name=agency_name
    ).order_by(AgencySnapshot.snapshot_date.desc()).all()


def get_all_agencies(db: Session) -> List[str]:
    """Get list of all unique agency names."""
    results = db.query(AgencySnapshot.agency_name).distinct().all()
    return [r[0] for r in results]


def calculate_changes(db: Session, agency_name: str) -> Optional[dict]:
    """Calculate changes between latest two snapshots for an agency."""
    snapshots = db.query(AgencySnapshot).filter_by(
        agency_name=agency_name
    ).order_by(AgencySnapshot.snapshot_date.desc()).limit(2).all()

    if len(snapshots) < 2:
        return None

    latest, previous = snapshots[0], snapshots[1]

    return {
        'agency_name': agency_name,
        'word_count_change': latest.word_count - previous.word_count,
        'word_count_pct_change': round(
            ((latest.word_count - previous.word_count) / previous.word_count * 100)
            if previous.word_count > 0 else 0, 2
        ),
        'complexity_change': round(latest.complexity_score - previous.complexity_score, 2),
        'checksum_changed': latest.checksum != previous.checksum,
        'latest_date': latest.snapshot_date.isoformat(),
        'previous_date': previous.snapshot_date.isoformat()
    }


def get_deregulation_cache(db: Session, agency_slug: str) -> Optional[DeregulationCache]:
    """Get cached deregulation analysis for an agency."""
    return db.query(DeregulationCache).filter_by(agency_slug=agency_slug).first()


def save_deregulation_cache(
    db: Session,
    agency_slug: str,
    agency_name: str,
    likelihood: str,
    label: str,
    recent_revisions: int,
    analysis: str,
    full_analysis: Optional[str] = None
) -> DeregulationCache:
    """Save or update deregulation analysis cache."""
    cached = db.query(DeregulationCache).filter_by(agency_slug=agency_slug).first()

    if cached:
        # Update existing
        cached.agency_name = agency_name
        cached.likelihood = likelihood
        cached.label = label
        cached.recent_revisions = recent_revisions
        cached.analysis = analysis
        cached.full_analysis = full_analysis
        cached.computed_at = datetime.utcnow()
    else:
        # Create new
        cached = DeregulationCache(
            agency_slug=agency_slug,
            agency_name=agency_name,
            likelihood=likelihood,
            label=label,
            recent_revisions=recent_revisions,
            analysis=analysis,
            full_analysis=full_analysis,
            computed_at=datetime.utcnow()
        )
        db.add(cached)

    db.commit()
    return cached


def get_all_deregulation_cache(db: Session) -> List[DeregulationCache]:
    """Get all cached deregulation analyses."""
    return db.query(DeregulationCache).all()
