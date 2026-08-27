# Drive archive diagnostic — 2026-08-28

## Verified identity

- Reviewed implementation HEAD: `9096a2f7cf6f4c4f0560dc882bc21c8ce824d227`
  (`fix: redact Drive upload credentials`).
- Runtime mode for the controlled invocation: collector with explicit raw
  backfill opt-in, Drive backend, `vault:` remote, and the new prefix
  `KReports Data Lake/source-archive-diagnostics-2026-08-28`.
- Remote configuration inspection returned `type=drive`. The bounded quota
  check returned total `5,497,558,138,880`, used `34,599,565,163`, free
  `5,461,211,200,579` bytes (plus `270,717,607` trashed and
  `1,747,373,138` other bytes).

## Pre-deadline controlled write and readback

One pre-existing v2 `drive-archive-*.xml.gz` spool was read and decompressed
in memory. No source body or source credentials are included here. The
adapter was called once for the write with synthetic required provenance and a
deliberately over-limit optional `container_storage_uri`.

- Safe object URI:
  `vault:KReports Data Lake/source-archive-diagnostics-2026-08-28/objects/sha256/24/b6/24b6b78f6269f76f8bc6686bd961de07bf4e9e2d8e5415242a468e2c00207cb4.xml.gz`
- Raw SHA-256: `24b6b78f6269f76f8bc6686bd961de07bf4e9e2d8e5415242a468e2c00207cb4`
- Raw byte length: `1,760,285`
- Compressed object byte length: `211,011`
- Transport metadata keys copied: `archive_version`, `byte_length`,
  `compressed_length`, `sha256`, `source_receipt`, `source_uri`
- Transport metadata key omitted: `container_storage_uri` (optional value
  exceeded the 124-byte Drive custom-property bound)
- Adapter readback: passed; the returned object was read and verified against
  the SHA-256 and raw byte length above.
- Prefix listing before the write was empty; the final read-only listing
  contains exactly the one object above.

The first post-copy readback exceeded the 60-second operational observation
boundary and was stopped after the object had been created. This is
pre-deadline metadata evidence, not a time-bounded v3 readiness result: one
read-only deadline-bound reverification is still required after code review.
No second upload was attempted. A bounded, read-only `DriveArchive.verify_object`
readback then passed for the existing object. The adapter-created diagnostic
spool was removed only after this successful verification; all four pre-existing
v2 XML spools remained untouched.

## Scope and limitations

This is a transport-boundary diagnostic only. It is not a v2 retry, v3
campaign run, DART request, database operation, Lightsail call, deployment,
release promotion, or candidate database release. No pre-existing v2 spool or
state was deleted or modified, and no credential/configuration change was
made.

## Post-review deadline-bound read-only verification

- Reviewed implementation head: `1596990b2438c75f8341205a08db859f4e906f3e`.
- The fixed diagnostic object above was verified once with
  `DriveArchive.verify_object` using the explicit 60-second command deadline.
- Result: passed; decompression, raw byte length, and SHA-256 matched the
  recorded identity (`1,760,285` bytes and the SHA-256 above).
- The shell-level 75-second outer boundary was unavailable in this
  environment; the adapter-level 60-second boundary was applied.
- This was read-only verification only: no `archive_bytes`, `copyto`, listing,
  campaign, DART, database, Lightsail, deployment, or configuration action was
  performed.

This completes the required post-review deadline-bound readback for the
existing diagnostic object. v3 may proceed to a separately authorized,
no-write preflight; this result does not authorize a campaign run or any write.
