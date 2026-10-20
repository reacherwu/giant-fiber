import warnings

import pytest

from giantfiber.envelope import (DEFAULT_RIG, VALIDATED_MAX_CLOSING_SPEED_MPS, OutsideEnvelopeWarning,
                                 check_threat_speed)


def test_inside_envelope_no_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert check_threat_speed(30.0)


def test_outside_envelope_warns():
    with pytest.warns(OutsideEnvelopeWarning):
        assert not check_threat_speed(60.0)


def test_defaults():
    assert VALIDATED_MAX_CLOSING_SPEED_MPS == 30.0
    assert len(DEFAULT_RIG) == 10
