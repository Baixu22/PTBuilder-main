ptbError = function(error) {
    return {
        ok: false,
        error: {
            message: String(error && error.message ? error.message : error),
            name: error && error.name ? String(error.name) : "Error"
        }
    };
};

ptbOk = function(value) {
    return {
        ok: true,
        value: value === undefined ? null : value
    };
};

ptbEnsureDevice = function(args) {
    var name = args.name;
    var existing = ipc.network().getDevice(name);
    if (existing) {
        return { name: name, created: false, device: getDeviceInfo(name) };
    }
    var ok = addDevice(name, args.model, args.x || 100, args.y || 100);
    return { name: name, created: !!ok, device: ok ? getDeviceInfo(name) : null };
};

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
};

ptbConfigureIos = function(args) {
    configureIosDevice(args.name, args.commands || "");
    return getDeviceInfo(args.name);
};

ptbPortLinkSummary = function(deviceName, portName) {
    var device = ipc.network().getDevice(deviceName);
    if (!device) return null;
    var port = device.getPort(portName);
    if (!port) return null;
    var link = undefined;
    try { link = port.getLink(); } catch (error) { link = undefined; }
    return {
        device: deviceName,
        port: portName,
        hasLink: !!link,
        remotePortName: callIfExists(port, "getRemotePortName"),
        isPortUp: callIfExists(port, "isPortUp"),
        isProtocolUp: callIfExists(port, "isProtocolUp")
    };
};

ptbEnsureLink = function(args) {
    var left = ptbPortLinkSummary(args.device1, args.interface1);
    if (!left) {
        return { ok: false, reason: "missing-left-port", device: args.device1, port: args.interface1 };
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
};

ptbDispatch = function(request) {
    try {
        if (!request || !request.op) return ptbError("Missing operation");
        var args = request.args || {};
        var handlers = {
            ensureDevice: ptbEnsureDevice,
            configurePc: ptbConfigurePc,
            configureIos: ptbConfigureIos,
            ensureLink: ptbEnsureLink,
            inspectDevice: function(args) { return getDeviceInfo(args.name); },
            listDevices: function(args) { return getDevices(args && args.filter, args && args.startsWith); },
            getNetwork: function() { return getNetworkInfo(); },
            sendPdu: function(args) { return sendSimplePdu(args.source, args.destination); }
        };
        var handler = handlers[request.op];
        if (!handler) return ptbError("Unknown operation: " + request.op);
        return ptbOk(handler(args));
    } catch (error) {
        return ptbError(error);
    }
};

return { ok: true, dispatch: typeof ptbDispatch };
