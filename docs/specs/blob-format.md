# Spec: SMF configuration blob format

Version 0.1, 14.11.2026. Implements FR-B1 – FR-B9 (`../requirements/requirements.md`). Tests: AC-B1 – AC-B6.

Evidence markers as in the research notes:
- **[C]**: read from the Windows driver code;
- **[M]**: confirmed by measurement;
- **[H]**: hypothesis;
- **[D]**: our own design decision (not Windows behaviour).

This spec describes the format only as far as needed to interoperate. It contains no blob content.

## 1. Sources (FR-B1, FR-B2)

| Source | Where | Notes |
|---|---|---|
| UEFI variable | `/sys/firmware/efi/efivars/SMF_DATA_BLOB-a3d05fa9-db5e-490c-956e-a57384a4b70b` | Readable without root. The first 4 bytes are the EFI attributes and are stripped before decoding [C]. |
| File | any path | A raw blob in delivery form A, B or C, an extracted text, or a Microsoft SMF firmware capsule (`Smf_*.bin`, `Surface.SMF*.bin`). |

The loader takes a file path; with no path, it uses the UEFI variable. An empty or missing variable is reported as "no blob" (FR-D2).

## 2. Delivery forms (FR-B3)

Byte order is little-endian. `u32[i]` is the i-th 32-bit word of a header.

| Form | Detection | Payload |
|---|---|---|
| **B** (header + LZNT1) | at least 20 bytes; `u32[2] == 1`, `u32[3] == 5`, and `u32[4]` = number of bytes after the header (`len − 20`) [C] | `LZNT1(data[20:20+u32[4]])` |
| **A** (header + plain text) | 28-byte header; `u32[6]` = text length; the text starts with the UTF-8 BOM `EF BB BF` [C] | `data[28:28+u32[6]]` |
| **C** (raw LZNT1) | the data starts with a valid LZNT1 chunk header (see 3) and decodes to text | `LZNT1(data)` |
| **Text** | starts with a BOM or a printable ASCII character, has no NUL bytes | used as is (for tests and extracted files) [D] |
| **Capsule** | none of the above; the file contains a form-B header (byte pattern `01 00 00 00 05 00 00 00` at `u32[2..3]`) or a BOM preceded by a plausible u32 length | the embedded blob; the first one that decodes to text wins [D] |

Detection order: B, A, C, Text, Capsule. A blob that matches none fails with "unknown format".

## 3. LZNT1 (MS-XCA)
- **Chunks:** The stream is a sequence of chunks. Each chunk starts with a u16 header:
  - bits 0–11: chunk size − 1, without the 2 header bytes;
  - bits 12–14: signature `0b011`;
  - bit 15: compressed.
  - A header of 0 ends the stream.
- **Uncompressed chunks** are copied as they are.
- **Compressed chunks** are made of groups: one flag byte, then 8 items, bit 0 first.
  - A clear flag bit means a literal byte.
  - A set flag bit means a u16 copy token.
- **Copy tokens:**
  - The token is split into a displacement part and a length part. The split depends on the current position `p` within the chunk's decompressed output: the displacement width is the smallest `b ≥ 4` with `2^b ≥ p`.
  - Displacement = `(token >> (16 − b)) + 1`.
  - Length = `(token & (2^(16−b) − 1)) + 3`.
  - Copies may overlap.
- **Errors:** A truncated chunk, a displacement before the chunk start, or a signature other than `0b011` is an error.
- **Size limit:** The SB3 core accepts at most 30 720 bytes of decompressed output [C]. Newer blobs are larger: the Laptop Studio 2 dGPU blob decodes to 30 825 bytes, the macro-source blobs to about 33 kB. The loader therefore accepts up to 1 MiB [D].

## 4. Text forms (FR-B4)

The decoded text is ASCII (UTF-8 with an optional BOM). Line ends are CRLF or LF. Three forms exist:

1. **Plain CSV** (all cores): `GUID,channel` as two fields, numbers as parameters.
2. **Expressions** (cores ≥ 3.204.23):
   - Channels may be written `{GUID}_N`.
   - Parameters may be bracketed arithmetic expressions such as `(28000+10000)`.
3. **Macro source** (form C, cores ≥ 3.205.17): adds line comments `//`, macro definitions `#macro NAME VALUE`, and references `%NAME%` [C].

