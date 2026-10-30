"""Application channel lifecycle; reliability belongs exclusively to QUIC."""
from dataclasses import dataclass

DEFAULT_STREAM = bytes(16)
MAX_STREAMS = 65  # 64 proxy channels plus the generic transport channel.
MAX_STREAMS_PER_ALLOCATION = 4096
MAX_PENDING_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


@dataclass
class ChannelState:
    key: bytes
    local_closed: bool = False
    remote_closed: bool = False
