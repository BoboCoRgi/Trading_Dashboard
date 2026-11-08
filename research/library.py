from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Catalyst, ResearchNote, Thesis, WatchlistItem


def library(session: Session) -> dict[str, list]:
    return {
        "theses": list(session.scalars(select(Thesis).order_by(Thesis.updated_at.desc())).all()),
        "catalysts": list(session.scalars(select(Catalyst).order_by(Catalyst.created_at.desc())).all()),
        "notes": list(session.scalars(select(ResearchNote).order_by(ResearchNote.created_at.desc())).all()),
        "watchlist": list(session.scalars(select(WatchlistItem).order_by(WatchlistItem.added_at.desc())).all()),
    }


def add_note(session: Session, *, instrument_id, title: str, body: str, thesis_id=None) -> ResearchNote:
    from core.enums import DataOrigin

    note = ResearchNote(
        instrument_id=instrument_id,
        thesis_id=thesis_id,
        title=title.strip(),
        body=body.strip(),
        data_origin=DataOrigin.MOCK,
    )
    session.add(note)
    session.flush()
    return note
