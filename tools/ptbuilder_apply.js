var spec = __PTB_SPEC__;
var report = {
    createdDevices: [],
    configuredPcs: [],
    configuredIos: [],
    links: [],
    errors: []
};

function ensureDevice(device) {
    if (getDevices().indexOf(device.name) >= 0) {
        return;
    }
    var ok = addDevice(device.name, device.model, device.x || 100, device.y || 100);
    if (ok) {
        report.createdDevices.push(device.name);
    } else {
        report.errors.push("Failed to add device " + device.name);
    }
}

function ensureLink(link) {
    var left = ipc.network().getDevice(link[0]);
    if (left) {
        var leftPort = left.getPort(link[1]);
        if (leftPort && leftPort.getLink && leftPort.getLink()) {
            report.links.push({
                from: link[0] + " " + link[1],
                to: link[2] + " " + link[3],
                type: link[4] || "straight",
                ok: true,
                existing: true
            });
            return;
        }
    }
    var ok = addLink(link[0], link[1], link[2], link[3], link[4] || "straight");
    report.links.push({
        from: link[0] + " " + link[1],
        to: link[2] + " " + link[3],
        type: link[4] || "straight",
        ok: !!ok
    });
}

function configurePc(name, config) {
    configurePcIp(
        name,
        !!config.dhcp,
        config.ip || undefined,
        config.mask || config.subnetMask || undefined,
        config.gateway || undefined,
        config.dns || undefined
    );
    report.configuredPcs.push(name);
}

function configureIos(name, commands) {
    configureIosDevice(name, commands);
    report.configuredIos.push(name);
}

if (spec.devices) {
    for (var i = 0; i < spec.devices.length; i++) {
        ensureDevice(spec.devices[i]);
    }
}

if (spec.pcConfigs) {
    for (var pcName in spec.pcConfigs) {
        configurePc(pcName, spec.pcConfigs[pcName]);
    }
}

if (spec.iosConfigs) {
    for (var iosName in spec.iosConfigs) {
        configureIos(iosName, spec.iosConfigs[iosName]);
    }
}

if (spec.links) {
    for (var l = 0; l < spec.links.length; l++) {
        ensureLink(spec.links[l]);
    }
}

report.network = getNetworkInfo();
report.targetDevices = [];
if (spec.devices) {
    for (var d = 0; d < spec.devices.length; d++) {
        var info = getDeviceInfo(spec.devices[d].name);
        var compact = {
            name: info.name,
            model: info.model,
            type: info.type,
            power: info.power
        };
        if (info.type == 8 || info.type == 18 || info.type == 9) {
            for (var p = 0; p < info.ports.length; p++) {
                if (info.ports[p].name == "FastEthernet0") {
                    compact.pc = info.ports[p];
                }
            }
        }
        report.targetDevices.push(compact);
    }
}
delete report.network;
return report;
