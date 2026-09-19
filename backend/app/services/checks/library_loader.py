"""Load a check-library JSON document into the database.

Expected shape (keys mirror the model columns):

    {
      "retailer": {"code": "acme", "name": "Acme Motors"},
      "library": {
        "name": "Acme QA checklist", "version": 2,
        "effective_from": "2026-01-01T00:00:00+00:00",
        "effective_to": null
      },
      "checks": [
        {"code": "GREETING_01", "name": "Greeting", "description": "...",
         "check_type": "VERBATIM", "critical": true,
         "configuration": {"phrase": "..."}}
      ]
    }

This only maps JSON onto rows. Business rules (overlapping versions, config
shape per check_type, ...) are deliberately not checked here.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import Check, CheckLibrary, CheckType, Retailer
from app.repositories.base import BaseRepository
from app.repositories.retailer_repository import RetailerRepository


def load_check_library_from_json(
    session: Session, source: dict[str, Any] | str | Path
) -> CheckLibrary:
    """Get/create the retailer, create the versioned library and its checks, commit.

    `source` is the parsed JSON (dict) or a path to a JSON file. All-or-nothing:
    on any error the session is rolled back and nothing is persisted.
    """
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text("utf-8"))

    retailers = RetailerRepository(session)
    libraries = BaseRepository(CheckLibrary, session)
    checks = BaseRepository(Check, session)

    try:
        r = data["retailer"]
        retailer = next(iter(retailers.list(code=r["code"])), None) or retailers.create(
            code=r["code"], name=r["name"]
        )

        lib = data["library"]
        library = libraries.create(
            retailer_id=retailer.id,
            name=lib["name"],
            version=lib["version"],
            effective_from=datetime.fromisoformat(lib["effective_from"]),
            effective_to=_parse_optional_datetime(lib.get("effective_to")),
        )

        for c in data["checks"]:
            checks.create(
                library_id=library.id,
                code=c["code"],
                name=c["name"],
                description=c.get("description"),
                check_type=CheckType(c["check_type"]),
                critical=c.get("critical", False),
                configuration=c.get("configuration") or {},
            )

        libraries.commit()
    except Exception:
        session.rollback()
        raise
    return library


def _parse_optional_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
