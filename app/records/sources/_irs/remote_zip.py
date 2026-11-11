"""Members of a zip file on a web server, read with HTTP range requests: the central directory
from the end of the file, then each wanted member's bytes, without downloading the archive.
read_member() also reads Deflate64 members (compression method 9, which Python's zipfile
can't), as some of the IRS's archives use."""

from __future__ import annotations

import io
import struct
import zipfile

DEFLATE64 = 9


class RangeFile(io.RawIOBase):
    """A read-only, seekable file over a URL, each read a range request (blocks cached)."""

    BLOCK = 256 * 1024

    def __init__(self, fetcher, url: str, size: int | None = None):
        self.fetcher, self.url, self.pos = fetcher, url, 0
        self.size = size if size is not None else self._size()
        self._blocks: dict[int, bytes] = {}

    def _size(self) -> int:
        resp = self.fetcher.request("HEAD", self.url, timeout=120)
        return int(resp.headers["content-length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def _range(self, start: int, end: int) -> bytes:
        resp = self.fetcher.request("GET", self.url, timeout=300,
                                    headers={"Range": f"bytes={start}-{end - 1}"})  # fmt: skip
        if resp.status_code != 206:
            raise OSError(f"{self.url}: no range support (HTTP {resp.status_code})")
        return resp.content

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        if not n:
            return b""
        start, end = self.pos, self.pos + n
        if n > self.BLOCK:  # a large read (a member's data): one request
            data = self._range(start, end)
        else:
            first, last = start // self.BLOCK, (end - 1) // self.BLOCK
            for b in range(first, last + 1):
                if b not in self._blocks:
                    lo = b * self.BLOCK
                    self._blocks[b] = self._range(lo, min(lo + self.BLOCK, self.size))
            data = b"".join(self._blocks[b] for b in range(first, last + 1))
            data = data[start - first * self.BLOCK : end - first * self.BLOCK]
        self.pos = end
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def open_zip(fetcher, url: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BufferedReader(RangeFile(fetcher, url), buffer_size=64 * 1024))


def read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    """A member's bytes, whatever its compression (Deflate64 included)."""
    info = archive.getinfo(name)
    if info.compress_type != DEFLATE64:
        return archive.read(name)
    import inflate64

    fp = archive.fp
    fp.seek(info.header_offset)
    header = fp.read(30)
    if header[:4] != b"PK\x03\x04":
        raise zipfile.BadZipFile(f"{name}: no local file header")
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    fp.seek(info.header_offset + 30 + name_len + extra_len)
    data = inflate64.Inflater().inflate(fp.read(info.compress_size))
    if zipfile.crc32(data) != info.CRC:
        raise zipfile.BadZipFile(f"{name}: CRC doesn't match")
    return data
