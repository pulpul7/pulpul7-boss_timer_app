# Edge TTS optional module release

The BossTimer main EXE does not contain the online Edge TTS engine. Build the
module on the same Python major/minor version as the EXE, then publish the ZIP
as a GitHub Release asset.

The main GUI build requires `python -m pip install -r requirements-gui.txt`.
It bundles truststore for the installer's Windows-native HTTPS verification;
shipping truststore only inside the downloadable ZIP would be too late.
This installer-only fix requires a new main EXE, not a new TTS module ZIP.

```powershell
python build_edge_tts_module.py
```

Upload `dist/tts_module/boss_timer_edge_tts_module-v1.0.0.zip` to this tag:

```text
tts-module-v1.0.0
```

The program installs the asset from that tag into
`%APPDATA%\BossTimer\tts_module`. To use a private test asset before release,
set `BOSS_TIMER_EDGE_TTS_MODULE_URL` to the direct ZIP URL before launching the
program.

When changing the module, increase `EDGE_TTS_MODULE_VERSION` in
`edge_tts_module.py`, rebuild the ZIP, and publish the matching tag and asset.
