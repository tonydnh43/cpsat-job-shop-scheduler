"""Reserve dated output paths without overwriting files from earlier runs."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


OUTPUT_TIMEZONE = ZoneInfo("Asia/Bangkok")


def get_output_timestamp():
    return datetime.now(OUTPUT_TIMEZONE)


def reserve_output_path(output_dir, filename, *, output_timestamp=None):
    """Create YYMMDD/HHMM_filename, adding _2, _3, ... on collisions.

    An exclusive empty file reserves the path before the exporter writes it,
    so concurrent runs also select distinct names.
    """
    timestamp = output_timestamp if output_timestamp is not None else get_output_timestamp()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=OUTPUT_TIMEZONE)
    timestamp = timestamp.astimezone(OUTPUT_TIMEZONE)
    directory = Path(output_dir) / timestamp.strftime("%y%m%d")
    directory.mkdir(parents=True, exist_ok=True)
    name = Path(filename).name
    path = directory / f"{timestamp:%H%M}_{name}"
    candidate = path
    counter = 2
    while True:
        try:
            candidate.touch(exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            counter += 1
