import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import hashlib, struct, shutil

TAG_COUNT = 100
TAGGED_THRESHOLD = 0xE4
STUNT_COUNT = 70
STUNT_RECORD_SIZE = 0x44
STUNT_DONE_OFF = 0x40
STUNT_FOUND_OFF = 0x41

def calc_checksum(data):
    tmp = bytearray(data)
    tmp[:24] = b"\0" * 24
    digest = hashlib.md5(tmp).digest()
    return bytes((~x) & 0xFF for x in digest)

def checksum_valid(data):
    return len(data) >= 24 and calc_checksum(data) == data[8:24]

def fix_checksum(data):
    data[8:24] = calc_checksum(bytes(data))

def find_block(data, name):
    needle = name.encode("ascii") + b"\0"
    pos = 0
    while True:
        pos = data.find(needle, pos)
        if pos < 0:
            return None
        if pos >= 4:
            n = struct.unpack_from("<I", data, pos - 4)[0]
            if n == len(needle):
                value_off = pos + len(needle)
                if value_off + 4 <= len(data):
                    value = struct.unpack_from("<I", data, value_off)[0]
                    return {"header": pos-4, "value": value_off,
                            "payload": value_off+4, "count": value}
        pos += 1

def get_tags(data):
    b = find_block(data, "TAGS")
    if not b or b["count"] != 100:
        raise ValueError("TAGS block not found or invalid.")
    vals = data[b["payload"]:b["payload"]+100]
    if len(vals) != 100:
        raise ValueError("TAGS data is truncated.")
    return b, vals

def get_stunts(data):
    b = find_block(data, "STUNTJUMPS")
    if not b or b["count"] != 70:
        return None, []
    rows=[]
    for i in range(70):
        off=b["payload"]+i*0x44
        rows.append((data[off+0x40], data[off+0x41]))
    return b, rows

