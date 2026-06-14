param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("OpenDevice", "ClickDeviceTab", "ClickDeviceControl", "ConfigureServerFtp")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$DeviceName,

    [Nullable[double]]$LogicalX = $null,

    [Nullable[double]]$LogicalY = $null,

    [int]$TimeoutSeconds = 20,

    [string]$TabName = "",

    [string]$ControlName = "",

    [ValidateSet("", "Button", "CheckBox", "RadioButton", "TabItem", "Edit")]
    [string]$ControlTypeName = "",

    [ValidateSet("true", "false", "1", "0", "yes", "no", "on", "off")]
    [string]$Enabled = "true",

    [string]$Username = "",

    [string]$Password = "",

    [string]$Permission = "RWDNL"
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class NativeDeviceUi {
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
}
"@

function Get-SafeValue([scriptblock]$Block, $Fallback = $null) {
    try {
        return & $Block
    } catch {
        return $Fallback
    }
}

function Convert-Rect($Rect) {
    if (-not $Rect) {
        return $null
    }
    return @{
        x = [int]$Rect.X
        y = [int]$Rect.Y
        width = [int]$Rect.Width
        height = [int]$Rect.Height
    }
}

function Convert-ControlType($ControlType) {
    if (-not $ControlType) {
        return $null
    }
    $programmatic = Get-SafeValue { $ControlType.ProgrammaticName } ""
    if ($programmatic.StartsWith("ControlType.")) {
        return $programmatic.Substring("ControlType.".Length)
    }
    return $programmatic
}

function Convert-BooleanArgument([string]$Value) {
    $text = ""
    if ($null -ne $Value) {
        $text = $Value.ToString().Trim().ToLowerInvariant()
    }
    return $text -in @("true", "1", "yes", "on")
}

function Convert-ElementBrief($Element) {
    if (-not $Element) {
        return $null
    }
    return @{
        name = Get-SafeValue { $Element.Current.Name } ""
        automationId = Get-SafeValue { $Element.Current.AutomationId } ""
        className = Get-SafeValue { $Element.Current.ClassName } ""
        controlType = Convert-ControlType (Get-SafeValue { $Element.Current.ControlType } $null)
        processId = Get-SafeValue { $Element.Current.ProcessId } $null
        bounds = Convert-Rect (Get-SafeValue { $Element.Current.BoundingRectangle } $null)
    }
}

