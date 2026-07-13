# Building ArkAP.dll

The VS project references the vendored ArkApi SDK at
`H:\Ark archipelago\vendor\Framework-ArkServerApi` (headers + `out_lib\ArkApi.lib`).
Build on THIS PC (where the clone lives), then transfer the resulting `.dll`.

## 1. Get a C++ compiler
The installed VS 2022 BuildTools is missing the compiler. In **Visual Studio Installer**:
- Modify the Build Tools (or install Visual Studio Community 2022)
- Check **"Desktop development with C++"** → Install.

(If you only have VS **2019**, open `ArkAP.vcxproj` and change both `PlatformToolset`
from `v143` to `v142`.)

## 2. Build
- Open `plugin\ArkAP\ArkAP.sln`.
- Set configuration to **Release | x64**.
- Build → Build Solution (Ctrl+Shift+B).
- Output: `plugin\ArkAP\x64\Release\ArkAP.dll`.

If it's the first real compile, expect a few errors at the `// VERIFY` spots in
`PluginMain.cpp` (SDK accessor names). Paste any errors back and they get fixed fast.

## 3. Deploy to the Server PC
Copy to `...\ARK\Server\ShooterGame\Binaries\Win64\ArkApi\Plugins\ArkAP\`:
```
ArkAP.dll
ArkAP.config.json
engrams.json        (from repo data\)
locations.json      (from repo data\)
```
The plugin creates `ipc\` + `state.json` next to itself on first run.

## 4. First-run data harvest
1. Start the server. Watch `ArkApi\Logs\` for ArkAP load + no crash.
2. In the server console run: `ArkAP.DumpEngrams`
3. It writes `ArkAP_engrams_dump.json` in the plugin folder — send that back.
   We regenerate `engrams.json` with the real engram item-classes, redeploy, and
   engram gating goes live.

## 5. Connector
Run `connector\ark_ap_connector.py` on the Server PC (needs Python), pointed at the
same `ipc\` dir, to bridge to the Archipelago server.