class Editor:
    def __init__(self, root):
        self.root=root
        self.root.title("GTA San Andreas DE Save Editor")
        self.root.geometry("850x720")
        self.root.minsize(760,620)
        self.path=None
        self.data=None
        self.tag_vars=[]
        self.build()

    def build(self):
        top=ttk.Frame(self.root,padding=10); top.pack(fill="x")
        ttk.Button(top,text="Open Save",command=self.open_save).pack(side="left")
        ttk.Button(top,text="Save As...",command=self.save_as).pack(side="left",padx=6)
        ttk.Button(top,text="Refresh",command=self.refresh).pack(side="left")
        self.file_label=ttk.Label(top,text="No save loaded")
        self.file_label.pack(side="left",padx=15)

        self.status=ttk.Label(self.root,text="Open a GTA San Andreas Definitive Edition .sav file.",padding=(10,5))
        self.status.pack(fill="x")

        nb=ttk.Notebook(self.root); nb.pack(fill="both",expand=True,padx=10,pady=5)
        self.tags_tab=ttk.Frame(nb); nb.add(self.tags_tab,text="Gang Tags")
        self.stunt_tab=ttk.Frame(nb); nb.add(self.stunt_tab,text="Stunt Jumps")

        self.build_tags()
        self.build_stunts()

        bottom=ttk.Frame(self.root,padding=10); bottom.pack(fill="x")
        ttk.Button(bottom,text="Set ALL Tags Complete",command=self.all_tags).pack(side="left")
        ttk.Button(bottom,text="Clear ALL Tags",command=self.clear_tags).pack(side="left",padx=6)
        ttk.Button(bottom,text="Set ALL Stunts Complete",command=self.all_stunts).pack(side="left")
        self.info=ttk.Label(bottom,text="")
        self.info.pack(side="right")

    def build_tags(self):
        head=ttk.Frame(self.tags_tab,padding=8); head.pack(fill="x")
        self.tag_count=ttk.Label(head,text="No save loaded")
        self.tag_count.pack(side="left")
        ttk.Label(head,text="Completed = alpha > 0xE4").pack(side="right")
        frame=ttk.Frame(self.tags_tab); frame.pack(fill="both",expand=True,padx=8,pady=5)
        canvas=tk.Canvas(frame)
        scroll=ttk.Scrollbar(frame,orient="vertical",command=canvas.yview)
        inner=ttk.Frame(canvas)
        inner.bind("<Configure>",lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0),window=inner,anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left",fill="both",expand=True); scroll.pack(side="right",fill="y")
        self.tag_vars=[]
        for i in range(100):
            r=i//4; c=(i%4)*3
            ttk.Label(inner,text=f"Tag {i:02d}").grid(row=r,column=c,padx=8,pady=4,sticky="w")
            v=tk.StringVar(value="00")
            self.tag_vars.append(v)
            ttk.Entry(inner,textvariable=v,width=5).grid(row=r,column=c+1,padx=2,pady=4)
            s=tk.StringVar(value="—")
            setattr(self,f"tag_state_{i}",s)
            ttk.Label(inner,textvariable=s,width=10).grid(row=r,column=c+2,padx=4,pady=4,sticky="w")

    def build_stunts(self):
        frame=ttk.Frame(self.stunt_tab,padding=10); frame.pack(fill="both",expand=True)
        ttk.Label(frame,text="Index     Done     Found").grid(row=0,column=0,columnspan=3,sticky="w")
        self.stunt_labels=[]
        for i in range(70):
            ttk.Label(frame,text=f"{i:02d}").grid(row=i+1,column=0,padx=10,pady=2,sticky="w")
            d=ttk.Label(frame,text="—"); d.grid(row=i+1,column=1,padx=20,sticky="w")
            f=ttk.Label(frame,text="—"); f.grid(row=i+1,column=2,padx=20,sticky="w")
            self.stunt_labels.append((d,f))

    def open_save(self):
        p=filedialog.askopenfilename(title="Open GTA SA DE Save",
            filetypes=[("GTA San Andreas saves","*.sav"),("All files","*.*")])
        if not p:return
        try:
            self.path=Path(p); self.data=bytearray(self.path.read_bytes())
            self.refresh()
        except Exception as e:
            messagebox.showerror("Error",str(e))

    def refresh(self):
        if self.data is None:return
        try:
            b,tags=get_tags(bytes(self.data))
            done=sum(x>TAGGED_THRESHOLD for x in tags)
            self.file_label.config(text=self.path.name if self.path else "")
            self.tag_count.config(text=f"Tags: {done}/100    TAGS payload: 0x{b['payload']:08X}")
            for i,v in enumerate(tags):
                self.tag_vars[i].set(f"{v:02X}")
                getattr(self,f"tag_state_{i}").set("COMPLETE" if v>TAGGED_THRESHOLD else "incomplete")
            sb,rows=get_stunts(bytes(self.data))
            if sb:
                for i,(d,f) in enumerate(rows):
                    self.stunt_labels[i][0].config(text="YES" if d else "NO")
                    self.stunt_labels[i][1].config(text="YES" if f else "NO")
                sd=sum(x[0] != 0 for x in rows); sf=sum(x[1] != 0 for x in rows)
                stunt_text=f"Stunts: {sd}/70 done, {sf}/70 found"
            else: stunt_text="Stunt block not found"
            self.info.config(text=f"Checksum: {'VALID' if checksum_valid(bytes(self.data)) else 'INVALID'}")
            self.status.config(text=stunt_text)
        except Exception as e:
            messagebox.showerror("Invalid save",str(e))

    def apply_tag_fields(self):
        b,_=get_tags(bytes(self.data))
        for i,v in enumerate(self.tag_vars):
            s=v.get().strip().replace("0x","").replace("0X","")
            val=int(s,16)
            if not 0<=val<=255: raise ValueError(f"Tag {i}: alpha must be 00-FF")
            self.data[b["payload"]+i]=val

    def all_tags(self):
        if self.data is None:return
        try:
            b,_=get_tags(bytes(self.data))
            self.data[b["payload"]:b["payload"]+100]=bytes([0xFF])*100
            fix_checksum(self.data); self.refresh()
        except Exception as e: messagebox.showerror("Error",str(e))

    def clear_tags(self):
        if self.data is None:return
        if not messagebox.askyesno("Clear Tags","Set all 100 tags to 00?"):return
        try:
            b,_=get_tags(bytes(self.data))
            self.data[b["payload"]:b["payload"]+100]=bytes(100)
            fix_checksum(self.data); self.refresh()
        except Exception as e: messagebox.showerror("Error",str(e))

    def all_stunts(self):
        if self.data is None:return
        try:
            b,rows=get_stunts(bytes(self.data))
            if not b: raise ValueError("STUNTJUMPS block not found.")
            for i in range(70):
                off=b["payload"]+i*0x44
                self.data[off+0x40]=1
                self.data[off+0x41]=1
            fix_checksum(self.data); self.refresh()
        except Exception as e: messagebox.showerror("Error",str(e))

    def save_as(self):
        if self.data is None:
            messagebox.showinfo("No save","Open a save first."); return
        try:
            self.apply_tag_fields()
            fix_checksum(self.data)
        except Exception as e:
            messagebox.showerror("Tag value error",str(e)); return
        p=filedialog.asksaveasfilename(title="Save Edited GTA SA Save",
            defaultextension=".sav",
            filetypes=[("GTA San Andreas saves","*.sav"),("All files","*.*")])
        if not p:return
        try:
            Path(p).write_bytes(self.data)
            self.path=Path(p)
            self.refresh()
            messagebox.showinfo("Saved","Save written successfully.\nChecksum recalculated and verified.")
        except Exception as e: messagebox.showerror("Save error",str(e))

root=tk.Tk()
try:
    root.tk.call("tk", "scaling", 1.1)
except Exception:
    pass
Editor(root)
root.mainloop()
