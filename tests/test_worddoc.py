"""The .doc reader (app/assistant/worddoc.py): a real Word 97-2003 file, and crafted ones that must
fail with DocError, fast, without growing memory."""

import contextlib
import pathlib
import random
import struct
import time

import pytest

from app.assistant.worddoc import DocError, doc_text

DOC = pathlib.Path("tests/fixtures/docs/rules.doc").read_bytes()


def test_reads_text_and_tables() -> None:
    text = doc_text(DOC)
    assert "Quy tắc bảo mật" in text and "tenantId" in text
    assert "| Mức | Ví dụ |\n|---|---|\n| critical | SQL injection |" in text


def test_not_a_doc() -> None:
    with pytest.raises(DocError):
        doc_text(b"PK\x03\x04 not ole")


def test_huge_fat_count_and_repeated_sectors_stay_bounded() -> None:
    data = bytearray(DOC)
    struct.pack_into("<I", data, 0x2C, 0xFFFFFFFF)  # FAT sector count
    first = struct.unpack_from("<I", data, 0x4C)[0]
    for i in range(109):  # every DIFAT slot names the same FAT sector
        struct.pack_into("<I", data, 0x4C + 4 * i, first)
    struct.pack_into("<I", data, 0x44, 0)  # a DIFAT chain pointing at sector 0…
    started = time.monotonic()
    with contextlib.suppress(DocError):
        doc_text(bytes(data))
    assert time.monotonic() - started < 2


def test_random_corruption_only_raises_doc_error() -> None:
    rng = random.Random(7)  # noqa: S311 - a fuzz seed, not a secret
    started = time.monotonic()
    for _ in range(150):
        data = bytearray(DOC)
        for _ in range(12):
            at = rng.randrange(0, min(len(data), 8192))
            data[at] = rng.randrange(256)
        with contextlib.suppress(DocError):
            doc_text(bytes(data))
    assert time.monotonic() - started < 20
