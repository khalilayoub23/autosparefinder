Part-family assets

This folder is intentionally kept to verified local image files only.

On 2026-04-05 the asset set was audited and most attempted Wikimedia downloads were removed because they were not images at all; they were saved HTML 429 error pages. The frontend currently uses generated SVG thumbnails from `frontend/src/components/partFamilyVisuals.js`, so these local files are optional reference assets rather than required runtime dependencies.

Currently verified files:

- filters: Bosch Oil Filter.JPG - https://commons.wikimedia.org/wiki/File:Bosch_Oil_Filter.JPG
- fluids: Old Engine Oil 001.jpg - https://commons.wikimedia.org/wiki/File:Old_Engine_Oil_001.jpg

Before adding more files to this directory, run `npm run validate:part-family-assets` from `frontend/`.