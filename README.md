# mote

`mote` is a minimal Decky Loader plugin that sends HDMI-CEC volume commands through the SteamOS `cecd` D-Bus service. It provides three controller-friendly actions in a Decky panel:

- `Volume Up`
- `Volume Down`
- `Mute`

The plugin does not talk to the CEC adapter directly. It uses `cecd` as the sole controller and sends only the validated high-level D-Bus methods exposed by `com.steampowered.CecDaemon1.CecDevice1`.
