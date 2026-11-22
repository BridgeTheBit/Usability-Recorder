Add-Type -AssemblyName System.Drawing

$source = Join-Path $PSScriptRoot '..\assets\atlas-icon.png'
$destination = Join-Path $PSScriptRoot '..\assets\atlas-icon.ico'
$sizes = @(16, 24, 32, 48, 64, 128, 256)
$image = [System.Drawing.Image]::FromFile($source)
$frames = @()

try {
    foreach ($size in $sizes) {
        $bitmap = [System.Drawing.Bitmap]::new(
            $size,
            $size,
            [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
        )
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $stream = [System.IO.MemoryStream]::new()
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.DrawImage($image, 0, 0, $size, $size)
            $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
            $frames += ,$stream.ToArray()
        }
        finally {
            $stream.Dispose()
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
}
finally {
    $image.Dispose()
}

$file = [System.IO.File]::Create($destination)
$writer = [System.IO.BinaryWriter]::new($file)
try {
    $writer.Write([UInt16]0)
    $writer.Write([UInt16]1)
    $writer.Write([UInt16]$sizes.Count)

    $offset = 6 + 16 * $sizes.Count
    for ($index = 0; $index -lt $sizes.Count; $index++) {
        $size = $sizes[$index]
        $frame = $frames[$index]
        $writer.Write([byte]($size % 256))
        $writer.Write([byte]($size % 256))
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]32)
        $writer.Write([UInt32]$frame.Length)
        $writer.Write([UInt32]$offset)
        $offset += $frame.Length
    }

    foreach ($frame in $frames) {
        $writer.Write([byte[]]$frame)
    }
}
finally {
    $writer.Dispose()
    $file.Dispose()
}

Write-Output "Created $destination"