### 4.1 Preprocessor
The preprocessor processes the text line by line:
1. **Comments:** cut each line at the first `//`.
2. **Macro definitions:** a line whose first token is `#macro` defines `NAME → VALUE`. The name is at most 63 characters [C]; the value is the rest of the line, trimmed. A definition line produces no output line.
3. **References:** `%NAME%` is replaced by its value, repeatedly, until nothing changes. The depth limit is 16 [D]. Exceeding it, or an undefined name, is an error that names the line.
4. **Built-in macro** `VN<n>` → `{FB805E77-616E-4EA8-BAC1-6859A6C2D4B1}_<n>`, the internal-node channel `n` [C].
5. **Braced channels:** a field `{GUID}_N` becomes the two fields `GUID,N`. The parser does this after splitting the line at commas, because braced channels also occur inside expressions (4.2).
6. **Whitespace:** leading and trailing whitespace around each field is removed. GUIDs are compared case-insensitively and stored in upper case.

### 4.2 Expressions (AC-B5)
- **Where:** a field that starts with `(` is an expression.
- **Grammar** [D, covers every expression in the known blobs]:
  ```
  expr   := term (('+' | '-') term)*
  term   := factor (('*' | '/') factor)*
  factor := number | channel | '(' expr ')' | '-' factor
  channel := '{' GUID '}_' digits
  number := digits ['.' digits] [('e'|'E') ['+'|'-'] digits]
  ```
- **Evaluator:** a recursive-descent evaluator over this grammar; Python `eval` is never used. Division by zero and any other token are errors.
- **Windows behaviour:** the Windows core keeps expressions and evaluates them on every run [C]. They may also reference channels [C].
  - Contrary to the earlier survey, some blobs do this. Laptop 7 Intel, Pro 11 Intel and the macro-source blobs use for example `({FB805E77-…}_208+30)` as table bounds.
- **Our handling:**
  - A constant expression is evaluated once at load time.
  - An expression with braced channel references is kept as text and marks its line as *dynamic*. It is compiled once and evaluated in every run (`engine.md` 3.6). For blocks without that support, `blob-format.md` 7 applies to the line like to an unknown GUID.
  - Any other content (names, operators outside the grammar) is a parse error.

### 4.3 Numbers
- **Integer fields** (counts, ids, kinds, flags) are parsed like C `atoi`: an optional sign and digits; anything after the digits is ignored [C].
- **Real fields** use a decimal parser with an optional sign, digits and an optional fraction [C]. The implementation uses IEEE double.
- **Empty fields** are errors [D].

## 5. Sections

After preprocessing, the text has these sections, in order:

| Section | Ends with | Content |
|---|---|---|
| Channel lines | a line `$` | input and output channel definitions |
| Algorithm lines | a line `$` | the block graph |
| Telemetry | end of text | `#VNTELEMETRY` and `#IOTELEMETRY` blocks: display ranges, not used for control [C] |

- **Empty lines** are skipped.
- **Lines without a comma** in the first two sections are skipped with a warning. Pro 7+ blobs contain a separator line of asterisks [H: the Windows core skips such lines too].
- **The first section** may be empty.
- **Unknown lines in the telemetry section** are kept as text for the `blob` tool.

### 5.1 Input channel line [C]
```
GUID, channel, trip, f2, period_ms, timer_div, bg_div, kind, max, min
```
- **Fields:**
  - `trip`: the EC trip point the Windows client sets;
  - `period_ms`: the client poll period;
  - `kind`: 1 = event based, 2 = level triggered, 3 = dual speed;
  - `max`, `min`: the valid range in channel units.
- **Checks:** `kind < 4`, `min ≤ max`, `trip ≤ max`.
- **Use on Linux:** only `max`/`min` (validity range, FR-I2) and `period_ms` (informational). The trip-point handling is a Windows client detail; Linux polls (see `inputs.md`).

### 5.2 Output channel line [C]
```
GUID, channel, default, max, min
```
- **Check:** `min ≤ default ≤ max`.
- **Use:** the range bounds every write to this channel (FR-O3, `outputs.md`).
- A line with 5 fields is an output line; a line with 10 fields is an input line [D: told apart by field count].

### 5.2a Other channel lines
Newer blobs define composite sensors (interface `87008365`) with 14 or 19 fields. Their layout is not analysed. The parser keeps them as "other channel" lines and warns [D]. They give no validity range.

### 5.2b Placeholder input
Some blobs pass a constant (e.g. a date-like number) through a pass-through block whose input is the placeholder channel `99999999-9999-9999-9999-999999999999/1`. No client provides it, so it stays invalid and the block outputs its constant default [C, blob text].

