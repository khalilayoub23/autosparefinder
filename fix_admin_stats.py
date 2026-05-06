import os

filepath = '/opt/autosparefinder/frontend/src/pages/Admin.jsx'
with open(filepath, 'r') as f:
    content = f.read()

import re
old_func_pattern = re.compile(r"function StatCard\(\{ label, value, sub, icon: Icon, color = 'brand' \}\) \{.*?\n\}", re.DOTALL)
new_func = """function StatCard({ label, value, sub, icon: Icon, color = 'brand' }) {
  return (
    <div className="bg-white p-5 rounded-brand border border-brand-border shadow-sm transition-shadow hover:shadow-electric flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 bg-brand-surface text-brand-blue`}>
        <Icon className="w-6 h-6" />
      </div>
      <div>
        <p className="text-sm text-gray-500">{label}</p>
        <p className="text-2xl font-bold text-brand-navy">{value ?? <span className="skeleton w-16 h-6 inline-block" />}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}"""

content = old_func_pattern.sub(new_func, content)

with open(filepath, 'w') as f:
    f.write(content)

print("Updated Admin.jsx")
