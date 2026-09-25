# Skill: sound-generator

## What it does
Generates sound effects and short audio clips from text prompts or parameters. Creates game-ready SFX (WAV/OGG) for UI interactions, game events, and ambient sounds.

## Repos included
1. **MMAudio (hkchengrex/MMAudio)** - Text-to-audio generation using diffusion models
2. **ai-sfx (siliconjungle/ai-sfx)** - Web-based SFX generator using jsfxr/lib

## Install steps
### MMAudio
```bash
cd .opencode/skills/sound-generator/repo
pip install -r pyproject.toml
# Download model weights from Hugging Face
```

### ai-sfx
```bash
cd .opencode/skills/sound-generator-2/repo
npm install
npm run dev
```

## How to invoke
### MMAudio (CLI)
```bash
python demo.py --prompt "casino chip stacking sound, crisp, short" --duration 1.0 --output chip_stack.wav
```

### ai-sfx (Web)
- Open http://localhost:3000
- Use parameters or text prompt
- Generate and download WAV/OGG

## Model weights
### MMAudio
- Model weights from Hugging Face (hkchengrex/MMAudio)
- ~1-2GB for base model
- `huggingface-cli download hkchengrex/MMAudio --local-dir ./weights`

### ai-sfx
- No model weights (procedural synthesis using jsfxr)

## Hardware needs
- **MMAudio**: GPU recommended (8GB+ VRAM), CPU fallback slow
- **ai-sfx**: CPU only, runs in browser

## Example prompts
```
"casino chip placing on felt, soft thud, 0.3s"
"card flip, paper snap, crisp, 0.2s"
"button click, subtle UI click, 0.1s"
"coin win, multiple coins dropping, 0.8s"
"round lose, descending tone, sad, 1s"
"round win, ascending chime, celebratory, 1.5s"
"roulette ball spinning, decelerating, 2s"
"timer tick, subtle electronic pulse, 0.1s loop"
```

## Fallback if model unavailable
- Use ai-sfx web editor (no install)
- Use jsfxr.js directly in code (procedural)
- Use existing WAV files from master pack
- Record simple sounds manually

## License
- MMAudio: Apache-2.0
- ai-sfx: MIT