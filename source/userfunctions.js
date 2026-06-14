addDevice = function(deviceName, deviceModel, x, y) {
    var deviceType = allDeviceTypes[deviceModel];
    var originalDeviceName = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace().addDevice(deviceType, deviceModel, x, y);

    if (!originalDeviceName) { return false; }

    var device = ipc.network().getDevice(originalDeviceName);
    device.setName(deviceName);

    if (deviceType <= 1 || deviceType == 16) {
        device.skipBoot();
    }

    return true;
}

moveDeviceTo = function(deviceName, x, y, centered = true) {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
        return {
            ok: false,
            reason: "missing-device",
            name: deviceName
        };
    }

    var before = getDeviceInfo(deviceName);
    try {
        if (centered && typeof device.moveToLocationCentered == "function") {
            device.moveToLocationCentered(Number(x), Number(y));
        } else if (typeof device.moveToLocation == "function") {
            device.moveToLocation(Number(x), Number(y));
        } else {
            return {
                ok: false,
                reason: "move-unavailable",
                name: deviceName,
                before: before
            };
        }
    } catch (error) {
        return {
            ok: false,
            reason: "move-failed",
            name: deviceName,
            error: String(error && error.message ? error.message : error),
            before: before
        };
    }

    return {
        ok: true,
        name: deviceName,
        before: before,
        device: getDeviceInfo(deviceName)
    };
}

addModule = function(deviceName, slot, model) {
    var device = ipc.network().getDevice(deviceName);

    powerState = device.getPower();
    device.setPower(false);

    var moduleType = allModuleTypes[model];
    result = device.addModule(slot, moduleType, model);

    if (powerState) {
        device.setPower(true);
        var deviceType = device.getType();
        if (deviceType <= 1 || deviceType == 16) {
            device.skipBoot();
        }
    }

    if (result != true) { return false; }

    return true;
}

addLink = function(device1Name, device1Interface, device2Name, device2Interface, linkType) {
    var linkType = allLinkTypes[linkType];
    result = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace().createLink(device1Name, device1Interface, device2Name, device2Interface, linkType);
    if (result != true) { return false; }

    return true;
}

configurePcIp = function(deviceName, dhcpEnabled = undefined, ipaddress = undefined, subnetMask = undefined, defaultGateway = undefined, dnsServer = undefined) {
    var device = ipc.network().getDevice(deviceName);
    var port = device.getPort("FastEthernet0");
    if (dhcpEnabled) device.setDhcpFlag(dhcpEnabled);
    if (ipaddress && subnetMask) port.setIpSubnetMask(ipaddress, subnetMask);
    if (defaultGateway) port.setDefaultGateway(defaultGateway);
    if (dnsServer) port.setDnsServerIp(dnsServer);
}

configureIosDevice = function(deviceName, commands) {
    var device = ipc.network().getDevice(deviceName);
    var deviceType = device.getType();
    if (deviceType <= 1 || deviceType == 16) {
        device.skipBoot();
    }
    commandsArray = commands.split("\n");
    device.enterCommand("!", "global");
    for (var command of commandsArray) {
        device.enterCommand(command, "");
    }
    device.enterCommand("write memory", "enable");
}

var deviceTypes = {
    router: 0,
    switch: 1,
    cloud: 2,
    bridge: 3,
    hub: 4,
    repeater: 5,
    coaxialsplitter: 6,
    accesspoint: 7,
    pc: 8,
    server: 9,
    printer: 10,
    wirelessrouter: 11,
    ipphone: 12,
    dslmodem: 13,
    cablemodem: 14,
    remotenetwork: 15,
    multilayerswitch: 16,
    laptop: 17,
    tabletpc: 18,
    pda: 19,
    wirelessenddevice: 20,
    wiredenddevice: 21,
    tv: 22,
    homevoip: 23,
    analogphone: 24,
    multiuser: 25,
    asa: 26,
    ioe: 27,
    homegateway: 28,
    celltower: 29,
    ciscoaccesspoint: 30,
    centralofficeserver: 31,
    embeddedciscoaccesspoint: 32,
    sniffer: 33,
    mcu: 34,
    sbc: 35,
    thing: 36,
    mcucomponent: 37,
    embeddedserver: 38
}

