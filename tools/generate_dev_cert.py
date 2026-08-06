"""Generates a self-signed TLS certificate for local HTTPS.

Browsers only expose camera/microphone access (getUserMedia) in a "secure
context" - https, or localhost. A phone joining over the LAN is neither by
default, so the selfie feature needs HTTPS even for local/home use. A
self-signed cert is fine for a LAN app; each device just needs to click
through one browser warning the first time it connects.

Run whenever the machine's LAN IP changes (e.g. after reconnecting WiFi):
    python tools/generate_dev_cert.py

Output: config/certs/dev-cert.pem, config/certs/dev-key.pem
Then run uvicorn with --ssl-keyfile / --ssl-certfile - see README.md.
"""
from __future__ import annotations

import datetime
import ipaddress
import pathlib
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))
from network import list_lan_ips  # noqa: E402

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "certs"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "autodarts.local")])

    ip_addrs = ["127.0.0.1", *list_lan_ips()]
    san_names = [x509.DNSName("localhost")]
    san_names += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ip_addrs]

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    (OUT_DIR / "dev-key.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    (OUT_DIR / "dev-cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"wrote {OUT_DIR / 'dev-cert.pem'} and dev-key.pem")
    print(f"covers: localhost, {', '.join(ip_addrs)}")
    print("Regenerate this whenever your LAN IP changes.")


if __name__ == "__main__":
    main()
