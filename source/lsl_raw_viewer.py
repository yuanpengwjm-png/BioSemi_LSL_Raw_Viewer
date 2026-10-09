"""English raw LSL viewer for comparison with ActiView."""
from pathlib import Path
import csv
import json
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np
from lsl_core import Receiver, DemoOutlet
from raw_view import display_values, visible_window, line_segments

APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
MAX_DRAW_SAMPLES = 65536


class App:
    def __init__(self, root):
        self.root=root;root.title('BioSemi LSL Raw Viewer');root.geometry('1220x890');root.minsize(1040,760)
        self.events=queue.Queue();self.receiver=self.demo=self.meta=None;self.streams=[]
        self.active=self.scanning=self.closed=False;self.frozen=None;self.error_text='';self.auto_fitted=False
        self.build();root.protocol('WM_DELETE_WINDOW',self.close)
        root.report_callback_exception=lambda c,e,t:self.log('UI error: '+str(e))
        self.timer=root.after(100,self.tick)

    def build(self):
        style=ttk.Style();style.theme_use('clam');style.configure('.',font=('Segoe UI',10));style.configure('TButton',padding=(10,6))
        outer=ttk.Frame(self.root,padding=16);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='ActiView → LSL → Raw samples',font=('Segoe UI',21,'bold')).pack(anchor='w')
        ttk.Label(outer,text='No added filters, baseline subtraction, detrending, smoothing, resampling or sine fitting.').pack(anchor='w',pady=(3,8))
        bar=ttk.Frame(outer);bar.pack(fill='x')
        self.search_btn=ttk.Button(bar,text='1. Search streams',command=self.search);self.search_btn.pack(side='left')
        self.stream_box=ttk.Combobox(bar,state='readonly',width=65);self.stream_box.pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(bar,text='2. Connect',command=self.connect_selected).pack(side='left')
        ttk.Button(bar,text='Disconnect',command=self.stop).pack(side='left',padx=(8,0))
        self.banner=tk.Label(outer,text='Not connected',anchor='w',bg='#e2eaf4',fg='#142b46',font=('Segoe UI',11,'bold'),padx=10,pady=7);self.banner.pack(fill='x',pady=8)
        self.source=tk.StringVar(value='Select ActiView. The viewer uses LSL metadata; no TCP port setting is needed.')
        ttk.Label(outer,textvariable=self.source,wraplength=1170).pack(anchor='w')
        row=ttk.Frame(outer);row.pack(fill='x',pady=7);self.channels=[];self.centers=[]
        for i in range(2):
            ttk.Label(row,text=f'Trace {i+1}').pack(side='left')
            box=ttk.Combobox(row,state='readonly',width=29);box.pack(side='left',padx=(6,12));box.bind('<<ComboboxSelected>>',self.selection_changed);self.channels.append(box)
        self.freeze_btn=ttk.Button(row,text='Freeze view',command=self.freeze);self.freeze_btn.pack(side='right')
        row=ttk.Frame(outer);row.pack(fill='x',pady=3)
        ttk.Label(row,text='Time span (s)').pack(side='left');self.seconds=tk.StringVar(value='2')
        ttk.Combobox(row,textvariable=self.seconds,values=('1','2','4','8','10'),state='readonly',width=5).pack(side='left',padx=(5,12))
        ttk.Label(row,text='Display unit').pack(side='left');self.unit=tk.StringVar(value='mV')
        ub=ttk.Combobox(row,textvariable=self.unit,values=('Native','mV','uV'),state='readonly',width=8);ub.pack(side='left',padx=(5,12));ub.bind('<<ComboboxSelected>>',self.selection_changed)
        ttk.Label(row,text='Y units / division').pack(side='left');self.ydiv=tk.StringVar(value='2')
        ttk.Entry(row,textvariable=self.ydiv,width=9).pack(side='left',padx=(5,12));ttk.Button(row,text='Fit axes once',command=self.fit_axes).pack(side='left')
        row=ttk.Frame(outer);row.pack(fill='x',pady=3)
        for i in range(2):
            ttk.Label(row,text=f'Trace {i+1} Y-axis center').pack(side='left');center=tk.StringVar(value='0');self.centers.append(center)
            ttk.Entry(row,textvariable=center,width=11).pack(side='left',padx=(5,16))
        ttk.Label(row,text='Viewport only; samples keep their DC offset.',foreground='#52657c').pack(side='left')
        ttk.Label(outer,text='Initial axes fit once, then stay fixed. Unknown voltage units remain native.',foreground='#52657c').pack(anchor='w',pady=4)
        footer=ttk.Frame(outer);footer.pack(side='bottom',fill='x');self.status=tk.StringVar(value='Waiting for data. No signal-quality pass/fail is performed.')
        ttk.Label(footer,textvariable=self.status,wraplength=1170).pack(anchor='w',pady=5)
        row=ttk.Frame(footer);row.pack(fill='x')
        ttk.Button(row,text='Save displayed data + PNG',command=self.save).pack(side='left')
        ttk.Button(row,text='Stream metadata',command=self.show_metadata).pack(side='left',padx=8)
        ttk.Button(row,text='Synthetic LSL demo',command=self.start_demo).pack(side='left')
        self.log_box=tk.Text(footer,height=3,wrap='word',font=('Segoe UI',9),bg='#eaf0f7',relief='flat');self.log_box.pack(fill='x',pady=(6,0))
        self.canvases=[]
        for i in range(2):
            canvas=tk.Canvas(outer,bg='white',highlightthickness=1,highlightbackground='#cfd9e5',height=215);canvas.pack(fill='both',expand=True,pady=4);self.canvases.append(canvas)
        self.log('Ready. Match channel, reference, time span and display settings with ActiView.')

    def log(self,text):
        self.log_box.configure(state='normal');self.log_box.insert('end',time.strftime('%H:%M:%S  ')+text+'\n')
        if int(self.log_box.index('end-1c').split('.')[0])>100:self.log_box.delete('1.0','30.0')
        self.log_box.see('end');self.log_box.configure(state='disabled')

    def search(self):
        if self.scanning:return
        self.scanning=True;self.search_btn.configure(state='disabled');self.log('Searching LSL streams for 3 seconds…')
        def work():
            try:
                import pylsl
                self.events.put(('streams',pylsl.resolve_streams(wait_time=3)))
            except Exception as exc:self.events.put(('search_error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def connect_selected(self):
        index=self.stream_box.current()
        if not 0<=index<len(self.streams):messagebox.showinfo('Select stream','Search and select ActiView first.');return
        self.stop();self.connect_info(self.streams[index])

    def connect_info(self,info):
        self.meta=None;self.frozen=None;self.auto_fitted=False;self.error_text='';self.active=True;self.freeze_btn.configure(text='Freeze view')
        for box in self.channels:box.configure(values=[]);box.set('')
        self.receiver=Receiver(info,self.events);self.receiver.start();self.banner.configure(text='Connecting…',bg='#e2eaf4');self.log('Connecting to '+info.name())

    def stop(self):
        self.active=False
        if self.receiver:self.receiver.stop()
        if self.demo:self.demo.stop();self.demo=None
        self.banner.configure(text='Disconnected / cached samples only',bg='#e2eaf4')

    def start_demo(self):
        self.stop();demo=self.demo=DemoOutlet();demo.start();self.log('Synthetic source: 5 Hz, 10 mVpp; DC offsets +1.3 mV and -0.9 mV. Not hardware.')
        def work():
            try:
                import pylsl
                if not demo.ready.wait(6) or demo.error:raise RuntimeError(demo.error or 'Demo startup timeout')
                streams=pylsl.resolve_byprop('source_id',demo.source_id,timeout=5)
                if not streams:raise RuntimeError('Synthetic LSL stream not found')
                self.events.put(('demo_ready',(demo,streams[0])))
            except Exception as exc:self.events.put(('demo_error',(demo,str(exc))))
        threading.Thread(target=work,daemon=True).start()

    def handle_events(self):
        while True:
            try:kind,payload=self.events.get_nowait()
            except queue.Empty:return
            if kind in ('streams','search_error'):
                self.scanning=False;self.search_btn.configure(state='normal')
                if kind=='search_error':self.log('Search failed: '+payload);continue
                self.streams=payload;self.stream_box.configure(values=[f'{s.name()} | {s.channel_count()} ch | {s.nominal_srate():g} Hz | {s.hostname()}' for s in payload]);self.stream_box.set('')
                if len(payload)==1:self.stream_box.current(0)
                self.log(f'Found {len(payload)} streams.'+(' Select ActiView and connect.' if payload else ' Check acquisition and LSL availability in ActiView.'))
            elif kind=='connected' and payload is self.receiver and self.active:
                self.meta=payload.meta;m=self.meta;self.source.set(f"{m['name']} | {m['hostname']} | {m['channel_count']} channels | {m['nominal_srate']:g} Hz")
                values=['— Select channel —']+[f"{i+1:03d}  {n or 'unlabeled'}  [{m['units'][i] or 'unit unspecified'}]" for i,n in enumerate(m['labels'])]
                for box,label in zip(self.channels,('C11','C12')):
                    box.configure(values=values);box.current(0);matches=[i for i,n in enumerate(m['labels']) if n.upper()==label]
                    if len(matches)==1:box.current(matches[0]+1)
                self.log('Receiving original LSL samples. Confirm the actual physical channel mapping.')
            elif kind=='error' and payload[0] is self.receiver:self.active=False;self.error_text=payload[1];self.log('Receive error: '+payload[1])
            elif kind=='ended' and payload is self.receiver:self.active=False
            elif kind=='demo_ready' and payload[0] is self.demo:self.connect_info(payload[1])
            elif kind=='demo_error' and payload[0] is self.demo:self.log('Demo failed: '+payload[1])

    def selection_changed(self,*_):self.auto_fitted=False

    def snapshot(self):
        if self.frozen is not None:return self.frozen
        if not self.receiver or not self.receiver.buffer:return None
        return self.receiver.buffer.snapshot()

    def freeze(self):
        if self.frozen is None:
            self.frozen=self.snapshot()
            if self.frozen is not None:self.freeze_btn.configure(text='Resume live view')
        else:self.frozen=None;self.freeze_btn.configure(text='Freeze view')

    def view(self):
        snap=self.snapshot()
        if snap is None or not self.meta:return None
        seconds=float(self.seconds.get());x,t,stats=snap;x,t=visible_window(x,t,seconds);traces=[]
        for slot,box in enumerate(self.channels):
            index=box.current()-1
            if index<0:traces.append(None);continue
            native=x[:,index];shown,unit,factor=display_values(native,self.meta['units'][index],self.unit.get())
            traces.append(dict(slot=slot,index=index,name=self.meta['labels'][index] or f'Column {index+1}',native=native,shown=shown,unit=unit,factor=factor))
        return dict(traces=traces,t=t,stats=stats,seconds=seconds)

    def fit_axes(self):
        v=self.view()
        if not v or not len(v['t']):return
        spans=[]
        for trace,center in zip(v['traces'],self.centers):
            if trace is None or not np.isfinite(trace['shown']).all():continue
            lo,hi=float(trace['shown'].min()),float(trace['shown'].max());center.set(f'{(lo+hi)/2:.8g}');spans.append(max((hi-lo)/5,1e-6))
        if spans:self.ydiv.set(f'{max(spans):.8g}');self.auto_fitted=True

    def tick(self):
        if self.closed:return
        try:self.handle_events();self.refresh()
        except (ValueError,OverflowError):self.status.set('Enter finite numeric axis values; Y units/division must be positive.')
        except Exception as exc:self.log('Viewer error: '+str(exc))
        self.timer=self.root.after(100,self.tick)

    def refresh(self):
        v=self.view()
        if not v:
            if self.error_text:self.banner.configure(text='Connection failed: '+self.error_text,bg='#fde6de')
            return
        if not self.auto_fitted and len(v['t'])>=self.meta['nominal_srate']*.5:self.fit_axes()
        division=float(self.ydiv.get());centers=[float(c.get()) for c in self.centers]
        if division<=0 or not np.isfinite([division,*centers]).all():raise ValueError('Invalid axes')
        prefix='SYNTHETIC DEMO — NOT HARDWARE | ' if self.meta['demo'] else 'LSL SOURCE | '
        fresh=self.active and self.receiver.buffer.last_received is not None and time.monotonic()-self.receiver.buffer.last_received<2
        mode='FROZEN VIEW (receiver continues)' if self.frozen is not None and fresh else 'FROZEN / receiver stopped' if self.frozen is not None else 'LIVE' if fresh else 'NO NEW DATA / cached view'
        self.banner.configure(text=prefix+mode,bg='#fff0d8' if self.meta['demo'] or not fresh or self.frozen is not None else '#dcf2eb');stats=[]
        for canvas,trace,center in zip(self.canvases,v['traces'],centers):
            self.draw(canvas,trace,v['t'],v['seconds'],division,center)
            if trace is not None and len(trace['shown']) and np.isfinite(trace['shown']).all():
                y=trace['shown'];stats.append(f"{trace['name']}: min {y.min():.6g}, max {y.max():.6g}, raw Vpp {np.ptp(y):.6g} {trace['unit']}")
        text=f"{len(v['t']):,} displayed samples/channel | "+' | '.join(stats)
        if any(z is not None and z['unit'] not in ('mV','uV') for z in v['traces']) and self.unit.get()!='Native':text+=' | Unrecognized unit: native values retained.'
        if len(v['t'])>MAX_DRAW_SAMPLES:text='Too many samples to draw without decimation. Select a shorter time span. Saving retains all samples in the window.'
        self.status.set(text)

    def draw(self,c,trace,t,seconds,division,center):
        c.delete('all');w,h=max(300,c.winfo_width()),max(130,c.winfo_height());left,right,top,bottom=100,w-18,30,h-27
        low,high=center-3*division,center+3*division
        for k in range(9):xx=left+(right-left)*k/8;c.create_line(xx,top,xx,bottom,fill='#e5ebf3')
        for k in range(7):yy=top+(bottom-top)*k/6;c.create_line(left,yy,right,yy,fill='#c5d1df' if k==3 else '#e5ebf3')
        if trace is not None and len(t)>1 and len(t)<=MAX_DRAW_SAMPLES:
            y=trace['shown'];xx=left+(t-t[-1]+seconds)/seconds*(right-left);yy=bottom-(y-low)/(high-low)*(bottom-top)
            for a,b in line_segments(t,self.meta['nominal_srate']):
                good=np.isfinite(y[a:b]);edges=np.flatnonzero(np.diff(np.r_[False,good,False]))+a
                for start,end in zip(edges[::2],edges[1::2]):
                    if end-start>1:c.create_line(*np.column_stack((xx[start:end],yy[start:end])).ravel().tolist(),fill='#1376c7' if trace['slot']==0 else '#c16c13',width=1)
        # Margin masks clip the viewport without changing or clamping sample values.
        for rect in ((0,0,w,top),(0,bottom,w,h),(0,0,left,h),(right,0,w,h)):c.create_rectangle(*rect,fill='white',outline='white')
        for k in range(7):c.create_text(left-8,top+(bottom-top)*k/6,anchor='e',text=f'{high-k*division:.6g}',fill='#52657c')
        for k in range(9):c.create_text(left+(right-left)*k/8,bottom+14,text=f'{-seconds+seconds*k/8:g}',fill='#52657c')
        label='Select a channel' if trace is None else f"{trace['name']} | {trace['unit']} | {division:g} units/div | DC offset retained"
        c.create_text(left,12,anchor='w',text=label,fill='#142b46')
        if trace is not None and len(t):
            outside=np.count_nonzero((trace['shown']<low)|(trace['shown']>high))
            if outside:c.create_text(right,12,anchor='e',text=f'{outside} samples outside Y range — use Fit axes once',fill='#ac4b00')
        if len(t)>MAX_DRAW_SAMPLES:c.create_text((left+right)/2,(top+bottom)/2,text='Choose a shorter time span; display decimation is disabled.',fill='#ac4b00')

    def show_metadata(self):
        if not self.meta:messagebox.showinfo('No stream','Connect to a stream first.');return
        win=tk.Toplevel(self.root);win.title('Original LSL metadata');win.geometry('900x650')
        text=tk.Text(win,wrap='word');text.pack(fill='both',expand=True);text.insert('1.0',json.dumps(self.meta,indent=2,ensure_ascii=False));text.configure(state='disabled')

    def save(self,folder=None,notify=True):
        try:
            v=self.view()
            if not v or not len(v['t']):raise ValueError('Receive some samples first.')
            traces=[z for z in v['traces'] if z is not None]
            if not traces:raise ValueError('Select at least one channel.')
            division=float(self.ydiv.get());centers=[float(c.get()) for c in self.centers]
            if division<=0 or not np.isfinite([division,*centers]).all():raise ValueError('Invalid axis settings.')
            folder=Path(folder) if folder is not None else APP_DIR/'captures';folder.mkdir(parents=True,exist_ok=True)
            stem=('DEMO_RAW_' if self.meta['demo'] else 'LSL_RAW_')+time.strftime('%Y%m%d_%H%M%S')+f'_{time.time_ns()%1000000:06d}'
            metadata=dict(source=self.meta,selected_indices=[z['index'] for z in traces],channel_labels=[z['name'] for z in traces],
                source_units=[self.meta['units'][z['index']] for z in traces],display_units=[z['unit'] for z in traces],display_factors=[z['factor'] for z in traces],
                processing='None: no added filters, DC subtraction, detrending, smoothing, resampling or fitting.',
                upstream_processing='Unknown; raw means samples received from LSL, not guaranteed ADC-level raw data.',
                timestamps='Original LSL source clock seconds',orientation='samples_x_selected_channels',frozen=self.frozen is not None,receiver_active=self.active,stats=v['stats'],
                viewport=dict(seconds=v['seconds'],units_per_division=division,centers=[centers[z['slot']] for z in traces]))
            native=np.column_stack([z['native'] for z in traces]);shown=np.column_stack([z['shown'] for z in traces])
            np.savez_compressed(folder/(stem+'.npz'),samples_native=native,samples_display=shown,timestamps_lsl=v['t'],metadata_json=np.array(json.dumps(metadata,ensure_ascii=False)))
            (folder/(stem+'.json')).write_text(json.dumps(metadata,indent=2,ensure_ascii=False),encoding='utf-8')
            with (folder/(stem+'.csv')).open('w',newline='',encoding='utf-8') as f:
                writer=csv.writer(f);writer.writerow(['lsl_timestamp_s']+[f"{z['name']} [{self.meta['units'][z['index']] or 'native'}]" for z in traces]);writer.writerows(np.column_stack((v['t'],native)))
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            fig=Figure(figsize=(13,3*len(traces)),layout='constrained');FigureCanvasAgg(fig)
            for row,z in enumerate(traces):
                ax=fig.add_subplot(len(traces),1,row+1)
                for a,b in line_segments(v['t'],self.meta['nominal_srate']):ax.plot(v['t'][a:b]-v['t'][-1],z['shown'][a:b],lw=.8)
                center=centers[z['slot']];ax.set(xlim=(-v['seconds'],0),ylim=(center-3*division,center+3*division),ylabel=f"{z['name']} ({z['unit']})",xlabel='Time relative to last displayed sample (s)')
                ax.set_yticks(center+np.arange(-3,4)*division);ax.grid(alpha=.25)
            fig.suptitle(('SYNTHETIC DEMO | ' if self.meta['demo'] else '')+'Original LSL samples — DC retained, no added processing');fig.savefig(folder/(stem+'.png'),dpi=140)
            self.log('Saved NPZ, CSV, JSON and PNG: '+str(folder/stem))
            if notify:messagebox.showinfo('Saved','Original samples and the displayed plot were saved to:\n'+str(folder))
            return folder/(stem+'.npz')
        except Exception as exc:
            if notify:messagebox.showerror('Save failed',str(exc))
            else:raise

    def close(self):
        self.closed=True;self.stop()
        try:self.root.after_cancel(self.timer)
        except (AttributeError,tk.TclError):pass
        self.root.destroy()


def main():
    root=tk.Tk();app=App(root)
    if '--gui-smoke' in sys.argv:
        root.withdraw();app.start_demo()
        def finish():
            try:
                assert app.meta and app.meta['demo'];app.seconds.set('8');app.freeze();v=app.view();assert len(v['t'])>4096
                means=[float(z['shown'].mean()) for z in v['traces']]
                assert abs(means[0]-1.3)<.08 and abs(means[1]+.9)<.08,means
                path=app.save(APP_DIR/'raw_test_artifacts',notify=False)
                with np.load(path,allow_pickle=False) as saved:
                    np.testing.assert_array_equal(saved['samples_native'][:,0],v['traces'][0]['native'])
                    np.testing.assert_array_equal(saved['samples_display'][:,0],v['traces'][0]['native']*.001)
                result=dict(ok=True,synthetic=True,real_hardware_verified=False,mean_display_mV=means,samples=len(v['t']),capture=str(path))
            except Exception as exc:result=dict(ok=False,error=str(exc))
            (APP_DIR/'raw_gui_smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8');app.close()
        root.after(7000,finish)
    root.mainloop()


if __name__=='__main__':main()
