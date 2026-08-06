"""Self-update: check S3 for a newer release, download it, apply it on restart.

The pieces, in dependency order:

    version.py    what version am I, on which channel
    manifest.py   the release description format + canonical hashing
    signing.py    Ed25519 sign/verify - the trust anchor for everything above
    client.py     check / download / stage / verify, with progress
    apply.py      the atomic app/ directory swap, run by the launcher
    routes.py     /api/update/* for the UI

Design notes worth keeping are in manifest.py (why content-addressed blobs
make updates tiny) and apply.py (why the swap happens at launch, not live).
"""
