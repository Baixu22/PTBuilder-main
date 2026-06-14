param(
    [int]$TimeoutSeconds = 20,
    [int]$MaxRows = 200,
    [switch]$OpenList
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

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

function Convert-ElementSummary($Element) {
    return @{
        name = Get-SafeValue { $Element.Current.Name } ""
        automationId = Get-SafeValue { $Element.Current.AutomationId } ""
        className = Get-SafeValue { $Element.Current.ClassName } ""
        controlType = Convert-ControlType (Get-SafeValue { $Element.Current.ControlType } $null)
        enabled = Get-SafeValue { $Element.Current.IsEnabled } $null
        offscreen = Get-SafeValue { $Element.Current.IsOffscreen } $null
        bounds = Convert-Rect (Get-SafeValue { $Element.Current.BoundingRectangle } $null)
    }
}

function Find-PacketTracerWindow() {
    $packetTracerPids = @()
    try {
        $packetTracerPids = @(Get-Process PacketTracer -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    } catch {
        $packetTracerPids = @()
    }

    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $windows = Get-SafeValue {
        $root.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            [System.Windows.Automation.Condition]::TrueCondition
        )
    } $null

    if (-not $windows) {
        return $null
    }

    foreach ($window in $windows) {
        $processId = Get-SafeValue { $window.Current.ProcessId } $null
        $className = Get-SafeValue { $window.Current.ClassName } ""
        $name = Get-SafeValue { $window.Current.Name } ""
        if (($packetTracerPids -contains $processId) -and $className -eq "CAppWindow") {
            return $window
        }
        if ($className -eq "CAppWindow" -and $name.IndexOf("Packet Tracer", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $window
        }
    }

    foreach ($window in $windows) {
        $processId = Get-SafeValue { $window.Current.ProcessId } $null
        $name = Get-SafeValue { $window.Current.Name } ""
        if (($packetTracerPids -contains $processId) -or $name.IndexOf("Packet Tracer", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $window
        }
    }

    return $null
}

function Find-DescendantByAutomationIdContains($Root, [string]$Needle) {
    if (-not $Root) {
        return $null
    }
    $items = Get-SafeValue {
        $Root.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        )
    } $null
    if (-not $items) {
        return $null
    }
    foreach ($item in $items) {
        $automationId = Get-SafeValue { $item.Current.AutomationId } ""
        if ($automationId.IndexOf($Needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $item
        }
    }
    return $null
}

function Try-OpenPduList($Window) {
    $toggle = Find-DescendantByAutomationIdContains $Window "UserCreatedPDU.OpenListWindowBtn"
    if (-not $toggle) {
        return $false
    }
    try {
        $pattern = $toggle.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pattern.Invoke()
        Start-Sleep -Milliseconds 300
        return $true
    } catch {
        try {
            $pattern = $toggle.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
            $pattern.Toggle()
            Start-Sleep -Milliseconds 300
            return $true
        } catch {
            return $false
        }
    }
}

function Get-PduListTree($Window) {
    $tree = Find-DescendantByAutomationIdContains $Window "UserCreatedPDU.m_PDUListView"
    if ($tree -and -not (Get-SafeValue { $tree.Current.IsOffscreen } $false)) {
        return $tree
    }
    if ($OpenList) {
        [void](Try-OpenPduList $Window)
        $tree = Find-DescendantByAutomationIdContains $Window "UserCreatedPDU.m_PDUListView"
    }
    return $tree
}

function Get-TreeChildren($Tree) {
    return Get-SafeValue {
        $Tree.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            [System.Windows.Automation.Condition]::TrueCondition
        )
    } $null
}

function Get-ColumnName([int]$Index) {
    $columns = @(
        "active",
        "lastStatus",
        "source",
        "destination",
        "type",
        "color",
        "timeSeconds",
        "periodic",
        "id",
        "edit",
        "delete"
    )
    if ($Index -lt $columns.Count) {
        return $columns[$Index]
    }
    return "column$Index"
}

function Normalize-Status([string]$Status) {
    if (-not $Status) {
        return "unknown"
    }
    $text = $Status.Trim()
    $successZh = ([string][char]0x6210) + ([string][char]0x529f)
    $failedZh = ([string][char]0x5931) + ([string][char]0x8d25)
    $failedTraditionalZh = ([string][char]0x5931) + ([string][char]0x6557)
    if ($text.Contains($successZh) -or $text -match "(?i)success|successful|succeeded") {
        return "success"
    }
    if ($text.Contains($failedZh) -or $text.Contains($failedTraditionalZh) -or $text -match "(?i)fail|failed|unsuccessful|timed out|timeout") {
        return "failed"
    }
    return "unknown"
}

function Convert-PduRows($Children) {
    $headers = @()
    $items = @()
    foreach ($child in $Children) {
        $summary = Convert-ElementSummary $child
        if ($summary.controlType -eq "Header") {
            $headers += $summary
        } elseif ($summary.controlType -eq "TreeItem") {
            $items += $summary
        }
    }

    $headers = @($headers | Sort-Object @{ Expression = { if ($_.bounds) { $_.bounds.x } else { 0 } } })
    $items = @($items | Sort-Object @{ Expression = { if ($_.bounds) { $_.bounds.y } else { 0 } } }, @{ Expression = { if ($_.bounds) { $_.bounds.x } else { 0 } } })

    $groups = @()
    foreach ($item in $items) {
        if (-not $item.bounds) {
            continue
        }
        $matched = $null
        foreach ($group in $groups) {
            if ([Math]::Abs($group.y - $item.bounds.y) -le 3) {
                $matched = $group
                break
            }
        }
        if (-not $matched) {
            $matched = [ordered]@{ y = $item.bounds.y; cells = @() }
            $groups += $matched
        }
        $matched.cells += $item
    }

    $rows = @()
    $sortedGroups = @($groups | Sort-Object @{ Expression = { $_["y"] } })
    foreach ($group in $sortedGroups) {
        if ($rows.Count -ge $MaxRows) {
            break
        }
        $cells = @($group.cells | Sort-Object @{ Expression = { if ($_.bounds) { $_.bounds.x } else { 0 } } })
        if ($cells.Count -eq 0) {
            continue
        }
        $row = [ordered]@{
            y = $group.y
            cells = @()
        }
        for ($i = 0; $i -lt $cells.Count; $i++) {
            $name = Get-ColumnName $i
            $value = [string]$cells[$i].name
            $row[$name] = $value
            $row.cells += @{
                column = $name
                name = $value
                bounds = $cells[$i].bounds
            }
        }
        $row["status"] = Normalize-Status ([string]$row["lastStatus"])
        $rows += $row
    }

    return @{
        headers = $headers
        rows = $rows
    }
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$window = $null
$tree = $null
while ((Get-Date) -lt $deadline -and -not $tree) {
    $window = Find-PacketTracerWindow
    if ($window) {
        $tree = Get-PduListTree $window
    }
    if (-not $tree) {
        Start-Sleep -Milliseconds 300
    }
}

if (-not $window) {
    @{
        ok = $false
        schema = "ptbuilder.pdu.list.v1"
        error = "packet-tracer-window-not-found"
        summary = @{ rows = 0 }
        rows = @()
    } | ConvertTo-Json -Depth 20
    exit 1
}

if (-not $tree) {
    @{
        ok = $false
        schema = "ptbuilder.pdu.list.v1"
        error = "pdu-list-view-not-found"
        window = Convert-ElementSummary $window
        summary = @{ rows = 0 }
        rows = @()
    } | ConvertTo-Json -Depth 20
    exit 1
}

$children = Get-TreeChildren $tree
if (-not $children) {
    $children = @()
}
$converted = Convert-PduRows $children

@{
    ok = $true
    schema = "ptbuilder.pdu.list.v1"
    summary = @{
        rows = @($converted.rows).Count
        headers = @($converted.headers).Count
        maxRows = $MaxRows
    }
    tree = Convert-ElementSummary $tree
    headers = $converted.headers
    rows = $converted.rows
} | ConvertTo-Json -Depth 30
