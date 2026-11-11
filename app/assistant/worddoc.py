"""The text of a legacy Word document (.doc, Word 97-2003), with the standard library only.

A .doc is an OLE compound file (MS-CFB) holding a `WordDocument` stream and a `0Table` /
`1Table` stream (MS-DOC). The text is read through the piece table (the CLX at `fcClx` of the
FIB): each piece is a run of characters, 8-bit (Windows-1252) or UTF-16. Only the main document
(the first `ccpText` characters) is kept: no header, footnote, comment, macro or embedded object
is read, and nothing is executed. Field codes (between 0x13 and 0x14) are dropped, their shown
result kept; paragraph and cell marks become line breaks / tabs.

Every offset and length read from the file is checked against the data, sector chains are
bounded (a loop or a chain longer than the file ends the read), so a crafted file fails with
`DocError` instead of looping or allocating without limit. Encrypted documents are refused.
"""

import struct

SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
END_OF_CHAIN = 0xFFFFFFFE
FREE = 0xFFFFFFFF
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_DIRECTORY_BYTES = 1024 * 1024
MAX_CHARS = 2_000_000


class DocError(ValueError):
    """Not a .doc this reader can open (corrupt, encrypted, or another kind of OLE file)."""


def _u16(data: bytes, at: int) -> int:
    if at < 0 or at + 2 > len(data):
        raise DocError("tệp .doc bị hỏng (đọc ngoài phạm vi)")
    return int(struct.unpack_from("<H", data, at)[0])


