# Garmin epix Pro (Gen 2) — OS, Custom Firmware Feasibility, and Local App Development

> Research notes compiled 2026-08-10. Every claim below is backed by a reference link.
> Where something is **not** publicly documented, that is stated explicitly rather than guessed.

---

## 0. TL;DR — direct answers

| Question | Short answer |
|---|---|
| What OS does the watch run? | **GarminOS** — a fully custom, closed-source, in-house OS written mostly in C (UI layer moving to C++), running on an ARM Cortex-M class microcontroller. Third-party apps run inside Garmin's own bytecode VM, "TVM". |
| Can I replace it with Linux? | **No.** Not merely hard — effectively impossible, and nobody has done it. Cortex-M (no MMU), MCU-scale RAM, signed + undocumented firmware, no exposed bootloader or debug port, zero drivers. |
| Is there a known reset-and-flash-Linux pattern? | **No such pattern exists.** What does exist: supported factory reset, supported firmware (re)flash via signed `.GCD`, and security-research-grade firmware *extraction and analysis* — not replacement. |
| Can I install my own apps locally, without the store? | **Yes — USB sideloading.** Build a `.PRG`, sign with your own developer key, copy into `GARMIN/APPS/`. No Garmin review, no store publication. |
| Over Bluetooth? | **No.** BLE install is reserved for the Connect IQ Store flow through the Garmin Connect phone app. Sideloading is USB-only. |
| What language do apps use? | **Monkey C** — Garmin's own language, compiled to TVM bytecode. It is the only on-watch language. Companion phone apps can be Kotlin/Java or Swift; backends can be anything. |
| Then what OS *can* I run on this MCU? | **None — you cannot flash anything onto it**, RTOS included. Zephyr/FreeRTOS/NuttX are blocked by the same signed-firmware + no-bootloader + no-SWD wall as Linux. See **§8** for what to do instead: Connect IQ on the Garmin, or a flashable board (PineTime / nRF52840-DK / ESP32-S3) running Zephyr. |

---

## 1. What operating system does the epix Pro (Gen 2) run?

### 1.1 The OS itself

The watch runs **GarminOS**, Garmin's own operating system:

- **Fully custom, developed in-house by Garmin.** Not Linux, not Android / Wear OS, not FreeRTOS,
  not Zephyr, not a licensed commercial RTOS.
- **Written mostly in C**, with the UI framework progressively moving to C++.
- It implements threading and memory management, but has **no user-mode vs. kernel-mode separation**
  and **no concept of multiple processes**. It is closer to a rich RTOS than to a general-purpose OS.
- It provides classic RTOS primitives — semaphores, tasks, events, queues — plus a filesystem
  abstraction and memory allocation on top.
- Third-party apps do **not** run natively. They run inside **TVM** ("The Virtual Machine"), Garmin's
  stack-based bytecode interpreter, which enforces:
  - execution-time limits (a runaway app is aborted),
  - per-app memory quotas with reference counting / deterministic collection,
  - a permission model gating privileged modules (`Toybox.Positioning`, `Toybox.Ant`,
    `Toybox.UserProfile`, `Toybox.Communications`, …).

