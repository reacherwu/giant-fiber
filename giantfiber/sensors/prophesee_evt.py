"""
Prophesee Neuromorphic Event Stream Decoder (EVT2 & EVT3)
=========================================================
Zero-copy, microsecond-latency binary decoder for Prophesee GenX320, IMX636,
and Metavision event vision sensors.

Supported formats:
  - EVT2: 32-bit word stream (Standard for GenX320 / CCam3)
  - EVT3: 16-bit vectorized word stream (Next-gen Metavision standard)
"""

from __future__ import annotations
import struct
from dataclasses import dataclass
from typing import List, Tuple, Generator, Optional
from giantfiber.engine import GiantFiberCoprocessor


@dataclass
class DecodedSpike:
    x: int
    y: int
    timestamp_us: int
    polarity: int  # 0: OFF (contrast decrease), 1: ON (contrast increase)


class PropheseeEVT2Decoder:
    """
    Decodes Prophesee EVT2 32-bit binary event stream.
    
    EVT2 32-bit word format:
      Bits 31..28 (4 bits): Event Type
        0x0: CD_OFF (Contrast Decrease, polarity=0)
        0x1: CD_ON  (Contrast Increase, polarity=1)
        0x8: TIME_HIGH (Upper 12-24 bits of timestamp)
        0xC: EXT_TRIGGER
      
      For CD_OFF / CD_ON words:
        Bits 27..22 (6 bits): timestamp low bits
        Bits 21..11 (11 bits): Y coordinate (0..2047)
        Bits 10..0  (11 bits): X coordinate (0..2047)
        
      For TIME_HIGH words:
        Bits 27..0 (28 bits): timestamp high value
    """

    def __init__(self, time_high_shift: int = 6):
        self.time_high: int = 0
        self.time_high_shift: int = time_high_shift
        self.last_timestamp_us: int = 0
        self.total_events_decoded: int = 0

    def decode_buffer(self, raw_bytes: bytes) -> List[DecodedSpike]:
        """Decodes raw binary buffer of 32-bit words into list of DecodedSpikes."""
        num_words = len(raw_bytes) // 4
        if num_words == 0:
            return []

        spikes: List[DecodedSpike] = []
        # Unpack as little-endian 32-bit unsigned integers
        words = struct.unpack(f"<{num_words}I", raw_bytes[:num_words * 4])

        for word in words:
            ev_type = (word >> 28) & 0x0F

            if ev_type == 0x8:  # TIME_HIGH
                high_val = word & 0x0FFFFFFF
                self.time_high = high_val << self.time_high_shift
            elif ev_type in (0x0, 0x1):  # CD_OFF or CD_ON
                time_low = (word >> 22) & 0x3F
                y = (word >> 11) & 0x7FF
                x = word & 0x7FF
                polarity = 1 if ev_type == 0x1 else 0

                ts = self.time_high | time_low
                self.last_timestamp_us = ts
                self.total_events_decoded += 1
                spikes.append(DecodedSpike(x=x, y=y, timestamp_us=ts, polarity=polarity))

        return spikes

    def stream_into_coprocessor(
        self, raw_bytes: bytes, coprocessor: GiantFiberCoprocessor, scale_to_64: bool = True
    ) -> int:
        """
        Direct zero-copy streaming: decodes raw EVT2 buffer and directly pushes
        spikes into the GiantFiber coprocessor native memory manifold.
        Returns the number of valid spikes ingested.
        """
        num_words = len(raw_bytes) // 4
        if num_words == 0:
            return 0

        words = struct.unpack(f"<{num_words}I", raw_bytes[:num_words * 4])
        count = 0

        for word in words:
            ev_type = (word >> 28) & 0x0F

            if ev_type == 0x8:
                self.time_high = (word & 0x0FFFFFFF) << self.time_high_shift
            elif ev_type in (0x0, 0x1):
                time_low = (word >> 22) & 0x3F
                y = (word >> 11) & 0x7FF
                x = word & 0x7FF
                polarity = 1 if ev_type == 0x1 else 0
                ts = self.time_high | time_low

                # Downscale from GenX320 (320x320) to 64x64 if requested
                if scale_to_64:
                    x = (x * 64) // 320 if x < 320 else min(63, x // 5)
                    y = (y * 64) // 320 if y < 320 else min(63, y // 5)

                if 0 <= x < 64 and 0 <= y < 64:
                    coprocessor.feed_spike(x, y, ts, polarity)
                    count += 1

        self.total_events_decoded += count
        return count


