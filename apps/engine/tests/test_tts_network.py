"""How the voiceover stage reaches Azure.

edge-tts is the one outbound caller in this repo that does not go through `httpx`,
so it is the one that ignores `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and the proxy
variables. Behind a TLS-inspecting proxy it is the only provider that fails, and it
fails at stage 9 of 17 — after the research and the whole script chain have been
paid for.

The first attempt at this fix passed edge-tts a connector carrying our SSL context.
It looked right and did nothing: edge-tts passes its own context to
`ws_connect(ssl=...)`, and that explicit argument beats the connector's. The test
that pins the real seam is `test_the_bundle_lands_on_the_context_edge_tts_actually_uses`.
"""

from __future__ import annotations

import ssl

import pytest

from engine.workflows import media

#: Every variable this module's code reads. Cleared for all tests, because a
#: developer machine or CI runner behind a proxy has several of these set already —
#: and a test that silently reads the ambient `REQUESTS_CA_BUNDLE` passes or fails
#: depending on whose laptop it is running on.
_ENV_VARS = (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    media._trust_extra_cas.cache_clear()
    yield
    media._trust_extra_cas.cache_clear()


@pytest.fixture
def bundle(tmp_path):
    """A freshly minted CA that nothing already trusts.

    Deliberately not a copy of certifi's bundle: `load_verify_locations` de-dupes,
    so re-loading certs the context already holds leaves the count unchanged and
    every assertion below passes against a function that did nothing at all.
    """
    path = tmp_path / "corporate-ca.pem"
    path.write_bytes(_self_signed_ca())
    return path


def _self_signed_ca() -> bytes:
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Studio Test Root CA")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


class TestCaBundle:
    def test_no_bundle_configured_changes_nothing(self):
        assert media._trust_extra_cas() is None

    @pytest.mark.parametrize("var", ["SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"])
    def test_either_standard_variable_is_honoured(self, var, bundle, monkeypatch):
        """Both, because `httpx` reads both and the point is that one setting
        configures every outbound call in the repo."""
        monkeypatch.setenv(var, str(bundle))
        assert media._trust_extra_cas() == str(bundle)

    def test_a_set_but_empty_variable_is_not_a_bundle(self, monkeypatch):
        """`SSL_CERT_FILE=` is how a lot of CI config expresses "unset". Treated as
        a path it reaches `load_verify_locations("")`, which raises inside the
        voiceover stage."""
        monkeypatch.setenv("SSL_CERT_FILE", "")
        assert media._trust_extra_cas() is None

    def test_a_path_that_does_not_exist_is_ignored(self, monkeypatch, tmp_path):
        """A stale variable pointing at nothing must not crash the voiceover stage —
        `load_verify_locations` raises on a missing file."""
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "gone.pem"))
        assert media._trust_extra_cas() is None

    def test_the_bundle_lands_on_the_context_edge_tts_actually_uses(self, bundle, monkeypatch):
        """The seam the first attempt missed.

        A connector built with our context is silently discarded, because edge-tts
        passes `ssl=_SSL_CTX` to `ws_connect` explicitly. Verifying against the
        module global is the only assertion that can tell the two apart.
        """
        from edge_tts import communicate

        monkeypatch.setenv("SSL_CERT_FILE", str(bundle))

        # DER bytes, not subject names. `_SSL_CTX` is a process-wide global that
        # every test in this class adds to, and each mints a CA with the same
        # common name — keyed on the subject, this test's certificate is
        # indistinguishable from the previous test's and the diff comes back empty.
        def der() -> set[bytes]:
            return set(communicate._SSL_CTX.get_ca_certs(binary_form=True))

        before = der()
        media._trust_extra_cas()

        assert der() - before, (
            "the CA never reached the context edge-tts verifies against — a "
            "connector-based fix looks correct here and does nothing"
        )

    def test_the_public_roots_stay_trusted(self, bundle, monkeypatch):
        """Additive, not a replacement. Swapping the context out would break every
        network that is *not* behind a proxy — the common case."""
        from edge_tts import communicate

        monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
        media._trust_extra_cas()
        assert len(communicate._SSL_CTX.get_ca_certs()) > 100

    def test_verification_is_never_disabled(self, bundle, monkeypatch):
        """The shortcut this whole function exists to avoid."""
        from edge_tts import communicate

        monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
        media._trust_extra_cas()
        assert communicate._SSL_CTX.verify_mode is ssl.CERT_REQUIRED
        assert communicate._SSL_CTX.check_hostname is True

    def test_it_is_applied_once_not_per_voiceover(self, bundle, monkeypatch):
        """Reloading a bundle on every synthesis is pure waste on a hot path."""
        monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
        media._trust_extra_cas()
        media._trust_extra_cas()
        assert media._trust_extra_cas.cache_info().hits >= 1

    def test_an_upstream_rename_warns_instead_of_crashing(self, bundle, monkeypatch):
        """`_SSL_CTX` is a private global in someone else's package. If it moves,
        voiceover should keep working on every network that does not need it."""
        from edge_tts import communicate

        monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
        monkeypatch.delattr(communicate, "_SSL_CTX", raising=False)
        assert media._trust_extra_cas() is None


class TestProxy:
    def test_no_proxy_configured_returns_none(self):
        assert media._tts_proxy() is None

    @pytest.mark.parametrize("var", ["HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"])
    def test_each_standard_variable_is_read(self, var, monkeypatch):
        """edge-tts takes a proxy argument but never reads the environment for one,
        so on a network where outbound traffic *must* be proxied it simply fails."""
        monkeypatch.setenv(var, "http://proxy.internal:8080")
        assert media._tts_proxy() == "http://proxy.internal:8080"

    def test_https_proxy_wins_over_the_catch_all(self, monkeypatch):
        monkeypatch.setenv("ALL_PROXY", "http://catch-all:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://specific:2")
        assert media._tts_proxy() == "http://specific:2"

    def test_an_empty_variable_is_not_a_proxy(self, monkeypatch):
        """Set-but-empty is how shells express "unset" in a lot of CI config, and
        passing "" to aiohttp is a different failure from passing nothing."""
        monkeypatch.setenv("HTTPS_PROXY", "")
        assert media._tts_proxy() is None
