# GDPR export download contract

## Issue an export

`GET /api/gdpr/export` requires an authenticated owner session with MFA (`aal2`).
The response is `application/json` and includes a relative `download_url` and
`expires_in_minutes: 15`. The API sets an HttpOnly, Secure-by-default,
`SameSite=Strict` cookie scoped to `/api/gdpr/download`. The short-lived token is
bound to the authenticated user, current authentication session, owner
membership, and organization selected when the export was created.

## Download

Call the returned path with the same authenticated session. For a user with
multiple organization memberships, include the organization selected when the
export was issued:

```js
const response = await fetch(apiOrigin + downloadUrl, {
  credentials: "include",
  headers: selectedOrganizationId
    ? { "X-Organization-Id": selectedOrganizationId }
    : {},
});
```

The endpoint is `GET /api/gdpr/download`; the former bearer URL
`GET /api/gdpr/download/{token}` is removed. A successful `200` returns the JSON
export and atomically consumes the token. A repeated or expired download returns
`404`. Authentication, MFA, account, session, tenant, role, or membership
failures return `401`/`403` without consuming the token. Storage or membership
service failures fail closed with `503` and leave the token available when the
underlying store has not already completed an atomic consume.

The Redis consume is an atomic compare-and-delete. If a connection drops after
Redis executes the script but before the client receives its reply, the result
is inherently ambiguous: no export is returned by that request, and the owner
may need to request a new export.

## Browser and proxy requirements

The current repository has no dashboard client that calls the GDPR export or
download endpoints. New browser clients should use the same origin or a
same-site `/api` reverse-proxy route, send credentials, and use the organization
header when multiple memberships exist. `SameSite=Strict` intentionally does
not support a genuinely cross-site cookie flow; third-party-cookie behavior is
not assumed. Cross-origin use must also be allowed by the API's credentialed
CORS origin list.

The response uses a relative API path and does not derive the URL from
`Host`, `X-Forwarded-Host`, or `X-Forwarded-Proto`. The production Compose
configuration routes `/api/*` through Caddy to the API and starts Uvicorn with
proxy-header processing disabled. Caddy forwards the request path and method,
including CORS `OPTIONS` preflight requests. The relative download path avoids
depending on forwarded host or scheme headers, so callers must not rely on
`request.url_for` for this link.

For cross-origin browser calls, the API must list the exact frontend origin in
`CORS_ORIGINS` or set it as `PUBLIC_APP_URL`; wildcard origins are incompatible
with credentialed cookies. The API's preflight response allows `GET`, the
`Authorization` header and `X-Organization-Id`, and returns
`Access-Control-Allow-Credentials: true`. `SameSite=Strict` still requires the
frontend and API to be same-site; a genuinely cross-site cookie flow is not
supported. In production, the same-origin Caddy route is the supported path.
