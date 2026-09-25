# Skill: svg-generator

## What it does
Generates SVG assets from text prompts or simple descriptions. Uses DeepSVG or similar transformer-based models to create clean, scalable vector graphics suitable for UI icons, illustrations, and game assets.

## Repos included
1. **SVG-ORA-Studio** (seeb4coding/SVG-ORA-Studio) - Web-based SVG editor with generation capabilities
2. **OmniSVG** (OmniSVG/OmniSVG) - Transformer-based SVG generation from text

## Install steps
### SVG-ORA-Studio (web-based)
```bash
cd .opencode/skills/svg-generator/repo
npm install
npm run dev  # starts dev server at localhost:5173
```

### OmniSVG (Python)
```bash
cd .opencode/skills/svg-generator-2/repo
pip install -r requirements.txt
# Download model weights from: https://huggingface.co/OmniSVG/OmniSVG
```

## How to invoke
### SVG-ORA-Studio
- Open http://localhost:5173 in browser
- Use text-to-SVG panel
- Export as SVG file

### OmniSVG (CLI)
```bash
python inference.py --prompt "simple flat icon of a banana, yellow, minimal style" --output banana.svg
```

## Model weights
- **OmniSVG**: ~2GB model weights from Hugging Face (OmniSVG/OmniSVG)
- **SVG-ORA-Studio**: No local weights (web-based or uses browser APIs)

## Hardware needs
- **SVG-ORA-Studio**: CPU only, runs in browser
- **OmniSVG**: GPU recommended (CUDA, 8GB+ VRAM), CPU fallback available

## Example prompts
```
"flat style icon of a banana, bright yellow, clean lines, 64x64 viewBox"
"simple card back design, geometric pattern, gold on dark navy, 100x140 viewBox"
"poker chip, blue with gold rim, denomination 100, top-down view, 64x64"
"cartoon monkey character, idle pose, jungle green/brown palette, 100x100"
"royal crown icon, gold with gem accents, 64x64"
```

## Fallback if model unavailable
- Use SVG-ORA-Studio web editor (no local install needed)
- Hand-code simple SVGs using basic shapes (rect, circle, path)
- Use reference assets from Uradhura pack as templates

## License
- SVG-ORA-Studio: MIT
- OmniSVG: Apache-2.0