function Get-PacketTracerPids() {
    try {
        return @(Get-Process PacketTracer -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    } catch {
        return @()
    }
}

function Find-PacketTracerMainWindow([int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $packetTracerPids = Get-PacketTracerPids

    while ((Get-Date) -lt $deadline) {
        $windows = Get-SafeValue {
            $root.FindAll(
                [System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.Condition]::TrueCondition
            )
        } $null

        if ($windows) {
            foreach ($window in $windows) {
                $name = Get-SafeValue { $window.Current.Name } ""
                $processId = Get-SafeValue { $window.Current.ProcessId } $null
                $className = Get-SafeValue { $window.Current.ClassName } ""
                if (
                    ($packetTracerPids -contains $processId) -and
                    ($name.IndexOf("Packet Tracer", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) -and
                    ($className -eq "CAppWindow")
                ) {
                    return $window
                }
            }
        }
        Start-Sleep -Milliseconds 250
    }
    return $null
}

function Find-DescendantByAutomationId($Root, [string]$AutomationId) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
        $AutomationId
    )
    return Get-SafeValue {
        $Root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
    } $null
}

function Find-DescendantByAutomationIdContains($Root, [string]$AutomationIdPart) {
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::IsEnabledProperty,
        $true
    )
    $items = Get-SafeValue {
        $Root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
    } $null
    if (-not $items) {
        return $null
    }
    foreach ($item in $items) {
        $automationId = Get-SafeValue { $item.Current.AutomationId } ""
        if ($automationId.IndexOf($AutomationIdPart, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $item
        }
    }
    return $null
}

function Find-DescendantByNameContains($Root, [string]$NamePart) {
    $items = Get-SafeValue {
        $Root.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        )
    } $null
    if (-not $items) {
        return $null
    }

    $preferred = $null
    $fallback = $null
    foreach ($item in $items) {
        $name = Get-SafeValue { $item.Current.Name } ""
        if ($name.IndexOf($NamePart, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            continue
        }
        $rect = Get-SafeValue { $item.Current.BoundingRectangle } $null
        if (-not $rect -or $rect.Width -le 0 -or $rect.Height -le 0) {
            continue
        }
        if ($name -eq $NamePart) {
            return $item
        }
        if ($name.IndexOf(("Device " + $NamePart), [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            if (-not $preferred) {
                $preferred = $item
            }
            continue
        }
        if (-not $fallback) {
            $fallback = $item
        }
    }
    if ($preferred) {
        return $preferred
    }
    return $fallback
}

function Find-DescendantByNameAndControlType($Root, [string]$Name, $ControlType) {
    $condition = New-Object System.Windows.Automation.AndCondition(
        (New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::NameProperty,
            $Name
        )),
        (New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            $ControlType
        ))
    )
    return Get-SafeValue {
        $Root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
    } $null
}

function Find-DescendantByNamesAndControlType($Root, [string[]]$Names, $ControlType) {
    foreach ($name in $Names) {
        if (-not $name) {
            continue
        }
        $element = Find-DescendantByNameAndControlType $Root $name $ControlType
        if ($element) {
            return $element
        }
    }
    return $null
}

function Get-ServicesTabNameCandidates() {
    return @(
        "Services",
        [string]::Concat([char]0x670D, [char]0x52A1),
        [string]::Concat([char]0x670D, [char]0x52A1, [char]0x5668)
    )
}

function Resolve-ControlType([string]$ControlTypeName) {
    if (-not $ControlTypeName) {
        return $null
    }
    switch ($ControlTypeName) {
        "Button" { return [System.Windows.Automation.ControlType]::Button }
        "CheckBox" { return [System.Windows.Automation.ControlType]::CheckBox }
        "RadioButton" { return [System.Windows.Automation.ControlType]::RadioButton }
        "TabItem" { return [System.Windows.Automation.ControlType]::TabItem }
        "Edit" { return [System.Windows.Automation.ControlType]::Edit }
    }
    return $null
}

function Find-DescendantByName($Root, [string]$Name, [string]$ControlTypeName = "") {
    $controlType = Resolve-ControlType $ControlTypeName
    if ($controlType) {
        return Find-DescendantByNameAndControlType $Root $Name $controlType
    }
    $condition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty,
        $Name
    )
    return Get-SafeValue {
        $Root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
    } $null
}