### 5.3 Algorithm line [C]
```
instance, stage, divider, ALGORITHM-GUID, id, n_in, n_out, (GUID, channel) × (n_in + n_out), parameter …
```

| Field | Meaning | Check |
|---|---|---|
| `instance` | evaluation instance: 0 = 1000 ms, 1 = 5000 ms | ≤ 1 |
| `stage` | stages run in ascending order | ≥ 0 |
| `divider` | used by controllers: a law step on every (divider + 1)-th timer run (`controller.md`) | ≥ 0 |
| `ALGORITHM-GUID` | block type (FR-B7) | must be known, see 7 |
| `id` | line id, unique per algorithm GUID [H for uniqueness] | |
| `n_in`, `n_out` | number of input and output references | per type, see below |
| references | first the inputs, then the outputs | |
| parameters | per block type (`engine.md`, `controller.md`) | per type |

Header checks per block type [C]:
- **PID with parameter sets:** `n_in == 2`.
- **Parameter-set line:** `n_in == 0` and `n_out == 0`.
- **Lookup table, linear map, pass-through:** `n_in == 1`.
- **Multiplexer:** `n_in ≥ 3`.
- The newer controller types (single PID, surface PID) have `n_in ≤ 2`.

A channel reference is a pair `(GUID, channel)`:
- GUID `FB805E77-616E-4EA8-BAC1-6859A6C2D4B1` is an **internal node** `VN<channel>` (`engine.md`, 2).
- Any other GUID is a **device channel**, provided by an input adapter or consumed by an output adapter.

## 6. Graph build and validation (FR-B5, FR-B6)

The graph builder:
1. Creates one internal node per distinct `VN<n>` that is referenced anywhere, as input or as output [C].
2. Creates one input slot per distinct device channel referenced as an input, and one output slot per distinct device channel referenced as an output.
3. Builds each algorithm line into a block via the registry (section 7). The block parses and checks its own parameters, with the Windows core's checks for that type.
4. Attaches each parameter-set line to the controller of the same `id`.
5. Checks these rules:
   - every controller has at least one parameter set;
   - every internal node read by a block is written by some block;
   - every device output has exactly one writer.

   The checks differ in strictness:
   - **Error:** the first rule.
   - **Warning:** the second and third rules. A node that is read but never written is legal: such nodes exist in real blobs and hold 0 [C].

Every error carries the 1-based line number in the preprocessed text and a reason. A blob with any error is not used for control (G-4).

## 7. Block registry (FR-B7, FR-B8)

Blocks are looked up by algorithm GUID (upper case). Core type numbers are never used: they shift between core versions [C].

| GUID prefix | Block (our name) | Spec | Status |
|---|---|---|---|
| `3AFD73EE` | lookup table | engine 4.1 | implemented |
| `0AE20A40` | min/max select | engine 4.2 | implemented |
| `FD5F6C7E` | add/subtract/scale | engine 4.3 | implemented |
| `013CEB02` | multiplexer | engine 4.4 | implemented |
| `026CCFCC` | pass-through | engine 4.5 | implemented |
| `5974888D` | linear map | engine 4.6 | implemented |
| `2DB3B9EE` | window comparator | engine 4.7 | implemented |
| `9E2A7FED` | PID with parameter sets | controller | implemented |
| `83F47D50` | parameter set (data only) | controller | implemented |
| `76536F66` | conditional table | engine 5.1 | implemented |
| `3D42677C` | multiply/divide | engine 5.4 | implemented |
| `A58AF9AA` | hysteresis quantiser | engine 5.3 | implemented |
| `0DBF7FAB` | hysteresis table | engine 5.2 | implemented |
| `A0CE482E` | weighted mean | engine 5 | planned |
| `0D24F500` | variable-setpoint PID | controller 6 | implemented |
| `54C09C3D` | EWMA filter | engine 5.5 | implemented |
| `B34C9310`, `7C2D76F2` | PID cores (types 1, 2) | controller 7 | planned |

Full GUIDs are in `src/surface_power/engine/registry.py`.
- **Unknown GUID, not reaching an output:** a warning; the block is skipped and its outputs hold 0.
- **Unknown GUID on a path to a device output:** the blob is not used for control. The `blob` tool lists the affected outputs (AC-B3).

## 8. Export (FR-B9)
The `blob` tool prints the parsed graph (`tools.md`). It never writes the decoded blob text into the repository or into a report that is meant to be shared.