function getDevices(filter = undefined, startsWith = "") {
    // filter can be a string, number or array of strings/numbers (or nothing)
    // For example:
    // "router"
    // 0
    // [switch, router, multilayerswitch]
    // [0, 1, 16]

    if (filter) {
        if (typeof filter == "string") {
            filter = [filter];
        }
        if (typeof filter == "number") {
            filter = [filter];
        }
        for (var i = 0; i < filter.length; i++) {
            if (typeof filter[i] == "string") {
                filter[i] = deviceTypes[filter[i].toLowerCase()];
            }
        }
    }
    var deviceCount = ipc.network().getDeviceCount();
    var devices = [];
    for (var i = 0; i < deviceCount; i++) {
        var device = ipc.network().getDeviceAt(i);
        var deviceName = device.getName();
        var deviceType = device.getType();
        
        if ((!filter || filter.includes(deviceType)) && deviceName.startsWith(startsWith)) {
            devices.push(deviceName);
        }
    }
    return devices;
}

function callIfExists(target, methodName) {
    try {
        if (target && typeof target[methodName] == "function") {
            return target[methodName]();
        }
    } catch (error) {
        return undefined;
    }
    return undefined;
}

function getDeviceInfo(deviceName) {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
        return null;
    }

    var info = {
        name: callIfExists(device, "getName") || deviceName,
        type: callIfExists(device, "getType"),
        model: callIfExists(device, "getModel"),
        power: callIfExists(device, "getPower"),
        logical: {
            x: callIfExists(device, "getXCoordinate"),
            y: callIfExists(device, "getYCoordinate"),
            centerX: callIfExists(device, "getCenterXCoordinate"),
            centerY: callIfExists(device, "getCenterYCoordinate")
        },
        ports: []
    };

    var portCount = callIfExists(device, "getPortCount");
    if (typeof portCount == "number") {
        for (var i = 0; i < portCount; i++) {
            var port = undefined;
            try {
                if (typeof device.getPortAt == "function") {
                    port = device.getPortAt(i);
                }
            } catch (error) {
                port = undefined;
            }

            if (port) {
                info.ports.push({
                    name: callIfExists(port, "getName"),
                    ip: callIfExists(port, "getIpAddress"),
                    subnetMask: callIfExists(port, "getSubnetMask"),
                    status: callIfExists(port, "getStatus"),
                    isPortUp: callIfExists(port, "isPortUp"),
                    isProtocolUp: callIfExists(port, "isProtocolUp"),
                    isPowerOn: callIfExists(port, "isPowerOn"),
                    remotePortName: callIfExists(port, "getRemotePortName")
                });
            }
        }
    }

    return info;
}

function getPcNetworkConfig(deviceName) {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
        return null;
    }
    var port = device.getPort("FastEthernet0");
    if (!port) {
        return null;
    }
    return {
        name: deviceName,
        dhcp: callIfExists(device, "getDhcpFlag"),
        ip: callIfExists(port, "getIpAddress"),
        subnetMask: callIfExists(port, "getSubnetMask"),
        portUp: callIfExists(port, "isPortUp"),
        protocolUp: callIfExists(port, "isProtocolUp"),
        powerOn: callIfExists(port, "isPowerOn"),
        remotePortName: callIfExists(port, "getRemotePortName")
    };
}

function getNetworkInfo() {
    var deviceCount = ipc.network().getDeviceCount();
    var devices = [];
    for (var i = 0; i < deviceCount; i++) {
        var device = ipc.network().getDeviceAt(i);
        if (device) {
            devices.push(getDeviceInfo(device.getName()));
        }
    }
    return {
        deviceCount: deviceCount,
        devices: devices,
        links: getLinksInfo()
    };
}

function runIosCommands(deviceName, commands) {
    configureIosDevice(deviceName, commands);
    return getDeviceInfo(deviceName);
}

