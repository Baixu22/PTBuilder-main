function keysOf(target) {
    var keys = [];
    try {
        for (var key in target) {
            keys.push(String(key));
        }
    } catch (error) {
        keys.push("ERROR:" + String(error));
    }
    keys.sort();
    return keys;
}

var workspace = ipc.appWindow().getActiveWorkspace();
var logical = workspace.getLogicalWorkspace();
var network = ipc.network();
var pc = network.getDevice("PTB_TEST_PC1") || network.getDevice("BUS_PC01");

return {
    ipc: keysOf(ipc),
    appWindow: keysOf(ipc.appWindow()),
    workspace: keysOf(workspace),
    logicalWorkspace: keysOf(logical),
    network: keysOf(network),
    device: pc ? keysOf(pc) : []
};
