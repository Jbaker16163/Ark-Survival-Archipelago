# CustomGameConfigs / ArkAscended

UE4SS custom game config for ARK: Survival Ascended. Installed by
`tools/update_ue4ss_asa.ps1` to `...\Binaries\Win64\CustomGameConfigs\ArkAscended\`.

Holds per-build overrides UE4SS needs when ASA's engine differs from UE4SS defaults:

```
ArkAscended/
  UE4SS-settings.ini        # game-specific settings (engine version override, etc.)
  MemberVariableLayout.ini  # UObject/FName member offsets for this engine build (TODO)
  VTableLayout.ini          # vtable indices for this engine build (TODO)
  UE4SS_Signatures/         # custom AOB .lua files when a default scan fails (TODO)
```

## Current blocker
UE4SS (current experimental nightly, SHA 2352d15b) hangs at
`Locating KismetSystemLibrary CDO` on the current ASA build. FName/class
resolution works (it finds KismetSystemLibrary + Conv_NameToString by name), so
this is NOT a missing-AOB problem — it's a deeper UE5.6 init issue with no
published fix yet. Layout/signature files here will only help once we can derive
correct values (needs binary RE) OR upstream UE4SS ships ASA support.

The signature .lua format (for when a scan genuinely fails):

```lua
function Register()
    return "48 8B C4 57 48 83 EC 70 80 3D ?? ?? ?? ?? ?? 48 89"
end
function OnMatchFound(MatchAddress)
    return MatchAddress
end
```
