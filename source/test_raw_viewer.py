import json
from pathlib import Path
import unittest
import tkinter as tk
from types import SimpleNamespace
import numpy as np
from raw_view import display_values, visible_window, line_segments
from lsl_core import SampleBuffer
from lsl_raw_viewer import App


class RawViewerTests(unittest.TestCase):
    def test_units_preserve_dc_and_samples(self):
        x=np.array([1000.,5000.,9000.,-2000.]);original=x.copy()
        y,unit,factor=display_values(x,'uV','mV')
        np.testing.assert_array_equal(y,x*.001);np.testing.assert_array_equal(x,original)
        self.assertEqual(unit,'mV');self.assertEqual(factor,.001)
        y,unit,factor=display_values(x,'counts','mV')
        np.testing.assert_array_equal(y,x);self.assertEqual(unit,'counts');self.assertEqual(factor,1)

    def test_time_selection_and_gap_no_interpolation(self):
        x=np.arange(20).reshape(10,2);t=np.arange(10)/10
        selected,stamps=visible_window(x,t,.4)
        np.testing.assert_array_equal(selected,x[t>=t[-1]-.4]);np.testing.assert_array_equal(stamps,t[t>=t[-1]-.4])
        self.assertEqual(line_segments([0,.1,.2,.7,.8],10),[(0,3),(3,5)])

    def test_axes_freeze_export_keep_raw_values(self):
        root=tk.Tk();root.withdraw();app=App(root)
        try:
            fs=1024;t=np.arange(fs*2)/fs
            x=np.column_stack((1300+5000*np.sin(2*np.pi*5*t),-900+5000*np.sin(2*np.pi*5*t)))
            buffer=SampleBuffer(2,fs);buffer.append(x,t)
            app.receiver=SimpleNamespace(buffer=buffer,stop=lambda:None)
            app.meta=dict(demo=True,labels=['C11','C12'],units=['uV','uV'],nominal_srate=fs,name='TEST')
            for i,box in enumerate(app.channels):box.configure(values=['select','C11','C12']);box.current(i+1)
            app.fit_axes();app.freeze();app.refresh()
            frozen=app.view()['traces'][0]['native'].copy()
            buffer.append(x+20000,t+2)
            np.testing.assert_array_equal(app.view()['traces'][0]['native'],frozen)
            app.centers[0].set('1000');app.ydiv.set('200');app.refresh()
            np.testing.assert_array_equal(app.view()['traces'][0]['native'],x[:,0])
            out=Path(__file__).resolve().parent/'raw_test_artifacts'/'unit_export';out.mkdir(parents=True,exist_ok=True)
            file=app.save(out,notify=False)
            with np.load(file,allow_pickle=False) as data:
                np.testing.assert_array_equal(data['samples_native'],x)
                np.testing.assert_array_equal(data['samples_display'],x*.001)
                self.assertTrue(json.loads(str(data['metadata_json']))['frozen'])
            self.assertTrue(file.with_suffix('.png').exists())
            self.assertTrue(file.with_suffix('.csv').exists())
            app.freeze();self.assertGreater(app.view()['traces'][0]['native'].mean(),10000)
        finally:app.close()

    def test_english_application_strings(self):
        for name in ('lsl_raw_viewer.py','raw_view.py','lsl_core.py','lsl_check.py'):
            code=Path(__file__).with_name(name).read_text(encoding='utf-8')
            self.assertFalse(any('\u4e00'<=char<='\u9fff' for char in code),name)


if __name__=='__main__':unittest.main(verbosity=2)
