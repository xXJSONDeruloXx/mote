# cec-mote

<img width="548" height="443" alt="image" src="https://github.com/user-attachments/assets/ffae8a0b-2c81-4ffa-b153-d4028192968b" />

`cec-mote` is a Decky Loader plugin for SteamOS that now does two related jobs:

- sends HDMI-CEC volume commands through the built-in `cecd` D-Bus service
- assists with installing, repairing, verifying, and uninstalling the `steamos-cec-bt-wake` sleep/wake setup from inside Decky

## Features

### CEC remote

The original CEC remote panel remains available with three controller-friendly actions:

- `Volume Up`
- `Volume Down`
- `Mute`

The plugin does not talk to the CEC adapter directly. It uses `cecd` as the sole controller and sends only the validated high-level D-Bus methods exposed by `com.steampowered.CecDaemon1.CecDevice1`.

### Sleep / wake setup assistant

The Decky UI also includes a setup section that can:

- verify the current `steamos-cec-bt-wake` install state
- report whether the system is configured, needs setup, or needs repair
- show key details like the CEC device, physical address, Bluetooth wake target, and persistent layout
- run install/repair with the bundled `steamos-cec-bt-wake.sh`
- uninstall the setup when requested

The UI follows standard Decky panel components and avoids custom inline layout styling.

## Build requirements

- Node.js compatible with the current Decky template
- `pnpm`
- Python 3 for backend tests

## Local build

```bash
pnpm install
pnpm run build
python -m unittest tests/test_main.py
```

## Notes

- The setup assistant bundles a copy of `steamos-cec-bt-wake.sh` under `assets/`.
- The plugin uses Decky's standard `root` plugin flag so the backend can run install/verify/uninstall operations that require root.
- `cec-mote` still depends on SteamOS `cecd` for CEC control.
- The setup assistant is intended for SteamOS systems running Decky Loader.
