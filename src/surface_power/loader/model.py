"""Data model of a parsed blob (spec blob-format 5). Values are in blob units."""
from dataclasses import dataclass, field

from .preprocess import INTERNAL_NODE_GUID


@dataclass(frozen=True, order=True)
class ChannelRef:
    """A channel reference: interface GUID (upper case) and channel number."""

    guid: str
    channel: int

    @property
    def internal(self) -> bool:
        """True for internal nodes VN<n>."""
        return self.guid == INTERNAL_NODE_GUID

    def __str__(self) -> str:
        return f"VN{self.channel}" if self.internal else f"{self.guid[:8]}/{self.channel}"


@dataclass(frozen=True)
class InputChannel:
    line: int
    ref: ChannelRef
    trip: float
    period_ms: int
    kind: int
    max: float
    min: float


@dataclass(frozen=True)
class OutputChannel:
    line: int
    ref: ChannelRef
    default: float
    max: float
    min: float


@dataclass(frozen=True)
class AlgorithmLine:
    line: int
    instance: int
    stage: int
    divider: int
    guid: str
    id: int
    inputs: tuple[ChannelRef, ...]
    outputs: tuple[ChannelRef, ...]
    params: tuple[str, ...]  # parameter fields as text; each block parses its own (constant expressions already evaluated)
    dynamic: bool = False    # True if a parameter is an expression with channel references (evaluated at run time, phase 5)


@dataclass(frozen=True)
class OtherChannel:
    """A channel definition with a layout we do not interpret (e.g. composite sensors of newer blobs). Kept for the tools."""

    line: int
    ref: ChannelRef
    fields: tuple[str, ...]


@dataclass
class ParsedBlob:
    input_channels: list[InputChannel] = field(default_factory=list)
    output_channels: list[OutputChannel] = field(default_factory=list)
    other_channels: list[OtherChannel] = field(default_factory=list)
    algorithms: list[AlgorithmLine] = field(default_factory=list)
    telemetry: list[str] = field(default_factory=list)
    form: str = "text"
    warnings: list[tuple[int, str]] = field(default_factory=list)  # (line, message)
