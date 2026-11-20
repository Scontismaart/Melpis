# Optional SDK for the independent GDPR journal operator

`src/core/gdpr/operator_r2_client.py` provides `open_r2_journal_store(...)` only
for the independent operator's process. It is not imported by the application.
The operator passes the account identifier and explicit bucket-scoped access
key, secret and optional session token from authorized independent custody.
Do not paste credentials into a command, a notebook, a test, a log or the
repository. Use different credentials for the publisher and the verifier.

The factory derives the HTTPS EU account endpoint with signing region `auto`,
uses SigV4, TLS certificate validation, path-style addressing, disables SDK
proxy/configured endpoint overrides, and checks the actual outgoing method,
host and exact journal object path. The verifier also refuses writes locally.
The operation model must support `PutObject.IfNoneMatch`. No automatic retries
are enabled. `R2JournalStore.put_once` still requires authenticated readback to
resolve conflicts or uncertain writes. SDK debug logging must remain disabled
in the isolated operator process. This factory never provisions a bucket,
discovers operational credentials, changes a bucket, lists or deletes objects.

The R2 account owner must separately verify the dedicated private EU bucket,
the publisher's bucket-scoped Object Read & Write access, the verifier's
bucket-scoped Object Read only access, and the approved recovery inventory.
The client restriction supplements these provider-side controls. References:

- [Cloudflare boto3 example](https://developers.cloudflare.com/r2/examples/aws/boto3/)
- [R2 jurisdiction endpoints](https://developers.cloudflare.com/r2/reference/data-location/)
- [R2 S3 conditional object writes](https://developers.cloudflare.com/r2/api/s3/api/)
- [Botocore client configuration](https://docs.aws.amazon.com/botocore/latest/reference/config.html)

## Reproduce the isolated SDK test

The following is the separately pinned SDK test environment, not a change to
the application's reviewed-runtime dependency graph. Install into a fresh
Python 3.12 virtual environment, never the application's interpreter. On
Windows, use the virtual environment's `Scripts/python.exe`; on Linux, use
its `bin/python`.

```text
python -m venv <private-operator-venv>
<venv-python> -m pip install boto3==1.44.7 botocore==1.44.7 s3transfer==0.19.3 jmespath==1.1.0 python-dateutil==2.9.0.post0 urllib3==2.7.0 six==1.17.0 cryptography==50.0.1 cffi==2.1.0 pycparser==3.0
<venv-python> -m pip install pytest==8.4.2 colorama==0.4.6 iniconfig==2.3.1 packaging==26.3 pluggy==1.7.0 pygments==2.21.0
<venv-python> -m pip check
<venv-python> -m pytest --noconftest tests/unit/test_gdpr_operator_r2_client.py -q
```

Run the test from the root of the exact checkout. Its synthetic credentials,
SDK Stubber and replaced HTTP transport exercise conditional PUT, readback,
the local verifier role, explicit credentials and rejection of redirected or
wrong-bucket requests without contacting R2. A missing optional SDK skips only
the SDK-dependent tests; input and request-boundary tests still run. The
standalone invocation omits the application's unrelated test fixtures.

This evidence establishes SDK serialization and defensive local configuration.
It does not certify the account, actual bucket permissions, network TLS
interoperability, conditional-write behavior of a real bucket or key custody.
These require the separately authorized operational drill with real,
independently provisioned credentials. Review the dependency and security
inventory of the operator environment before operational commissioning.
