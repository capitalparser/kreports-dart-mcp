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

## Controlled write and readback

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
boundary and was stopped after the object had been created. No second upload
was attempted. A bounded, read-only `DriveArchive.verify_object` readback
then passed for the existing object. The adapter-created diagnostic spool was
removed only after this successful verification; all four pre-existing v2 XML
spools remained untouched.

## Scope and limitations

This is a transport-boundary diagnostic only. It is not a v2 retry, v3
campaign run, DART request, database operation, Lightsail call, deployment,
release promotion, or candidate database release. No pre-existing v2 spool or
state was deleted or modified, and no credential/configuration change was
made.

