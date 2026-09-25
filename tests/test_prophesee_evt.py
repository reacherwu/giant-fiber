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

        # Build mock EVT2 binary buffer:
        # Word 1: TIME_HIGH (0x8), high_val = 100
        # Word 2: CD_ON (0x1), x=120, y=80, time_low=24
        # Word 3: CD_OFF (0x0), x=150, y=90, time_low=30
        w1 = (0x8 << 28) | (100 & 0x0FFFFFFF)
        w2 = (0x1 << 28) | ((24 & 0x3F) << 22) | ((80 & 0x7FF) << 11) | (120 & 0x7FF)
        w3 = (0x0 << 28) | ((30 & 0x3F) << 22) | ((90 & 0x7FF) << 11) | (150 & 0x7FF)

        raw = struct.pack("<3I", w1, w2, w3)
        spikes = decoder.decode_buffer(raw)

        self.assertEqual(len(spikes), 2)
        self.assertEqual(spikes[0].x, 120)
        self.assertEqual(spikes[0].y, 80)
        self.assertEqual(spikes[0].polarity, 1)
        self.assertEqual(spikes[0].timestamp_us, (100 << 6) | 24)

        self.assertEqual(spikes[1].x, 150)
        self.assertEqual(spikes[1].y, 90)
        self.assertEqual(spikes[1].polarity, 0)

    def test_evt2_streaming_to_coprocessor(self):
        decoder = PropheseeEVT2Decoder()
        config = ReflexConfig()

        with GiantFiberCoprocessor(config) as coprocessor:
            w1 = (0x8 << 28) | (50 & 0x0FFFFFFF)
            w2 = (0x1 << 28) | ((10 & 0x3F) << 22) | ((160 & 0x7FF) << 11) | (160 & 0x7FF)
            raw = struct.pack("<2I", w1, w2)

            ingested = decoder.stream_into_coprocessor(raw, coprocessor, scale_to_64=True)
            self.assertEqual(ingested, 1)

    def test_evt3_decoding(self):
        decoder = PropheseeEVT3Decoder()

        # Build mock EVT3 binary buffer:
        # Word 1: TIME_HIGH (0x9), payload = 2
        # Word 2: TIME_LOW (0x8), payload = 500
        # Word 3: ADDR_Y (0x0), payload = 45
        # Word 4: ADDR_X (0x2), polarity=1, x=60
        w1 = (0x9 << 12) | (2 & 0x0FFF)
        w2 = (0x8 << 12) | (500 & 0x0FFF)
        w3 = (0x0 << 12) | (45 & 0x0FFF)
        w4 = (0x2 << 12) | (1 << 11) | (60 & 0x7FF)

        raw = struct.pack("<4H", w1, w2, w3, w4)
        spikes = decoder.decode_buffer(raw)

        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0].y, 45)
        self.assertEqual(spikes[0].x, 60)
        self.assertEqual(spikes[0].polarity, 1)
        self.assertEqual(spikes[0].timestamp_us, (2 << 12) | 500)


if __name__ == "__main__":
    unittest.main()
