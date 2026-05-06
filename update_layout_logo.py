import re

with open('/opt/autosparefinder/frontend/src/components/Layout.jsx', 'r') as f:
    content = f.read()

# Replace the specific logo container block
target = """          {/* Logo */}
          <Link to="/" className="flex items-center gap-2">
            <div className="w-9 h-9 bg-brand-600 rounded-xl flex items-center justify-center">
              <Wrench className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-gray-900">Auto <span className="text-brand-600">Spare</span></span>
          </Link>"""

replacement = """          {/* Logo - Root fix 1:1 image implementation with breakpoint support */}
          <Link to="/" className="flex items-center">
            <img 
              src="/logo.png" 
              alt="AutoSpare logo" 
              className="h-10 sm:h-12 w-auto max-w-[160px] sm:max-w-[200px] object-contain object-right"
              onError={(e) => {
                // Fallback rendering
                e.target.style.display='none';
                e.target.nextSibling.style.display='flex';
              }}
            />
            <div className="hidden items-center gap-2">
               <div className="w-9 h-9 bg-brand-600 rounded-xl flex items-center justify-center">
                 <Wrench className="w-5 h-5 text-white" />
               </div>
               <span className="text-xl font-bold text-gray-900">Auto <span className="text-brand-600">Spare</span></span>
            </div>
          </Link>"""

if target in content:
    new_content = content.replace(target, replacement)
    with open('/opt/autosparefinder/frontend/src/components/Layout.jsx', 'w') as f:
        f.write(new_content)
    print("Dashboard Layout Logo correctly updated!")
else:
    print("Could not find the target string in Layout.jsx. Please review.")

