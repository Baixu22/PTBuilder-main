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
};

return {
    ok: true,
    sendSimplePdu: typeof sendSimplePdu
};
