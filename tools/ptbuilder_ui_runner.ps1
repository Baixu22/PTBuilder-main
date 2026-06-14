param(
    [Parameter(Mandatory = $true)]
    [string]$CodePath,

    [int]$TimeoutSeconds = 20
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class NativeUi {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
}
"@

function Click-Point([int]$x, [int]$y) {
    [NativeUi]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 80
    [NativeUi]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    [NativeUi]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$root = [System.Windows.Automation.AutomationElement]::RootElement
$window = $null

while ((Get-Date) -lt $deadline -and -not $window) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty,
        "Builder Code Editor"
    )
    $window = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
    if (-not $window) {
        Start-Sleep -Milliseconds 300
    }
}

if (-not $window) {
    Write-Error "Builder Code Editor window was not found. Open Extensions > Builder Code Editor in Packet Tracer."
    exit 2
}

try {
    $window.SetFocus()
} catch {
    # Qt WebView title elements may not accept UIAutomation focus.
}
Start-Sleep -Milliseconds 200

$editCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Edit
)
$editor = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $editCondition)

$code = Get-Content -LiteralPath $CodePath -Raw
[System.Windows.Forms.Clipboard]::SetText($code)

if ($editor) {
    $editor.SetFocus()
    Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds 200
}

$runCondition = New-Object System.Windows.Automation.AndCondition(
    (New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button
    )),
    (New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty,
        "Run"
    ))
)
$runButton = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $runCondition)

if ($editor -and $runButton) {
    $invoke = $runButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invoke.Invoke()
    $method = "ui-automation"
} else {
    $handle = [NativeUi]::FindWindow($null, "Builder Code Editor")
    if ($handle -eq [IntPtr]::Zero) {
        Write-Error "Builder Code Editor native window was not found."
        exit 4
    }

    [NativeUi]::SetForegroundWindow($handle) | Out-Null
    Start-Sleep -Milliseconds 250
    $rect = New-Object NativeUi+RECT
    [NativeUi]::GetWindowRect($handle, [ref]$rect) | Out-Null

    # The editor fills the window below the toolbar in the legacy Builder UI.
    Click-Point ($rect.Left + 180) ($rect.Top + 155)
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 100
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds 250

    # Run is the first toolbar button near the top-left corner.
    Click-Point ($rect.Left + 40) ($rect.Top + 77)
    $method = "win32-coordinate"
}

Write-Output (@{
    ok = $true
    method = $method
    window = "Builder Code Editor"
} | ConvertTo-Json -Compress)