function Find-DeviceWindow([string]$DeviceName, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $packetTracerPids = Get-PacketTracerPids

    while ((Get-Date) -lt $deadline) {
        $windows = Get-SafeValue {
            $root.FindAll(
                [System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.Condition]::TrueCondition
            )
        } $null

        if ($windows) {
            foreach ($window in $windows) {
                $name = Get-SafeValue { $window.Current.Name } ""
                $processId = Get-SafeValue { $window.Current.ProcessId } $null
                $className = Get-SafeValue { $window.Current.ClassName } ""
                if ($packetTracerPids -contains $processId) {
                    if ($name.IndexOf($DeviceName, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                        return $window
                    }
                    if ($className -eq "CServerDialog") {
                        $containedDevice = Find-DescendantByNameContains $window $DeviceName
                        if ($containedDevice) {
                            return $window
                        }
                    }
                }
            }
        }
        Start-Sleep -Milliseconds 250
    }
    return $null
}

function Click-Point([int]$X, [int]$Y, [int]$ClickCount = 1) {
    [NativeDeviceUi]::SetCursorPos($X, $Y) | Out-Null
    Start-Sleep -Milliseconds 80
    for ($i = 0; $i -lt $ClickCount; $i++) {
        [NativeDeviceUi]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
        [NativeDeviceUi]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 110
    }
}

function Click-Element($Element) {
    if (-not $Element) {
        return $false
    }
    $invokePattern = Get-SafeValue {
        $Element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    } $null
    if ($invokePattern) {
        $invokePattern.Invoke()
        Start-Sleep -Milliseconds 200
        return $true
    }
    $selectPattern = Get-SafeValue {
        $Element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
    } $null
    if ($selectPattern) {
        $selectPattern.Select()
        Start-Sleep -Milliseconds 200
        return $true
    }
    $rect = Get-SafeValue { $Element.Current.BoundingRectangle } $null
    if (-not $rect -or $rect.Width -le 0 -or $rect.Height -le 0) {
        return $false
    }
    Click-Point ([int]($rect.X + ($rect.Width / 2))) ([int]($rect.Y + ($rect.Height / 2))) 1
    return $true
}

function Set-ElementText($Element, [string]$Text) {
    if (-not $Element) {
        return $false
    }
    $valuePattern = Get-SafeValue {
        $Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    } $null
    if ($valuePattern -and -not $valuePattern.Current.IsReadOnly) {
        $valuePattern.SetValue($Text)
        Start-Sleep -Milliseconds 120
        return $true
    }

    try {
        $Element.SetFocus()
    } catch {
    }
    Start-Sleep -Milliseconds 80
    [System.Windows.Forms.Clipboard]::SetText($Text)
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 60
    [System.Windows.Forms.SendKeys]::SendWait("^v")
    Start-Sleep -Milliseconds 120
    return $true
}

function Get-ToggleStateName($Element) {
    $toggle = Get-SafeValue {
        $Element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
    } $null
    if (-not $toggle) {
        return $null
    }
    return [string]$toggle.Current.ToggleState
}

function Set-CheckBoxState($Element, [bool]$Desired) {
    if (-not $Element) {
        return $false
    }
    $toggle = Get-SafeValue {
        $Element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
    } $null
    if ($toggle) {
        $current = [string]$toggle.Current.ToggleState
        $isOn = $current -eq "On"
        if ($isOn -ne $Desired) {
            $toggle.Toggle()
            Start-Sleep -Milliseconds 100
        }
        return $true
    }
    Click-Element $Element | Out-Null
    return $true
}

function Find-DataItemByName($Root, [string]$Name) {
    return Find-DescendantByNameAndControlType $Root $Name ([System.Windows.Automation.ControlType]::DataItem)
}

function Normalize-FtpPermission([string]$Value) {
    $text = ($Value -replace "[^A-Za-z]", "").ToUpperInvariant()
    if (-not $text) {
        $text = "RWDNL"
    }
    if ($text -in @("ALL", "FULL")) {
        $text = "RWDNL"
    }
    if ($text -eq "RW") {
        $text = "RWL"
    }
    if ($text -eq "R") {
        $text = "RL"
    }
    return @{
        raw = $text
        write = $text.Contains("W")
        read = $text.Contains("R")
        delete = $text.Contains("D")
        rename = $text.Contains("N")
        list = $text.Contains("L") -or $text.Contains("R") -or $text.Contains("W")
    }
}

function Open-DeviceWindow {
    $mainWindow = Find-PacketTracerMainWindow $TimeoutSeconds
    if (-not $mainWindow) {
        return @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "OpenDevice"
            device = $DeviceName
            error = "packet-tracer-main-window-not-found"
        }
    }

    try {
        $mainWindow.SetFocus()
    } catch {
    }
    $nativeHandle = Get-SafeValue { $mainWindow.Current.NativeWindowHandle } 0
    if ($nativeHandle) {
        $handle = [IntPtr]$nativeHandle
        [NativeDeviceUi]::ShowWindow($handle, 9) | Out-Null
        [NativeDeviceUi]::SetForegroundWindow($handle) | Out-Null
        Start-Sleep -Milliseconds 350
    }

    $workspace = Find-DescendantByAutomationId $mainWindow "CAppWindowBase.centralwidget.m_pWorkSpaceWnd"
    if (-not $workspace) {
        return @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "OpenDevice"
            device = $DeviceName
            error = "logical-workspace-not-found"
            mainWindow = Convert-ElementBrief $mainWindow
        }
    }

    $workspaceRect = Get-SafeValue { $workspace.Current.BoundingRectangle } $null
    if (-not $workspaceRect -or $workspaceRect.Width -le 0 -or $workspaceRect.Height -le 0) {
        return @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "OpenDevice"
            device = $DeviceName
            error = "logical-workspace-has-invalid-bounds"
            workspace = Convert-ElementBrief $workspace
        }
    }

    $workspaceDevice = Find-DescendantByNameContains $workspace $DeviceName
    if ($workspaceDevice) {
        $deviceRect = Get-SafeValue { $workspaceDevice.Current.BoundingRectangle } $null
        if ($deviceRect -and $deviceRect.Width -gt 0 -and $deviceRect.Height -gt 0) {
            $deviceScreenX = [int][Math]::Round($deviceRect.X + ($deviceRect.Width / 2))
            $deviceScreenY = [int][Math]::Round($deviceRect.Y + ($deviceRect.Height / 2))
            Click-Point $deviceScreenX $deviceScreenY 2
            $deviceWindowByElement = Find-DeviceWindow $DeviceName ([Math]::Max(3, [Math]::Min($TimeoutSeconds, 10)))
            if ($deviceWindowByElement) {
                return @{
                    ok = $true
                    schema = "ptbuilder.ui.device.v1"
                    action = "OpenDevice"
                    device = $DeviceName
                    method = "workspace-device-element-double-click"
                    logical = @{ x = $LogicalX; y = $LogicalY }
                    screen = @{ x = $deviceScreenX; y = $deviceScreenY }
                    workspace = Convert-ElementBrief $workspace
                    target = Convert-ElementBrief $workspaceDevice
                    window = Convert-ElementBrief $deviceWindowByElement
                    error = $null
                }
            }
        }
    }

    if ($null -eq $LogicalX -or $null -eq $LogicalY) {
        return @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "OpenDevice"
            device = $DeviceName
            method = "workspace-device-element-lookup"
            workspace = Convert-ElementBrief $workspace
            error = "missing-logical-coordinate"
        }
    }

    $screenX = [int][Math]::Round($workspaceRect.X + [double]$LogicalX)
    $screenY = [int][Math]::Round($workspaceRect.Y + [double]$LogicalY)
    Click-Point $screenX $screenY 2

    $deviceWindow = Find-DeviceWindow $DeviceName ([Math]::Max(3, [Math]::Min($TimeoutSeconds, 10)))
    $opened = $null -ne $deviceWindow
    return @{
        ok = $opened
        schema = "ptbuilder.ui.device.v1"
        action = "OpenDevice"
        device = $DeviceName
        method = "workspace-coordinate-double-click"
        logical = @{ x = $LogicalX; y = $LogicalY }
        screen = @{ x = $screenX; y = $screenY }
        workspace = Convert-ElementBrief $workspace
        window = Convert-ElementBrief $deviceWindow
        error = $(if ($opened) { $null } else { "device-window-not-found-after-double-click" })
    }
}

