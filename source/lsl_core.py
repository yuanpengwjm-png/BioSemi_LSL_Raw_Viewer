"""LSL reception and validation, independent of the GUI. No device-specific byte decoding."""
from __future__ import annotations

from collections import deque
import math
import threading
import time
import xml.etree.ElementTree as ET

import numpy as np


def unit_to_uv(unit):
    key = unit.strip().lower().replace('μ', 'u').replace('µ', 'u').replace(' ', '')
    return {'uv': 1., 'microvolt': 1., 'microvolts': 1., 'mv': 1000.,
            'millivolt': 1000., 'millivolts': 1000., 'v': 1e6,
            'volt': 1e6, 'volts': 1e6, 'nv': .001}.get(key)


def metadata(info):
    xml = info.as_xml()
    root = ET.fromstring(xml)
    channels = root.findall('./desc/channels/channel')
    names, units = [], []
    for i in range(info.channel_count()):
        node = channels[i] if i < len(channels) else None
        names.append((node.findtext('label') or node.findtext('name') or '').strip()
                     if node is not None else '')
        units.append((node.findtext('unit') or '').strip() if node is not None else '')
    return dict(name=info.name(), type=info.type(), source_id=info.source_id(),
                uid=info.uid(), hostname=info.hostname(), channel_count=info.channel_count(),
                nominal_srate=info.nominal_srate(), channel_format=info.channel_format(),
                labels=names, units=units, xml=xml,
                demo=info.source_id().startswith('DEMO_BioSemiLSL_'))