sendSimplePdu = function(sourceDeviceName, destinationDeviceName) {
    try {
        var pduTool = ipc.appWindow().getUserCreatedPDU();
        if (pduTool && typeof pduTool.addSimplePdu == "function") {
            var value = pduTool.addSimplePdu(sourceDeviceName, destinationDeviceName);
            return {
                ok: true,
                method: "UserCreatedPDU.addSimplePdu",
                value: value === undefined ? null : String(value)
            };
        }
    } catch (error) {
        return {
            ok: false,
            error: String(error && error.message ? error.message : error)
        };
    }

    return {
        ok: false,
        error: "This Packet Tracer scripting API does not expose UserCreatedPDU.addSimplePdu."
    };
}

function getLinksInfo() {
    var count = ipc.network().getLinkCount();
    var links = [];

    function portEndpoint(port) {
        if (!port) {
            return null;
        }
        var owner = undefined;
        try {
            owner = port.getOwnerDevice();
        } catch (error) {
            owner = undefined;
        }
        return {
            device: owner && typeof owner.getName == "function" ? owner.getName() : null,
            port: callIfExists(port, "getName"),
            uuid: callIfExists(port, "getObjectUuid"),
            ip: callIfExists(port, "getIpAddress"),
            subnetMask: callIfExists(port, "getSubnetMask"),
            isPortUp: callIfExists(port, "isPortUp"),
            isProtocolUp: callIfExists(port, "isProtocolUp")
        };
    }

    for (var i = 0; i < count; i++) {
        var link = ipc.network().getLinkAt(i);
        var item = {
            index: i,
            uuid: callIfExists(link, "getObjectUuid"),
            connectionType: callIfExists(link, "getConnectionType"),
            endpoints: [],
            receivers: []
        };
        try {
            if (typeof link.getPort1 == "function") {
                item.endpoints.push(portEndpoint(link.getPort1()));
            }
            if (typeof link.getPort2 == "function") {
                item.endpoints.push(portEndpoint(link.getPort2()));
            }
        } catch (error) {
            item.endpointError = String(error && error.message ? error.message : error);
        }
        var receiverCount = callIfExists(link, "getReceiverCount");
        if (typeof receiverCount == "number") {
            for (var r = 0; r < receiverCount; r++) {
                var receiver = undefined;
                try {
                    receiver = link.getReceiverAt(r);
                } catch (error) {
                    receiver = undefined;
                }
                if (receiver) {
                    item.receivers.push(String(receiver));
                }
            }
        }
        links.push(item);
    }
    return links;
}

ptbError = function(error) {
    return {
        ok: false,
        error: {
            message: String(error && error.message ? error.message : error),
            name: error && error.name ? String(error.name) : "Error"
        }
    };
}

ptbOk = function(value) {
    return {
        ok: true,
        value: value === undefined ? null : value
    };
}

ptbEnsureDevice = function(args) {
    var name = args.name;
    var existing = ipc.network().getDevice(name);
    if (existing) {
        if (args.relayout || args.moveExisting) {
            return moveDeviceTo(name, args.x || 100, args.y || 100, true);
        }
        return {
            name: name,
            created: false,
            device: getDeviceInfo(name)
        };
    }

    var ok = addDevice(name, args.model, args.x || 100, args.y || 100);
    return {
        name: name,
        created: !!ok,
        device: ok ? getDeviceInfo(name) : null
    };
}

ptbMoveDevice = function(args) {
    return moveDeviceTo(args.name, args.x || 100, args.y || 100, args.centered !== false);
}

ptbConfigurePc = function(args) {
    configurePcIp(
        args.name,
        !!args.dhcp,
        args.ip || undefined,
        args.mask || args.subnetMask || undefined,
        args.gateway || undefined,
        args.dns || undefined
    );
    return getDeviceInfo(args.name);
}

ptbConfigureIos = function(args) {
    configureIosDevice(args.name, args.commands || "");
    return getDeviceInfo(args.name);
}

