"""Optional SDK adapter for an isolated GDPR journal operator process.

The application runtime does not use this module. Credentials must be supplied
by the authorized operator from independent custody, and scoped to the exact
private EU journal bucket. No management API or credential discovery is used.
"""

from contextlib import contextmanager
import logging
import os
import re
from urllib.parse import urlsplit

from src.core.gdpr.journal_publication import R2JournalStore
from src.core.gdpr.restore_journal import RestoreJournalInvalid


class _ReadOnlyJournalStore(R2JournalStore):
    def put_once(self, name, ciphertext):
        raise RestoreJournalInvalid("journal_r2_read_only")


def _credential(value):
    return type(value) is str and re.fullmatch(r"[!-~]{1,4096}", value) is not None


def _require_safe_logging():
    # Botocore diagnostic logging can include signed headers. The operator
    # should run this in a dedicated process with SDK debugging disabled.
    names = ("botocore", "botocore.auth", "botocore.endpoint", "botocore.httpsession",
             "botocore.parsers", "boto3")
    if any(logging.getLogger(name).isEnabledFor(logging.DEBUG) for name in names):
        raise RestoreJournalInvalid("journal_r2_sdk_debug_logging_enabled")


def _restrict_request(host, request, *, read_only=False, **kwargs):
    _require_safe_logging()
    try:
        url = urlsplit(request.url)
        prefix = "/" + R2JournalStore._BUCKET + "/"
        methods = ("GET",) if read_only else ("GET", "PUT")
        if (url.scheme != "https" or url.netloc != host or url.fragment or url.query
                or not url.path.startswith(prefix) or request.method not in methods):
            raise ValueError
        R2JournalStore._validate_name(url.path[len(prefix):])
    except (AttributeError, TypeError, ValueError):
        raise RestoreJournalInvalid("journal_r2_request_endpoint_invalid") from None


@contextmanager
def open_r2_journal_store(account_id, *, access_key_id, secret_access_key,
                          read_only, session_token=None):
    """Construct a short-lived real SDK client with a mandatory EU endpoint.

    ``read_only`` adds a local write prohibition for a recovery verifier. It
    supplements (and cannot prove) the independently provisioned credential
    permissions. Publisher and verifier must possess different credentials.
    Creation does not make an HTTP request. Only the store's explicit object
    operations may access R2 when called by an authorized operator.
    """
    if (type(account_id) is not str or re.fullmatch(r"[0-9a-f]{32}", account_id) is None
            or not _credential(access_key_id) or not _credential(secret_access_key)
            or type(read_only) is not bool or (session_token is not None and not _credential(session_token))):
        raise RestoreJournalInvalid("journal_r2_explicit_credentials_required")
    _require_safe_logging()
    try:
        from boto3.session import Session
        from botocore.config import Config
        from botocore.session import Session as CoreSession
    except ImportError:
        raise RestoreJournalInvalid("journal_r2_operator_sdk_unavailable") from None

    client = None
    try:
        host = account_id + ".eu.r2.cloudflarestorage.com"
        # Override the profile resolver itself; setting profile=None alone
        # can fall through to AWS_PROFILE/AWS_DEFAULT_PROFILE in botocore.
        core = CoreSession(session_vars={"profile": (None, None, None, None)})
        # A dedicated session must not import a local AWS profile, shared
        # credential file, alternate service model, or environment endpoint.
        for name, value in (("config_file", os.devnull), ("credentials_file", os.devnull),
                            ("data_path", "")):
            core.set_config_variable(name, value)
        session = Session(botocore_session=core, aws_access_key_id=access_key_id,
                          aws_secret_access_key=secret_access_key, aws_session_token=session_token,
                          region_name="auto")
        client = session.client(
            "s3", region_name="auto", endpoint_url="https://" + host, use_ssl=True, verify=True,
            aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
            config=Config(
                signature_version="s3v4", s3={"addressing_style": "path"},
                retries={"mode": "standard", "total_max_attempts": 1},
                connect_timeout=10, read_timeout=30, proxies={},
                ignore_configured_endpoint_urls=True, use_fips_endpoint=False, use_dualstack_endpoint=False,
                request_checksum_calculation="when_required", response_checksum_validation="when_required",
                account_id_endpoint_mode="disabled",
            ),
        )
        if "IfNoneMatch" not in client.meta.service_model.operation_model("PutObject").input_shape.members:
            raise ValueError
        # Defense in depth against a redirected endpoint or SDK configuration
        # drift. Only the fixed EU bucket's object GET/PUT may leave the host.
        client.meta.events.register("before-sign.s3", lambda **kwargs: _require_safe_logging())
        client.meta.events.register(
            "before-send.s3", lambda request, **kwargs: _restrict_request(host, request, read_only=read_only),
        )
        store_type = _ReadOnlyJournalStore if read_only else R2JournalStore
        store = store_type(client)
    except Exception:
        if client is not None:
            client.close()
        raise RestoreJournalInvalid("journal_r2_operator_sdk_configuration_invalid") from None
    try:
        yield store
    finally:
        client.close()