class PropheseeEVT3Decoder:
    """
    Decodes Prophesee EVT3 16-bit vectorized binary event stream.
    
    EVT3 16-bit word format:
      Bits 15..12 (4 bits): Type
        0x0: ADDR_Y (Updates current Y row)
        0x2: ADDR_X (Contrast event: X coord with polarity in bit 11)
        0x3: VEC_BASE
        0x4: VEC_12
        0x8: TIME_LOW (Updates lower 12 bits of timestamp)
        0x9: TIME_HIGH (Updates higher 12 bits of timestamp)
    """

    def __init__(self):
        self.current_y: int = 0
        self.time_high: int = 0
        self.time_low: int = 0
        self.current_timestamp_us: int = 0
        self.total_events_decoded: int = 0

    def decode_buffer(self, raw_bytes: bytes) -> List[DecodedSpike]:
        num_words = len(raw_bytes) // 2
        if num_words == 0:
            return []

        spikes: List[DecodedSpike] = []
        words = struct.unpack(f"<{num_words}H", raw_bytes[:num_words * 2])

        for word in words:
            w_type = (word >> 12) & 0x0F
            payload = word & 0x0FFF

            if w_type == 0x0:  # ADDR_Y
                self.current_y = payload & 0x7FF
            elif w_type == 0x2:  # ADDR_X
                polarity = (payload >> 11) & 0x1
                x = payload & 0x7FF
                ts = self.current_timestamp_us
                self.total_events_decoded += 1
                spikes.append(DecodedSpike(x=x, y=self.current_y, timestamp_us=ts, polarity=polarity))
            elif w_type == 0x8:  # TIME_LOW
                self.time_low = payload
                self.current_timestamp_us = (self.time_high << 12) | self.time_low
            elif w_type == 0x9:  # TIME_HIGH
                self.time_high = payload
                self.current_timestamp_us = (self.time_high << 12) | self.time_low

        return spikes

    def stream_into_coprocessor(
        self, raw_bytes: bytes, coprocessor: GiantFiberCoprocessor, scale_to_64: bool = True
    ) -> int:
        num_words = len(raw_bytes) // 2
        if num_words == 0:
            return 0

        words = struct.unpack(f"<{num_words}H", raw_bytes[:num_words * 2])
        count = 0

        for word in words:
            w_type = (word >> 12) & 0x0F
            payload = word & 0x0FFF

            if w_type == 0x0:
                self.current_y = payload & 0x7FF
            elif w_type == 0x2:
                polarity = (payload >> 11) & 0x1
                x = payload & 0x7FF
                y = self.current_y
                ts = self.current_timestamp_us

                if scale_to_64:
                    x = (x * 64) // 320 if x < 320 else min(63, x // 5)
                    y = (y * 64) // 320 if y < 320 else min(63, y // 5)

                if 0 <= x < 64 and 0 <= y < 64:
                    coprocessor.feed_spike(x, y, ts, polarity)
                    count += 1
            elif w_type == 0x8:
                self.time_low = payload
                self.current_timestamp_us = (self.time_high << 12) | self.time_low
            elif w_type == 0x9:
                self.time_high = payload
                self.current_timestamp_us = (self.time_high << 12) | self.time_low

        self.total_events_decoded += count
        return count
