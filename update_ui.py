import os
import glob

def fix_file(filepath):
    try:
        with open(filepath, 'r') as f:
            content = f.read()

        new_content = content
        
        if 'Login.jsx' in filepath or 'Register.jsx' in filepath:
            new_content = new_content.replace(
                '<h1 className="text-3xl font-bold text-gray-900">Auto <span className="text-brand-600">Spare</span> Finder</h1>',
                '<h1 className="text-3xl font-bold"><span className="text-brand-navy">Auto</span> <span className="text-brand-blue">Spare</span> Finder</h1>'
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
                '<h1 className="text-xl font-black tracking-tight"><span className="text-brand-navy">AUTO</span><span className="text-brand-blue">SPARE</span></h1>',
                '<h1 className="text-xl font-black tracking-tight"><span className="text-white">AUTO</span><span className="text-brand-blue">SPARE</span></h1>'
            )

        if content != new_content:
            with open(filepath, 'w') as f:
                f.write(new_content)
            print(f"Updated {filepath}")
    except Exception as e:
        pass

src_dir = '/opt/autosparefinder/frontend/src'
for filepath in glob.glob(os.path.join(src_dir, '**', '*.jsx'), recursive=True):
    fix_file(filepath)
