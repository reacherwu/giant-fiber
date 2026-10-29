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
    """

    def __init__(self, time_high_shift: int = 6, sensor_w: int = 320, sensor_h: int = 320):
        self.time_high: int = 0
        self.time_high_shift: int = time_high_shift
        self.sensor_w: int = sensor_w
        self.sensor_h: int = sensor_h
        self.last_timestamp_us: int = 0
        self.total_events_decoded: int = 0

    def decode_buffer(self, raw_bytes: bytes) -> list[DecodedSpike]:
        num_words = len(raw_bytes) // 4
        if num_words == 0:
            return []

        spikes: list[DecodedSpike] = []
        words = struct.unpack(f"<{num_words}I", raw_bytes[:num_words * 4])

        for word in words:
            ev_type = (word >> 28) & 0x0F

            if ev_type == 0x8:  # TIME_HIGH
                high_val = word & 0x0FFFFFFF
                self.time_high = high_val << self.time_high_shift
            elif ev_type in (0x0, 0x1):  # CD_OFF or CD_ON
                time_low = (word >> 22) & 0x3F
                x = (word >> 11) & 0x7FF
                y = word & 0x7FF
                polarity = 1 if ev_type == 0x1 else 0

                ts = self.time_high | time_low
                self.last_timestamp_us = ts
                self.total_events_decoded += 1
                spikes.append(DecodedSpike(x=x, y=y, timestamp_us=ts, polarity=polarity))

        return spikes

    def stream_into_coprocessor(
        self,
        raw_bytes: bytes,
        coprocessor: GiantFiberCoprocessor,
        sensor_w: int | None = None,
        sensor_h: int | None = None,
    ) -> int:
        spikes = self.decode_buffer(raw_bytes)
        sw = sensor_w or self.sensor_w
        sh = sensor_h or self.sensor_h
        count = 0
        for sp in spikes:
            if 0 <= sp.x < sw and 0 <= sp.y < sh:
                polarity = 1 if sp.polarity == 1 else -1
                coprocessor.feed_spike(sp.x, sp.y, sp.timestamp_us, polarity, raw_w=sw, raw_h=sh)
                count += 1
        return count


class PropheseeEVT3Decoder:
    """
    Decodes Prophesee EVT3 16-bit vectorized binary event stream.
    """

    def __init__(self, sensor_w: int = 320, sensor_h: int = 320):
        self.sensor_w: int = sensor_w
        self.sensor_h: int = sensor_h
        self.current_y: int = 0
        self.base_x: int = 0
        self.base_polarity: int = 1
        self.time_high: int = 0
        self.time_low: int = 0
        self.current_timestamp_us: int = 0
        self.total_events_decoded: int = 0

    def decode_buffer(self, raw_bytes: bytes) -> list[DecodedSpike]:
        num_words = len(raw_bytes) // 2
        if num_words == 0:
            return []

        spikes: list[DecodedSpike] = []
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
            elif w_type == 0x3:  # VECT_BASE_X
                self.base_polarity = (payload >> 11) & 0x1
                self.base_x = payload & 0x7FF
            elif w_type == 0x4:  # VECT_12
                ts = self.current_timestamp_us
                for bit_idx in range(12):
                    if (payload >> bit_idx) & 1:
                        x = self.base_x + bit_idx
                        if 0 <= x < self.sensor_w:
                            self.total_events_decoded += 1
                            spikes.append(
                                DecodedSpike(x=x, y=self.current_y, timestamp_us=ts, polarity=self.base_polarity)
                            )
                self.base_x += 12
            elif w_type == 0x5:  # VECT_8
                ts = self.current_timestamp_us
                for bit_idx in range(8):
                    if (payload >> bit_idx) & 1:
                        x = self.base_x + bit_idx
                        if 0 <= x < self.sensor_w:
                            self.total_events_decoded += 1
                            spikes.append(
                                DecodedSpike(x=x, y=self.current_y, timestamp_us=ts, polarity=self.base_polarity)
                            )
                self.base_x += 8
            elif w_type == 0x6:  # TIME_LOW
                self.time_low = payload
                self.current_timestamp_us = (self.time_high << 12) | self.time_low
            elif w_type == 0x8:  # TIME_HIGH
                self.time_high = payload
                self.current_timestamp_us = (self.time_high << 12) | self.time_low

        return spikes

    def stream_into_coprocessor(
        self,
        raw_bytes: bytes,
        coprocessor: GiantFiberCoprocessor,
        sensor_w: int | None = None,
        sensor_h: int | None = None,
    ) -> int:
        spikes = self.decode_buffer(raw_bytes)
        sw = sensor_w or self.sensor_w
        sh = sensor_h or self.sensor_h
        count = 0
        for sp in spikes:
            if 0 <= sp.x < sw and 0 <= sp.y < sh:
                polarity = 1 if sp.polarity == 1 else -1
                coprocessor.feed_spike(sp.x, sp.y, sp.timestamp_us, polarity, raw_w=sw, raw_h=sh)
                count += 1
        return count