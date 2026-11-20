"""Real boto3 API model and HTTP boundary tests without opening a socket."""

import io
from types import SimpleNamespace

import pytest

from src.core.gdpr.operator_r2_client import open_r2_journal_store, _restrict_request
from src.core.gdpr.restore_journal import RestoreJournalInvalid


ACCOUNT = "a" * 32
HOST = ACCOUNT + ".eu.r2.cloudflarestorage.com"
BUCKET = "melpis-staging-gdpr-journal"
KEY = "11111111-1111-1111-1111-111111111111-1-" + "a" * 64 + ".fernet"
PAYLOAD = b"opaque-synthetic-ciphertext"


@pytest.fixture
def sdk(monkeypatch):
    pytest.importorskip("boto3", reason="optional isolated operator SDK")
    from botocore.httpsession import URLLib3Session
    from botocore.session import Session

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network request or SDK default credential discovery")

    monkeypatch.setattr(URLLib3Session, "send", forbidden)
    monkeypatch.setattr(Session, "get_credentials", forbidden)
    # Deliberately conflicting host environment: explicit credentials and a
    # fixed regional endpoint must be sufficient without AWS profiles.
    monkeypatch.setenv("AWS_PROFILE", "unknown-synthetic-profile")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://invalid.example")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unused-synthetic-environment-id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unused-synthetic-environment-key")
    monkeypatch.setenv("AWS_CA_BUNDLE", "nonexistent-synthetic-certificate-file")
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid.example")


def test_real_sdk_conditional_create_and_bounded_readback(sdk):
    from botocore.response import StreamingBody
    from botocore.stub import Stubber

    with open_r2_journal_store(ACCOUNT, access_key_id="synthetic-publisher-id",
                               secret_access_key="synthetic-publisher-secret", read_only=False) as store:
        client = store._client
        assert client.meta.endpoint_url == "https://" + HOST
        assert client.meta.region_name == "auto"
        assert client.meta.config.signature_version == "s3v4"
        assert client.meta.config.s3["addressing_style"] == "path"
        assert client.meta.config.proxies == {}
        assert client.meta.config.retries["total_max_attempts"] == 1
        assert client.meta.config.request_checksum_calculation == "when_required"
        assert client.meta.config.response_checksum_validation == "when_required"
        assert client._endpoint.http_session._verify is True
        assert client._request_signer._credentials.get_frozen_credentials().access_key == "synthetic-publisher-id"

        with Stubber(client) as stub:
            expected_put = {"Bucket": BUCKET, "Key": KEY, "Body": PAYLOAD, "IfNoneMatch": "*",
                            "ContentType": "application/octet-stream"}
            expected_get = {"Bucket": BUCKET, "Key": KEY}
            stub.add_response("put_object", {}, expected_put)
            stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(PAYLOAD), len(PAYLOAD)),
                                             "ContentLength": len(PAYLOAD)}, expected_get)
            store.put_once(KEY, PAYLOAD)
            assert store.read(KEY) == PAYLOAD
            # Provider conflict is accepted only through explicit readback.
            stub.add_client_error("put_object", service_error_code="PreconditionFailed",
                                  http_status_code=412, expected_params=expected_put)
            stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(PAYLOAD), len(PAYLOAD)),
                                             "ContentLength": len(PAYLOAD)}, expected_get)
            store.put_once(KEY, PAYLOAD)
            assert store.read(KEY) == PAYLOAD
            stub.assert_no_pending_responses()


def test_verifier_store_contains_local_write_prohibition(sdk):
    from botocore.response import StreamingBody
    from botocore.stub import Stubber

    with open_r2_journal_store(ACCOUNT, access_key_id="synthetic-reader-id",
                               secret_access_key="synthetic-reader-secret", read_only=True) as store:
        with Stubber(store._client) as stub:
            with pytest.raises(RestoreJournalInvalid, match="journal_r2_read_only"):
                store.put_once(KEY, PAYLOAD)
            stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(PAYLOAD), len(PAYLOAD)),
                                             "ContentLength": len(PAYLOAD)}, {"Bucket": BUCKET, "Key": KEY})
            assert store.read(KEY) == PAYLOAD
            stub.assert_no_pending_responses()


def test_wire_request_is_conditional_and_restricted_to_eu_endpoint(sdk, monkeypatch):
    from botocore.awsrequest import AWSResponse
    from botocore.httpsession import URLLib3Session

    captured = []

    class Raw(io.BytesIO):
        def stream(self, amt=1024, decode_content=False):
            while chunk := self.read(amt):
                yield chunk

    def fake_wire_send(_self, request):
        # This transport replacement observes the actual signed request;
        # no socket is opened and no credentials leave the process.
        captured.append(request)
        content = PAYLOAD if request.method == "GET" else b""
        return AWSResponse(request.url, 200, {"content-length": str(len(content))}, Raw(content))

    monkeypatch.setattr(URLLib3Session, "send", fake_wire_send)
    with open_r2_journal_store(ACCOUNT, access_key_id="synthetic-publisher-id",
                               secret_access_key="synthetic-publisher-secret", read_only=False) as store:
        store.put_once(KEY, PAYLOAD)
        assert store.read(KEY) == PAYLOAD
    assert [request.method for request in captured] == ["PUT", "GET"]
    assert all(request.url == f"https://{HOST}/{BUCKET}/{KEY}" for request in captured)
    assert captured[0].headers["If-None-Match"] in (b"*", "*")


@pytest.mark.parametrize("account,access,secret", [
    ("not-an-account", "id", "secret"), (ACCOUNT, "", "secret"),
    (ACCOUNT, "id", ""), (ACCOUNT, "id\n", "secret"),
])
def test_factory_rejects_invalid_explicit_inputs(account, access, secret):
    # Input checks occur before SDK import: no implicit credential fallback.
    with pytest.raises(RestoreJournalInvalid, match="journal_r2_explicit_credentials_required"):
        with open_r2_journal_store(account, access_key_id=access, secret_access_key=secret, read_only=False):
            pytest.fail("invalid SDK construction unexpectedly succeeded")


@pytest.mark.parametrize("url,method", [
    (f"http://{HOST}/{BUCKET}/{KEY}", "GET"),
    (f"https://{ACCOUNT}.r2.cloudflarestorage.com/{BUCKET}/{KEY}", "PUT"),
    (f"https://{HOST}/wrong-bucket/{KEY}", "GET"),
    (f"https://{HOST}/{BUCKET}/{KEY}", "DELETE"),
])
def test_redirect_or_wrong_object_target_is_blocked(url, method):
    with pytest.raises(RestoreJournalInvalid, match="journal_r2_request_endpoint_invalid"):
        _restrict_request(HOST, SimpleNamespace(url=url, method=method))


def test_wire_boundary_rejects_verifier_put():
    with pytest.raises(RestoreJournalInvalid, match="journal_r2_request_endpoint_invalid"):
        _restrict_request(HOST, SimpleNamespace(url=f"https://{HOST}/{BUCKET}/{KEY}", method="PUT"), read_only=True)
