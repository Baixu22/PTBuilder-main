function copyToClipboard() {
    var currentCode = document.getElementById("codeeditor").value;
    navigator.clipboard.writeText(currentCode);
}

function pasteFromClipboard() {
    navigator.clipboard.readText()
    .then((clipboardText) => {
        document.getElementById("codeeditor").value = clipboardText;
    })
}

function executeCode() {
    var code = document.getElementById("codeeditor").value;
    $se("runCode", code).then((result) => {
        setStatus(result && result.ok ? "Run complete" : "Run failed", result && result.ok ? "ok" : "bad");
        setResult(result);
    }).catch((error) => {
        setStatus("Run failed", "bad");
        setResult({ ok: false, error: String(error) });
    })
}

function clearEditor() {
    document.getElementById("codeeditor").value = "";
    setResult("Ready");
}

function saveCode() {
    let code = document.getElementById("codeeditor").value;
    $putData("code", code);
}

function loadCode() {
    $getData("code").then((code) => {
        document.getElementById("codeeditor").value = code;
    })
}

var bridgeUrl = "http://127.0.0.1:54321";
var bridgeEnabled = true;
var bridgeBusy = false;
var bridgeTaskCount = 0;
var lastStatus = "";

function setStatus(message, level) {
    lastStatus = message || "";
    var status = document.getElementById("status");
    if (status) {
        status.className = "status-pill " + (level || "");
        status.textContent = message || "";
    }
}

function setResult(result) {
    var panel = document.getElementById("resultPanel");
    if (!panel) {
        return;
    }
    if (typeof result == "string") {
        panel.textContent = result;
        return;
    }
    try {
        panel.textContent = JSON.stringify(result, null, 2);
    } catch (error) {
        panel.textContent = String(result);
    }
}

function setTaskCount() {
    var taskCount = document.getElementById("taskCount");
    if (taskCount) {
        taskCount.textContent = bridgeTaskCount + (bridgeTaskCount == 1 ? " task" : " tasks");
    }
}

function toggleBridge() {
    bridgeEnabled = document.getElementById("bridgeEnabled").checked;
    setStatus(bridgeEnabled ? "Bridge enabled" : "Bridge paused", bridgeEnabled ? "ok" : "warn");
}

async function pollBridge() {
    if (!bridgeEnabled || bridgeBusy) {
        return;
    }

    bridgeBusy = true;
    try {
        var response = await fetch(bridgeUrl + "/next", { cache: "no-store" });
        if (response.status == 204) {
            if (lastStatus != "Bridge connected") {
                setStatus("Bridge connected", "ok");
            }
            return;
        }
        if (!response.ok) {
            setStatus("Bridge waiting", "warn");
            return;
        }

        var task = await response.json();
        if (!task || !task.id || !task.code) {
            return;
        }

        bridgeTaskCount++;
        setTaskCount();
        setStatus("Running task", "warn");
        setResult({ task: task.id, state: "running" });
        var result = await $se("runCode", task.code, { silent: true });
        var payload = result || { ok: false, error: { message: "No result returned from Packet Tracer" } };
        await fetch(bridgeUrl + "/result", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                id: task.id,
                result: payload
            })
        });
        setStatus(payload && payload.ok ? "Task complete" : "Task failed", payload && payload.ok ? "ok" : "bad");
        setResult({ task: task.id, result: payload });
    } catch (error) {
        setStatus("Bridge offline", "bad");
    } finally {
        bridgeBusy = false;
    }
}

function startBridgePolling() {
    setTaskCount();
    setInterval(pollBridge, 300);
    pollBridge();
}
