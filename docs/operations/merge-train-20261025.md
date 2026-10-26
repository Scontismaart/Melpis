# Authorized V1 integration train — 25 October 2026

This record summarizes the six reviewed integrations merged in the authorized
normal merge train. It records repository, CI, and registry evidence only; it
does not certify staging, providers, operational readiness, or production.

## Integrated pull requests

| PR | Scope | Reviewed head | Merge commit | Post-merge CI | Passed / skipped / warnings |
|---|---|---|---|---|---|
| [#136](https://github.com/Scontismaart/Melpis/pull/136) | C01 local migration and schema evidence | `a6cc7d84a8da60caa1dac6061836e7eefaf491bc` | `9cb0482b42d68b31f1272ed4858d078dbfb3c50c` | [40211074370](https://github.com/Scontismaart/Melpis/actions/runs/40211074370) | 3,882 / 36 / 124 |
| [#137](https://github.com/Scontismaart/Melpis/pull/137) | C04 Stripe boundary and reliability controls | `937b6b8e8b2f61d15c2836b32a596f1c18d512bd` | `42dc1d0fddad69b6c8ec571642c6b188bc4c97f9` | [40213413682](https://github.com/Scontismaart/Melpis/actions/runs/40213413682) | 3,890 / 36 / 126 |
| [#135](https://github.com/Scontismaart/Melpis/pull/135) | B03 onboarding wizard and states | `25e6c06bfb8439c9bb2edb62dd138916d5134411` | `239b5f4a0af3dff04c9bbfe35997ce9ac29ac475` | [40215658663](https://github.com/Scontismaart/Melpis/actions/runs/40215658663) | 3,890 / 36 / 126 |
| [#138](https://github.com/Scontismaart/Melpis/pull/138) | B04 plans and truthful product copy | `172a11db02e6ba73eacf29cd4568a6dc9bda6649` | `7bef5f507e3ef9adfd2790d99a8191e9eb422c30` | [40218001703](https://github.com/Scontismaart/Melpis/actions/runs/40218001703) | 3,908 / 36 / 126 |
| [#141](https://github.com/Scontismaart/Melpis/pull/141) | Recovery-only exact OpenRouter model binding | `c263e66288162d3134f08d61e39fa35bf8e526f0` | `cfe09e2214431f4015e3fa01972cd1f553ad736e` | [40220861445](https://github.com/Scontismaart/Melpis/actions/runs/40220861445) | 3,918 / 36 / 126 |
| [#139](https://github.com/Scontismaart/Melpis/pull/139) | INF01 shared ONNX runtime and diagnostics | `fff34cbe0edb34e2f3a7ea82f808d31524ea65fa` | `800c56c0b631b81b7bbefe0f51c843dd13d498d1` | [40223566432](https://github.com/Scontismaart/Melpis/actions/runs/40223566432) | 3,972 / 36 / 126 |

The resulting main head is `800c56c0b631b81b7bbefe0f51c843dd13d498d1`.
The exact-head main CI passed. All four API/web amd64/arm64 image publications
and both multi-architecture manifests succeeded. The standalone Migration
check was not triggered because this train changed no SQL migration/schema
files; this is not a separate green Migration check.

## Published multi-architecture manifests

| PR | Image | Digest | Platforms |
|---|---|---|---|
| #136 | `ghcr.io/scontismaart/melpis-api:sha-9cb0482b42d68b31f1272ed4858d078dbfb3c50c` | `sha256:47dd40236248dbadf29a1dcbc2823d8d2125ae336a45299caa8b7d4ebb3a8191` | linux/amd64, linux/arm64 |
| #136 | `ghcr.io/scontismaart/melpis-web:sha-9cb0482b42d68b31f1272ed4858d078dbfb3c50c` | `sha256:6bce830cd9f27c54d547716f71c9246088f8de0ed3188d48655dfd9f4386b781` | linux/amd64, linux/arm64 |
| #137 | `ghcr.io/scontismaart/melpis-api:sha-42dc1d0fddad69b6c8ec571642c6b188bc4c97f9` | `sha256:5af655afeb5b71d07767364cb8d94f7b34dfbe07ddc80afa63fac21aa2c4e612` | linux/amd64, linux/arm64 |
| #137 | `ghcr.io/scontismaart/melpis-web:sha-42dc1d0fddad69b6c8ec571642c6b188bc4c97f9` | `sha256:9b3d543f82d4e13be8e122e7323776ce0a88b7dc250f2b6c4db76fc7cee6df9f` | linux/amd64, linux/arm64 |
| #135 | `ghcr.io/scontismaart/melpis-api:sha-239b5f4a0af3dff04c9bbfe35997ce9ac29ac475` | `sha256:a0985578743cec931ad2e801e0d8380913f536ce312b72fcbd31cb50d3460261` | linux/amd64, linux/arm64 |
| #135 | `ghcr.io/scontismaart/melpis-web:sha-239b5f4a0af3dff04c9bbfe35997ce9ac29ac475` | `sha256:54c5aec538cd5aacb22f69e6591fa39a8004de866df7d5722c22d54bc1ba9c6d` | linux/amd64, linux/arm64 |
| #138 | `ghcr.io/scontismaart/melpis-api:sha-7bef5f507e3ef9adfd2790d99a8191e9eb422c30` | `sha256:a51988881eca804175264ebe5379bb867ebb5ef30e468fdc11e2bc5942551370` | linux/amd64, linux/arm64 |
| #138 | `ghcr.io/scontismaart/melpis-web:sha-7bef5f507e3ef9adfd2790d99a8191e9eb422c30` | `sha256:18a93e3b5f4d25d8998eec76f2ab3450a5f1b92b5ed509eb77acdf0e8044886e` | linux/amd64, linux/arm64 |
| #141 | `ghcr.io/scontismaart/melpis-api:sha-cfe09e2214431f4015e3fa01972cd1f553ad736e` | `sha256:d7b61fa84a5bb6776eccecc213c5129984e45357b16c4bbd4043e178b35d19c2` | linux/amd64, linux/arm64 |
| #141 | `ghcr.io/scontismaart/melpis-web:sha-cfe09e2214431f4015e3fa01972cd1f553ad736e` | `sha256:4a1246fbcad7aacbd90158c9a08422867dadda1f7267b4b55ce14c1357594b73` | linux/amd64, linux/arm64 |
| #139 | `ghcr.io/scontismaart/melpis-api:sha-800c56c0b631b81b7bbefe0f51c843dd13d498d1` | `sha256:4eb4461f233cd5e7704c368aabcc1b95c5a251958457e84c88320a7d10ef887e` | linux/amd64, linux/arm64 |
| #139 | `ghcr.io/scontismaart/melpis-web:sha-800c56c0b631b81b7bbefe0f51c843dd13d498d1` | `sha256:597c976587505b2957415d29bd68257de67070e3fc27a324cc02f21f0bffa5b0` | linux/amd64, linux/arm64 |

The registry manifests are build and publication evidence, not deployment
evidence. No service was deployed by this train.

## Remaining boundaries

- The reviewed Python dependency audit passed its existing policy. It continues
  to report nine known residual advisories across `chromadb==1.1.1` and
  `json-repair==0.25.3`; no new exception or policy weakening was added. The
  frontend audit found zero vulnerabilities.
- 36 tests were skipped in each main CI run. The train does not certify those
  skipped cases.
- No deployment, remote migration, provider operation, commercial activation,
  retention/erasure activation, or issue closure occurred.
- Operational certification remains open for C01–C05, A03/A05/A06, B01/B02,
  D01, and D02. All named issues and decision gates remain tracked in the
  [private-beta progress register](v1-private-beta-progress.md).
