"""
Unit tests for Prophesee EVT2 and EVT3 raw event stream decoders
"""

import struct
import unittest
from giantfiber import ReflexConfig, GiantFiberCoprocessor
from giantfiber.sensors.prophesee_evt import (
    PropheseeEVT2Decoder,
    PropheseeEVT3Decoder,
    DecodedSpike,
)


class TestPropheseeEVT(unittest.TestCase):
    def test_evt2_decoding(self):
        decoder = PropheseeEVT2Decoder()

        # Build mock EVT2 binary buffer according to official Prophesee EVT2 specification:
        # Word layout for CD_ON / CD_OFF:
        # Bits 31..28: Type (0x0 CD_OFF, 0x1 CD_ON)
        # Bits 27..22: timestamp low bits
        # Bits 21..11: X coordinate (11 bits)
        # Bits 10..0:  Y coordinate (11 bits)
        # Word 1: TIME_HIGH (0x8), high_val = 100
        # Word 2: CD_ON (0x1), x=120, y=80, time_low=24
        # Word 3: CD_OFF (0x0), x=280, y=75, time_low=30 (asymmetric coordinates)
        w1 = (0x8 << 28) | (100 & 0x0FFFFFFF)
        w2 = (0x1 << 28) | ((24 & 0x3F) << 22) | ((120 & 0x7FF) << 11) | (80 & 0x7FF)
        w3 = (0x0 << 28) | ((30 & 0x3F) << 22) | ((280 & 0x7FF) << 11) | (75 & 0x7FF)

        raw = struct.pack("<3I", w1, w2, w3)
        spikes = decoder.decode_buffer(raw)

        self.assertEqual(len(spikes), 2)
        self.assertEqual(spikes[0].x, 120)
        self.assertEqual(spikes[0].y, 80)
        self.assertEqual(spikes[0].polarity, 1)
        self.assertEqual(spikes[0].timestamp_us, (100 << 6) | 24)

        self.assertEqual(spikes[1].x, 280)
        self.assertEqual(spikes[1].y, 75)
        self.assertEqual(spikes[1].polarity, 0)
        self.assertEqual(spikes[1].timestamp_us, (100 << 6) | 30)

    def test_evt2_streaming_to_coprocessor(self):
        decoder = PropheseeEVT2Decoder(sensor_w=320, sensor_h=320)
        config = ReflexConfig()

        with GiantFiberCoprocessor(config) as coprocessor:
            w1 = (0x8 << 28) | (50 & 0x0FFFFFFF)
            # x=160, y=160, time_low=10
            w2 = (0x1 << 28) | ((10 & 0x3F) << 22) | ((160 & 0x7FF) << 11) | (160 & 0x7FF)
            # x=100, y=200, time_low=12, CD_OFF
            w3 = (0x0 << 28) | ((12 & 0x3F) << 22) | ((100 & 0x7FF) << 11) | (200 & 0x7FF)
            raw = struct.pack("<3I", w1, w2, w3)

            ingested = decoder.stream_into_coprocessor(raw, coprocessor)
            self.assertEqual(ingested, 2)

    def test_evt3_decoding(self):
        decoder = PropheseeEVT3Decoder()

        # Build mock EVT3 binary buffer according to official Prophesee EVT3 specification:
        # Word 1: TIME_HIGH (0x8), payload = 2
        # Word 2: TIME_LOW (0x6), payload = 500
        # Word 3: ADDR_Y (0x0), payload = 45
        # Word 4: ADDR_X (0x2), polarity=1, x=60
        w1 = (0x8 << 12) | (2 & 0x0FFF)
        w2 = (0x6 << 12) | (500 & 0x0FFF)
        w3 = (0x0 << 12) | (45 & 0x0FFF)
        w4 = (0x2 << 12) | (1 << 11) | (60 & 0x7FF)

        raw = struct.pack("<4H", w1, w2, w3, w4)
        spikes = decoder.decode_buffer(raw)

        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0].y, 45)
        self.assertEqual(spikes[0].x, 60)
        self.assertEqual(spikes[0].polarity, 1)
        self.assertEqual(spikes[0].timestamp_us, (2 << 12) | 500)

    def test_evt3_vector_decoding(self):
        decoder = PropheseeEVT3Decoder(sensor_w=320, sensor_h=320)

        # Word 1: TIME_HIGH (0x8) = 1
        # Word 2: TIME_LOW (0x6) = 100
        # Word 3: ADDR_Y (0x0) = 50
        # Word 4: VECT_BASE_X (0x3), polarity=1, base_x=100
        # Word 5: VECT_12 (0x4), mask=0b000000000101 (bits 0 and 2 set -> x=100 and x=102)
        # Word 6: VECT_8 (0x5), mask=0b00000011 (bits 0 and 1 set -> x=112 and x=113)
        w1 = (0x8 << 12) | 1
        w2 = (0x6 << 12) | 100
        w3 = (0x0 << 12) | 50
        w4 = (0x3 << 12) | (1 << 11) | 100
        w5 = (0x4 << 12) | 0b000000000101
        w6 = (0x5 << 12) | 0b00000011

        raw = struct.pack("<6H", w1, w2, w3, w4, w5, w6)
        spikes = decoder.decode_buffer(raw)

        self.assertEqual(len(spikes), 4)
        self.assertEqual([(s.x, s.y, s.polarity) for s in spikes], [
            (100, 50, 1),
            (102, 50, 1),
            (112, 50, 1),
            (113, 50, 1),
        ])


if __name__ == "__main__":
    unittest.main()
