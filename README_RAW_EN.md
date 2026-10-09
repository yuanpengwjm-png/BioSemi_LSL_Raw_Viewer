# BioSemi LSL Raw Viewer

An English-only viewer for comparing incoming LSL samples with the ActiView display.

## Start

1. Extract the complete ZIP. Keep `BioSemi_LSL_Raw_Viewer.exe` beside `_internal`.
2. Double-click the EXE. Python installation is not required.
3. Start acquisition in ActiView. Click **Search streams**, select the correct ActiView stream, then **Connect**.
4. Select the two physical channels. C11/C12 are preselected only when those exact unique labels exist.
5. Match **Time span**, **Display unit**, and **Y units / division** to the comparison display.
6. Use **Fit axes once** if the raw DC offset places the signal outside the visible range. This only moves the axes; it does not subtract or change data. The initial axes fit once after samples arrive, then remain fixed.
7. **Freeze view** freezes the displayed data while reception continues. **Resume live view** returns to current samples.
8. **Save displayed data + PNG** writes the exact selected capture window to `captures` beside the EXE: NPZ, CSV, JSON and PNG.

## What changed

- English interface, messages, metadata viewer and instructions.
- No baseline subtraction, high-pass, low-pass, notch, smoothing, detrending, resampling, sine fitting, FFT or quality pass/fail in this viewer.
- Original DC offsets, noise, transients and source timestamps are retained.
- All samples in the visible time window are drawn; there is no min/max decimation. At more than 65,536 samples per trace, the viewer asks for a shorter time span rather than silently reducing the data. Data saving retains every sample in the requested window.
- Adjacent received samples are connected with straight plot lines; missing intervals are not filled or resampled. Lines break at timestamp discontinuities or nonfinite samples.
- Only channel selection, time-window selection and explicit metadata-based voltage-unit conversion are applied. Choose **Native** for the original received numeric scale. If the source unit is unknown or counts, native values remain unchanged and the trace label identifies that unit.
- Raw minimum, maximum and peak-to-peak range are descriptive readouts; no fitted amplitude or automatic acceptance verdict is calculated.

## A fair comparison with ActiView

Compare the same physical channel and reference, time interval, voltage scale and units. This viewer does not rereference channels or compensate for another application's display filters. ActiView screen pixels and time cursors are not expected to be synchronized with this viewer.

For a raw comparison, use a corresponding unfiltered ActiView view where supported. The earlier laboratory screenshot had a 0.16 Hz high-pass and 100 Hz low-pass enabled; that display can differ from an unfiltered LSL trace. A different baseline alone is not proof of incorrect samples.

**Raw means original samples received from LSL.** This viewer cannot undo processing already performed by the source, and it does not claim that the stream is unprocessed ADC output. Inspect **Stream metadata** and confirm the source configuration if the traces differ.

The default time span is 2 s. For 5 Hz this shows about 10 cycles. Set 8 s to match an 8-second ActiView display when the sample count permits it. If ActiView is set to 20 mV/div, choose mV and enter 20 units/div here, after confirming the metadata unit.

## Save format

- `samples_native`: original received numeric values, shape `[samples, selected channels]`.
- `samples_display`: the same values with the recorded voltage-unit conversion only.
- `timestamps_lsl`: original LSL source-clock seconds (not wall-clock time).
- `metadata_json`: source XML, labels, source/display units, conversion factors, axis settings and freeze state.
- CSV contains the source timestamp and native-unit values; its headers identify the source units.
- PNG uses the same axes and time span as the captured view, without filtering or baseline subtraction.

The buffer retains at most 10 seconds and 64 MiB. This is a comparison viewer, not a continuous recording system.

## Synthetic check

**Synthetic LSL demo** sends two 5 Hz, 10 mVpp signals with DC offsets of +1.3 mV and -0.9 mV. These offsets should remain visible in the axis values. Synthetic data are clearly labeled and saved with `DEMO_RAW_` filenames.

## Source

`lsl_raw_viewer.py` provides the viewer; `raw_view.py` contains only display selection/conversion; `lsl_core.py` provides the existing receiver. Legacy analysis helpers in the core are not called by the raw viewer. `lsl_check.py` is a compatibility launch entry for this English viewer.

```powershell
python -m pip install -r requirements.txt
python lsl_raw_viewer.py
python -m unittest -v test_raw_viewer.py
```

Verified with synthetic local LSL transmission and raw-sample save/readback. Physical ActiView channel mapping, voltage calibration and source processing still require confirmation in the laboratory.