function Configure-ServerFtp {
    $ftpEnabled = Convert-BooleanArgument $Enabled
    if (-not $Username -or -not $Password) {
        return @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "ConfigureServerFtp"
            device = $DeviceName
            service = "ftp"
            error = "missing-username-or-password"
        }
    }

    $openMethod = "existing-window"
    $deviceWindow = Find-DeviceWindow $DeviceName 2
    if (-not $deviceWindow) {
        $openResult = Open-DeviceWindow
        if (-not $openResult.ok) {
            $openResult.action = "ConfigureServerFtp"
            $openResult.service = "ftp"
            return $openResult
        }
        $openMethod = Get-SafeValue { $openResult["method"] } "unknown"
        $deviceWindow = Find-DeviceWindow $DeviceName ([Math]::Max(3, [Math]::Min($TimeoutSeconds, 10)))
    }
    if (-not $deviceWindow) {
        return @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "ConfigureServerFtp"
            device = $DeviceName
            service = "ftp"
            error = "device-window-not-found"
        }
    }

    try {
        $deviceWindow.SetFocus()
    } catch {
    }
    $nativeHandle = Get-SafeValue { $deviceWindow.Current.NativeWindowHandle } 0
    if ($nativeHandle) {
        $handle = [IntPtr]$nativeHandle
        [NativeDeviceUi]::ShowWindow($handle, 9) | Out-Null
        [NativeDeviceUi]::SetForegroundWindow($handle) | Out-Null
        Start-Sleep -Milliseconds 250
    }

    $steps = @()
    $servicesTab = Find-DescendantByNamesAndControlType $deviceWindow (Get-ServicesTabNameCandidates) ([System.Windows.Automation.ControlType]::TabItem)
    $steps += @{ step = "select-services-tab"; ok = [bool](Click-Element $servicesTab); element = Convert-ElementBrief $servicesTab }
    Start-Sleep -Milliseconds 250

    $ftpNav = Find-DescendantByName $deviceWindow "FTP" "CheckBox"
    $steps += @{ step = "select-ftp-service"; ok = [bool](Click-Element $ftpNav); element = Convert-ElementBrief $ftpNav }
    Start-Sleep -Milliseconds 350

    $ftpRoot = Find-DescendantByAutomationIdContains $deviceWindow "CServerServiceFtp"
    if (-not $ftpRoot) {
        return @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "ConfigureServerFtp"
            device = $DeviceName
            service = "ftp"
            error = "ftp-panel-not-found"
            steps = $steps
        }
    }

    $onRadio = Find-DescendantByAutomationIdContains $ftpRoot ".m_ftpOn"
    $offRadio = Find-DescendantByAutomationIdContains $ftpRoot ".m_ftpOff"
    $targetRadio = $(if ($ftpEnabled) { $onRadio } else { $offRadio })
    $steps += @{ step = $(if ($ftpEnabled) { "enable-ftp" } else { "disable-ftp" }); ok = [bool](Click-Element $targetRadio); element = Convert-ElementBrief $targetRadio }
    Start-Sleep -Milliseconds 200

    $userEdit = Find-DescendantByAutomationIdContains $ftpRoot ".m_userNameEdit"
    $passwordEdit = Find-DescendantByAutomationIdContains $ftpRoot ".m_passwordEdit"
    $steps += @{ step = "set-username"; ok = [bool](Set-ElementText $userEdit $Username); element = Convert-ElementBrief $userEdit }
    $steps += @{ step = "set-password"; ok = [bool](Set-ElementText $passwordEdit $Password); element = Convert-ElementBrief $passwordEdit }

    $permissionState = Normalize-FtpPermission $Permission
    $permissionTargets = @(
        @{ suffix = ".m_ftpWriteCheckBox"; key = "write"; label = "W" },
        @{ suffix = ".m_ftpReadCheckBox"; key = "read"; label = "R" },
        @{ suffix = ".m_ftpDeleteCheckBox"; key = "delete"; label = "D" },
        @{ suffix = ".m_ftpRenameCheckBox"; key = "rename"; label = "N" },
        @{ suffix = ".m_ftpListCheckBox"; key = "list"; label = "L" }
    )
    foreach ($target in $permissionTargets) {
        $element = Find-DescendantByAutomationIdContains $ftpRoot $target["suffix"]
        $desired = [bool]$permissionState[$target["key"]]
        $steps += @{
            step = "set-permission-" + $target["label"]
            ok = [bool](Set-CheckBoxState $element $desired)
            desired = $desired
            state = Get-ToggleStateName $element
            element = Convert-ElementBrief $element
        }
    }

    $addButton = Find-DescendantByAutomationIdContains $ftpRoot ".m_addBtn"
    $steps += @{ step = "add-user"; ok = [bool](Click-Element $addButton); element = Convert-ElementBrief $addButton }
    Start-Sleep -Milliseconds 500

    $verifiedUser = Find-DataItemByName $ftpRoot $Username
    $failedSteps = @($steps | Where-Object { -not $_.ok })
    $verified = $null -ne $verifiedUser
    return @{
        ok = ($failedSteps.Count -eq 0 -and $verified)
        schema = "ptbuilder.ui.device.v1"
        action = "ConfigureServerFtp"
        device = $DeviceName
        service = "ftp"
        enabled = $ftpEnabled
        username = $Username
        permission = $permissionState["raw"]
        recipe = "server-pt-ftp-ui-v1"
        verified = $verified
        openMethod = $openMethod
        user = Convert-ElementBrief $verifiedUser
        summary = @{
            steps = $steps.Count
            failed = $failedSteps.Count
            verified = $verified
        }
        steps = $steps
        error = $(if ($verified) { $null } else { "ftp-user-not-found-after-apply" })
    }
}

