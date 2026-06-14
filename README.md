**NEW:** Check out the [cisco-pt-mcp](https://muhammadbalawal.github.io/cisco-pt-mcp/) extension: Have AI create your Packet Tracer networks! Open source!

---

# Packet Tracer Builder

_Packet Tracer Builder_ is an extension that allows you to use JavaScript code to create networks.

It provides a code editor window and a set of simple functions that can be called to create, configure, and link devices.

For example:

```
addDevice("R1","2911",100,100);
addDevice("S1","2960-24TT",200,100);
addDevice("PC1","PC-PT",300,100);
addLink("R1","GigabitEthernet0/1","S1","GigabitEthernet0/1", "straight");
addLink("S1","FastEthernet0/1","PC1","FastEthernet0", "straight");
```

Produces:

![Screenshot](screenshot1.jpg)

You can also use loops and other JavaScript features to automate it. For example, to create 10 switches:

```
for (let n=1; n <= 10; n++) {
    addDevice("S" + n,"2960-24TT",n * 100,100);
}
```

Produces:

![Screenshot](screenshot2.jpg)

**But why???**

This was dreamt up as a method to allow **AI chatbots** to **automate** the **creation** of **Packet Tracer networks**.

## Installation

1. Download [Builder.pts](https://github.com/kimmknight/PTBuilder/blob/main/Builder.pts)

2. In Packet Tracer, click **Exensions** > **Scripting** > **Configure PT Script Modules**

3. Click the **Add...** button and locate **Builder.pts**

## Use

Once installed, you can open the code editor at any time by clicking **Extensions** > **Builder Code Editor**.

## CLI bridge

This fork can also be driven from a local CLI so agents do not need to paste
JavaScript into the Packet Tracer window manually.

1. Start the bridge server:

```
python ptbuilder.py serve
```

2. Launch Cisco Packet Tracer 8.2 if needed:

```
python ptbuilder.py launch
```

If Packet Tracer is installed in a non-standard location:

```
python ptbuilder.py launch --pt-path "C:\Path\To\PacketTracer.exe"
```

3. Open Packet Tracer and open **Extensions** > **Builder Code Editor**. Keep
   the **CLI Bridge** checkbox enabled.

4. Confirm the WebView is connected:

```
python ptbuilder.py wait-connected
python ptbuilder.py status
```

5. Run commands from a terminal:

```
python ptbuilder.py add-device --require-connected R1 2911 100 100
python ptbuilder.py add-device --require-connected PC1 PC-PT 300 100
python ptbuilder.py add-link --require-connected R1 GigabitEthernet0/1 PC1 FastEthernet0 straight
python ptbuilder.py get-network --require-connected
python ptbuilder.py run --require-connected topology.js
```

If the installed `Builder.pts` is the original legacy module without the CLI
Bridge controls, the CLI automatically falls back to driving the visible
**Builder Code Editor** window. Keep that window open and run:

```
python ptbuilder.py pc-link-test
```

Use `python ptbuilder.py status` to see the selected transport. It reports
`bridge` for the updated module and `ui` for legacy window automation.

The bridge listens on `http://127.0.0.1:54321`. The WebView polls `/next`,
executes the JavaScript through Packet Tracer's scripting engine, and posts the
structured result back to `/result`.

For setup checks, run:

```
python ptbuilder.py doctor
```

## Documentation

In the [Wiki](https://github.com/kimmknight/PTBuilder/wiki), you will find information on the available functions, as well as lists of usable devices, links, and modules.
