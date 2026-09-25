# Skill: sprite-sheet

## What it does
Generates and processes sprite sheets from individual frames or animations. Creates optimized sprite atlases for 2D games with proper packing, metadata, and format conversion.

## Repos included
1. **SpriteForge (francesco-sodano/spriteforge)** - Sprite sheet generator and editor
2. **Sprite Sheet Generator (NO6KIKO/gorest-2d-animation-spritesheet-generator)** - CLI tool for sprite sheet creation

## Install steps
### SpriteForge
```bash
cd .opencode/skills/sprite-sheet/repo
pip install -e .
# or: pip install -r requirements.txt
```

### Sprite Sheet Generator
```bash
cd .opencode/skills/sprite-sheet-2/repo
npm install
# or use CLI directly
```

## How to invoke
### SpriteForge (CLI)
```bash
spriteforge pack --input frames/ --output spritesheet.png --meta spritesheet.json --padding 2
```

### Sprite Sheet Generator
```bash
npx sprite-sheet-gen --input frames/ --output assets/spritesheet.png --format json
```

## Model weights
- No ML model weights required (algorithmic packing)

## Hardware needs
- CPU only
- No GPU required

## Example prompts / use cases
```
"pack 8 frames of monkey idle animation into horizontal strip"
"create sprite atlas for 16 option icons (8 monkey + 8 baby king)"
"generate Lottie-compatible sprite sheet for card deal animation"
"pack 6 character pose frames (idle, win, lose, spin, deal, flip)"
"create chip animation sprite sheet (bet, stack, collect, fly)"
```

## Fallback if model unavailable
- Use TexturePacker (free version) or online sprite sheet packers
- Manual packing with ImageMagick: `montage frame*.png -tile 4x2 -geometry +2+2 spritesheet.png`
- Manual JSON metadata creation

## License
- SpriteForge: MIT
- Sprite Sheet Generator: MIT