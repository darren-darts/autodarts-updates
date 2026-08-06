"""Where updates are fetched from.

Set at release time by `tools/release.py`, which reads `release.toml` and
writes the value here so every published build knows its own origin. A
source checkout leaves it empty, which disables update checking rather than
pointing somewhere wrong.

Point this at CloudFront rather than the bucket's REST endpoint if you add a
distribution later: the client only ever performs plain HTTPS GETs, so the
two are interchangeable, and nothing else has to change.

The URL is not a secret and does not need to be - the signature in
signing.py, not the obscurity of this address, is what makes an update
trustworthy.
"""

UPDATE_BASE_URL = "https://darren-darts.github.io/autodarts-updates"

# Overridable from settings for testing against a local server; see
# client.base_url(). Production installs never need to set it.
SETTINGS_KEY = "updates"
