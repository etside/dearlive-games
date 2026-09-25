# Skill: lottie-generator

## What it does
Generates and edits Lottie (Bodymovin) JSON animations from text prompts, keyframes, or After Effects compositions. Creates complex animations for UI, characters, and effects.

## Repos included
1. **Lottie (diffusionstudio/lottie)** - Lottie player and editor framework
2. **Kin3o (affromero/kin3o)** - Animation generation and editing tool

## Install steps
### Lottie (diffusionstudio)
```bash
cd .opencode/skills/lottie-generator/repo
npm install
npm run dev  # starts dev server
```

### Kin3o
```bash
cd .opencode/skills/lottie-generator-2/repo
npm install
npm run dev
```

## How to invoke
### Lottie Editor
- Open http://localhost:3000 (or configured port)
- Import SVGs or create shapes
- Animate with keyframes
- Export as .json (Lottie) and .gif fallback

### Kin3o
- Open http://localhost:5173 (or configured port)
- Create animations from prompts or keyframes
- Export as .json + .gif

## Model weights
- No external model weights required (procedural animation)
- Uses Bodymovin/Lottie spec natively

## Hardware needs
- CPU only, runs in browser/Node.js
- No GPU required

## Example prompts
```
"character idle animation: subtle breathing, 2s loop, 60fps"
"wheel spin: 2s rotation with ease-out, lands on winner, highlight pulse"
"card deal: 3 cards fan out from deck to positions, stagger 100ms"
"chip bet: arc from seat to pot, scale pop on land, 500ms"
"win celebration: fireworks burst, 2s, gold particles"
"timer pulse: scale + color shift, speeds up at 5s, 1s loop"
"coin fly: arc from pot to wallet counter, 800ms, trail effect"
"character win: jump + cheer, 1.5s"
"character lose: slump + sigh, 1.5s"
```

## Fallback if model unavailable
- Use LottieFiles.com online editor (free)
- Hand-code simple Lottie JSON using keyframes
- Use GIF fallbacks from master pack

## License
- diffusionstudio/lottie: MIT
- affromero/kin3o: MIT