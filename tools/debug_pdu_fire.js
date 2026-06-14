var pduTool = ipc.appWindow().getUserCreatedPDU();
var results = [];

function attempt(name, fn) {
    try {
        var value = fn();
        results.push({ name: name, ok: true, value: value === undefined ? null : String(value) });
    } catch (error) {
        results.push({ name: name, ok: false, error: String(error && error.message ? error.message : error) });
    }
}

attempt("setSimulationMode", function() { return ipc.simulation().setSimulationMode(); });
attempt("activateScenario", function() { return pduTool.activateScenario(); });
attempt("addSimplePdu", function() { return pduTool.addSimplePdu("PTB_TEST_PC1", "PTB_TEST_PC2"); });
attempt("firePDU-number", function() { return pduTool.firePDU(0); });
attempt("firePDU-string", function() { return pduTool.firePDU("0"); });
attempt("forward", function() { return ipc.simulation().forward(); });

return {
    results: results,
    frameCount: ipc.simulation().getFrameInstanceCount(),
    current: ipc.simulation().getCurrentFrameInstanceIndex()
};
