"""Line parser (spec blob-format 5): splits the decoded text into channel lines, algorithm lines and telemetry.

Only the structure is checked here. Parameters are parsed and checked by each block when the graph is built
(surface_power.engine.graph), because their layout depends on the block type.
"""
import re

from . import expr, preprocess
from .errors import BlobError
from .model import AlgorithmLine, ChannelRef, InputChannel, OtherChannel, OutputChannel, ParsedBlob
from .numbers import atoi, real

_GUID = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")
_BRACED = re.compile(r"\{([0-9A-Fa-f-]{36})\}_(\d+)")


def _fields(text: str) -> list[str]:
    """Split a line at commas. A braced channel `{GUID}_N` becomes the two fields GUID and N."""
    out = []
    for f in (f.strip() for f in text.split(",")):
        m = _BRACED.fullmatch(f)
        out += [m.group(1), m.group(2)] if m else [f]
    return out


def _guid(text: str, line: int) -> str:
    g = text.upper()
    if not _GUID.fullmatch(g):
        raise BlobError(f"not a GUID: {text!r}", line)
    return g


def _int(text: str, what: str, line: int) -> int:
    try:
        return atoi(text)
    except ValueError:
        raise BlobError(f"{what}: not an integer: {text!r}", line) from None


def _real(text: str, what: str, line: int) -> float:
    try:
        return real(text)
    except ValueError:
        raise BlobError(f"{what}: not a number: {text!r}", line) from None


def _param(text: str, line: int) -> tuple[str, bool]:
    """Evaluate constant expressions. Returns (text, dynamic). An expression with channel references stays as text and is
    marked dynamic: the Windows core evaluates it on every run (spec blob-format 4.2)."""
    if not text:
        raise BlobError("empty parameter", line)
    if text.startswith("("):
        if "{" in text:
            return text, True
        try:
            value = expr.evaluate(text)
        except BlobError as e:
            raise BlobError(e.message, line) from None
        return (str(int(value)) if value == int(value) else repr(value)), False
    return text, False


def _channel_line(f: list[str], line: int, blob: ParsedBlob) -> None:
    ref = ChannelRef(_guid(f[0], line), _int(f[1], "channel", line))
    if len(f) == 10:
        ch = InputChannel(line, ref, trip=_real(f[2], "trip point", line), period_ms=_int(f[4], "period", line),
                          kind=_int(f[7], "kind", line), max=_real(f[8], "max", line), min=_real(f[9], "min", line))
        if not 0 <= ch.kind < 4:
            raise BlobError(f"input channel kind {ch.kind} not in 0..3", line)
        if ch.min > ch.max:
            raise BlobError("input channel min > max", line)
        if ch.trip > ch.max:
            raise BlobError("input channel trip point > max", line)
        blob.input_channels.append(ch)
    elif len(f) == 5:
        ch = OutputChannel(line, ref, default=_real(f[2], "default", line), max=_real(f[3], "max", line),
                           min=_real(f[4], "min", line))
        if not ch.min <= ch.default <= ch.max:
            raise BlobError("output channel needs min <= default <= max", line)
        blob.output_channels.append(ch)
    else:
        # Newer blobs define composite sensors with 14 or 19 fields. We keep them as they are [D].
        blob.other_channels.append(OtherChannel(line, ref, tuple(f[2:])))
        blob.warnings.append((line, f"channel definition with {len(f)} fields for {ref} is not interpreted"))


def _algorithm_line(f: list[str], line: int) -> AlgorithmLine:
    if len(f) < 7:
        raise BlobError(f"algorithm line with only {len(f)} fields", line)
    instance = _int(f[0], "instance", line)
    if not 0 <= instance <= 1:
        raise BlobError(f"instance {instance} not 0 or 1", line)
    stage = _int(f[1], "stage", line)
    divider = _int(f[2], "divider", line)
    if stage < 0 or divider < 0:
        raise BlobError("stage and divider must not be negative", line)
    guid = _guid(f[3], line)
    id_ = _int(f[4], "id", line)
    n_in, n_out = _int(f[5], "number of inputs", line), _int(f[6], "number of outputs", line)
    if n_in < 0 or n_out < 0:
        raise BlobError("negative number of inputs or outputs", line)
    end = 7 + 2 * (n_in + n_out)
    if len(f) < end:
        raise BlobError(f"line declares {n_in} inputs and {n_out} outputs but has only {(len(f) - 7) // 2} references", line)
    refs = [ChannelRef(_guid(f[i], line), _int(f[i + 1], "channel", line)) for i in range(7, end, 2)]
    parsed = [_param(p, line) for p in f[end:]]
    params = tuple(text for text, _ in parsed)
    dynamic = any(d for _, d in parsed)
    return AlgorithmLine(line, instance, stage, divider, guid, id_, tuple(refs[:n_in]), tuple(refs[n_in:]), params, dynamic)


def parse_text(text: str, form: str = "text") -> ParsedBlob:
    """Parse decoded blob text. Raises BlobError with the line number on the first problem."""
    blob = ParsedBlob(form=form)
    section = 0  # 0 channels, 1 algorithms, 2 telemetry
    for ln in preprocess.run(text):
        if not ln.text:
            continue
        if section < 2 and ln.text == "$":
            section += 1
            continue
        if section == 2:
            blob.telemetry.append(ln.text)
            continue
        if "," not in ln.text:
            # Separator lines such as "*****" occur in some blobs; the Windows core evidently skips them [H].
            blob.warnings.append((ln.number, f"line without fields skipped: {ln.text[:20]!r}"))
            continue
        f = _fields(ln.text)
        if section == 0:
            _channel_line(f, ln.number, blob)
        else:
            blob.algorithms.append(_algorithm_line(f, ln.number))
    if section < 1:
        raise BlobError("no algorithm section (missing '$' line)")
    if not blob.algorithms:
        raise BlobError("no algorithm lines")
    return blob
