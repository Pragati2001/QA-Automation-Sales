"""
GET /calls/{call_id}/audio: serve the call recording, with HTTP Range support.

Range requests are what let the browser's <audio> element jump to an evidence timestamp without
downloading the whole file first. The bytes come from the AudioStorage interface (not a file
path), so this keeps working when storage moves from local disk to blob storage. Each request
reads the whole object and slices it: fine for call-length audio, revisit if files get large.

Range handling: `bytes=a-b`, `bytes=a-` and `bytes=-n` (last n bytes) give 206. A range that
starts past the end gives 416. A syntactically invalid Range gives 400. Several ranges at once
are not supported and are answered with the whole file (RFC 9110 allows ignoring Range).
"""

import mimetypes
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Call, Recording
from app.services.storage.audio_storage import AudioStorage, get_audio_storage

router = APIRouter(prefix="/calls", tags=["audio"])

_RANGE_SPEC = re.compile(r"(\d*)-(\d*)")


@router.get("/{call_id}/audio")
def get_call_audio(
    call_id: int,
    range_header: str | None = Header(default=None, alias="Range"),
    db: Session = Depends(get_db),
    storage: AudioStorage = Depends(get_audio_storage),
):
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, f"Call {call_id} not found")
    recording = call.recording
    if recording is None:
        raise HTTPException(404, f"Call {call_id} has no recording")
    try:
        data = storage.get(recording.storage_reference)
    except FileNotFoundError:
        raise HTTPException(404, f"The audio file for call {call_id} is missing from storage") from None

    size = len(data)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private",  # recordings are sensitive: no shared caches
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(recording.original_filename)}",
    }
    media_type = _media_type(recording)

    byte_range = _parse_range(range_header, size) if range_header else None
    if byte_range is None:
        return Response(content=data, media_type=media_type, headers=headers)

    start, end = byte_range
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(content=data[start : end + 1], status_code=206, media_type=media_type, headers=headers)


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """-> (first, last) byte positions, inclusive; None to send the whole file."""
    unit, _, spec = header.strip().partition("=")
    if unit.strip().lower() != "bytes":
        raise HTTPException(400, "Unsupported Range unit; only 'bytes' is supported")
    if "," in spec:
        return None  # multiple ranges are not supported: answer with the full file
    match = _RANGE_SPEC.fullmatch(spec.strip())
    if not match or match.group(1) == match.group(2) == "":
        raise HTTPException(400, f"Malformed Range header: {header!r}")
    first, last = match.groups()

    unsatisfiable = HTTPException(
        416, "Requested range not satisfiable", headers={"Content-Range": f"bytes */{size}"}
    )
    if first == "":  # suffix range: the last N bytes
        n = int(last)
        if n == 0 or size == 0:
            raise unsatisfiable
        return max(size - n, 0), size - 1
    start = int(first)
    if last and int(last) < start:
        raise HTTPException(400, f"Malformed Range header: {header!r}")
    if start >= size:
        raise unsatisfiable
    return start, min(int(last), size - 1) if last else size - 1


def _media_type(recording: Recording) -> str:
    """The browser needs a sensible audio type to play the file; trust the upload's type only
    when it is one, otherwise go by the file name."""
    stored = recording.content_type
    if stored and stored.lower().startswith("audio/"):
        return stored
    guessed, _ = mimetypes.guess_type(recording.original_filename)
    if guessed and guessed != "application/octet-stream":  # a generic guess must not beat a specific stored type
        return guessed
    return stored or guessed or "application/octet-stream"
