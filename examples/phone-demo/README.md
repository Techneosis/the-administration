# phone-demo

The demo's Notes app, tested on an Android phone through the house screen
(`server = "screen"`, docs/equipment.md). The Administration ships no
emulator: bring your own Android SDK (the emulator and platform-tools) and
an AVD with Wi-Fi, then:

```sh
export ANDROID_HOME=~/Android/Sdk ADMINISTRATION_AVD=<your AVD>
# and ANDROID_AVD_HOME if your AVDs aren't in ~/.android/avd
git init -q && git add . && git commit -qm demo
administration place "Sign up and save a first note on a phone" --cast phone-first-timer
```

Make an AVD for rooms alone (not the one you develop on), so nobody, and
no agent, mistakes a room's phone for theirs. Each room claims an emulator
console port in the rooms' range (5620-5680, `ADMINISTRATION_DEVICE_PORTS`),
passing over any port another emulator holds; keep your own emulators below
it. Each room boots its own read-only emulator, opens the app in the phone's
browser at `http://10.0.2.2:<port>/`, and hands the professional the
screen: pictures, taps, swipes, typing and the phone's keys. `--show-browsers`
shows the emulator's window.
