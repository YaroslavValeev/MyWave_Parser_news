`runtime-postimage.json.gz` contains only the UTF-8 LF-normalized
`services/publication.py` and `storage/repository.py` from commit
`07975c262a6864919729cc6be4e193bc57b3f629`, compressed with deterministic gzip
mtime 0. No environment or data files are included.

The historical manual-final-post overlay test needs its original audited
postimage to reverse that immutable patch. New publication code must not silently
be substituted for that historical baseline. After reversal, the unchanged
release manifest verifies every before/guard hash; forward staging verifies the
after hashes. This fixture does not update any production audit profile.
