import os
import glob

src_dir = '/opt/autosparefinder/frontend/src'
for filepath in glob.glob(os.path.join(src_dir, '**', '*.jsx'), recursive=True):
    with open(filepath, 'r') as f:
        content = f.read()

    new_content = content
    
    if 'AuthBrandHeader.jsx' in filepath:
        new_content = new_content.replace(
            'title = <>Auto <span className="text-brand-600">Spare</span></>',
            'title = <><span className="text-brand-navy">Auto</span> <span className="text-brand-blue">Spare</span></>'
        )
        new_content = new_content.replace(
            'bg-brand-600 rounded-2xl flex items-center justify-center shadow-lg',
            'bg-brand-navy rounded-brand flex items-center justify-center shadow-electric border border-brand-border'
        )
        new_content = new_content.replace(
            'Wrench className="w-9 h-9 text-white"',
            'Wrench className="w-9 h-9 text-brand-blue"'
        )
        
    if 'DashboardLayout.jsx' in filepath:
        new_content = new_content.replace(
            '<h1 className="text-white text-xl font-black tracking-tight">AUTO<span className="text-brand-blue">SPARE</span></h1>',
            '<h1 className="text-xl font-black tracking-tight"><span className="text-brand-navy">AUTO</span><span className="text-brand-blue">SPARE</span></h1>'
        )
        # Fix nav active state highlight
        new_content = new_content.replace(
            "text-slate-400 hover:bg-slate-800 hover:text-white'",
            "text-slate-400 hover:bg-slate-800 hover:text-white transition-shadow'"
        )

    if content != new_content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        print(f"Updated {filepath}")
