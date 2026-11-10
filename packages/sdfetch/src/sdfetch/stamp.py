"""Trusted timestamps (RFC 3161) on a store's manifest.

    from sdfetch import stamp

    entry = stamp.stamp_manifest(store)    # after a run
    stamp.verify(store, entry)             # the token matches the manifest's first N lines

The manifest is append-only, and each line holds a file's SHA-256. So stamping the
manifest's SHA-256 stamps every file fetched so far at once, and a stamp keeps verifying as
the manifest grows, because it covers a fixed number of lines (`lines`). Only the hash goes
to the timestamp authority.

Each stamp is saved as stamps/<time>.tsr (the authority's DER reply) and a line in
stamps.jsonl. To check one independently of this code (with OpenSSL 3; the LibreSSL that
ships with macOS can't build the certificate chain):

    head -n <lines> manifest.jsonl > covered.jsonl
    openssl ts -verify -data covered.jsonl -in stamps/<time>.tsr -CAfile <CA bundle>

verify_signature() runs exactly that.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from ._http import Fetcher
from ._store import Store, now

# Public RFC 3161 authorities, tried in order
TSAS = [
    "http://timestamp.digicert.com",
    "http://timestamp.sectigo.com",
    "https://freetsa.org/tsr",
]


class StampError(Exception):
    pass


def request_token(digest: bytes, nonce: int) -> bytes:
    """A DER TimeStampReq for a SHA-256 digest."""
    from asn1crypto import algos, tsp

    return tsp.TimeStampReq(
        {
            "version": 1,
            "message_imprint": tsp.MessageImprint(
                {
                    "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                    "hashed_message": digest,
                }
            ),
            "nonce": nonce,
            "cert_req": True,
        }
    ).dump()


def read_reply(reply: bytes, digest: bytes | None = None, nonce: int | None = None) -> dict:
    """Check an authority's reply and return what it attests: gen_time, serial, policy, the
    hashed message. Raises StampError if it wasn't granted or doesn't match."""
    from asn1crypto import tsp

    resp = tsp.TimeStampResp.load(reply)
    status = resp["status"]["status"].native
    if status not in ("granted", "granted_with_mods"):
        raise StampError(f"not granted: {status} {resp['status']['status_string'].native}")
    signed = resp["time_stamp_token"]["content"]
    info = signed["encap_content_info"]["content"].parsed
    imprint = info["message_imprint"]
    if imprint["hash_algorithm"]["algorithm"].native != "sha256":
        raise StampError("the token isn't over a SHA-256 digest")
    hashed = imprint["hashed_message"].native
    if digest is not None and hashed != digest:
        raise StampError("the token is for a different digest")
    if nonce is not None and info["nonce"].native != nonce:
        raise StampError("the token's nonce doesn't match the request")
    gen_time: datetime = info["gen_time"].native
    return {
        "gen_time": gen_time.isoformat(),
        "serial": str(info["serial_number"].native),
        "policy": info["policy"].native,
        "sha256": hashed.hex(),
        "signers": len(signed["signer_infos"]),
    }


def timestamp(
    digest: bytes,
    *,
    fetcher: Fetcher | None = None,
    tsas: list[str] | None = None,
    nonce: int | None = None,
):
    """Ask each authority in turn for a token over digest; returns (tsa, reply, attested).
    nonce: random unless given."""
    f = fetcher or Fetcher(strategy="http", memory=False, delay=1)
    errors = []
    for tsa in tsas or TSAS:
        n = nonce if nonce is not None else secrets.randbits(63)
        try:
            resp = f.request(
                "POST",
                tsa,
                data=request_token(digest, n),
                headers={"Content-Type": "application/timestamp-query"},
                check_blocked=False,
            )
            return tsa, resp.content, read_reply(resp.content, digest, n)
        except Exception as exc:  # noqa: BLE001 - try the next authority
            errors.append(f"{tsa}: {type(exc).__name__}: {exc}")
    raise StampError("no authority gave a timestamp: " + "; ".join(errors))


def _manifest_prefix(store: Store, lines: int) -> bytes:
    data = (store.root / "manifest.jsonl").read_bytes()
    kept = data.splitlines(keepends=True)[:lines]
    if len(kept) < lines:
        raise StampError(f"the manifest has fewer than {lines} lines")
    return b"".join(kept)


def stamp_manifest(store: Store, *, fetcher: Fetcher | None = None, tsas=None, nonce=None) -> dict:
    """Timestamp the manifest as it is now. Returns the stamps.jsonl entry."""
    path = store.root / "manifest.jsonl"
    if not path.exists():
        raise StampError("nothing to stamp: the store has no manifest")
    lines = len(path.read_bytes().splitlines())
    covered = _manifest_prefix(store, lines)
    digest = hashlib.sha256(covered).digest()
    tsa, reply, attested = timestamp(digest, fetcher=fetcher, tsas=tsas, nonce=nonce)
    when = attested["gen_time"].replace(":", "").replace("-", "")[:15]  # 20261110T172001
    name = f"stamps/{when}-{attested['serial'][-8:]}.tsr"
    target = store.path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(reply)
    entry = {"stamped_at": now(), "lines": lines, "tsa": tsa, "token": name, **attested}
    with (store.root / "stamps.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def stamps(store: Store) -> list[dict]:
    path = store.root / "stamps.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify(store: Store, entry: dict) -> dict:
    """Check a stamp against the manifest: the token is the authority's grant, and its digest
    is the SHA-256 of the manifest's first `lines` lines. Returns what the token attests.
    (This checks the token's contents, not the authority's signature; for that, use
    `openssl ts -verify` as in the module docstring.)"""
    digest = hashlib.sha256(_manifest_prefix(store, entry["lines"])).digest()
    return read_reply(store.read(entry["token"]), digest)


def openssl() -> str | None:
    """An OpenSSL that can verify timestamps: $SDFETCH_OPENSSL, Homebrew's OpenSSL 3, or the
    one on PATH if it isn't LibreSSL."""
    candidates = [os.environ.get("SDFETCH_OPENSSL"), "/opt/homebrew/opt/openssl@3/bin/openssl",
                  "/usr/local/opt/openssl@3/bin/openssl", shutil.which("openssl")]  # fmt: skip
    for c in candidates:
        if c and Path(c).exists():
            version = subprocess.run([c, "version"], capture_output=True, text=True).stdout
            if "LibreSSL" not in version:
                return c
    return None


def verify_signature(store: Store, entry: dict, *, ca_file: str | None = None) -> str:
    """Verify a stamp's signature and certificate chain with `openssl ts -verify` against
    the manifest's first `lines` lines. Returns OpenSSL's output; raises StampError if it
    fails or no suitable OpenSSL is installed."""
    import certifi

    binary = openssl()
    if binary is None:
        raise StampError("no OpenSSL 3 found (brew install openssl@3, or set SDFETCH_OPENSSL)")
    with tempfile.NamedTemporaryFile(suffix=".jsonl") as covered:
        covered.write(_manifest_prefix(store, entry["lines"]))
        covered.flush()
        result = subprocess.run(
            [binary, "ts", "-verify", "-data", covered.name, "-in", str(store.path(entry["token"])),
             "-CAfile", ca_file or certifi.where()],
            capture_output=True, text=True,
        )  # fmt: skip
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or "Verification: OK" not in output:
        raise StampError(f"openssl ts -verify failed: {output[-300:]}")
    return output