ptbPortLinkSummary = function(deviceName, portName) {
    var device = ipc.network().getDevice(deviceName);
    if (!device) {
        return null;
    }
    var port = device.getPort(portName);
    if (!port) {
        return null;
    }
    var link = undefined;
    try {
        link = port.getLink();
    } catch (error) {
        link = undefined;
    }
    return {
        device: deviceName,
        port: portName,
        hasLink: !!link,
        remotePortName: callIfExists(port, "getRemotePortName"),
        isPortUp: callIfExists(port, "isPortUp"),
        isProtocolUp: callIfExists(port, "isProtocolUp")
    };
}

ptbEnsureLink = function(args) {
    var left = ptbPortLinkSummary(args.device1, args.interface1);
    if (!left) {
        return {
            ok: false,
            reason: "missing-left-port",
            device: args.device1,
            port: args.interface1
        };
    }

    if (left.hasLink) {
        return {
            ok: true,
            existing: true,
            left: left,
            right: ptbPortLinkSummary(args.device2, args.interface2)
        };
    }

    var ok = addLink(args.device1, args.interface1, args.device2, args.interface2, args.linkType || "straight");
    return {
        ok: !!ok,
        created: !!ok,
        left: ptbPortLinkSummary(args.device1, args.interface1),
        right: ptbPortLinkSummary(args.device2, args.interface2)
    };
}

ptbDeleteLink = function(args) {
    var device = ipc.network().getDevice(args.device);
    if (!device) {
        return {
            ok: false,
            reason: "missing-device",
            device: args.device
        };
    }
    var port = device.getPort(args.port);
    if (!port) {
        return {
            ok: false,
            reason: "missing-port",
            device: args.device,
            port: args.port
        };
    }
    var before = ptbPortLinkSummary(args.device, args.port);
    if (!before || !before.hasLink) {
        return {
            ok: true,
            deleted: false,
            before: before,
            after: before
        };
    }
    try {
        if (typeof port.deleteLink == "function") {
            port.deleteLink();
        } else {
            return {
                ok: false,
                reason: "deleteLink-unavailable",
                before: before
            };
        }
    } catch (error) {
        return {
            ok: false,
            reason: "deleteLink-failed",
            error: String(error && error.message ? error.message : error),
            before: before
        };
    }
    return {
        ok: true,
        deleted: true,
        before: before,
        after: ptbPortLinkSummary(args.device, args.port)
    };
}

ptbDeleteDevice = function(args) {
    var name = args.name;
    var device = ipc.network().getDevice(name);
    if (!device) {
        return {
            ok: true,
            deleted: false,
            reason: "missing-device",
            name: name
        };
    }
    var before = getDeviceInfo(name);
    try {
        var workspace = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
        if (typeof workspace.removeDevice != "function") {
            return {
                ok: false,
                reason: "removeDevice-unavailable",
                name: name,
                before: before
            };
        }
        workspace.removeDevice(name);
    } catch (error) {
        return {
            ok: false,
            reason: "removeDevice-failed",
            name: name,
            error: String(error && error.message ? error.message : error),
            before: before
        };
    }
    return {
        ok: true,
        deleted: !ipc.network().getDevice(name),
        name: name,
        before: before
    };
}

ptbInspectDevice = function(args) {
    return getDeviceInfo(args.name);
}

ptbListDevices = function(args) {
    return getDevices(args && args.filter, args && args.startsWith);
}

ptbSendPdu = function(args) {
    return sendSimplePdu(args.source, args.destination);
}

ptbDispatch = function(request) {
    try {
        if (!request || !request.op) {
            return ptbError("Missing operation");
        }

        var args = request.args || {};
        var handlers = {
            ensureDevice: ptbEnsureDevice,
            configurePc: ptbConfigurePc,
            configureIos: ptbConfigureIos,
            ensureLink: ptbEnsureLink,
            deleteLink: ptbDeleteLink,
            deleteDevice: ptbDeleteDevice,
            inspectDevice: ptbInspectDevice,
            listDevices: ptbListDevices,
            getNetwork: function () { return getNetworkInfo(); },
            sendPdu: ptbSendPdu,
            moveDevice: ptbMoveDevice
        };

        var handler = handlers[request.op];
        if (!handler) {
            return ptbError("Unknown operation: " + request.op);
        }

        return ptbOk(handler(args));
    } catch (error) {
        return ptbError(error);
    }
}
