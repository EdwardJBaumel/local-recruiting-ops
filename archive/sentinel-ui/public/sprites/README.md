# Helper sprites

Animated pixel-art GIFs for the dashboard companion. **Generated
procedurally** by `scripts/build_sprites.py` — do not hand-edit the
GIFs in this directory, edit the generator and re-run.

## Regenerating

```bash
python scripts/build_sprites.py
```

Writes 18 files: six sprites × three animation states.

## Sprites + states

Each sprite has three animation states. Filename convention is
`{key}_{state}.gif`.

| Sprite  | Key     | Vibe                              |
|---------|---------|-----------------------------------|
| Joby    | joby    | round peach blob, default         |
| Rollo   | rollo   | scruffy pixel dog                 |
| Momo    | momo    | tabby pixel cat                   |
| Hoot    | hoot    | pixel owl, watchful               |
| Slim    | slim    | bouncy green slime                |
| Pixel   | pixel   | blocky retro robot                |

| State      | When shown                        | Feel                |
|------------|-----------------------------------|---------------------|
| idle       | default loop                      | gentle bob          |
| celebrate  | milestone fires (e.g. 10 saves)   | jump + sparkles     |
| sleep      | user hasn't interacted in a while | flattened + Z's     |

## Format notes

- **Animated GIF** with transparent background, looped, 32×32 source.
- Dashboard renders at 96×96 with `image-rendering: pixelated` so the
  3× upscale stays crisp.
- Joby is 2-frame idle at 220ms/frame; celebrate is 4-frame at
  120ms/frame; sleep is 3-frame at 500ms/frame. Same cadence for all
  sprites, tuned in `scripts/build_sprites.py`.

## Adding a new sprite

1. Add a palette + draw functions to `scripts/build_sprites.py`
   (follow the pattern of any existing sprite).
2. Register it in the `SPRITES` dispatch at the bottom of that file.
3. Add an entry in `sentinel/core/helper.py` under `SPRITES` (the
   `_assets_for(key)` helper builds the state→URL map automatically).
4. Run `python scripts/build_sprites.py`.
5. Frontend picks it up on next dashboard refresh.

If a GIF file is missing the dashboard falls back to a coloured
placeholder tile with the sprite's label, so a half-shipped sprite
doesn't break the UI.
