# Read-only diagnostics and recordings

`read_runtime_state` reads ADS/device state and symbol version. It does not
establish online program identity, machine readiness or safety.
`describe_symbols` returns metadata for the value-read allowlist.
`browse_runtime_symbols` requires operator configuration `allowSymbolBrowse: true`
(default false), which does not authorize new value reads. Each browse fetches
the bounded table (100000 entries/16 MiB), checks its version, then pages locally.

`read_typed_symbols` accepts optional TMC `xml_text` and explicit `string_encoding`
(`latin-1` or `utf-8`). It decodes integers, finite floats, packed booleans,
aliases, enums, explicit structures and multidimensional arrays. STRING needs
an encoding and terminator; WSTRING uses UCS-2 and rejects surrogates. Time/date
results retain numeric values, units and PLC type, without timezone conversion.
Unknown offsets, overlapping unions, inheritance, pointers and inconsistent
lengths fail explicitly. An offline TMC can be stale even when byte sizes match.
Transport errors are never converted into zero/false values.

`decode_tmc_value` decodes supplied hexadecimal bytes offline, up to 64 KiB.
Live reads allow 4096 bytes per symbol and 128 symbols per snapshot.

## Recording lifecycle

Set `--recording-root <private-local-directory>` for a known archive location.
Otherwise the first use creates a retained temporary directory, reported by
status. Files are never automatically deleted or uploaded. Keep them out of Git.

1. `start_recording` returns an id immediately. Duration: 0.1..3600 seconds;
   interval: 20..60000 ms; one active recording per service.
2. `recording_status` reports progress, per-symbol errors, invalid samples,
   interval overruns and completion reason. `stop_recording` requests cancellation;
   poll until terminal. Service shutdown also stops owned workers.
3. `read_recording_samples` and `export_recording_csv` page results. Follow
   `nextOffset`; CSV includes a header each page and escapes formula-like strings.
4. `analyze_recording` calculates numeric min/max/mean and observed boolean
   edges/high pulses for finished archives. Errors and gaps greater than 1.5
   requested intervals break continuity. Pulse lists cap at 1000 with total count.

Optional trigger: `{"symbol":"Demo.Ready","operator":"rising","value":0}`.
Operators: `eq`, `gt`, `lt`, `rising`, `falling`. Rising crosses from at/below
the threshold to above it. The trigger symbol must be acquired. Pretrigger
storage is limited to 1000 frames/2 MiB. Duration and sample limits include
waiting; a trigger never observed is reported explicitly.

Limits: 100000 observed frames, 200000 symbol observations, 32 MiB per archive,
256 MiB root budget (including reserve for a new archive), 32 session handles,
1024 root entries. Use a different root per process; this is not a cross-process
disk quota. Finished archives can reopen in later sessions. Unfinished archives
report `owner_not_this_session`: a file does not prove its worker stopped, and
an unrelated session cannot stop or replay it.

Live recording uses one persistent bridge, with a separate single-shot read
allowed concurrently. Stop/deadline terminates the owned worker. A detected
program-version change stops recording before storing the new program's data.
Times are PC polling times, not PLC task timestamps. Polling can miss events;
it is neither an atomic scan nor Scope server acquisition. Recordings use the
native primitive decoder; unsupported complex values retain raw bytes for
explicit offline TMC decoding.

References: [ADS symbol access](https://infosys.beckhoff.com/content/1033/tc3_adsdll2/124827915.html),
[STRING](https://infosys.beckhoff.com/content/1033/tc3_plc_intro/2529410443.html),
[WSTRING](https://infosys.beckhoff.com/content/1033/tc3_plc_intro/2529437323.html),
[TIME/LTIME](https://infosys.beckhoff.com/content/1033/tc3_plc_intro/12189021579.html).