References:
[Anvil Secure — *Compromising Garmin's Sport Watches: A Deep Dive into GarminOS and its MonkeyC Virtual Machine*](https://www.anvilsecure.com/blog/compromising-garmins-sport-watches-a-deep-dive-into-garminos-and-its-monkeyc-virtual-machine.html) ·
[Atredis Partners — *A Watch, a Virtual Machine, and Broken Abstractions*](https://www.atredis.com/blog/2020/11/4/garmin-forerunner-235-dion-blazakis) ·
[Garmin forum: "Question regarding the operating system of Garmin's smartwatches"](https://forums.garmin.com/developer/connect-iq/f/discussion/260863/question-regarding-the-operating-system-of-the-garmins-smartwatches/1250915#1250915)

### 1.2 Hardware class

Garmin's watch line runs on **ARM Cortex-M series microcontrollers**. This is confirmed by published
reverse-engineering: firmware for the analysed devices loads as `ARM:LE:32:Cortex`, and a teardown of
the closely related Forerunner 735XT identified a Maxim MAX32630 (Cortex-M4) SoC.

⚠️ There is **no public teardown that definitively identifies the exact SoC in the epix Pro (Gen 2)**.
"Cortex-M class MCU" is the reliable statement; do not assume a specific part number.

The practical consequence is the point that matters: **this is a microcontroller, not an application
processor.** No MMU, no DDR, RAM in the hundreds-of-KB to low-MB range, storage in internal flash plus
an external flash part.

### 1.3 Firmware versioning and packaging

- The user-facing version is "System Software x.yz". The **epix (Gen 2), epix Pro (Gen 2), fēnix 7,
  fēnix 7 Pro, Enduro 2, quatix 7 and tactix 7** models all share the same firmware platform and
  release train.
- Firmware ships as **`.GCD`** container files — a TLV-structured Garmin format that is not officially
  documented but has been publicly reverse-engineered.
- Beta `.GCD` images contain a `FW_ALL_BIN` record with the full raw image — but **not the
  bootloader**, which lives below the main image (on the analysed device, below address `0x3000`).

References:
[Garmin — Update and learn more about the software on your epix Pro Gen 2](https://support.garmin.com/en-GB/?faq=rPMYb00EKm9pWrcxiL5Fz9) ·
[Herbert Oppmann — Garmin GCD firmware update file format (PDF)](https://www.memotech.franken.de/FileFormats/Garmin_GCD_Format.pdf) ·
[Herbert Oppmann — Garmin BIN firmware file format (PDF)](https://www.memotech.franken.de/FileFormats/Garmin_BIN_Format.pdf)

### 1.4 Connect IQ level for your exact watch

The epix Pro (Gen 2) ships in three sizes and **they are three separate build targets**:

| Model | SDK device ID | Screen | Display | CIQ API level |
|---|---|---|---|---|
| epix Pro (Gen 2) 42mm | `epix2pro42mm` | 390 × 390 | AMOLED, 65 536 colours, touch | 5.2 |
| epix Pro (Gen 2) 47mm / quatix 7 Pro | `epix2pro47mm` | 416 × 416 | AMOLED, 65 536 colours, touch | 5.2 |
| epix Pro (Gen 2) 51mm / D2 Mach 1 Pro / tactix 7 – AMOLED | `epix2pro51mm` | 454 × 454 | AMOLED, 65 536 colours, touch | 5.2 |

Buttons on all three: `enter, up, menu, down, esc`. Launcher icon 60 × 60.
Part number for the 47mm variant: `006-B4313-00`.

**Per-app memory ceilings** (47mm; the other sizes are the same family of limits):

| App type | Max memory |
|---|---|
| Watch App | 786 432 B (768 KB) |
| Widget | 786 432 B (768 KB) |
| Audio Content Provider | 524 288 B (512 KB) |
| Data Field | 262 144 B (256 KB) |
| Watch Face | 131 072 B (128 KB) |
| Background service | 65 536 B (64 KB) |
| Glance | 65 536 B (64 KB) |

References:
[Connect IQ compatible devices](https://developer.garmin.com/connect-iq/compatible-devices/) ·
[Device reference index](https://developer.garmin.com/connect-iq/device-reference/) ·
[epix Pro (Gen 2) 47mm device page](https://developer.garmin.com/connect-iq/device-reference/epix2pro47mm/)

---

## 2. Can I replace the OS and run a Linux distribution on the watch?

**No. This is not achievable, and nobody has done it.** This is not pessimism — it is a stack of
independent blockers, each of which alone is fatal.

### 2.1 The hardware cannot run Linux in any meaningful sense

- Mainline Linux requires an **MMU**. **Cortex-M has none.**
- `uClinux` / NOMMU Linux technically runs on some Cortex-M7 parts, but needs megabytes of RAM,
  external RAM support, and a maintained board port. Best case you would get a bare console with no
  display, no sensors and no radios — a brick with a kernel on it.
- The realistic ceiling for this hardware class is **Zephyr / NuttX / RT-Thread / bare metal**, not
  "a Linux distribution". And even that requires everything below to be solved first.

### 2.2 There is no way in

- **No exposed bootloader interface.** The bootloader is not present in downloadable firmware images —
  the public research explicitly notes it is missing from beta `.GCD` files.
- **No documented DFU/recovery path that accepts arbitrary images.** The update path consumes
  Garmin-signed `.GCD` files only.
- **No exposed JTAG/SWD.** Reaching debug pads means opening a sealed 10 ATM case (destroying the water
  seal and the warranty), and production MCUs almost always ship with readout protection enabled.
- Even Connect IQ `PRG` app files are **RSA/PKCS#1 v1.5 + SHA-1 signed**; firmware verification is at
  least as strict, and the signature-validation path in firmware has not been publicly broken.

### 2.3 There is no documentation and there are no drivers

Every peripheral would need a new driver written blind, with no datasheets: AMOLED panel + controller,
touch controller, the multi-LED optical HR sensor array, barometer, compass, accelerometer, multi-band
GNSS, Bluetooth/BLE, Wi-Fi, ANT+, PMIC/charging, haptics, flashlight LED driver, external flash — plus
the power management that produces multi-week battery life. That last item alone is the product.

### 2.4 Legal / contractual

- The **Connect IQ Developer Agreement** explicitly forbids modifying, creating derivative works of,
  decompiling, reverse engineering, disassembling, deriving source code from, or decrypting the
  Program Materials, and forbids activity that "interferes with, disrupts, damages, or accesses in an
  unauthorized manner any Garmin devices, platforms, or systems".
- Garmin's **firmware download EULA** similarly forbids reverse engineering, reducing the software to
  human-readable form, or creating derivative works.
- Coordinated security research on your own device is a different matter (Anvil and Atredis both did
  responsible disclosure with Garmin). But "replace the shipped firmware with my own build" sits
  outside what Garmin permits, voids the warranty, and voids the water-resistance rating.

Reference: [Connect IQ SDK page — full Developer Agreement text](https://developer.garmin.com/connect-iq/sdk/)

### 2.5 If the real goal is "run my own OS/code on a wrist device"

Buy hardware designed for it, and keep the Garmin for what it is genuinely excellent at:

| Platform | What you get |
|---|---|
| [Bangle.js 2](https://banglejs.com/) | Open smartwatch, JavaScript (Espruino), full firmware source, fully hackable |
| [Watchy](https://watchy.sqfmi.com/) | ESP32 + e-paper, open hardware, Arduino / ESP-IDF |
| [PineTime](https://pine64.org/devices/pinetime/) | nRF52832, open, runs InfiniTime (FreeRTOS) or Zephyr, ~US$27 |
| [PebbleOS (open-sourced by Google, 2025)](https://github.com/google/pebble) / [Rebble](https://rebble.io/) | Full original smartwatch OS source; new Pebble-compatible hardware exists |
| Any nRF52/nRF53 or ESP32-S3 dev board + display | Zephyr / NuttX / ESP-IDF, complete control |

---

## 3. "Step by step: reset the OS and replace it with my own Linux distro"

**There is no such procedure and no known pattern.** Below is everything that genuinely exists, in
three tiers from safe to research-only.

### Tier 1 — Supported: factory reset (this is the real "reset the OS")

Wipes user data/settings and restores the shipped software state. It does **not** change firmware
version.

1. On the watch, press and hold **MENU**.
2. Go to **System → Reset**.
3. Choose one:
   - **Reset Default Settings** — settings only, data preserved.
   - **Delete Data and Reset Settings** — full wipe: activity history, saved locations, downloaded
     music/maps, **and any sideloaded apps**.
4. Confirm. The watch reboots into first-time setup and must be re-paired with Garmin Connect.

Menu wording varies slightly across firmware versions — check the
[epix Pro (Gen 2) product support page](https://support.garmin.com/en-US/?faq=rPMYb00EKm9pWrcxiL5Fz9)
for your exact release.

### Tier 2 — Supported-ish: reinstall or roll back firmware with a `.GCD`

This is the only "flash the OS" operation Garmin's stack accepts, and it only accepts
**Garmin-signed** images.

1. Connect the watch by USB. On **Windows** it appears as an **MTP** device in Explorer. On **macOS**
   it cannot be browsed natively — see §4.3.
2. Obtain the official `.GCD` for the **epix Pro (Gen 2) platform** from Garmin (public software
   update page, or the Garmin beta programme). Never use a `.GCD` from another product family.
3. Copy the `.GCD` into the device's **`GARMIN/`** folder (some flows use `GARMIN/RemoteSW/`).
4. Safely eject and disconnect. The watch detects the file on boot and prompts to install.
5. Let it complete on a well-charged battery. Do not disconnect mid-flash.

**Risks:** a wrong file or an interrupted flash can brick the watch. Downgrades are not officially
supported and may be refused or may lose data. For normal updates, use Garmin Express or Garmin
Connect instead.

### Tier 3 — Research only: what the security community actually did

This is the *real* known pattern for working with GarminOS firmware. It is **analysis, not
replacement**, and it has only been demonstrated on older devices (Forerunner 235, Forerunner 245
Music) — never on an epix Pro:

1. Download an official **beta** `.GCD` from Garmin (beta images are full images; the incremental
   update the watch downloads over the air is not).
2. Parse the `.GCD` TLV container and extract the `FW_ALL_BIN` record → raw firmware image.
3. Load it in Ghidra/IDA as `ARM:LE:32:Cortex` with a hand-built memory map (on the analysed device,
   flash started at `0x3000`; the bootloader below that is absent from the image).
4. Locate the embedded API data section (magic `0xc1a55def`) — the firmware carries a compiled copy of
   the Connect IQ SDK — and cross-reference symbol IDs against `api.db` inside `monkeybrains.jar` from
   the SDK, to name the ~460 native callback functions.
5. Analyse `PRG` loading, the TVM opcode dispatch loop (53–55 opcodes), the native-callback table and
   the permission checks.

That work yielded a dozen CVEs (CVE-2023-23298 … CVE-2023-23306, plus earlier CVE-2020-27486) which
allowed **escaping the VM and gaining native code execution on the watch** — but these were disclosed
to Garmin and **patched in CIQ API 3.1.x**. Your epix Pro (Gen 2) runs CIQ 5.2 and is not affected.
Even at their peak, these were exploits, not a firmware-replacement mechanism.

Tooling, if you want to read the research:
[anvilsecure/garmin-ciq-app-research](https://github.com/anvilsecure/garmin-ciq-app-research) ·
[anvilsecure/GhidraGarminApp](https://github.com/anvilsecure/GhidraGarminApp) ·
[Anvil's HITBSecConf2023 talk](https://www.youtube.com/watch?v=KsqLb-l-TjA) ·
[Atredis advisories ATREDIS-2020-0004…0007](https://github.com/atredispartners/advisories)

---

## 4. Can I install apps locally, without publishing to the Garmin store?

**Yes — this is fully supported and documented. It's called side loading.** It requires no Garmin
review, no store account beyond a free Garmin Connect login for the SDK Manager, and no publication.

### 4.1 What side loading is

You build your project into a **`.PRG`** executable, signed with **your own RSA 4096-bit developer
key**, and copy it into the watch's **`GARMIN/APPS/`** directory over USB. On next disconnect the
watch picks it up and it appears in the app/widget/watch-face list.

Notably, GarminOS accepts two `PRG` signature forms — an **App Store signature** and a **Developer
signature** (which embeds your public key alongside the signature) — and there is **no option on the
watch to reject developer-signed apps**. That is precisely why side loading works.

Reference: [Garmin — Your First Connect IQ App → "Side Loading an App"](https://developer.garmin.com/connect-iq/connect-iq-basics/your-first-app/)

### 4.2 What about Bluetooth?

**Not possible.** There is no public API, tool or protocol to push a `PRG` to the watch over
Bluetooth/BLE. The BLE install path is proprietary and used only by the Garmin Connect mobile app
talking to the Connect IQ Store.

The two Bluetooth-adjacent things that *do* exist, and what they actually are:

| Feature | What it really does |
|---|---|
| [Connect IQ Mobile SDK (Android / iOS)](https://developer.garmin.com/connect-iq/core-topics/communicating-with-mobile-apps/) | Lets an **already-installed** CIQ app exchange messages with your phone app over BLE. It cannot install anything. |
| [Beta Apps](https://developer.garmin.com/connect-iq/core-topics/beta-apps/) | Upload a build to the store marked "Beta" under an alternate app UUID. It stays private to your account and its URL is not visible externally, but it still installs *through* the store/Garmin Connect. Useful for testing app settings and Garmin Connect integration; **not** a local install. |

So: **USB for local iteration, Beta Apps if you need to exercise the real store/settings pipeline.**

### 4.3 macOS caveat — the epix Pro (Gen 2) is an MTP device

This matters a lot for your workflow. Garmin explicitly lists **epix Pro (Gen 2) – Standard and
Sapphire Editions** among the devices that use **Media Transfer Protocol (MTP)** rather than USB Mass
Storage, and states that access to those files "requires a Windows based computer and is not
compatible with macOS".

Older Garmins (e.g. fēnix 6 non-music) mounted as a plain `/Volumes/GARMIN` drive, which is why older
sideloading tutorials show a simple `cp`. **Your watch will not do that.**

Practical options on macOS:

| Approach | Notes |
|---|---|
| **OpenMTP** (free, open source GUI) | Currently the most reliable way to browse a Garmin over MTP on macOS. Drag the `.PRG` into `GARMIN/APPS/`. |
| **Android File Transfer** | Historically worked; users report frequent failures on recent macOS versions. |
| **`libmtp` CLI** (`brew install libmtp`) | `mtp-detect`, `mtp-folders`, `mtp-sendfile` — scriptable, good for a build → deploy loop. |
| **Windows machine or VM with USB passthrough** | The path Garmin actually supports; MTP appears natively in Explorer. |
| **Garmin Express** | Handles firmware and store apps, but is not a general file browser. |

References:
[Garmin — Devices with Media Transfer Protocol](https://support.garmin.com/en-US/?faq=CZqibgTHMb0dAYEaj2UiU7) ·
[Garmin forum — "USB Drive Mode is needed! MTP is not supported on Mac" (epix 2)](https://forums.garmin.com/outdoor-recreation/outdoor-recreation/f/epix-2/285733/usb-drive-mode-is-needed-mtp-is-not-supported-on-mac) ·
[OpenMTP](https://openmtp.ganeshrvel.com/)

---

## 5. What language are the apps written in?

### 5.1 If you could install your own OS

Moot — see §2. You cannot. But for completeness: if you somehow ran Linux on wrist hardware, yes, any
language with a toolchain targeting that architecture would work (C, C++, Rust, Go, Python, …). On
real Cortex-M wearables you would realistically use **C, C++, Rust, or MicroPython/Espruino**.

### 5.2 On GarminOS — the actual answer

**Monkey C is the only language for on-device apps.** There is no C/C++/Rust/Python SDK, no WASM
runtime, no scripting escape hatch.

Monkey C in one paragraph:

- Object-oriented, **duck-typed** (with optional gradual typing via "Monkey Types"), compiles to
  bytecode interpreted by TVM.
- Memory is managed automatically by **reference counting** — no `malloc`/`free`.
- Syntax is deliberately familiar: heavily influenced by C, Java, JavaScript, Python, Lua, Ruby, PHP.
- Key differences that bite newcomers:
  - Everything is an object — even `Integer`, `Float`, `Char` have methods.
  - **Functions are not first class.** Callbacks use `Method` objects (`method(:onReceive)`), not bare
    function references.
  - Classes are compiled and **cannot be modified at runtime** — all variables must be declared.
  - You `import`/`using` **modules**, not classes; classes are referenced through their parent module.
  - Runtime type errors instead of compile-time type safety (unless you raise the type-check level).
- The root system module is **`Toybox`** (`Toybox.System`, `Toybox.WatchUi`, `Toybox.Graphics`,
  `Toybox.Position`, `Toybox.Sensor`, `Toybox.Communications`, `Toybox.Ant`, …).

Hello world:

```monkeyc
import Toybox.Application as App;
import Toybox.System;

class MyProjectApp extends App.AppBase {
    function onStart(state) {
        System.println("Hello Monkey C!");
    }
    function onStop(state) {}
    function getInitialView() {
        return [ new MyProjectView() ];
    }
}
```

**App types you can build** (each has its own memory budget — see §1.4):
Watch Face · Data Field · Widget · Device (Watch) App · Glance · Background Service ·
Audio Content Provider · Complication publisher/subscriber.

**Everything else in the stack is normal:**

| Layer | Language |
|---|---|
| On-watch app | Monkey C only |
| Companion Android app | Kotlin/Java + [Connect IQ Android SDK (Maven Central)](https://central.sonatype.com/artifact/com.garmin.connectiq/ciq-companion-app-sdk) — [sample](https://github.com/garmin/connectiq-android-sdk) |
| Companion iOS app | Swift/Obj-C + [Connect IQ iOS SDK](https://github.com/garmin/connectiq-companion-app-sdk-ios) — [sample](https://github.com/garmin/connectiq-companion-app-example-ios) |
| Web backend the watch calls | Anything. `Toybox.Communications` does HTTPS + OAuth against REST services. |

References:
[Monkey C guide](https://developer.garmin.com/connect-iq/monkey-c/) ·
[Connect IQ Basics / Programmer's Guide](https://developer.garmin.com/connect-iq/connect-iq-basics/) ·
[API docs](https://developer.garmin.com/connect-iq/api-docs/) ·
[Core Topics](https://developer.garmin.com/connect-iq/core-topics/)

---

## 6. Step by step: build an app and load it onto your epix Pro (Gen 2)

Target here: **macOS** (your environment), with the MTP caveat handled. The SDK also supports Windows
and Ubuntu Linux.

### Step 0 — Prerequisites

1. A free **Garmin Connect account** (the SDK Manager requires login).
2. **Java Runtime 11 or higher** — the compiler and VS Code extension both need it.
   ```bash
   brew install --cask temurin@21
   java -version
   ```
3. **VS Code** (you already have it).
4. A USB data cable for the watch (not a charge-only cable).
5. On macOS, an MTP browser: `brew install --cask openmtp` (or `brew install libmtp` for CLI).

### Step 1 — Install the Connect IQ SDK Manager

1. Go to [developer.garmin.com/connect-iq/sdk](https://developer.garmin.com/connect-iq/sdk) and accept
   the licence, then **Accept & Download for Mac**.
2. Open the `.dmg` and copy the SDK Manager into a folder you control (e.g. `~/Applications`).
3. Launch it, press **Login**, enter your Garmin Connect credentials.
4. Choose whether the SDK auto-updates, then whether device definitions auto-update.
5. In the **SDK** tab, download the latest SDK (current release at time of writing: **9.2.0**) and set
   it as the **active** SDK.
6. In the **Devices** tab, download **epix Pro (Gen 2)** — pick the size matching your watch
   (42/47/51mm). This gives you the simulator model and the compile target.

### Step 2 — Install the Monkey C VS Code extension

1. VS Code → **Extensions** → search **"Monkey C"** → install the one published by **Garmin**.
2. Restart VS Code.
3. `Cmd+Shift+P` → **Monkey C: Verify Installation**. It should report success.

### Step 3 — Generate a developer signing key

The compiler requires an **RSA 4096-bit private key** to sign every `PRG`.

Option A — from VS Code:
1. `Cmd+Shift+P` → **Monkey C: Generate a Developer Key**.
2. Pick a directory to store it.

Option B — OpenSSL (matches what the CLI expects, DER format):
```bash
openssl genrsa -out developer_key.pem 4096
openssl pkcs8 -topk8 -inform PEM -outform DER \
  -in developer_key.pem -out developer_key.der -nocrypt
```

Then set **Settings → Monkey C → Developer Key Path** to the key file.

> **Back this key up.** If you ever publish to the store, updates must be signed with the *same* key.
> Lose it and you cannot update your published app.

### Step 4 — Create the project

1. `Cmd+Shift+P` → **Monkey C: New Project**.
2. Enter a project name.
3. Choose the project type — **Watch App** for a full app, or **Watch Face** for the simplest first
   build.
4. Choose a template (**Simple**).
5. Choose a minimum API level. For epix Pro (Gen 2)-only work you can safely pick a high one; for wide
   compatibility pick lower.
6. Choose the parent directory.

You get:

```
myproject/
├── bin/            # build output: .prg, .prg.debug.xml
├── resources/      # layouts, strings, images, fonts (resource compiler inputs)
│   └── ...
├── source/         # Monkey C sources (.mc) — App + View
│   ├── MyProjectApp.mc
│   └── MyProjectView.mc
├── manifest.xml    # app id/UUID, app type, permissions, target products
└── monkey.jungle   # build config (targets, per-device resource overrides)
```

### Step 5 — Target your device in the manifest

1. `Cmd+Shift+P` → **Monkey C: Edit Products**.
2. Tick **epix Pro (Gen 2) 42mm / 47mm / 51mm** (whichever you own; you can tick all three).
3. If you need GPS, HR history, web requests, ANT, etc.:
   `Cmd+Shift+P` → **Monkey C: Edit Permissions** and add the required `Toybox.*` modules.
   See [Manifest and Permissions](https://developer.garmin.com/connect-iq/core-topics/manifest-and-permissions/).

### Step 6 — Run it in the simulator first

1. Open a `.mc` file from `source/` so it is the active editor.
2. **Run → Run Without Debugging** (`Cmd+F5`).
3. Pick your product from the list. The simulator launches with the epix Pro model.
4. For breakpoints and variable inspection: **Run → Start Debugging**.
   ⚠️ **Debugging only works in the simulator** — you cannot attach a debugger to the physical watch.

### Step 7 — Build the sideload artifact

**From VS Code:**
1. `Cmd+Shift+P` → **Monkey C: Build for Device**.
2. Select the product (e.g. `epix2pro47mm`).
3. Choose an output directory.
4. It produces `YourApp.prg` (plus `YourApp.prg.debug.xml`).

**From the CLI** (useful for scripting / CI):
```bash
# Put the active SDK on PATH (macOS)
export PATH="$PATH:$(cat "$HOME/Library/Application Support/Garmin/ConnectIQ/current-sdk.cfg")/bin"

# Build a signed PRG for the 47mm epix Pro
monkeyc \
  -d epix2pro47mm \
  -f "$PWD/monkey.jungle" \
  -o "$PWD/bin/MyApp.prg" \
  -y "$HOME/keys/developer_key.der"

# Optional: run it in the simulator
connectiq &                        # start the simulator
monkeydo bin/MyApp.prg epix2pro47mm
```

Reference: [Monkey C command line setup](https://developer.garmin.com/connect-iq/reference-guides/monkey-c-command-line-setup/)

### Step 8 — Copy the PRG onto the watch (the MTP step)

1. Plug the watch into the Mac with a **data** cable.
2. Open **OpenMTP** (or Android File Transfer). The watch appears as an MTP device.
3. Navigate to **`GARMIN/APPS/`**.
4. Drag **`MyApp.prg`** in.
5. *(Recommended, see Step 10)* also create **`GARMIN/APPS/LOGS/MYAPP.TXT`** — an empty file whose
   name matches the PRG — so `System.println()` output is captured.
6. Eject the device properly, then unplug.

CLI alternative with `libmtp`:
```bash
brew install libmtp
mtp-detect                                    # confirm the watch is seen
mtp-folders                                   # find the folder id of GARMIN/APPS
mtp-sendfile bin/MyApp.prg MyApp.prg          # push (folder targeting varies by tool version)
```

If macOS MTP proves painful, the reliable fallback is a **Windows machine/VM**, where `GARMIN/APPS`
is a plain Explorer folder.

### Step 9 — Launch it on the watch

- **Watch app / widget:** press the hotkey/button to open the activity or app list; your app appears
  near the bottom of the list.
- **Watch face:** long-press **MENU** → **Watch Face** → scroll to it.
- **Data field:** open an activity profile → **Data Screens** → edit a field → **Connect IQ**.

### Step 10 — Debug on the actual device

There is no on-device debugger, so you rely on log files written to the watch's filesystem:

| File | Purpose |
|---|---|
| `GARMIN/APPS/LOGS/<APPNAME>.TXT` | Your `System.println()` output. **You must create this file yourself** — the watch will not create it. Name must match the PRG name. |
| `GARMIN/APPS/LOGS/CIQ_LOG.YAML` | Auto-written on an app crash (the "IQ!" icon): error name, timestamp, device id, firmware version, and a full stack trace with file/line/function. |
| `GARMIN/ERR_LOG.txt` | Written on a **device** crash (reboot/freeze) — indicates a firmware bug; attach it when reporting on the Garmin developer forum. |

Logs roll over: past 5 KB a log is archived to `<LOGNAME>.BAK`, so max ~10 KB is retained.

Reference: [Testing and Debugging](https://developer.garmin.com/connect-iq/core-topics/debugging/)

### Step 11 — Iterate

Typical loop, scriptable end-to-end:

```
edit .mc  →  monkeyc -d epix2pro47mm ... -o bin/MyApp.prg  →  copy to GARMIN/APPS/  →  eject → test
                    ↳ simulator (connectiq + monkeydo) for fast checks and breakpoints
```

Run in the simulator for everything you can; push to the watch only for real sensors, real GNSS, real
battery behaviour and real AMOLED rendering.

### Optional — unit tests

Connect IQ ships a test framework ("Run No Evil"):
`Cmd+Shift+P` → **Monkey C: Run Tests**, or `monkeydo bin/MyApp.prg epix2pro47mm -t`.
See [Unit Testing](https://developer.garmin.com/connect-iq/core-topics/unit-testing/).

---

## 7. Constraints worth knowing before you design anything

- **Memory is the hard limit,** not CPU. A watch app on epix Pro (Gen 2) has 768 KB total; a watch face
  only 128 KB. Bitmaps and fonts eat this fast.
- **The VM will kill a slow app.** Long synchronous work in `onUpdate()` gets aborted.
- **Watch faces are heavily throttled** — full per-second updates are only allowed in specific states.
  See [How do I get my watch face to update every second](https://developer.garmin.com/connect-iq/connect-iq-faq/how-do-i-get-my-watch-face-to-update-every-second/).
- **AMOLED devices have extra rules** (burn-in protection, always-on mode constraints). Your watch is
  AMOLED — read [How do I make a watch face for AMOLED products](https://developer.garmin.com/connect-iq/connect-iq-faq/how-do-i-make-a-watch-face-for-amoled-products/).
- **Network access goes through the phone or Wi-Fi**, via `Toybox.Communications`, and requires the
  matching permission. See [HTTPS](https://developer.garmin.com/connect-iq/core-topics/https/) and
  [Authenticated Web Services](https://developer.garmin.com/connect-iq/core-topics/authenticated-web-services/).
- **APIs are per-device.** Use the `has` operator to feature-detect at runtime rather than assuming an
  API exists — Connect IQ links dynamically and will fail at the point of use, not at load.
- **Background services get 64 KB** and are heavily time-limited. See
  [Backgrounding](https://developer.garmin.com/connect-iq/core-topics/backgrounding/).

---

## 8. Using the watch for research workloads — and MCU/RTOS alternatives

> Follow-up question: *"If I can't run Linux, what OS can I run on this microcontroller? I want to
> use the device as a microcontroller for my own research workloads."*

### 8.1 First, correct the premise: the blocker is flashing, not the OS

The reason Linux is out is **not** "Linux is too heavy for a Cortex-M". That is only reason #1 of
several. Reasons #2–#5 apply identically to **every** replacement OS:

| Blocker | Blocks Linux? | Blocks Zephyr / FreeRTOS / NuttX / bare metal? |
|---|---|---|
| No MMU, MCU-scale RAM | ✅ Yes | ❌ No — these are designed for this hardware |
| Firmware update path only accepts Garmin-signed `.GCD` | ✅ Yes | ✅ **Yes** |
| Bootloader is not in any downloadable image; no DFU that takes arbitrary code | ✅ Yes | ✅ **Yes** |
| No exposed SWD/JTAG; sealed 10 ATM case; production readout protection | ✅ Yes | ✅ **Yes** |
| Unknown SoC, zero datasheets, zero drivers for panel/sensors/radios/PMIC | ✅ Yes | ✅ **Yes** |

**So there is no OS — of any size — that you can put on an epix Pro (Gen 2).** The device is not a
development board. Treat it as a sealed appliance with a sandboxed app API, which is exactly what it
is.

The useful question is therefore split in two, answered in §8.2 and §8.3.

### 8.2 Option A — run research workloads *on the Garmin*, inside Connect IQ

This is more capable than people assume, and it is the **only** way to get Garmin-grade sensors,
multi-band GNSS and multi-week battery life. If your research involves a human wearing a device and
doing something physical, this is almost certainly the right answer.

**What you actually get access to:**

| Capability | Module | Research relevance |
|---|---|---|
| Accelerometer sample batches at a configurable rate | `Toybox.Sensor.registerSensorDataListener` | Gait, motion classification, activity segmentation |
| Heart rate (incl. beat-to-beat where exposed), HR zones | `Toybox.Sensor`, `Toybox.ActivityMonitor` | Physiological workloads |
| Historical sensor series (HR, elevation, pressure, stress, temperature…) | `Toybox.SensorHistory` | Longitudinal data without continuous sampling cost |
| Multi-band GNSS position, accuracy, velocity | `Toybox.Position` | Movement/localisation studies |
| Barometric altimeter, compass, thermometer | `Toybox.Sensor` | Environmental context |
| **Write your own data series into the recorded `.FIT` file** | `Toybox.FitContributor` | ⭐ Your custom metrics land in the activity file, exportable over USB and via Garmin Connect |
| Periodic background wake-ups | `Toybox.Background` | Sampling when the app isn't open (64 KB, coarse intervals) |
| BLE **central** — talk to arbitrary BLE peripherals | `Toybox.BluetoothLowEnergy` | ⭐ Pair the watch with your *own* custom sensor board (see §8.5) |
| Raw ANT generic channel + ANT+ profiles | `Toybox.Ant` | Custom wireless sensors, chest straps, pods |
| HTTPS + OAuth to your own backend | `Toybox.Communications` | Ship data to a server |
| Key/value + persisted content storage | `Toybox.Application.Storage`, `Toybox.PersistedContent` | On-device buffering |

**Honest limitations:**

- Monkey C runs as **bytecode in an interpreted VM**. This is slow. Do not plan on heavy DSP, FFT
  chains, or on-device model training.
- **768 KB** total for a watch app, **128 KB** for a watch face, **64 KB** for a background service.
- The **VM aborts** anything that takes too long — no long synchronous compute in `onUpdate()`.
- **No raw ADC, no register access, no native code, no custom threads, no custom sample rates** beyond
  what the API exposes. You get the sensors Garmin decided to expose, at the rates Garmin allows.
- Background sampling is **heavily duty-cycled** — you cannot continuously log at high rate while the
  app is closed.

**The realistic pipeline:** Connect IQ app samples sensors → writes custom `FitContributor` fields →
you pull the `.FIT` off `GARMIN/ACTIVITY/` over USB (or POST to your own API) → analyse offline in
Python/Rust. That is a legitimate, low-friction research rig, and nothing else on the market gives you
those sensors with that battery life.

### 8.3 Option B — buy hardware you *can* flash, then pick an OS

If you need register-level control, custom sampling, native code or hard real-time, you need different
hardware. Here is the actual RTOS landscape for Cortex-M class MCUs in 2026:

| OS / runtime | Licence | Character | Pick it when |
|---|---|---|---|
| **[Zephyr](https://www.zephyrproject.org/)** | Apache 2.0 | Full modern RTOS: devicetree + Kconfig, preemptive/cooperative/EDF scheduling, BLE 5 host **and** controller, networking, LittleFS/FatFS, shell, logging, power management, MPU-based thread isolation. Supports ARMv6-M/v7-M/v8-M, Cortex-A/R, RISC-V, Xtensa, x86. `native_sim` builds it as a Linux app for host-side dev/test. | **Default choice today**, especially for BLE + sensors |
| **[FreeRTOS](https://www.freertos.org/)** | MIT | Just a kernel + scheduler + a few libraries. Tiny, everywhere. Most vendor SDKs (ESP-IDF, nRF5 SDK, STM32Cube) sit on top of it. | Minimum footprint, or you're living inside a vendor SDK |
| **[Apache NuttX](https://nuttx.apache.org/)** | Apache 2.0 | POSIX/ANSI-compliant: real filesystem, `pthread`, sockets, a proper shell. Feels like a tiny Unix. | Porting existing POSIX code to an MCU |
| **[Eclipse ThreadX](https://threadx.io/)** (was Azure RTOS) | Permissive | Small, fast, **safety-certified**; Microsoft donated it to the Eclipse Foundation in 2023. | Safety/certification-adjacent work |
| **[RIOT OS](https://www.riot-os.org/)** | LGPLv2.1 | Explicitly research-oriented; strong 6LoWPAN/RPL/mesh stack, large academic user base | IoT/networking protocol research |
| **[RT-Thread](https://www.rt-thread.io/)** | Apache 2.0 | Big component ecosystem and package manager | You want batteries-included components |
| **[Contiki-NG](https://www.contiki-ng.org/)** | BSD | Low-power wireless / WSN research heritage | Sensor-network protocol research |
| **[Mbed OS](https://github.com/ARMmbed/mbed-os)** | Apache 2.0 | ⚠️ **Sunset by Arm in July 2026.** Source stays public but is unmaintained; a community fork ([Mbed CE](https://github.com/mbed-ce/mbed-os)) exists. Arm itself now points people at Zephyr/FreeRTOS/CMSIS-RTX. | **Don't start new work here** |
| **Bare metal + [CMSIS](https://arm-software.github.io/CMSIS_6/)** | — | No OS at all. Full determinism, nothing between you and the silicon. | Hard real-time, cycle-accurate measurement |
| **[Embassy](https://embassy.dev/)** (Rust) | Apache/MIT | `async`/`await` embedded Rust, no RTOS needed, memory-safe | You want Rust |
| **[MicroPython](https://micropython.org/) / [CircuitPython](https://circuitpython.org/)** | MIT | Python + REPL over serial, no toolchain pain | Fast prototyping, quick experiments |
| **[Espruino](https://www.espruino.com/)** | MPL 2.0 | JavaScript on MCU; this is what powers Bangle.js | Wearable prototyping without a build step |
| **[TinyGo](https://tinygo.org/)** | BSD | Go for microcontrollers | You want Go |

**If you want one recommendation: Zephyr.** It is the only option in that list that is simultaneously
vendor-neutral, actively developed, wearable-capable (BLE host + controller in-tree), and testable on
your Mac via `native_sim` before you touch hardware.

### 8.4 Which board to actually buy

Match the board to the workload, not the other way round:

| Your research goal | Hardware | OS / stack |
|---|---|---|
| Wrist-worn, closest thing to a hackable Garmin | **[PineTime](https://pine64.org/devices/pinetime/)** (nRF52832, ~US$27) | [InfiniTime](https://github.com/InfiniTimeOrg/InfiniTime) (FreeRTOS) or Zephyr — both supported |
| Wrist-worn, polished, scriptable in minutes | **[Bangle.js 2](https://banglejs.com/)** (nRF52840) | Espruino JS out of the box; reflashable |
| Wrist-worn e-paper, trivially easy | **[Watchy](https://watchy.sqfmi.com/)** (ESP32) | Arduino / ESP-IDF (FreeRTOS underneath) |
| BLE + sensors, maximum flexibility | **Nordic [nRF52840-DK](https://www.nordicsemi.com/Products/Development-hardware/nRF52840-DK) / [nRF5340-DK](https://www.nordicsemi.com/Products/Development-hardware/nRF5340-DK)** | Zephyr — Nordic's nRF Connect SDK **is** Zephyr |
| Cheap, Wi-Fi + BLE, more RAM and compute | **ESP32-S3 devkit** | ESP-IDF (FreeRTOS) or Zephyr |
| Hard real-time, lots of I/O, PIO tricks | **RP2350 / Pico 2** | Bare metal, FreeRTOS, Zephyr, or Embassy |
| **On-device ML inference** | nRF5340, STM32H7, or ESP32-S3 | Zephyr + [TFLite Micro](https://github.com/tensorflow/tflite-micro), or [Edge Impulse](https://edgeimpulse.com/) |
| **You genuinely wanted a Linux userspace** | **Raspberry Pi Zero 2 W** (~US$15) or a CM4/CM5 | Full Debian. Wi-Fi + BLE, I²C/SPI/UART headers for sensors. |

> ⚠️ If "run a Linux distro" was the actual requirement, **an MCU is never the answer** — not the
> Garmin's, not any other. A Pi Zero 2 W gives you a real Linux distro, real package management, real
> Python/PyTorch, and GPIO/I²C/SPI for sensors, for the price of a coffee round. Cortex-M boards are
> for when you need microamps and microseconds, not for when you need an OS.

### 8.5 The hybrid pattern (often the best of both)

Because Connect IQ exposes **`Toybox.BluetoothLowEnergy` as a BLE central**, you can build a two-part
rig without fighting anyone's firmware:

```
┌──────────────────────────┐        BLE GATT         ┌───────────────────────────────┐
│  Your custom board       │ ──────────────────────▶ │  Garmin epix Pro (Gen 2)      │
│  nRF52840 / ESP32-S3     │  your own service/UUIDs │  Connect IQ app (Monkey C)    │
│  Zephyr / Embassy        │                         │  • displays + interacts       │
│  • your sensors          │ ◀────────────────────── │  • adds Garmin HR/GNSS context│
│  • your sampling rates   │        commands         │  • FitContributor → .FIT      │
│  • your algorithms       │                         │  • HTTPS → your backend       │
└──────────────────────────┘                         └───────────────────────────────┘
```

You get full register-level freedom on hardware you own, **plus** Garmin's sensors, screen, GNSS,
battery and FIT/Garmin Connect pipeline — and you never have to modify Garmin firmware. For most
wearable research this is strictly better than trying to own the whole stack.

### 8.6 Decision guide

| If your research is… | Do this |
|---|---|
| Human physiology / sport / GNSS data collection | **Keep the Garmin.** Connect IQ app + `FitContributor` + USB/HTTPS export (§8.2) |
| A new sensor or a new wrist-worn algorithm | PineTime or nRF52840-DK + **Zephyr** (§8.3–8.4) |
| Custom sensing *with* Garmin-quality context | **Hybrid rig** (§8.5) |
| On-device ML inference | ESP32-S3 / nRF5340 + Zephyr + TFLite Micro, or just use a Pi |
| Anything that needs a Linux userspace or Python | Raspberry Pi Zero 2 W — not an MCU |
| Studying GarminOS / Connect IQ internals themselves | Simulator + Ghidra + the published research (§3, Tier 3) |

---

## 9. Shopping list — what to actually buy

> ⚠️ **Prices are indicative EUR/USD ballparks as of 2026-08 and vary a lot by region and reseller.**
> Always check the current price at the vendor before ordering. Nothing here is a price quote.

### 9.1 Tier 0 — Garmin-only path (Option A): **€0**

You already own everything. Confirm you have:

| Item | Cost | Note |
|---|---|---|
| epix Pro (Gen 2) | owned | — |
| Garmin 4-pin USB **data** cable | in the box | A charge-only cable will not enumerate |
| Connect IQ SDK + SDK Manager | free | §6, Step 1 |
| Java 21 (Temurin) | free | `brew install --cask temurin@21` |
| VS Code + Monkey C extension | free | — |
| OpenMTP | free | `brew install --cask openmtp` — needed because the watch is MTP (§4.3) |

**Total: €0.** Start here regardless of what else you buy — it costs nothing and tells you within a
weekend whether Connect IQ is enough for your research.

### 9.2 Tier 1 — Minimum viable "I control the whole stack" rig: **~€60**

| Item | ~Price | Why |
|---|---|---|
| ⭐ **Nordic [nRF52840-DK](https://www.nordicsemi.com/Products/Development-hardware/nRF52840-DK)** | ~€50–60 | **The single most important purchase.** Cortex-M4F, BLE 5, **on-board J-Link debugger** (no separate probe needed), Arduino headers, current-measurement header. First-class Zephyr in-tree board. |
| USB micro-B cable | ~€5 | Usually included |
| Zephyr / nRF Connect SDK | free | Nordic's SDK **is** Zephyr |

**Why this board and not something cheaper:** the built-in debugger saves you a separate €20 probe,
**and** it can act as an external programmer for other Nordic targets later (including a PineTime).
Nordic's [Developer Academy](https://academy.nordicsemi.com/) is the best free embedded course
material available, and the nRF52 family is what PineTime, Bangle.js and most BLE wearables use — so
the skills transfer directly.

**Alternatives, if your priorities differ:**

| Instead of | ~Price | Trade-off |
|---|---|---|
| [nRF54L15-DK](https://www.nordicsemi.com/Products/Development-hardware/nRF54L15-DK) | ~€60–80 | Newer generation (Cortex-M33, much better power figures). Fewer tutorials/community answers than nRF52840 — pick it if you're comfortable being slightly ahead of the docs. |
| ESP32-S3-DevKitC-1 | ~€12–20 | Cheapest path to Wi-Fi **and** BLE, plus much more RAM for ML. But the ecosystem is ESP-IDF-first; Zephyr support is less polished. |
| Raspberry Pi Pico 2 (RP2350) | ~€6–10 | Superb for deterministic real-time and PIO tricks. **No radio.** |

### 9.3 Tier 2 — Wrist-worn research rig: **~€30–80 on top of Tier 1**

This is where the PineTime comes in — but **the variant matters and people get this wrong:**

| Variant | ~Price | Buy this if… |
|---|---|---|
| **PineTime Dev Kit** (unsealed) | ~€30 | You want **Zephyr, bare metal, or your own bootloader**. SWD pads are reachable. Case does not lock shut, **not water resistant, not wearable long-term.** |
| **PineTime Sealed** | ~€30–50 | You want to **modify InfiniTime and actually wear it**. IP67, ships with InfiniTime + a bootloader that accepts **OTA firmware over BLE** (Gadgetbridge / nRF Connect / InfiniLink) — so you can still flash your own InfiniTime builds without opening it. |

Pine64's own guidance: *"If you want to do development, buy a development kit."* Practical answer for
research: **buy one of each** if budget allows — dev kit on the bench, sealed one on your wrist.

| Also needed / nice to have | ~Price | Note |
|---|---|---|
| SWD wiring to the dev kit | ~€5 | Dupont jumpers; see [PineTime Devkit Wiring](https://wiki.pine64.org/wiki/PineTime_Devkit_Wiring) |
| A debug probe — **only if you skipped Tier 1** | ~€12–25 | [Raspberry Pi Debug Probe](https://www.raspberrypi.com/products/debug-probe/) (~€12), J-Link EDU Mini (~€20, **non-commercial use only**), or an ST-Link clone + OpenOCD. **If you bought the nRF52840-DK, use it as the programmer — buy nothing.** |
| Spare charging cradle | ~€10–15 | One is included; they're fragile |
| 20 mm quick-release strap | ~€10 | PineTime uses standard 20 mm; some thick NATO straps don't fit the recessed lugs |

**Reality check on PineTime specs:** nRF52832, **64 KB RAM**, 512 KB flash + 4 MB SPI NOR, 240×240
ST7789 IPS, CST816S touch, Bosch BMA421/BMA425 accelerometer, HRS3300 PPG heart rate, 170–180 mAh.
That is **less RAM than your Connect IQ app gets on the Garmin** — you're buying *control*, not
capability. Full pinouts and datasheets: [PineTime wiki](https://wiki.pine64.org/wiki/PineTime).

| Wrist alternatives | ~Price | Note |
|---|---|---|
| [Bangle.js 2](https://banglejs.com/) | ~€80–100 | nRF52840, waterproof, ships wearable. Write JavaScript over BLE with **zero toolchain** — fastest path from idea to something on your wrist. Reflashable to Zephyr/Arduino later. |
| [Watchy](https://watchy.sqfmi.com/) | ~€50–80 | ESP32 + e-paper, fully open hardware, Arduino workflow. Chunky, but trivial to program. |

### 9.4 Tier 3 — The hybrid rig (Option C, §8.5): **~€40–120 on top of Tier 1**

Custom sensor board talking BLE to a Connect IQ app on your Garmin:

| Item | ~Price | Why |
|---|---|---|
| **Seeed XIAO nRF52840** (or nRF52840 Sense) | ~€12–25 | Thumbnail-sized, wearable, same SoC as the DK so code ports directly. The *Sense* variant has an IMU + microphone on board. |
| LiPo battery 150–400 mAh + JST connector | ~€8 | XIAO has an on-board charger |
| Sensor breakouts — **pick per research question** | €5–35 each | IMU: **BMI270** / **LSM6DSV**. PPG heart rate: **MAX30102**. Single-lead ECG: **MAX30003** / AD8232. Pressure/altitude: **BMP390**. Temp/humidity: **SHT4x**. Force/flex: load cell + HX711. |
| Qwiic / STEMMA QT cables + breadboard + jumpers | ~€15 | Solderless I²C chaining saves hours |
| Your epix Pro | owned | Display, HR/GNSS context, FIT logging, Garmin Connect sync |

> **I can give you an exact sensor list once you tell me what the research is measuring.** "IMU vs
> PPG vs ECG vs environmental" changes this table completely.

### 9.5 Tier 4 — Instrumentation (buy *when you hit the need*, not upfront)

| Item | ~Price | Buy it when |
|---|---|---|
| Nordic **Power Profiler Kit II** | ~€90 | Battery life or duty-cycling is part of your research. Measures sub-µA. Nothing else in this range comes close. |
| Cheap 8-channel logic analyzer (Saleae clone) | ~€10 | First time an I²C/SPI bus misbehaves |
| Saleae Logic 8 | ~€400 | Only if the cheap one becomes a bottleneck |
| Soldering iron — [Pinecil V2](https://pine64.com/product/pinecil-smart-mini-portable-soldering-iron/) | ~€30 | First time you need to attach a wire |
| Flux, 0.5 mm solder, 30 AWG wire, fine tweezers, kapton tape | ~€30 | With the iron |
| USB–UART adapter (CP2102 / FTDI) | ~€8 | Only if a board lacks a built-in one — the DK and XIAO don't |
| Bench multimeter or decent DMM | ~€30+ | General debugging |

### 9.6 Tier 5 — "I actually wanted a Linux userspace": **~€30**

| Item | ~Price |
|---|---|
| Raspberry Pi Zero 2 W | ~€15–20 |
| microSD 32 GB (A1/A2 rated) | ~€8 |
| USB power supply + cable | ~€10 |
| *(optional)* Pi Camera / sensor HAT / USB-serial console cable | €10–30 |

Full Debian, `apt`, Python, PyTorch, Docker, plus I²C/SPI/UART headers for the same sensor breakouts
as Tier 3. If any part of your research involves a language or library that doesn't cross-compile to a
Cortex-M, buy this instead of fighting an MCU.

### 9.7 What I'd actually buy, in order

1. **€0 — Nothing.** Install the Connect IQ SDK, build a Watch App, log accelerometer + HR to a
   `FitContributor` field, pull the `.FIT` over MTP. One weekend. This answers *"is Connect IQ
   enough?"* before you spend anything.
2. **~€55 — nRF52840-DK.** The universal unlock: dev board, debugger, and BLE peer for testing the
   hybrid rig, in one purchase.
3. **~€30 — PineTime** (Dev Kit for bench work, Sealed if you want to wear it). Now you have a real
   wrist target you fully own.
4. **~€20 — XIAO nRF52840 + battery**, once you know which sensors you need.
5. **~€90 — Power Profiler Kit II**, only if power is part of the research question.

**Cumulative for a complete, serious wearable-research bench: roughly €150–250.**

### 9.8 What *not* to buy

- ❌ **A second Garmin, or a "cheaper Garmin to experiment on."** Every Garmin has the same locked
  firmware. A cheaper model buys you nothing extra.
- ❌ **Mbed-branded boards or Mbed-first tutorials.** Mbed was sunset by Arm in July 2026 (§8.3).
- ❌ **A J-Link EDU Mini for anything commercial** — the licence is non-commercial only. If your
  research is funded/commercial, use the DK's on-board debugger or a CMSIS-DAP probe.
- ❌ **A charge-only USB cable** for the Garmin. It will silently fail to enumerate and you'll waste an
  hour.
- ❌ **Expensive gear before Tier 0.** Do the free Connect IQ experiment first.

---

## 10. Reference index

### Garmin official
- [Connect IQ overview](https://developer.garmin.com/connect-iq/overview/)
- [Get the SDK (+ full Developer Agreement)](https://developer.garmin.com/connect-iq/sdk/)
- [Getting Started (SDK Manager, VS Code extension, developer key)](https://developer.garmin.com/connect-iq/connect-iq-basics/getting-started/)
- [Your First App — includes "Side Loading an App"](https://developer.garmin.com/connect-iq/connect-iq-basics/your-first-app/)
- [App Types](https://developer.garmin.com/connect-iq/connect-iq-basics/app-types/)
- [Monkey C language guide](https://developer.garmin.com/connect-iq/monkey-c/)
- [Compiler options](https://developer.garmin.com/connect-iq/monkey-c/compiler-options/)
- [Monkey C command line setup](https://developer.garmin.com/connect-iq/reference-guides/monkey-c-command-line-setup/)
- [VS Code extension reference](https://developer.garmin.com/connect-iq/reference-guides/visual-studio-code-extension/)
- [Jungle (build config) reference](https://developer.garmin.com/connect-iq/reference-guides/jungle-reference/)
- [Manifest and permissions](https://developer.garmin.com/connect-iq/core-topics/manifest-and-permissions/)
- [Testing and debugging](https://developer.garmin.com/connect-iq/core-topics/debugging/)
- [Beta apps](https://developer.garmin.com/connect-iq/core-topics/beta-apps/)
- [Compatible devices list](https://developer.garmin.com/connect-iq/compatible-devices/)
- [Device reference — epix Pro (Gen 2) 47mm](https://developer.garmin.com/connect-iq/device-reference/epix2pro47mm/)
- [API documentation](https://developer.garmin.com/connect-iq/api-docs/)
- [Developer forum](https://forums.garmin.com/developer/connect-iq)
- [Garmin — Devices with Media Transfer Protocol](https://support.garmin.com/en-US/?faq=CZqibgTHMb0dAYEaj2UiU7)
- [Garmin — epix Pro (Gen 2) software updates](https://support.garmin.com/en-GB/?faq=rPMYb00EKm9pWrcxiL5Fz9)

### Independent research on GarminOS / firmware
- [Anvil Secure — Compromising Garmin's Sport Watches: GarminOS + MonkeyC VM deep dive](https://www.anvilsecure.com/blog/compromising-garmins-sport-watches-a-deep-dive-into-garminos-and-its-monkeyc-virtual-machine.html)
- [Anvil Secure — Reverse Engineering Garmin Watch Applications with Ghidra](https://www.anvilsecure.com/blog/reverse-engineering-garmin-watch-applications-with-ghidra.html)
- [Atredis Partners — A Watch, a Virtual Machine, and Broken Abstractions (Forerunner 235)](https://www.atredis.com/blog/2020/11/4/garmin-forerunner-235-dion-blazakis)
- [anvilsecure/garmin-ciq-app-research (Kaitai PRG spec, PoCs, signing scripts)](https://github.com/anvilsecure/garmin-ciq-app-research)
- [anvilsecure/GhidraGarminApp (Ghidra loader/processor for CIQ apps)](https://github.com/anvilsecure/GhidraGarminApp)
- [pzl/ciqdb — PRG parser in Go](https://github.com/pzl/ciqdb)
- [Garmin GCD firmware format (Herbert Oppmann, PDF)](https://www.memotech.franken.de/FileFormats/Garmin_GCD_Format.pdf)
- [Garmin BIN firmware format (Herbert Oppmann, PDF)](https://www.memotech.franken.de/FileFormats/Garmin_BIN_Format.pdf)

### Open/hackable wearable alternatives
- [Bangle.js 2](https://banglejs.com/) · [Watchy](https://watchy.sqfmi.com/) · [PineTime](https://pine64.org/devices/pinetime/) · [InfiniTime](https://github.com/InfiniTimeOrg/InfiniTime) · [PebbleOS source](https://github.com/google/pebble) · [Rebble](https://rebble.io/)

### Where to buy / hardware docs (see §9)
- [PINE STORE \u2014 PineTime Dev Kit](https://pine64.com/product/pinetime-dev-kit/) · [PineTime Sealed](https://pine64.com/product/pinetime-smartwatch-sealed/) · [PINE64 EU store](https://pine64eu.com/shop/)
- [PineTime wiki \u2014 full hardware reference, pinouts, datasheets](https://wiki.pine64.org/wiki/PineTime) · [Devkit wiring](https://wiki.pine64.org/wiki/PineTime_Devkit_Wiring) · [Reprogramming the PineTime](https://wiki.pine64.org/wiki/Reprogramming_the_PineTime)
- [Nordic nRF52840-DK](https://www.nordicsemi.com/Products/Development-hardware/nRF52840-DK) · [nRF5340-DK](https://www.nordicsemi.com/Products/Development-hardware/nRF5340-DK) · [nRF54L15-DK](https://www.nordicsemi.com/Products/Development-hardware/nRF54L15-DK) · [Power Profiler Kit II](https://www.nordicsemi.com/Products/Development-hardware/Power-Profiler-Kit-2)
- [Nordic Developer Academy (free courses)](https://academy.nordicsemi.com/)
- [Raspberry Pi Debug Probe](https://www.raspberrypi.com/products/debug-probe/) · [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/)
- [Gadgetbridge (PineTime OTA companion)](https://www.gadgetbridge.org/) · [InfiniLink (iOS)](https://github.com/xan-m/InfiniLink)

### RTOS / embedded runtimes (see §8.3)
- [Zephyr](https://www.zephyrproject.org/) · [Zephyr docs — supported architectures](https://docs.zephyrproject.org/latest/introduction/index.html) · [Zephyr supported boards](https://docs.zephyrproject.org/latest/boards/index.html)
- [FreeRTOS](https://www.freertos.org/) · [Apache NuttX](https://nuttx.apache.org/) · [Eclipse ThreadX (ex Azure RTOS)](https://threadx.io/)
- [RIOT OS](https://www.riot-os.org/) · [RT-Thread](https://www.rt-thread.io/) · [Contiki-NG](https://www.contiki-ng.org/)
- [Arm Mbed sunset notice + recommended alternatives](https://github.com/ARMmbed) · [Mbed CE community fork](https://github.com/mbed-ce/mbed-os)
- [Embassy (async Rust)](https://embassy.dev/) · [MicroPython](https://micropython.org/) · [CircuitPython](https://circuitpython.org/) · [Espruino](https://www.espruino.com/) · [TinyGo](https://tinygo.org/)
- [Nordic nRF Connect SDK (Zephyr-based)](https://www.nordicsemi.com/Products/Development-software/nRF-Connect-SDK) · [TensorFlow Lite for Microcontrollers](https://github.com/tensorflow/tflite-micro) · [Edge Impulse](https://edgeimpulse.com/)

### macOS ↔ Garmin file transfer
- [OpenMTP](https://openmtp.ganeshrvel.com/)
- [libmtp](https://github.com/libmtp/libmtp)
- [Garmin forum: MTP not supported on Mac (epix 2)](https://forums.garmin.com/outdoor-recreation/outdoor-recreation/f/epix-2/285733/usb-drive-mode-is-needed-mtp-is-not-supported-on-mac)

---

## 11. Caveats on this document

- Device-specific values (screen sizes, memory limits, part number, CIQ level) come from Garmin's
  device reference and are accurate as of the SDK version current on 2026-08-10; re-check the device
  page if Garmin bumps firmware.
- The GarminOS internals described in §1 are derived from **published reverse engineering of the
  Forerunner 235 and Forerunner 245 Music**, not of the epix Pro (Gen 2) specifically. The
  architecture (GarminOS + TVM + `PRG` + Cortex-M) is consistent across the product line, but
  low-level details such as memory maps, opcode counts and native-callback offsets are firmware- and
  device-specific.
- The vulnerabilities referenced in §3 are **fixed** in CIQ 3.1.x and later. They are cited as evidence
  about the architecture, not as a usable technique.
- Nothing here should be read as encouragement to modify firmware. Doing so breaches Garmin's licence
  terms, voids the warranty, voids water resistance, and will very likely brick a ~€900 device.
- **All prices in §9 are indicative ballparks, not quotes.** Hardware pricing moves constantly and
  varies by region, reseller, VAT and stock. Verify at the vendor before ordering.