class SampleBuffer:
    """Bounded raw-unit buffer; one row = one timestamp, one column = one channel."""
    def __init__(self, channels, rate):
        self.capacity = max(1, min(int(math.ceil(rate * 10)), 64 * 1024**2 // (8 * (channels + 1))))
        self.channels = channels
        self.rate = rate
        self.parts = deque()
        self.count = self.total = self.gaps = self.nonmonotonic = self.nonfinite = 0
        self.last_timestamp = None
        self.last_received = None
        self.first_received = None
        self.started = time.monotonic()
        self.lock = threading.Lock()

    def append(self, samples, timestamps):
        x = np.asarray(samples, dtype=np.float64)
        t = np.asarray(timestamps, dtype=np.float64)
        if x.shape != (len(t), self.channels) or not np.isfinite(t).all():
            raise ValueError('Invalid sample dimensions or timestamps')
        if not len(t):
            return
        with self.lock:
            dt = np.diff(t if self.last_timestamp is None else np.r_[self.last_timestamp, t])
            self.gaps += int(np.count_nonzero(dt > 1.5 / self.rate))
            self.nonmonotonic += int(np.count_nonzero(dt <= 0))
            self.nonfinite += int(np.count_nonzero(~np.isfinite(x)))
            self.last_timestamp = float(t[-1])
            self.last_received = time.monotonic()
            if self.first_received is None:
                self.first_received = self.last_received
            self.total += len(t)
            self.parts.append((x.copy(), t.copy()))
            self.count += len(t)
            while self.count > self.capacity:
                a, b = self.parts.popleft()
                excess = self.count - self.capacity
                if len(b) <= excess:
                    self.count -= len(b)
                else:
                    self.parts.appendleft((a[excess:].copy(), b[excess:].copy()))
                    self.count -= excess

    def snapshot(self, columns=None, seconds=10):
        with self.lock:
            n = min(self.count, int(seconds * self.rate))
            rows, stamps = [], []
            for x, t in reversed(self.parts):
                take = min(n, len(t))
                if take:
                    rows.append(x[-take:] if columns is None else x[-take:, columns])
                    stamps.append(t[-take:])
                n -= take
                if not n:
                    break
            stats = dict(total=self.total, buffered=self.count, gaps=self.gaps,
                         nonmonotonic=self.nonmonotonic, nonfinite=self.nonfinite,
                         silence_s=None if self.last_received is None else time.monotonic()-self.last_received,
                         elapsed_s=time.monotonic()-self.started)
            if not stamps:
                return np.empty((0, self.channels if columns is None else len(columns))), np.empty(0), stats
            return np.concatenate(rows[::-1]), np.concatenate(stamps[::-1]), stats


class Receiver(threading.Thread):
    def __init__(self, info, events):
        super().__init__(daemon=True)
        self.info, self.events = info, events
        self.stop_event = threading.Event()
        self.buffer = None
        self.meta = None
        self.failure = None
        self.clock_offset = None

    def stop(self):
        self.stop_event.set()

    def run(self):
        import pylsl
        from pylsl.util import TimeoutError as LSLTimeoutError
        inlet = None
        try:
            # Keep original source timestamps. Store clock correction separately.
            inlet = pylsl.StreamInlet(self.info, max_buflen=3, recover=False)
            inlet.open_stream(timeout=5)
            self.meta = metadata(inlet.info(timeout=5))
            fs, nc = self.meta['nominal_srate'], self.meta['channel_count']
            if not math.isfinite(fs) or fs <= 0 or nc < 1 or nc > 4096:
                raise ValueError('Select a numeric continuous stream with a positive sample rate and at most 4096 channels.')
            if self.meta['channel_format'] == pylsl.cf_string:
                raise ValueError('This is a text/event stream. Select a numeric EEG stream.')
            self.buffer = SampleBuffer(nc, fs)
            self.events.put(('connected', self))
            last_correction = 0.
            # Limit each pull to ~50 ms and at most 1 million values.
            chunk_size = max(1, min(4096, int(fs*.05), 1000000 // nc))
            while not self.stop_event.is_set():
                x, t = inlet.pull_chunk(timeout=.1, max_samples=chunk_size)
                if len(t):
                    self.buffer.append(x, t)
                if time.monotonic()-last_correction > 2:
                    try:
                        self.clock_offset = float(inlet.time_correction(timeout=0.))
                        last_correction = time.monotonic()
                    except LSLTimeoutError:
                        pass
        except Exception as exc:
            self.failure = str(exc)
            self.events.put(('error', (self, str(exc))))
        finally:
            if inlet is not None:
                try:
                    inlet.close_stream()
                except Exception:
                    pass
            self.events.put(('ended', self))


def sine_metrics(x, timestamps, rate, scale_uv=None, target_hz=5., target_vpp_mv=10.):
    """Estimate dominant sine in a recent window; never infer unknown physical units."""
    x, t = np.asarray(x, float), np.asarray(timestamps, float)
    result = dict(valid=False, reason='Waiting for at least 2 seconds of data', unit_known=scale_uv is not None)
    if len(x) < 32 or len(t) != len(x):
        return result
    t = t - t[0]
    if t[-1] < 2:
        return result
    if not np.isfinite(x).all() or not np.isfinite(t).all():
        return dict(result, reason='Nonfinite samples')
    dt = np.diff(t)
    if np.any(dt <= 0) or np.max(dt) > 1.5/rate:
        return dict(result, reason='Discontinuous timestamps; frequency/amplitude assessment unavailable')
    actual_rate = (len(t)-1)/t[-1]
    if abs(actual_rate/rate-1) > .05 or np.std(dt)/np.mean(dt) > .2:
        return dict(result, reason='Timestamp intervals do not match the nominal rate')
    raw_pp = float(np.ptp(x))
    if raw_pp < 1e-12:
        return dict(result, reason='Flat signal')
    # Cap calculation cost. Original data remain intact in the receive buffer.
    step = max(1, len(x)//8192)
    y, tt = x[::step], t[::step]
    centered = y-y.mean()
    # Estimate frequency before reducing fit points: otherwise high frequencies
    # could alias into a false 5 Hz match during validation.
    spectrum = np.abs(np.fft.rfft((x-x.mean())*np.hanning(len(x))))
    spectrum[0] = 0
    k = int(np.argmax(spectrum))
    spacing = actual_rate / len(x)
    initial = k*spacing
    def fit(f):
        phase = 2*np.pi*f*tt
        mat = np.column_stack((np.sin(phase), np.cos(phase), np.ones(len(tt))))
        coefficients, *_ = np.linalg.lstsq(mat, y, rcond=None)
        error = float(np.sum((y-mat@coefficients)**2))
        return error, coefficients
    lo, hi = max(.01, initial-spacing), min(actual_rate/2*.999, initial+spacing)
    # Golden-section minimization of sinusoidal least-squares residual.
    g = (5**.5-1)/2
    a, b = hi-g*(hi-lo), lo+g*(hi-lo)
    fa, fb = fit(a)[0], fit(b)[0]
    for _ in range(24):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi-g*(hi-lo); fa = fit(a)[0]
        else:
            lo, a, fa = a, b, fb
            b = lo+g*(hi-lo); fb = fit(b)[0]
    frequency = (lo+hi)/2
    residual, coefficients = fit(frequency)
    pp = float(2*np.hypot(*coefficients[:2]))
    r2 = float(1-residual/max(float(np.sum(centered**2)), 1e-30))
    freq_ok = abs(frequency-target_hz) <= max(.1, .02*target_hz)
    shape_ok = r2 >= .95 and raw_pp <= 1.25*pp
    amp_ok = scale_uv is not None and abs(pp*scale_uv-target_vpp_mv*1000) <= target_vpp_mv*100
    return dict(valid=True, reason='', frequency_hz=frequency, fitted_vpp_native=pp,
                raw_vpp_native=raw_pp, offset_native=float(coefficients[2]), r_squared=r2,
                actual_rate_hz=actual_rate, fitted_vpp_uv=None if scale_uv is None else pp*scale_uv,
                raw_vpp_uv=None if scale_uv is None else raw_pp*scale_uv,
                frequency_ok=bool(freq_ok), shape_ok=bool(shape_ok), amplitude_ok=bool(amp_ok),
                unit_known=scale_uv is not None, matches_target=bool(freq_ok and shape_ok and amp_ok))


class DemoOutlet(threading.Thread):
    """An actual LSL outlet, unmistakably labeled as synthetic."""
    def __init__(self):
        import uuid
        super().__init__(daemon=True)
        self.source_id = 'DEMO_BioSemiLSL_'+uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.error = None

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            import pylsl
            fs = 1024
            info = pylsl.StreamInfo('DEMO_ONLY_5Hz_10mVpp', 'EEG', 2, fs, 'float32', self.source_id)
            channels = info.desc().append_child('channels')
            for name in ('C11', 'C12'):
                ch = channels.append_child('channel')
                ch.append_child_value('label', name)
                ch.append_child_value('unit', 'uV')
            outlet = pylsl.StreamOutlet(info, chunk_size=32)
            self.ready.set()
            start, n = pylsl.local_clock(), 0
            while not self.stop_event.is_set():
                due = start+(n+32)/fs
                if self.stop_event.wait(max(0, due-pylsl.local_clock())):
                    break
                t = np.arange(n, n+32)/fs
                x = np.column_stack((5000*np.sin(2*np.pi*5*t)+1300,
                                     5000*np.sin(2*np.pi*5*t+.3)-900))
                outlet.push_chunk(x.tolist(), timestamp=start+(n+31)/fs)
                n += 32
        except Exception as exc:
            self.error = str(exc)
            self.ready.set()
