"""Trusted timestamps, checked against a real DigiCert reply saved in tests/data."""

import hashlib
from pathlib import Path

import pytest

import sdfetch
from sdfetch import stamp

DATA = Path(__file__).parent / "data"
REPLY = (DATA / "digicert.tsr").read_bytes()
MANIFEST = (DATA / "digicert-manifest.jsonl").read_bytes()
DIGEST = hashlib.sha256(MANIFEST).digest()


def test_read_reply():
    got = stamp.read_reply(REPLY, DIGEST)
    assert got["sha256"] == DIGEST.hex()
    assert got["gen_time"].startswith("2026-11-10T")
    assert got["signers"] == 1
    with pytest.raises(stamp.StampError, match="different digest"):
        stamp.read_reply(REPLY, hashlib.sha256(b"something else").digest())


def test_request_token_round_trips():
    from asn1crypto import tsp

    req = tsp.TimeStampReq.load(stamp.request_token(DIGEST, 12345))
    assert req["message_imprint"]["hashed_message"].native == DIGEST
    assert req["nonce"].native == 12345 and req["cert_req"].native is True


def store_with_stamp(tmp_path):
    store = sdfetch.Store(tmp_path)
    (tmp_path / "manifest.jsonl").write_bytes(MANIFEST)
    store.path("stamps/x.tsr").parent.mkdir(parents=True)
    store.path("stamps/x.tsr").write_bytes(REPLY)
    return store, {"lines": 2, "token": "stamps/x.tsr"}


def test_verify_covers_the_first_lines_as_the_manifest_grows(tmp_path):
    store, entry = store_with_stamp(tmp_path)
    store.save("c.html", b"<p>c</p>", "https://example.gov/c")  # appended after the stamp
    assert stamp.verify(store, entry)["sha256"] == DIGEST.hex()
    # Changing a covered line breaks it
    lines = (tmp_path / "manifest.jsonl").read_bytes().splitlines(keepends=True)
    lines[0] = lines[0].replace(b"example.gov/a", b"example.gov/z")
    (tmp_path / "manifest.jsonl").write_bytes(b"".join(lines))
    with pytest.raises(stamp.StampError):
        stamp.verify(store, entry)


@pytest.mark.skipif(stamp.openssl() is None, reason="needs OpenSSL 3")
def test_verify_signature_with_openssl(tmp_path):
    store, entry = store_with_stamp(tmp_path)
    assert "Verification: OK" in stamp.verify_signature(store, entry)


def test_stamp_manifest_with_a_fake_authority(tmp_path):
    store = sdfetch.Store(tmp_path)
    (tmp_path / "manifest.jsonl").write_bytes(MANIFEST)
    asked = []

    def tsa(method, url, data=None, headers=None, **kw):
        asked.append((method, url, headers["Content-Type"]))
        from sdfetch._browser import BrowserResponse

        return BrowserResponse(url, 200, REPLY, {"Content-Type": "application/timestamp-reply"})

    from asn1crypto import tsp

    # The saved reply answers the nonce it was asked with, so ask with that one
    info = tsp.TimeStampResp.load(REPLY)["time_stamp_token"]["content"]
    nonce = info["encap_content_info"]["content"].parsed["nonce"].native
    f = sdfetch.Fetcher(strategy="http", memory=False, delay=0, clients={"http": tsa})
    entry = stamp.stamp_manifest(store, fetcher=f, tsas=["http://tsa.example"], nonce=nonce)
    assert asked == [("POST", "http://tsa.example", "application/timestamp-query")]
    assert entry["lines"] == 2 and store.has(entry["token"])
    assert stamp.stamps(store) == [entry]
    assert stamp.verify(store, entry)["sha256"] == DIGEST.hex()


@pytest.mark.network
def test_real_authority(tmp_path):
    store = sdfetch.Store(tmp_path)
    store.save("a.html", b"<p>a</p>", "https://example.gov/a")
    entry = stamp.stamp_manifest(store)
    assert stamp.verify(store, entry)["gen_time"] == entry["gen_time"]
    if stamp.openssl():
        stamp.verify_signature(store, entry)
