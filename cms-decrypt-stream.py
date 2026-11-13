#!/usr/bin/env python3
"""Stream-decrypt a CMS/PKCS#7 EnvelopedData file (DER/BER) with constant memory.

`openssl cms -decrypt` loads the whole input into one buffer and refuses
anything over ~1.5 GiB (LIMIT_BEFORE_EXPANSION in crypto/buffer/buffer.c),
reporting it as "BUF_MEM_grow_clean:malloc failure". This script reads only the
small CMS header, unwraps the content key with `openssl pkeyutl`, and pipes the
encrypted content through `openssl enc -d` chunk by chunk.

Handles backups made by `openssl cms -encrypt -aes256 -binary -stream -outform DER`
and by the older `openssl smime -encrypt ... -outform DER`.

Usage: cms-decrypt-stream.py <private_key.pem> <in.ssl> <out|->
Only stdlib + the openssl CLI are required.
"""
import io
import subprocess
import sys

OID_ENVELOPED_DATA = bytes.fromhex("2a864886f70d010703")  # 1.2.840.113549.1.7.3
OID_RSA_ENCRYPTION = bytes.fromhex("2a864886f70d010101")  # 1.2.840.113549.1.1.1
CIPHERS = {
    bytes.fromhex("608648016503040102"): ("aes-128-cbc", 16),
    bytes.fromhex("608648016503040116"): ("aes-192-cbc", 24),
    bytes.fromhex("60864801650304012a"): ("aes-256-cbc", 32),
}
CHUNK = 1 << 20


def read_exact(f, n):
    b = f.read(n)
    if len(b) != n:
        raise ValueError("unexpected end of input")
    return b


def read_hdr(f):
    """Return (tag, length, header_size); length is None for indefinite."""
    tag, first = read_exact(f, 2)
    if tag & 0x1F == 0x1F:
        raise ValueError("high-tag-number form not supported")
    if first == 0x80:
        return tag, None, 2
    if first & 0x80:
        n = first & 0x7F
        return tag, int.from_bytes(read_exact(f, n), "big"), 2 + n
    return tag, first, 2


def enc_len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def read_tlv(f):
    """Read one small element fully; indefinite lengths are re-encoded as definite."""
    tag, length, _ = read_hdr(f)
    if length is not None:
        return tag, read_exact(f, length)
    parts = []
    while True:
        ctag, cval = read_tlv(f)
        if ctag == 0 and not cval:  # end-of-contents
            return tag, b"".join(parts)
        parts.append(bytes([ctag]) + enc_len(len(cval)) + cval)


def parse_all(buf):
    f, out = io.BytesIO(buf), []
    while f.tell() < len(buf):
        out.append(read_tlv(f))
    return out


def expect(tag, want, what):
    if tag != want:
        raise ValueError(f"{what}: expected tag 0x{want:02x}, got 0x{tag:02x}")


def copy(f, n, out):
    while n:
        b = read_exact(f, min(n, CHUNK))
        out.write(b)
        n -= len(b)


def copy_octets(f, length, out):
    """Copy the payload of a constructed OCTET STRING; returns bytes consumed."""
    consumed = 0
    while length is None or consumed < length:
        tag, clen, hsize = read_hdr(f)
        consumed += hsize
        if length is None and tag == 0 and clen == 0:
            break
        if tag == 0x04:
            copy(f, clen, out)
            consumed += clen
        elif tag == 0x24:
            consumed += copy_octets(f, clen, out)
        else:
            raise ValueError(f"unexpected tag 0x{tag:02x} in encrypted content")
    return consumed


def unwrap_key(key_path, recipients, key_len):
    for tag, val in recipients:
        if tag != 0x30:  # only KeyTransRecipientInfo
            continue
        fields = parse_all(val)
        alg = parse_all(fields[2][1])
        if alg[0][1] != OID_RSA_ENCRYPTION:
            continue
        p = subprocess.run(
            ["openssl", "pkeyutl", "-decrypt", "-inkey", key_path],
            input=fields[3][1], capture_output=True,
        )
        if p.returncode == 0 and len(p.stdout) == key_len:
            return p.stdout
    raise ValueError("no RSA recipient could be decrypted with this private key")


def main(key_path, in_path, out_path):
    with open(in_path, "rb", buffering=CHUNK) as f:
        expect(read_hdr(f)[0], 0x30, "ContentInfo")
        tag, oid = read_tlv(f)
        if tag != 0x06 or oid != OID_ENVELOPED_DATA:
            raise ValueError("not a CMS EnvelopedData file")
        expect(read_hdr(f)[0], 0xA0, "ContentInfo content")
        expect(read_hdr(f)[0], 0x30, "EnvelopedData")
        expect(read_tlv(f)[0], 0x02, "version")
        tag, val = read_tlv(f)
        if tag == 0xA0:  # optional originatorInfo
            tag, val = read_tlv(f)
        expect(tag, 0x31, "recipientInfos")
        recipients = parse_all(val)

        expect(read_hdr(f)[0], 0x30, "EncryptedContentInfo")
        expect(read_tlv(f)[0], 0x06, "contentType")
        tag, val = read_tlv(f)
        expect(tag, 0x30, "contentEncryptionAlgorithm")
        alg = parse_all(val)
        if alg[0][1] not in CIPHERS:
            raise ValueError(f"unsupported content cipher OID {alg[0][1].hex()}")
        cipher, key_len = CIPHERS[alg[0][1]]
        iv = alg[1][1]

        key = unwrap_key(key_path, recipients, key_len)
        out = sys.stdout.buffer if out_path == "-" else open(out_path, "wb")
        dec = subprocess.Popen(
            ["openssl", "enc", "-d", f"-{cipher}", "-K", key.hex(), "-iv", iv.hex()],
            stdin=subprocess.PIPE, stdout=out,
        )
        tag, length, _ = read_hdr(f)
        if tag == 0x80:  # primitive [0] IMPLICIT encryptedContent
            copy(f, length, dec.stdin)
        elif tag == 0xA0:  # constructed (what -stream produces)
            copy_octets(f, length, dec.stdin)
        else:
            raise ValueError(f"unexpected encryptedContent tag 0x{tag:02x}")
        dec.stdin.close()
        if dec.wait() != 0:
            raise ValueError("openssl enc -d failed")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__.strip().splitlines()[-2])
    try:
        main(*sys.argv[1:])
    except (ValueError, OSError) as e:
        sys.exit(f"cms-decrypt-stream: {e}")
