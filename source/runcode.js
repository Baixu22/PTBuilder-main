function serializeError(error) {
    return {
        name: error && error.name ? String(error.name) : "Error",
        message: error && error.message ? String(error.message) : String(error),
        lineNumber: error && error.lineNumber ? error.lineNumber : undefined,
        stack: error && error.stack ? String(error.stack) : undefined
    };
}

function runCode(scriptText, options) {
    options = options || {};
    try {
        const codeFunction = new Function(scriptText);
        try {
            const value = codeFunction();
            return {
                ok: true,
                value: value === undefined ? null : value
            };
        } catch (error) {
            console.log(error);
            if (!options.silent) {
                ipc.appWindow().showMessageBox("Builder" + " ".repeat(100), "An error occurred on line " + error.lineNumber + ":", error, 3, 0x00000400, 0x00000400, 0x00000400);
            }
            return {
                ok: false,
                error: serializeError(error)
            };
        }
    } catch (error) {
        console.log(error);
        if (!options.silent) {
            ipc.appWindow().showMessageBox("Builder" + " ".repeat(100), "An error occurred:", error, 3, 0x00000400, 0x00000400, 0x00000400);
        }
        return {
            ok: false,
            error: serializeError(error)
        };
    }
}
