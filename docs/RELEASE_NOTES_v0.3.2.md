Release Notes:

1. Changing your room's port no longer re-delivers everything. When an archipelago.gg room went to sleep and woke on a new port, the server could dial the old port, land in someone else's room, and treat your real room as a brand new seed the next time you connected. Every resource pack and boon fired again. The plugin now only resets for a new seed after the server actually accepts your slot.
2. Exploration regions on The Island trigger at any altitude again. Since v179 an Island region only counted inside a thin height band (2 m for the obelisks, about 27 m for the Eastern Swamp), so flying over one did nothing. Fjordur's realm bands are unchanged.
3. Trap dinos scale with your server's max wild level instead of assuming 150. A Hungry Rex is 80% of your max, so 120 on a 150 server, 96 on a 120 server, 144 on a 180 server.
4. `engrams_per_item` and `tames_per_item` work on every map. On Scorched, Valguero, Fjordur, Ragnarok and The Center, grouping could fold real items under an item from another map, so they quietly never existed in the seed (on Scorched, the Rex, Spino and Therizino tames). Fur gear grouping could also make the snow caves, and the Dragon behind them, impossible. Both fixed.
5. Big logic pass against Lurch's newest sheet. Around 60 checks and tames that required nothing now have their real requirement, including the Bronto, Mosasaur, Dunkle, Therizino and Tuso tames and kills, the Megalania, Megalosaurus and Pulmonoscorpius tames, the "Tame N Species" milestones, Collect Element, and the higher tiers of the Collect checks (Cooked Meat x50 and up now need a Campfire).
6. Foundations are required. The Forge and Mortar & Pestle need a Thatch, Wood or Stone foundation to build, so opening Tier 1 now needs one too. Foundations land early instead of spheres after the stations.
7. Island bosses (Broodmother, Megapithecus, Dragon, Overseer) need a strong fight tame, same as Ragnarok: Rex, Therizino, Spino, Yutyrannus, Thylacoleo or Rhyniognatha, or a Giga or Carcha.
8. Explorer notes follow Lurch's sheet. Snow notes need the right amount of fur, Dead Island and South Haven notes need a boat, flyer, water tame or flippers, Ocean Cave notes need a water tame or the full Scuba Set, and the Swamp Cave dossiers need a Gas Mask or the full Scuba Set. Hologram: Overseer had no rule at all and was always in logic. Fixed.
9. Gear sets mean the whole set. DeepDive is now Scuba Tank + Mask + Flippers, and the Scuba Set and Ghillie Set need every piece. Bows and crossbows need arrows.
10. Kill and tame fixes: the Unicorn has a kill rule, the Island Yeti also needs the Snow Cave, the Giant Queen Bee needs a Dire Bear (Bug Repellent on Scorched, which has no Dire Bears), and the Manta tame only needs shallow water access.
11. Crafting fixes from the sheet: Grenade, Simple Shotgun, metal tools, Chainsaw, Mining Drill and Gas Mask have their real stations, Gasoline needs a Metal Pick, Sap checks need the Tree Sap Tap, and Carno Cave notes need a way onto the Dead Island.
12. `Collect 30 Sap` and `Cooked Meat Jerky x10` had no rule, so early items could land on them. Fixed.
13. `Killed: Kairuku` needs the snow (3 fur pieces or an Otter) and a ranged weapon, and the `Collect N Explorer Notes` checks only come into logic once that many notes are reachable. The Campfire is no longer locked onto an early dino kill; it is a normal item, so the cooked meat checks wait until you actually have it.
14. Hint by in-game name, in game (`/hint Smithy`) and in the Archipelago client (`!hint Smithy`): Refining Forge, Carbonemys Saddle, Stone Foundation and about 200 more. Every saddle answers to "<Creature> Saddle". In-game `/hint` also prefers vanilla items over mod items now, so `/hint Engram: Smithy` finds the vanilla Smithy instead of Structures Plus's "Smithy Plus", and a vague search lists what else it matched. Item names and ids are unchanged.
15. Plugin v193 to v201. Generation sweep is 52/52, now including grouped and bundled seeds on every map.

Update `ark_ase.apworld` AND the server plugin. Logic changes only apply to newly generated seeds; the port, altitude and trap fixes live in the plugin.

ATTACHED IS AVOCADO's LAUNCHER and LURCH's POPTRACKER.

Lurch's GitHub: https://github.com/lurch9229/Arkipelago-Poptracker/releases

Avocado's Launcher: https://github.com/aSoberAvocado/ARK-Ipelago-Evolved-Launcher/releases