if ($Action -eq "OpenDevice") {
    Open-DeviceWindow | ConvertTo-Json -Depth 20
}

if ($Action -eq "ClickDeviceTab") {
    if (-not $TabName) {
        @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "ClickDeviceTab"
            device = $DeviceName
            error = "missing-tab-name"
        } | ConvertTo-Json -Depth 20
        exit 1
    }
    $deviceWindow = Find-DeviceWindow $DeviceName $TimeoutSeconds
    if (-not $deviceWindow) {
        @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "ClickDeviceTab"
            device = $DeviceName
            tab = $TabName
            error = "device-window-not-found"
        } | ConvertTo-Json -Depth 20
        exit 1
    }
    $tab = Find-DescendantByNameAndControlType $deviceWindow $TabName ([System.Windows.Automation.ControlType]::TabItem)
    $clicked = Click-Element $tab
    @{
        ok = [bool]$clicked
        schema = "ptbuilder.ui.device.v1"
        action = "ClickDeviceTab"
        device = $DeviceName
        tab = $TabName
        element = Convert-ElementBrief $tab
        error = $(if ($clicked) { $null } else { "tab-not-found-or-not-clickable" })
    } | ConvertTo-Json -Depth 20
}

if ($Action -eq "ClickDeviceControl") {
    if (-not $ControlName) {
        @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "ClickDeviceControl"
            device = $DeviceName
            error = "missing-control-name"
        } | ConvertTo-Json -Depth 20
        exit 1
    }
    $deviceWindow = Find-DeviceWindow $DeviceName $TimeoutSeconds
    if (-not $deviceWindow) {
        @{
            ok = $false
            schema = "ptbuilder.ui.device.v1"
            action = "ClickDeviceControl"
            device = $DeviceName
            control = $ControlName
            controlType = $ControlTypeName
            error = "device-window-not-found"
        } | ConvertTo-Json -Depth 20
        exit 1
    }
    $control = Find-DescendantByName $deviceWindow $ControlName $ControlTypeName
    $clicked = Click-Element $control
    @{
        ok = [bool]$clicked
        schema = "ptbuilder.ui.device.v1"
        action = "ClickDeviceControl"
        device = $DeviceName
        control = $ControlName
        controlType = $ControlTypeName
        element = Convert-ElementBrief $control
        error = $(if ($clicked) { $null } else { "control-not-found-or-not-clickable" })
    } | ConvertTo-Json -Depth 20
}

if ($Action -eq "ConfigureServerFtp") {
    Configure-ServerFtp | ConvertTo-Json -Depth 30
}
