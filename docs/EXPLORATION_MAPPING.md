# Exploration checks - position mapping guide

Worksheet for capturing the real world-coordinates of every Island region, so the exploration
checks fire in the right place. We do NOT guess these from the wiki: the lat/lon to world-coordinate
formula is inconsistently documented, and a check in the wrong place is worse than no check at all.

## How to map

In game chat, stand somewhere inside the region and run:

```
/dumppos <key>
```

Use the short **key** from the table (no spaces). Run it **as many times as you like per region** -
every call appends one sample. Walk or fly the region's edges and take samples around the perimeter;
the more you take, the tighter the area fits. Roughly 6 is fine, 20 is better.

Each call appends a line to `ArkAP_positions.jsonl` next to `ArkAP.dll` and replies in chat with the
sample count so far, so you can see it registered.

Tips:
- Sample the **outer edges**, not just the middle. The area is built from the extremes.
- For a long or bent region (Redwoods, the coasts), sample it in **clusters** - two or three tight
  groups. Those become separate boxes that are OR'd together, instead of one huge loose box.
- Altitude is ignored, so it does not matter whether you are flying or on foot.
- Wrong sample? Delete the line from the jsonl, or just re-run the region and tell me to drop it.

When done, send me `ArkAP_positions.jsonl` and I will build the areas from it.

## Regions to map

Gate = the survival gear the check will require, so it lands at a sensible point in the run.

### Mountains
| Key | Region | Gate |
|-----|--------|------|
| `farspeak` | Far's Peak | - |
| `frozentooth` | The Frozen Tooth | Fur |
| `grandhills` | The Grand Hills | - |
| `redpeak` | The Red Peak | - |
| `volcano` | Volcano | - |
| `weathertop` | Weathertop | - |
| `whitesky` | Whitesky Peak | Fur |
| `wintersmouth` | Winter's Mouth | Fur |

### Islands
| Key | Region | Gate |
|-----|--------|------|
| `craggs` | Cragg's Island | - |
| `southernislets` | Southern Islets | - |
| `southhaven` | South Haven (Herbivore Island) | - |
| `deadisland` | The Dead Island (Carno Island) | - |
| `footpaw` | The Footpaw | - |

### Plains
| Key | Region | Gate |
|-----|--------|------|
| `easternplains` | The Eastern Plains | - |
| `frigidplains` | The Frigid Plains | Fur |
| `westernplains` | The Western Plains | - |

### Forests
| Key | Region | Gate |
|-----|--------|------|
| `southernjungle` | Southern Jungle | - |
| `easternforest` | The Eastern Forest | - |
| `redwoods` | The Redwood Forests | - |

### Shores
| Key | Region | Gate |
|-----|--------|------|
| `northshores` | Northern Shores | - |
| `neshores` | Northeast Shores | - |
| `nwshores` | Northwest Shores | - |
| `seshores` | Southeast Shores | - |
| `westernapproach` | The Western Approach | - |
| `westerncoast` | The Western Coast | - |

### Other
| Key | Region | Gate |
|-----|--------|------|
| `deepocean` | Deep Ocean | Scuba |
| `drayoscove` | Drayo's Cove | - |
| `swamps` | The Writhing Swamps | - |
| `smugglerspass` | Smuggler's Pass | - |
| `volcanicmaw` | Volcanic Maw | - |

### Caves

Sampled exactly like a surface region - circle the cave's footprint. A cave polygon always sits
inside the surface region above it; that is expected, and the smallest-polygon-wins rule means the
cave is what gets credited when you are in one.

| Key | Cave | Gate |
|-----|------|------|
| `centralcave` | Central Cave | - |
| `icecave` | Ice Cave | Fur |
| `lavacave` | Lava Cave | - |
| `lowersouthcave` | Lower South Cave | - |
| `northeastcave` | North East Cave | - |
| `northwestcave` | North West Cave | - |
| `swampcave` | Swamp Cave | - |
| `uppersouthcave` | Upper South Cave | - |
| `necave_underwater` | North East Underwater Cave | Scuba |
| `nwcave_underwater` | North West Underwater Cave | Scuba |
| `secave_underwater` | South East Underwater Cave | Scuba |
| `swcave_underwater` | South West Underwater Cave | Scuba |

I have deliberately NOT tied these keys to the artifacts they contain - tell me which artifact each
one holds when you map it and I will wire the cave requirement to the matching `cave_reqs` rule
we already have.

**30 surface regions + 12 caves.** Region names are the official in-game ones (the map labels them
as you fly over), with the community nickname in brackets where it differs.

## How the area is built (updated)

Your samples are the VERTICES OF A POLYGON, taken in flight order - circle the area and the whole
interior counts. Two consequences:

- **Order matters.** Do not sort or reshuffle the file. Interleaving different regions is fine;
  only the order *within* one key matters.
- **Re-flying a region means deleting its old lines first.** Appending a second lap onto a key
  weaves both loops into one garbage shape.

Where polygons nest - a cave under a biome, Volcanic Maw inside the Volcano, Red Peak inside the
Redwoods - the SMALLEST containing polygon is the one credited, so the specific place wins over
the broad one. Altitude is recorded but not used: you fly through the volcano to reach its maw, so
a height band would only cause false negatives.

## What happens with the data

1. Samples are grouped per key and turned into one or more padded bounding boxes.
2. Those become `Explore: <Region>` locations in `locations.json`.
3. The plugin checks each connected player's position against the boxes on its normal tick and
   reports the check, exactly like the existing level and inventory checks. Per player, so in
   multiplayer everyone visits for their own slot.
4. Gated regions get their gear requirement in the access logic, reusing the same macros the caves
   use, so a snow region genuinely needs Fur before the fill will place progression there.
