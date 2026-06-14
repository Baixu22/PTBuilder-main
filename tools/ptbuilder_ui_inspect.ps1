param(
    [int]$TimeoutSeconds = 20,
    [string]$WindowName = "",
    [int]$MaxDepth = 4,
    [int]$MaxChildren = 250
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

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

function Convert-Element($Element, [int]$Depth, [ref]$Count) {
    $Count.Value += 1
    $item = @{
        name = Get-SafeValue { $Element.Current.Name } ""
        automationId = Get-SafeValue { $Element.Current.AutomationId } ""
        className = Get-SafeValue { $Element.Current.ClassName } ""
        controlType = Convert-ControlType (Get-SafeValue { $Element.Current.ControlType } $null)
        processId = Get-SafeValue { $Element.Current.ProcessId } $null
        enabled = Get-SafeValue { $Element.Current.IsEnabled } $null
        offscreen = Get-SafeValue { $Element.Current.IsOffscreen } $null
        bounds = Convert-Rect (Get-SafeValue { $Element.Current.BoundingRectangle } $null)
        children = @()
    }

    if ($Depth -ge $MaxDepth -or $Count.Value -ge $MaxChildren) {
        return $item
    }

    $children = Get-SafeValue {
        $Element.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            [System.Windows.Automation.Condition]::TrueCondition
        )
    } $null

    if ($children) {
        foreach ($child in $children) {
            if ($Count.Value -ge $MaxChildren) {
                break
            }
            $item.children += Convert-Element $child ($Depth + 1) $Count
        }
    }
    return $item
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$packetTracerPids = @()
try {
    $packetTracerPids = @(Get-Process PacketTracer -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
} catch {
    $packetTracerPids = @()
}

$matchedWindows = @()
$root = [System.Windows.Automation.AutomationElement]::RootElement

while ((Get-Date) -lt $deadline -and $matchedWindows.Count -eq 0) {
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
            $matchesName = $false
            if ($WindowName) {
                $matchesName = $name.IndexOf($WindowName, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
            } else {
                $matchesName = (
                    $name.IndexOf("Packet Tracer", [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $name.IndexOf("Builder Code Editor", [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $name.IndexOf("Cisco", [System.StringComparison]::OrdinalIgnoreCase) -ge 0
                )
            }
            $matchesProcess = $packetTracerPids -contains $processId
            if ($matchesName -or $matchesProcess) {
                $matchedWindows += $window
            }
        }
    }

    if ($matchedWindows.Count -eq 0) {
        Start-Sleep -Milliseconds 300
    }
}

if ($matchedWindows.Count -eq 0) {
    @{
        ok = $false
        schema = "ptbuilder.ui.inspect.v1"
        error = "packet-tracer-window-not-found"
        summary = @{
            windows = 0
            packetTracerPids = $packetTracerPids
        }
        windows = @()
    } | ConvertTo-Json -Depth 20
    exit 1
}

$controls = 0
$windowItems = @()
foreach ($window in $matchedWindows) {
    $count = [ref]0
    $windowItems += Convert-Element $window 0 $count
    $controls += $count.Value
}

@{
    ok = $true
    schema = "ptbuilder.ui.inspect.v1"
    summary = @{
        windows = $windowItems.Count
        controls = $controls
        maxDepth = $MaxDepth
        maxChildren = $MaxChildren
        packetTracerPids = $packetTracerPids
    }
    windows = $windowItems
    files = @{}
} | ConvertTo-Json -Depth 40