def _u32(data: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(data):
        raise DocError("tệp .doc bị hỏng (đọc ngoài phạm vi)")
    return int(struct.unpack_from("<I", data, at)[0])


class _Cfb:
    """Just enough of MS-CFB to read named streams of the root storage."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or data[:8] != SIGNATURE:
            raise DocError("không phải tệp Word .doc (thiếu chữ ký OLE)")
        self.data = data
        shift = _u16(data, 0x1E)
        mini_shift = _u16(data, 0x20)
        if shift not in (9, 12) or mini_shift != 6:
            raise DocError("tệp .doc có kích thước sector lạ")
        self.size = 1 << shift
        self.mini_size = 1 << mini_shift
        self.mini_cutoff = _u32(data, 0x38)
        self.sectors = max(0, (len(data) - self.size) // self.size)
        self.fat = self._fat()
        self.entries = self._directory(_u32(data, 0x30))
        root = self.entries[0] if self.entries else None
        if root is None or root[1] != 5:
            raise DocError("tệp .doc thiếu thư mục gốc")
        self.mini_stream = self._chain_bytes(root[2], root[3]) if root[3] else b""
        first_mini_fat, mini_fat_count = _u32(data, 0x3C), _u32(data, 0x40)
        raw = (
            self._chain_bytes(first_mini_fat, min(mini_fat_count, self.sectors) * self.size)
            if mini_fat_count
            else b""
        )
        self.mini_fat = [_u32(raw, i) for i in range(0, len(raw) - 3, 4)]

    def _sector(self, n: int) -> bytes:
        if n >= self.sectors:
            raise DocError("tệp .doc bị hỏng (sector ngoài tệp)")
        start = (n + 1) * self.size
        return self.data[start : start + self.size]

    def _fat(self) -> list[int]:
        """The FAT: one entry per sector of the file, never more (a crafted header cannot
        list more FAT sectors than the file has, or the same one twice)."""
        data = self.data
        difat = [_u32(data, 0x4C + 4 * i) for i in range(109)]
        next_difat = _u32(data, 0x44)
        visited: set[int] = set()
        while next_difat not in (END_OF_CHAIN, FREE):
            if next_difat in visited or len(visited) > self.sectors:
                raise DocError("tệp .doc bị hỏng (DIFAT vòng lặp)")
            visited.add(next_difat)
            block = self._sector(next_difat)
            per = self.size // 4 - 1
            difat += [_u32(block, 4 * i) for i in range(per)]
            next_difat = _u32(block, 4 * per)
        wanted = min(_u32(data, 0x2C), self.sectors)
        fat: list[int] = []
        used: set[int] = set()
        for n in difat[:wanted]:
            if n in (FREE, END_OF_CHAIN) or n in used:
                continue
            used.add(n)
            block = self._sector(n)
            fat += [_u32(block, 4 * i) for i in range(self.size // 4)]
            if len(fat) >= self.sectors:
                break
        return fat[: self.sectors]

    @staticmethod
    def _chain(start: int, table: list[int], most: int) -> list[int]:
        """The sector chain from `start`: a loop, a sector out of the table, or more than `most`
        sectors (the length the stream says it has) is a corrupt file."""
        out: list[int] = []
        seen: set[int] = set()
        n = start
        while n not in (END_OF_CHAIN, FREE):
            if n >= len(table) or n in seen or len(out) >= most:
                raise DocError("tệp .doc bị hỏng (chuỗi sector)")
            seen.add(n)
            out.append(n)
            n = table[n]
        return out

    def _chain_bytes(self, start: int, size: int) -> bytes:
        if size > MAX_STREAM_BYTES:
            raise DocError("tệp .doc quá lớn")
        most = -(-size // self.size) + 1
        parts = [self._sector(n) for n in self._chain(start, self.fat, most)]
        return b"".join(parts)[:size]

    def _directory(self, start: int) -> list[tuple[str, int, int, int]]:
        most = MAX_DIRECTORY_BYTES // self.size
        raw = b"".join(self._sector(n) for n in self._chain(start, self.fat, most))
        entries: list[tuple[str, int, int, int]] = []
        for at in range(0, len(raw) - 127, 128):
            name_len = _u16(raw, at + 64)
            name = raw[at : at + max(0, min(name_len, 64) - 2)].decode("utf-16-le", "replace")
            kind = raw[at + 66]
            start_sector = _u32(raw, at + 116)
            size = _u32(raw, at + 120)
            entries.append((name, kind, start_sector, size))
        return entries

    def stream(self, name: str) -> bytes:
        for entry_name, kind, start, size in self.entries:
            if kind == 2 and entry_name == name:
                if size > MAX_STREAM_BYTES:
                    raise DocError("tệp .doc quá lớn")
                if size < self.mini_cutoff:
                    most = -(-size // self.mini_size) + 1
                    sectors = self._chain(start, self.mini_fat, most)
                    out = bytearray()
                    for n in sectors:
                        begin = n * self.mini_size
                        if begin + self.mini_size > len(self.mini_stream):
                            raise DocError("tệp .doc bị hỏng (mini stream)")
                        out += self.mini_stream[begin : begin + self.mini_size]
                    return bytes(out[:size])
                return self._chain_bytes(start, size)
        raise DocError(f"tệp .doc thiếu stream {name}")


def _clean(chars: str) -> str:
    """Field codes dropped (their result kept), Word's marks turned into plain text."""
    out: list[str] = []
    depth = 0  # inside 0x13 … 0x14: field instructions, not shown text
    stack: list[bool] = []
    for ch in chars:
        code = ord(ch)
        if code == 0x13:
            stack.append(True)
            depth += 1
            continue
        if code == 0x14:
            if stack and stack[-1]:
                stack[-1] = False
                depth -= 1
            continue
        if code == 0x15:
            if stack and stack.pop():
                depth -= 1
            continue
        if depth > 0:
            continue
        if ch == "\r":
            out.append("\n")
        elif code == 0x07:  # end of a table cell / row
            out.append("\t")
        elif code in (0x0B, 0x0C, 0x0E):  # line, page, column break
            out.append("\n")
        elif code in (0x01, 0x08, 0x02, 0x05, 0x1E, 0x1F):  # pictures, notes, hyphens…
            if code == 0x1E:
                out.append("-")
            continue
        elif code < 0x20 and ch not in "\t\n":
            continue
        else:
            out.append(ch)
    # A cell ends with a cell mark (tab here), a row with one more: "a\tb\t\t" is a row.
    text = "".join(out).replace("\t\t", "\t\n")
    lines: list[str] = []
    in_table = False
    for line in text.split("\n"):
        if line.endswith("\t"):  # a table row
            cells = [" ".join(c.split()).replace("|", "\\|") for c in line[:-1].split("\t")]
            if not in_table and lines and lines[-1].strip():
                lines.append("")
            lines.append("| " + " | ".join(cells) + " |")
            if not in_table:
                lines.append("|" + "---|" * len(cells))
            in_table = True
            continue
        if in_table:
            lines.append("")
            in_table = False
        lines.append(line.rstrip())
    return "\n".join(lines)


def doc_text(data: bytes) -> str:
    """The main text of a Word 97-2003 document."""
    cfb = _Cfb(data)
    word = cfb.stream("WordDocument")
    if _u16(word, 0) != 0xA5EC:
        raise DocError("không phải tài liệu Word (.doc)")
    flags = _u16(word, 0x0A)
    if flags & 0x0100:
        raise DocError("tài liệu .doc có mật khẩu: mở bằng Word, bỏ mật khẩu rồi tải lại")
    table = cfb.stream("1Table" if flags & 0x0200 else "0Table")
    ccp_text = _u32(word, 0x4C)
    fc_clx, lcb_clx = _u32(word, 0x01A2), _u32(word, 0x01A6)
    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        raise DocError("tài liệu .doc không có bảng nội dung (piece table)")
    clx = table[fc_clx : fc_clx + lcb_clx]
    at = 0
    while at < len(clx) and clx[at] == 0x01:  # Prc: formatting, skipped
        at += 3 + _u16(clx, at + 1)
    if at >= len(clx) or clx[at] != 0x02:
        raise DocError("tài liệu .doc bị hỏng (piece table)")
    lcb = _u32(clx, at + 1)
    plc = clx[at + 5 : at + 5 + lcb]
    if lcb < 4 or len(plc) < lcb:
        raise DocError("tài liệu .doc bị hỏng (piece table)")
    pieces = (lcb - 4) // 12
    cps = [_u32(plc, 4 * i) for i in range(pieces + 1)]
    out: list[str] = []
    total = 0
    limit = min(ccp_text or MAX_CHARS, MAX_CHARS)
    for i in range(pieces):
        start, end = cps[i], cps[i + 1]
        if end <= start:
            continue
        if start >= limit:
            break
        end = min(end, limit)
        pcd = 4 * (pieces + 1) + 8 * i
        fc = _u32(plc, pcd + 2)
        count = end - start
        if fc & 0x40000000:
            offset = (fc & ~0x40000000) // 2
            raw = word[offset : offset + count]
            text = raw.decode("cp1252", "replace")
        else:
            raw = word[fc : fc + 2 * count]
            text = raw.decode("utf-16-le", "replace")
        out.append(text)
        total += len(text)
        if total >= MAX_CHARS:
            break
    return _clean("".join(out))
