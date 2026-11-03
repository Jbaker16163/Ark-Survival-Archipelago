# PopTracker art

PNG files placed here are copied into the pack's `images/` folder (keeping the same subfolder)
before any map is added, by `tools/poptracker_add_map.py`. They only fill gaps and never replace
an image Lurch's pack ships. Square, transparent background, 64x64 or larger. They're scaled down
to fit. `_source/` keeps the originals as supplied and is ignored by the build.

- `dinos/` - creatures (grid icon + map sidebar)
- `engrams/` - items
- `maps/` - real cave maps. Name one after its cave tab's map, e.g. `Valguero_The_Lair.png`,
  `Ragnarok_Life_s_Labyrinth.png`, and it replaces that tab's "Coming Soon" card.

Every run ends with an "art still missing" list. As of 2026-10-29 the only gap is
`engrams/parachute.png` (Lurch's 0.0.9 note rules need a Parachute item he never defined).
