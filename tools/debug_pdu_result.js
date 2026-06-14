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

var sim = ipc.simulation();
var before = sim.getFrameInstanceCount();
var pdu = sendSimplePdu("PTB_TEST_PC1", "PTB_TEST_PC2");
try {
    sim.setSimulationMode();
} catch (error) {}
var steps = [];
for (var i = 0; i < 8; i++) {
    try {
        steps.push(String(sim.forward()));
    } catch (error) {
        steps.push("ERROR:" + String(error && error.message ? error.message : error));
        break;
    }
}
var after = sim.getFrameInstanceCount();
var frame = after > 0 ? sim.getFrameInstanceAt(after - 1) : null;

return {
    pdu: pdu,
    before: before,
    after: after,
    current: sim.getCurrentFrameInstanceIndex(),
    steps: steps,
    frameKeys: frame ? keysOf(frame) : [],
    frameClass: frame ? frame.getClassName() : null
};
