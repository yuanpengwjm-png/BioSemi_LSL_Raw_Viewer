"""Display selection and explicit unit conversion; no signal processing."""
import numpy as np
from lsl_core import unit_to_uv


def display_values(native, source_unit, display_unit):
    values = np.asarray(native, dtype=float)
    factor = unit_to_uv(source_unit)
    if display_unit == 'Native' or factor is None:
        return values.copy(), source_unit or 'native (unit unspecified)', 1.
    factor /= 1000. if display_unit == 'mV' else 1.
    return values * factor, display_unit, factor


def visible_window(samples, timestamps, seconds):
    if not len(timestamps):return samples, timestamps
    keep = timestamps >= timestamps[-1] - seconds
    return samples[keep], timestamps[keep]


def line_segments(timestamps, rate):
    t = np.asarray(timestamps)
    if not len(t):return []
    breaks = np.flatnonzero((np.diff(t) <= 0) | (np.diff(t) > 1.5/rate)) + 1
    edges = np.r_[0, breaks, len(t)]
    return [(int(a), int(b)) for a, b in zip(edges[:-1], edges[1:])]
