function keysOf(target) {
    var keys = [];
    try {
        for (var key in target) keys.push(String(key));
    } catch (error) {
        keys.push("ERROR:" + String(error));
    }
    keys.sort();
    return keys;
}

var network = ipc.network();
var link = network.getLinkCount() > 0 ? network.getLinkAt(0) : null;
var pc = network.getDevice("PTB_TEST_PC1") || network.getDevice("BUS_PC01");
var port = pc ? pc.getPort("FastEthernet0") : null;
var pdu = null;
try {
    pdu = ipc.appWindow().getUserCreatedPDU();
} catch (error) {
    pdu = null;
}

return {
    simulation: keysOf(ipc.simulation()),
    pduListWindow: keysOf(ipc.appWindow().getPDUListWindow()),
    userCreatedPdu: pdu ? keysOf(pdu) : [],
    link: link ? keysOf(link) : [],
    port: port ? keysOf(port) : []
};
